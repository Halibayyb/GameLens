"""Turn tracking frames into internal match events.

This is the step the tests pin: tracks on the 105×68 pitch become passes,
shots, possession changes and a pressing flag. No video and no network.
"""
from __future__ import annotations

import math

from engine.models import L as PITCH_L
from engine.models import W as PITCH_W
from engine.models import xg
from feeds.schema import format_clock, format_minute
from feeds.tracking import annotate_frame, keeper_ids


def closest_player(frame: dict, ball: dict | None = None) -> dict | None:
    ball = ball or frame.get("ball")
    if not ball:
        return None
    best, dist = None, 1e9
    for player in frame.get("players") or []:
        gap = math.hypot(player["x"] - ball["x"], player["y"] - ball["y"])
        if gap < dist:
            best, dist = player, gap
    return best if best is not None and dist <= 6.0 else None


def ball_speed(prev: dict | None, cur: dict) -> float:
    if not prev or not prev.get("ball") or not cur.get("ball"):
        return 0.0
    dt = float(cur["match_seconds"]) - float(prev["match_seconds"])
    if dt <= 0:
        return 0.0
    return math.hypot(cur["ball"]["x"] - prev["ball"]["x"], cur["ball"]["y"] - prev["ball"]["y"]) / dt


def toward_goal(ball: dict, team: str, period: int = 1) -> bool:
    """Home attacks +x in period 1. A shot is a ball already in the attacking third moving that way."""
    right = (team == "home") == (period == 1)
    return ball["x"] > 70 if right else ball["x"] < (PITCH_L - 70)


def moving_toward_goal(prev_ball: dict, ball: dict, team: str, period: int = 1) -> bool:
    right = (team == "home") == (period == 1)
    dx = ball["x"] - prev_ball["x"]
    return dx > 0.4 if right else dx < -0.4


def in_box(ball: dict, team: str, period: int = 1) -> bool:
    """Opposition penalty area, or over the goal line."""
    right = (team == "home") == (period == 1)
    return ball["x"] >= 88.5 if right else ball["x"] <= (PITCH_L - 88.5)


def pressing(frame: dict, carrier: dict | None) -> bool:
    if not carrier or carrier.get("team") not in ("home", "away"):
        return False
    opp = "away" if carrier["team"] == "home" else "home"
    n = 0
    for player in frame.get("players") or []:
        if player.get("team") != opp:
            continue
        if math.hypot(player["x"] - carrier["x"], player["y"] - carrier["y"]) <= 10:
            n += 1
    return n >= 3


def derive_events(frames: list[dict], teams: dict | None = None, match_id: str = "HIGHLIGHT") -> list[dict]:
    """Return event dicts (no event_id) from annotated tracking frames."""
    names = teams or {"home": "Home", "away": "Away"}
    keepers = set()
    annotated = [annotate_frame(dict(frame), keepers) for frame in frames]
    out: list[dict] = []
    score = {"home": 0, "away": 0}
    prev = None
    last_carrier = None
    last_poss = None
    last_poss_since = None
    last_press = -10.0
    last_shot = -10.0
    pass_from = None

    def emit(frame, etype, team, player, x, y, **extra):
        t = float(frame["match_seconds"])
        period = int(frame.get("period") or 1)
        side = team if team in ("home", "away") else None
        pid = player.get("player_id") if player else None
        row = {
            "match_id": match_id,
            "period": period,
            "clock": frame.get("clock") or format_clock(period, t),
            "minute": format_minute(period, t),
            "match_seconds": round(t, 2),
            "type": etype,
            "team": side,
            "team_name": names.get(side) if side else None,
            "player_id": pid,
            "player_name": (player or {}).get("name") or (f"{names.get(side, '')} {pid}" if pid else None),
            "x": round(float(x), 2),
            "y": round(float(y), 2),
            "score": f'{score["home"]}-{score["away"]}',
            "synthetic": extra.pop("synthetic", False),
        }
        row.update(extra)
        out.append(row)
        return row

    for frame in annotated:
        ball = frame.get("ball")
        poss = frame.get("possession")
        carrier = closest_player(frame, ball)
        t = float(frame["match_seconds"])
        period = int(frame.get("period") or 1)

        if poss in ("home", "away"):
            if last_poss is None:
                last_poss, last_poss_since = poss, t
            elif poss != last_poss:
                if last_poss_since is not None and t - last_poss_since >= 0.3 and ball:
                    emit(frame, "possession", poss, carrier, ball["x"], ball["y"])
                last_poss, last_poss_since = poss, t

        if carrier and pressing(frame, carrier) and t - last_press >= 2.0:
            emit(frame, "pressing", carrier["team"], carrier, carrier["x"], carrier["y"])
            last_press = t

        # Shot before a keeper standing on the line is treated as the new carrier.
        if prev and ball and last_carrier:
            speed = ball_speed(prev, frame)
            team = last_carrier.get("team")
            moving = moving_toward_goal(prev["ball"], ball, team, period) if prev.get("ball") and team else False
            boxed = in_box(ball, team, period) if team else False
            if speed >= 12 and team and moving and toward_goal(ball, team, period) and boxed and t - last_shot >= 1.2:
                right = (team == "home") == (period == 1)
                goal_x = PITCH_L if right else 0.0
                dist = math.hypot(goal_x - ball["x"], PITCH_W / 2 - ball["y"])
                value = round(xg(ball["x"], ball["y"], right), 3)
                outcome = "off_target"
                if (right and ball["x"] >= 102) or ((not right) and ball["x"] <= 3):
                    outcome = "goal"
                elif speed < 18 and 20 < ball["y"] < 48:
                    outcome = "saved"
                emit(frame, "shot", team, last_carrier, ball["x"], ball["y"],
                     outcome=outcome, xg=value, shot_type="open_play", body_part="foot",
                     distance_m=round(dist, 1))
                if outcome == "goal" and team in score:
                    score[team] += 1
                    emit(frame, "goal", team, last_carrier, ball["x"], ball["y"])
                last_shot = t
                last_carrier = None
                pass_from = None
                prev = frame
                continue

        if last_carrier and carrier and ball and last_carrier["player_id"] != carrier["player_id"]:
            if last_carrier.get("team") == carrier.get("team") and pass_from:
                start, start_t, starter = pass_from
                length = math.hypot(ball["x"] - start[0], ball["y"] - start[1])
                if length >= 6 and t - start_t <= 3.0:
                    emit(frame, "pass", carrier["team"], starter, start[0], start[1],
                         outcome="complete", end_x=round(ball["x"], 2), end_y=round(ball["y"], 2),
                         receiver_id=carrier["player_id"], receiver_name=carrier.get("name"),
                         length_m=round(length, 1), pass_type="open_play")
            elif last_carrier.get("team") and carrier.get("team") and last_carrier["team"] != carrier["team"] and ball:
                emit(frame, "interception", carrier["team"], carrier, ball["x"], ball["y"])
            pass_from = None

        if carrier and ball:
            pass_from = ((carrier["x"], carrier["y"]), t, carrier)
            last_carrier = carrier

        prev = frame

    if annotated:
        last = annotated[-1]
        ball = last.get("ball") or {}   # None when the clip ends without a ball detection
        emit(last, "period_end", None, None, ball.get("x") or 52.5, ball.get("y") or 34.0, period_name="clip end")
    return out


def frames_to_rows(frames: list[dict]) -> list[dict]:
    """Flatten annotated frames into ``tracking.csv`` rows."""
    rows = []
    for frame in frames:
        annotated = annotate_frame(dict(frame), keeper_ids({"teams": {}}))
        poss = annotated.get("possession") or ""
        in_play = 1 if annotated.get("ball") else 0
        if annotated.get("ball"):
            rows.append(_row(annotated, "ball", "", annotated["ball"]["x"], annotated["ball"]["y"], poss, in_play))
        for player in annotated.get("players") or []:
            rows.append(_row(annotated, player["player_id"], player.get("team") or "",
                             player["x"], player["y"], poss, in_play))
    return rows


def _row(frame, oid, team, x, y, poss, in_play) -> dict:
    return {
        "frame": frame.get("frame", 0),
        "period": frame.get("period", 1),
        "match_seconds": frame["match_seconds"],
        "clock": frame.get("clock", ""),
        "object_id": oid,
        "team": team,
        "x": x,
        "y": y,
        "ball_possession": poss,
        "ball_in_play": in_play,
    }

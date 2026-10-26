"""Structured game context for one timestamp.

Every field is computed from tracking frames and internal events. The
explainer is allowed to rephrase these facts and nothing else.
"""
from __future__ import annotations

import math

from engine.models import L, W, pass_success, to_frame, xg

GRID_X, GRID_Y = 21, 14
CHAIN_WINDOW_S = 12.0
MOMENTUM_S = 60.0
LANE_MAX_M = 35.0

KIND_RANK = ("build_up", "line_break", "assist", "shot", "goal")


def xt(x: float, y: float, attack_right: bool) -> float:
    """Coarse expected-threat: how dangerous a location is if the attack keeps the ball."""
    u, v = to_frame(x, y, attack_right)
    dist = math.hypot(L - u, 34 - v)
    return round(max(0.01, min(0.45, 0.42 * math.exp(-dist / 28))), 3)


def frame_at(frames: list[dict], t: float) -> dict | None:
    if not frames:
        return None
    best = frames[0]
    for frame in frames:
        if frame["match_seconds"] <= t + 1e-6:
            best = frame
        else:
            break
    return best


def attack_right(team: str, period: int) -> bool:
    return (team == "home") == (period == 1)


def team_shape(players: list[dict], team: str, period: int) -> dict:
    side = [p for p in players if p.get("team") == team]
    outfield = [p for p in side if p.get("role") != "GK"]
    if not outfield:
        return {"team": team, "n": len(side), "width_m": None, "length_m": None,
                "compactness_m": None, "centroid": None, "line": None}
    xs = [p["x"] for p in outfield]
    ys = [p["y"] for p in outfield]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    compact = sum(math.hypot(p["x"] - cx, p["y"] - cy) for p in outfield) / len(outfield)
    own_left = (team == "home") == (period == 1)
    line = min(xs) if own_left else max(xs)
    return {
        "team": team,
        "n": len(side),
        "width_m": round(max(ys) - min(ys), 1),
        "length_m": round(max(xs) - min(xs), 1),
        "compactness_m": round(compact, 1),
        "centroid": {"x": round(cx, 1), "y": round(cy, 1)},
        "line": round(line, 1),
    }


def voronoi_control(players: list[dict]) -> dict:
    """Nearest-player share of a coarse pitch grid. Home + away = 1 when anyone is on the pitch."""
    home = away = 0
    for i in range(GRID_X):
        for j in range(GRID_Y):
            x = (i + 0.5) * L / GRID_X
            y = (j + 0.5) * W / GRID_Y
            best, dist = None, 1e18
            for player in players:
                gap = (player["x"] - x) ** 2 + (player["y"] - y) ** 2
                if gap < dist:
                    best, dist = player, gap
            if best and best.get("team") == "home":
                home += 1
            elif best and best.get("team") == "away":
                away += 1
    total = home + away
    if not total:
        return {"home": 0.5, "away": 0.5, "cells": 0}
    return {"home": round(home / total, 3), "away": round(away / total, 3), "cells": total}


def defenders_bypassed(start: tuple, end: tuple, defenders: list[tuple], right: bool) -> int:
    su = to_frame(start[0], start[1], right)[0]
    eu = to_frame(end[0], end[1], right)[0]
    lo, hi = min(su, eu), max(su, eu)
    n = 0
    for dx, dy in defenders:
        u = to_frame(dx, dy, right)[0]
        if lo + 1 < u < hi - 1:
            n += 1
    return n


def pressing_intensity(frame: dict, carrier: dict | None) -> dict:
    if not carrier or carrier.get("team") not in ("home", "away"):
        return {"intensity": 0.0, "opponents_within_10": 0, "nearest_m": None}
    opp = "away" if carrier["team"] == "home" else "home"
    gaps = []
    for player in frame.get("players") or []:
        if player.get("team") != opp:
            continue
        gaps.append(math.hypot(player["x"] - carrier["x"], player["y"] - carrier["y"]))
    near = sum(1 for g in gaps if g <= 10)
    nearest = min(gaps) if gaps else None
    intensity = min(1.0, near / 4.0)
    return {"intensity": round(intensity, 2), "opponents_within_10": near,
            "nearest_m": None if nearest is None else round(nearest, 1)}


def passing_lanes(frame: dict, carrier: dict, period: int) -> list[dict]:
    if not carrier or not frame.get("ball"):
        return []
    team = carrier["team"]
    right = attack_right(team, period)
    defenders = [(p["x"], p["y"]) for p in frame.get("players") or [] if p.get("team") and p["team"] != team]
    start = (carrier["x"], carrier["y"])
    here = xt(carrier["x"], carrier["y"], right)
    out = []
    for mate in frame.get("players") or []:
        if mate.get("team") != team or mate["player_id"] == carrier["player_id"]:
            continue
        if mate.get("role") == "GK":
            continue
        end = (mate["x"], mate["y"])
        length = math.dist(start, end)
        if length > LANE_MAX_M or length < 3:
            continue
        success = pass_success(start, end, defenders)
        value = xt(mate["x"], mate["y"], right)
        out.append({
            "receiver": mate.get("name") or mate["player_id"],
            "receiver_id": mate["player_id"],
            "success": round(success, 3),
            "xt": value,
            "xt_gain": round(value - here, 3),
            "distance_m": round(length, 1),
            "bypassed": defenders_bypassed(start, end, defenders, right),
            "end_x": round(mate["x"], 1),
            "end_y": round(mate["y"], 1),
        })
    out.sort(key=lambda row: row["success"] * max(0.01, row["xt"]), reverse=True)
    return out[:6]


def possession_chain(events: list[dict], t: float, team: str | None) -> list[dict]:
    chain = []
    for event in events:
        if event["match_seconds"] > t + 1e-6:
            break
        if t - event["match_seconds"] > CHAIN_WINDOW_S:
            continue
        if event["type"] in ("possession", "interception") and event.get("team") and event["team"] != team:
            chain = []
            continue
        if event["type"] in ("pass", "shot", "goal", "pressing", "carry"):
            chain.append({
                "type": event["type"],
                "clock": event.get("clock"),
                "match_seconds": event["match_seconds"],
                "player_name": event.get("player_name"),
                "team": event.get("team"),
                "length_m": event.get("length_m"),
                "outcome": event.get("outcome"),
                "xg": event.get("xg"),
            })
    return chain[-8:]


def momentum(events: list[dict], t: float, window: float = MOMENTUM_S) -> dict:
    shots = xg_sum = passes = 0
    for event in events:
        if event["match_seconds"] > t or t - event["match_seconds"] > window:
            continue
        if event["type"] == "shot":
            shots += 1
            xg_sum += float(event.get("xg") or 0)
        elif event["type"] == "pass" and event.get("outcome") == "complete":
            passes += 1
    return {"window_s": window, "shots": shots, "xg": round(xg_sum, 3), "passes": passes}


def score_state(score: str, team: str | None) -> str:
    try:
        home, away = [int(p) for p in str(score).split("-")]
    except (ValueError, TypeError):
        return "unknown"
    if team == "away":
        home, away = away, home
    if home > away:
        return "winning"
    if home < away:
        return "losing"
    return "drawing"


def principles_from(ctx: dict) -> list[str]:
    """Tactical labels that the numbers actually support. Empty if none apply."""
    found = []
    event = ctx.get("event") or {}
    ball = ctx.get("ball") or {}
    press = ctx.get("pressing") or {}
    kind = ctx.get("kind")
    x, y = ball.get("x"), ball.get("y")
    if kind == "line_break" or (event.get("type") == "pass" and (event.get("length_m") or 0) >= 12):
        found.append("line-breaking pass")
    if (press.get("opponents_within_10") or 0) >= 3:
        found.append("press")
    if x is not None and y is not None:
        if x >= 70 and (18 <= y <= 28 or 40 <= y <= 50):
            found.append("half-space")
        if x >= 88 and (y <= 20 or y >= 48):
            found.append("cutback zone")
    lanes = ctx.get("lanes") or []
    if lanes and lanes[0].get("bypassed", 0) >= 2:
        found.append("third-man run")
    shapes = ctx.get("shapes") or {}
    poss = ctx.get("possession")
    if poss in shapes and (shapes[poss] or {}).get("n") and (shapes[poss].get("compactness_m") or 99) <= 14:
        found.append("rest defence")
    near = 0
    if x is not None and y is not None:
        # Overload: more teammates than opponents in a 18 m pocket around the ball, from lane + press counts.
        near = max(0, (ctx.get("attackers_nearby") or 0) - (press.get("opponents_within_10") or 0))
        if near >= 1 and (ctx.get("attackers_nearby") or 0) >= 3:
            found.append("overload")
    # unique, stable order
    order = ["line-breaking pass", "third-man run", "overload", "half-space",
             "cutback zone", "rest defence", "press"]
    return [name for name in order if name in found]


def attackers_nearby(frame: dict, carrier: dict | None, radius: float = 18.0) -> int:
    if not carrier:
        return 0
    n = 0
    for player in frame.get("players") or []:
        if player.get("team") != carrier.get("team"):
            continue
        if math.hypot(player["x"] - carrier["x"], player["y"] - carrier["y"]) <= radius:
            n += 1
    return n


def pick_key_moments(events: list[dict], frames: list[dict] | None = None) -> list[dict]:
    """Build-up start, line-breaking pass, assist, shot, goal."""
    out: list[dict] = []
    last_poss = None
    last_pass_at = -100.0
    pending_pass = None

    def add(kind, event, title):
        out.append({
            "id": f"xm_{kind}_{event.get('event_id', '')}_{event['match_seconds']}",
            "kind": kind,
            "title": title,
            "match_seconds": event["match_seconds"],
            "clock": event.get("clock"),
            "team": event.get("team"),
            "team_name": event.get("team_name"),
            "player_name": event.get("player_name"),
            "event_type": event["type"],
            "event": {k: event.get(k) for k in (
                "type", "team", "team_name", "player_name", "player_id",
                "x", "y", "outcome", "length_m", "xg", "receiver_name", "score",
            )},
        })

    for event in events:
        if event["type"] == "possession":
            last_poss = event.get("team")
            pending_pass = None
        if event["type"] == "pass" and event.get("outcome") == "complete":
            if pending_pass is None or event["match_seconds"] - last_pass_at >= 3 or event.get("team") != last_poss:
                add("build_up", event, "Build-up")
            progress = 0.0
            if event.get("end_x") is not None:
                right = attack_right(event.get("team") or "home", event.get("period") or 1)
                start_u = to_frame(event["x"], event["y"], right)[0]
                end_u = to_frame(event["end_x"], event.get("end_y") or event["y"], right)[0]
                progress = end_u - start_u
            line = None
            if frames:
                fr = frame_at(frames, event["match_seconds"])
                opp = "away" if event.get("team") == "home" else "home"
                line = (fr or {}).get("defensive_line", {}).get(opp)
            crossed = False
            if line is not None and event.get("end_x") is not None:
                right = attack_right(event.get("team") or "home", event.get("period") or 1)
                if right and event["x"] < line <= event["end_x"]:
                    crossed = True
                if (not right) and event["x"] > line >= event["end_x"]:
                    crossed = True
            if progress >= 12 or (event.get("length_m") or 0) >= 18 or crossed:
                add("line_break", event, "Line-breaking pass")
            pending_pass = event
            last_pass_at = event["match_seconds"]
            last_poss = event.get("team")
        if event["type"] == "shot":
            if pending_pass and pending_pass.get("team") == event.get("team"):
                if event["match_seconds"] - pending_pass["match_seconds"] <= 8:
                    add("assist", pending_pass, "Assist")
            add("shot", event, "Shot" if event.get("outcome") != "goal" else "Shot")
        if event["type"] == "goal":
            add("goal", event, "Goal")
    # stable order on the clock, then specificity
    rank = {k: i for i, k in enumerate(KIND_RANK)}
    out.sort(key=lambda m: (m["match_seconds"], rank.get(m["kind"], 9)))
    return out


def build_context(
    frames: list[dict],
    events: list[dict],
    teams: dict,
    t: float,
    event: dict | None = None,
    analysis: dict | None = None,
    kind: str | None = None,
    source: str | None = None,
    synthetic: bool = True,
) -> dict:
    frame = frame_at(frames, t) or {}
    period = int(frame.get("period") or (event or {}).get("period") or 1)
    ball = frame.get("ball") or ({"x": event["x"], "y": event["y"]} if event and event.get("x") is not None else None)
    poss = frame.get("possession") or (event or {}).get("team")
    carrier = _closest(frame, ball) if ball else None
    if event and event.get("type") == "pass" and event.get("x") is not None:
        passer = None
        if event.get("player_id"):
            passer = next((p for p in frame.get("players") or [] if p["player_id"] == event["player_id"]), None)
        if passer:
            carrier = {**passer, "x": event["x"], "y": event["y"]}
        elif event.get("player_name"):
            carrier = {"player_id": event.get("player_id") or "passer", "name": event["player_name"],
                       "team": event.get("team"), "x": event["x"], "y": event["y"], "role": "OUT"}
    if carrier is None and event and event.get("player_id"):
        carrier = next((p for p in frame.get("players") or [] if p["player_id"] == event["player_id"]), None)
    score = (event or {}).get("score") or _score_at(events, t)
    team = (event or {}).get("team") or poss
    right = attack_right(team, period) if team in ("home", "away") else True
    shot_xg = None
    if event and event.get("type") == "shot":
        shot_xg = event.get("xg")
    elif ball and team in ("home", "away"):
        shot_xg = round(xg(ball["x"], ball["y"], right), 3)
    lanes = passing_lanes(frame, carrier, period) if carrier else []
    decision = None
    if analysis and analysis.get("available"):
        decision = {
            "chosen": analysis.get("chosen"),
            "best": analysis.get("best"),
            "gap": analysis.get("gap"),
            "verdict": analysis.get("verdict"),
            "options": [
                {"key": o["key"], "label": o["label"], "reward": o["reward"], "risk": o["risk"],
                 "chosen": o.get("chosen"), "best": o.get("best")}
                for o in analysis.get("options") or []
            ],
        }
    ctx = {
        "source": source,
        "synthetic": synthetic,
        "kind": kind,
        "clock": frame.get("clock") or (event or {}).get("clock"),
        "minute": (event or {}).get("minute"),
        "match_seconds": round(t, 2),
        "period": period,
        "score": score,
        "game_state": score_state(score, team),
        "teams": teams,
        "possession": poss,
        "event": None if not event else {
            "type": event.get("type"),
            "team": event.get("team"),
            "team_name": event.get("team_name") or teams.get(event.get("team")),
            "player_name": event.get("player_name"),
            "x": event.get("x"),
            "y": event.get("y"),
            "outcome": event.get("outcome"),
            "length_m": event.get("length_m"),
            "xg": event.get("xg"),
            "receiver_name": event.get("receiver_name"),
        },
        "carrier": None if not carrier else {
            "name": carrier.get("name"), "team": carrier.get("team"),
            "x": round(carrier["x"], 1), "y": round(carrier["y"], 1),
        },
        "ball": None if not ball else {"x": round(ball["x"], 1), "y": round(ball["y"], 1)},
        "shapes": {
            "home": team_shape(frame.get("players") or [], "home", period),
            "away": team_shape(frame.get("players") or [], "away", period),
        },
        "defensive_line": frame.get("defensive_line"),
        "counter_risk": frame.get("counter_risk"),
        "pressing": pressing_intensity(frame, carrier),
        "pitch_control": voronoi_control(frame.get("players") or []),
        "lanes": lanes,
        "attackers_nearby": attackers_nearby(frame, carrier),
        "shot_xg": shot_xg,
        "decision": decision,
        "chain": possession_chain(events, t, team),
        "momentum": momentum(events, t),
        "attribution": (
            "Computed from player/ball tracking and derived events. "
            "No broadcast footage and no stats outside this clip."
        ),
    }
    ctx["principles"] = principles_from(ctx)
    return ctx


def _closest(frame: dict, ball: dict | None) -> dict | None:
    if not ball:
        return None
    best, dist = None, 1e9
    for player in frame.get("players") or []:
        gap = math.hypot(player["x"] - ball["x"], player["y"] - ball["y"])
        if gap < dist:
            best, dist = player, gap
    return best if best is not None and dist <= 6.0 else None


def _score_at(events: list[dict], t: float) -> str:
    score = "0-0"
    for event in events:
        if event["match_seconds"] <= t and event.get("score"):
            score = event["score"]
    return score

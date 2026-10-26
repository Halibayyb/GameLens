"""Passes, shots and goals from tracked frames, so a vision clip feeds ``engine.assist`` unchanged.

Input is the ``track_frames`` shape (world metres, 105 x 68). A touch is a player within TOUCH_M of the ball
on a frame where the ball bends or slows: a ball flying past a defender in the lane is not a touch. A pass is
the passer's last touch followed by a teammate's touch; a shot is the last touch before the ball reaches a
goal line, and a goal when it crosses between the posts. The shooter's team attacks towards that goal.

Lofted passes are not observable from a ground-plane projection, so ``aerial`` is False and flagged in debug.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

from engine import models
from feeds.schema import format_clock, format_minute

L, W = 105.0, 68.0
POST_LO, POST_HI = 30.34, 37.66
TOUCH_M = 1.6          # ball within this of a player can be a touch
SLOW_MS = 3.0          # a ball this slow near a player is under control
KINK_DEG = 25.0        # direction change that marks a touch on a moving ball
SPEED_DROP = 0.6       # speed ratio below this either way marks a touch (a trap or a kick)
GOAL_LINE_M = 1.5      # ball this close to a goal line has reached it
SHOT_ZONE_M = 6.0      # ball this close to a goal line (inside the box width) after the last touch: a shot
GOAL_EST_M = 5.0       # a shot whose ball ends this close to the line inside the box: an estimated goal
BOX_LO, BOX_HI = 13.84, 54.16   # penalty box width
RUN_MS, TRACE_SLACK_M = 9.0, 2.5   # a player's top speed and the tracking noise allowed per step
MAX_GAP_S = 1.0        # ball gaps up to this long are interpolated


def _ball_series(frames: list[dict]) -> list[tuple[float, float] | None]:
    pts = [(f["ball"]["x"], f["ball"]["y"]) if f.get("ball") else None for f in frames]
    times = [f["match_seconds"] for f in frames]
    known = [i for i, p in enumerate(pts) if p]
    for a, b in zip(known, known[1:]):
        if b - a > 1 and times[b] - times[a] <= MAX_GAP_S:
            for i in range(a + 1, b):
                u = (times[i] - times[a]) / (times[b] - times[a])
                pts[i] = (pts[a][0] + u * (pts[b][0] - pts[a][0]), pts[a][1] + u * (pts[b][1] - pts[a][1]))
    return pts


def _is_touch(pts, times, i) -> bool:
    """The ball bends, slows sharply or is slow at frame i."""
    prev, cur, nxt = (pts[i - 1] if i else None), pts[i], (pts[i + 1] if i + 1 < len(pts) else None)
    if prev is None or nxt is None:
        return True
    dt_in, dt_out = times[i] - times[i - 1] or 1e-6, times[i + 1] - times[i] or 1e-6
    vin = ((cur[0] - prev[0]) / dt_in, (cur[1] - prev[1]) / dt_in)
    vout = ((nxt[0] - cur[0]) / dt_out, (nxt[1] - cur[1]) / dt_out)
    sin, sout = math.hypot(*vin), math.hypot(*vout)
    if min(sin, sout) < SLOW_MS:
        return True
    turn = abs((math.degrees(math.atan2(vout[1], vout[0]) - math.atan2(vin[1], vin[0])) + 180) % 360 - 180)
    return turn > KINK_DEG or sout / sin < SPEED_DROP or sin / sout < SPEED_DROP   # bend, trap or kick


def _at_body(ball_box, player_box) -> bool:
    """The ball box centre is on the player's body in the image (feet to chest, a little to each side).

    A lofted ball crossing above a player is mapped onto the grass near him; in the image it is above his head."""
    bx, by = (ball_box[0] + ball_box[2]) / 2, (ball_box[1] + ball_box[3]) / 2
    x1, y1, x2, y2 = player_box
    w, h = x2 - x1, y2 - y1
    return x1 - 0.4 * w <= bx <= x2 + 0.4 * w and y1 + 0.3 * h <= by <= y2 + 0.2 * h


def touches(frames: list[dict]) -> list[dict]:
    """Runs of touches by one player: {"player", "first", "last"} as frame indexes.

    With image boxes (vision frames) the ball must also be on the player's body in the image."""
    pts, times = _ball_series(frames), [f["match_seconds"] for f in frames]
    out: list[dict] = []
    for i, f in enumerate(frames):
        if pts[i] is None:
            continue
        near = min(((math.dist(pts[i], (p["x"], p["y"])), p) for p in f["players"] if p.get("team") in ("home", "away")),
                   default=(math.inf, None), key=lambda d: d[0])
        if near[0] > TOUCH_M or not _is_touch(pts, times, i):
            continue
        ball_box, player_box = (f.get("ball") or {}).get("xyxy"), near[1].get("xyxy")
        if ball_box and player_box and not _at_body(ball_box, player_box):
            continue
        pid = near[1]["player_id"]
        if out and out[-1]["player"]["player_id"] == pid:
            out[-1]["last"] = i
        else:
            out.append({"player": near[1], "first": i, "last": i})
    return out


def trace_back(frames: list[dict], pid: str, start: int, stop: int) -> str | None:
    """The id that player ``pid`` (seen at frame ``start``) had at the earlier frame ``stop``.

    A tracker can re-number a player during a pan; walking back frame by frame to the nearest teammate within
    running distance recovers who he was when the pass was played. None when the chain breaks."""
    me = next((p for p in frames[start]["players"] if p["player_id"] == pid), None)
    if me is None:
        return None
    team, at, seen = me["team"], (me["x"], me["y"]), frames[start]["match_seconds"]
    for j in range(start - 1, stop - 1, -1):
        mates = [p for p in frames[j]["players"] if p.get("team") == team]
        if not mates:
            continue   # an unmapped frame (a pan): the running budget keeps growing until he is seen again
        dt = max(0.05, seen - frames[j]["match_seconds"])
        same = next((p for p in mates if p["player_id"] == pid), None)
        best = same or min(mates, key=lambda p: math.dist(at, (p["x"], p["y"])))
        if math.dist(at, (best["x"], best["y"])) > RUN_MS * dt + TRACE_SLACK_M:
            if j == stop:
                return None
            continue   # he was not detected in this frame; the nearest teammate is someone else
        pid, at, seen = best["player_id"], (best["x"], best["y"]), frames[j]["match_seconds"]
    return pid if any(p["player_id"] == pid for p in frames[stop]["players"]) else None


def _in_box_width(y: float) -> bool:
    return BOX_LO <= y <= BOX_HI


def _shot_target(pts, start: int):
    """After the last touch, the first frame with the ball near a goal line inside the box width:
    (index, goal_x) or None."""
    for i in range(start + 1, len(pts)):
        p = pts[i]
        if p and _in_box_width(p[1]) and (p[0] <= SHOT_ZONE_M or p[0] >= L - SHOT_ZONE_M):
            return i, (0.0 if p[0] < L / 2 else L)
    return None


def _outcome(pts, start: int, goal_x: float, frames, scene_goals) -> tuple[str, int, str]:
    """(outcome, frame index of the goal or the end, source): crossed between the posts, a scene-model goal in this
    play, or the ball ending close to that goal line inside the box (broadcast mapping is rough near the goal)."""
    t0, t1 = frames[start]["match_seconds"], frames[-1]["match_seconds"]
    line = lambda p: abs(p[0] - goal_x)
    for i in range(start + 1, len(pts)):
        p = pts[i]
        if p and line(p) <= GOAL_LINE_M and POST_LO - 0.5 <= p[1] <= POST_HI + 0.5:
            return "goal", i, "line"
    if any(t0 - 1.0 <= g <= t1 + 1.0 for g in scene_goals):
        return "goal", len(frames) - 1, "scene"
    last = next((i for i in range(len(pts) - 1, start, -1) if pts[i]), None)
    if last is not None and line(pts[last]) <= GOAL_EST_M and _in_box_width(pts[last][1]):
        return "goal", last, "estimated"
    return "off_target", last if last is not None else start, "none"


def plays(frames: list[dict]) -> list[list[int]]:
    """Frame indexes per camera shot (consecutive frames with the same shot id): a montage cut starts a new play."""
    out: list[list[int]] = []
    last = object()
    for i, f in enumerate(frames):
        sid = (f.get("shot") or {}).get("id", 0)
        if sid != last:
            out.append([])
            last = sid
        out[-1].append(i)
    return out


def detect_events(frames: list[dict], fps: float, hints: dict | None = None) -> dict:
    """Pass, shot and goal events in the events.jsonl shape, plus the attack direction per team.

    Each camera play is read on its own (a highlight montage can hold several matches), and each pass and shot
    carries its play's ``attack_right``. hints: ``vision.scene_llm.to_event_hints`` output. A matching hint
    (same receiver, near in time) supplies what a ground-plane track cannot see: lofted (aerial), into space
    (into_space), header; a scene-model goal in a play confirms that play's goal.
    """
    events: list[dict] = []
    debug = {"touches": [], "plays": [], "aerial": "not observable; passes default to ground"}
    attack_right: dict[str, bool] = {}
    for n, idx in enumerate(plays(frames)):
        found, right, play_debug = _detect_play([frames[i] for i in idx], hints, n)
        events += found
        attack_right.update(right)
        debug["touches"] += play_debug["touches"]
        debug["plays"].append(play_debug)
    return {"events": events, "attack_right": attack_right, "debug": debug}


SCENE_MIN_CONF = 0.6   # a scene-model fact this sure may name the passer, receiver or shooter


def _scene_facts(frames: list[dict], hints: dict | None) -> tuple[list[dict], dict | None]:
    """The scene model's passes and last shot inside this play whose ids the tracker has (positions stay CV's)."""
    if not hints or not frames:
        return [], None
    t0, t1 = frames[0]["match_seconds"] - 0.3, frames[-1]["match_seconds"] + 0.3
    have = {p["player_id"] for f in frames for p in f["players"]}
    passes = [p for p in hints.get("passes") or [] if t0 <= p["t"] <= t1 and p.get("actor_id") in have
              and p.get("receiver_id") in have and (p.get("confidence") or 0) >= SCENE_MIN_CONF]
    shots = [s for s in hints.get("shots") or [] if t0 <= s["t"] <= t1 and s["actor_id"] in have
             and (s.get("confidence") or 0) >= SCENE_MIN_CONF]
    return passes, (max(shots, key=lambda s: s["t"]) if shots else None)


def _nearest_with(frames: list[dict], pid: str, t: float, after: bool = False) -> int | None:
    """Index of the frame nearest time t (at or after t when ``after``) in which player pid is tracked."""
    idx = [i for i, f in enumerate(frames) if any(p["player_id"] == pid for p in f["players"])
           and (not after or f["match_seconds"] >= t - 0.05)]
    return min(idx, key=lambda i: abs(frames[i]["match_seconds"] - t)) if idx else None


def _player(frame: dict, pid: str) -> dict:
    return next(p for p in frame["players"] if p["player_id"] == pid)


def _detect_play(frames: list[dict], hints: dict | None, play: int) -> tuple[list[dict], dict, dict]:
    pts = _ball_series(frames)
    runs = touches(frames)
    scene_passes, scene_shot = _scene_facts(frames, hints)
    if scene_shot and runs:   # the scene model saw who struck it; CV's nearest player may be a defender
        k = _nearest_with(frames, scene_shot["actor_id"], scene_shot["t"])
        if k is not None and runs[-1]["player"]["player_id"] != scene_shot["actor_id"]:
            runs = [r for r in runs if frames[r["first"]]["match_seconds"] < frames[k]["match_seconds"] - 0.1]
            runs.append({"player": _player(frames[k], scene_shot["actor_id"]), "first": k, "last": k, "scene": True})
    events: list[dict] = []
    t0 = frames[0]["match_seconds"] if frames else 0.0
    debug = {"play": play, "start": t0, "end": frames[-1]["match_seconds"] if frames else 0.0,
             "touches": [(frames[r["first"]]["match_seconds"], r["player"]["player_id"]) for r in runs]}
    attack_right: dict[str, bool] = {}
    if not runs:
        return events, attack_right, debug
    last = runs[-1]
    target = _shot_target(pts, last["last"])
    if target is None and last.get("scene") and pts[last["last"]]:   # a seen shot the ball track lost: nearer goal
        target = (last["last"], 0.0 if pts[last["last"]][0] < L / 2 else L)
    shot, right = None, None
    if target:
        k, goal_x = target
        shooter, team = last["player"], last["player"]["team"]
        right = goal_x == L
        attack_right[team] = right
        attack_right["away" if team == "home" else "home"] = not right
        x, y = pts[last["last"]]
        outcome, goal_index, source = _outcome(pts, last["last"], goal_x, frames, (hints or {}).get("goals") or [])
        shot = {"index": last["last"], "player": shooter, "x": x, "y": y, "outcome": outcome, "source": source,
                "xg": round(models.xg(x, y, right), 3), "goal_index": goal_index}
        debug["shot"] = {"t": frames[last["last"]]["match_seconds"], "by": shooter["player_id"], "outcome": outcome,
                         "source": source}
    # the scene model's passes (identity from the model, positions from the tracked frames) come first
    for h in scene_passes:
        i = _nearest_with(frames, h["actor_id"], h["t"])
        j = _nearest_with(frames, h["receiver_id"], h["t"] + 0.4, after=True)
        if i is None or j is None or j <= i:
            continue
        pa, pb = _player(frames[i], h["actor_id"]), _player(frames[j], h["receiver_id"])
        if pa["team"] != pb["team"]:
            continue
        extra = {"attack_right": right} if right is not None and pa["team"] == last["player"]["team"] else {}
        if not any(p["player_id"] == pb["player_id"] for p in frames[i]["players"]):
            extra["receiver_at_pass"] = trace_back(frames, pb["player_id"], j, i)
        events.append(_event(frames[i], "pass", pa, pa["x"], pa["y"], outcome="complete", receiver_id=pb["player_id"],
                             receiver_name=pb.get("name"), end_x=round(pb["x"], 3), end_y=round(pb["y"], 3),
                             aerial=bool(h.get("aerial")), into_space=bool(h.get("into_space")),
                             p_header=h.get("p_header"), hinted=True, source="scene", play=play, **extra))
    scene_receivers = {e["receiver_id"] for e in events}
    # every pass between teammates; the last one to the shooter is the assist
    for a, b in zip(runs, runs[1:]):
        pa, pb = a["player"], b["player"]
        if pa["team"] != pb["team"] or pa["player_id"] == pb["player_id"]:
            continue
        sx, sy = pts[a["last"]]
        ex, ey = pts[b["first"]]
        if math.dist((sx, sy), (ex, ey)) < 3:
            continue
        if pb["player_id"] in scene_receivers:
            continue   # the scene model already gave this pass, with the passer it saw
        hint = _hint(hints, pb["player_id"], frames[a["last"]]["match_seconds"])
        extra = {"attack_right": right} if right is not None and pa["team"] == last["player"]["team"] else {}
        if not any(p["player_id"] == pb["player_id"] for p in frames[a["last"]]["players"]):
            extra["receiver_at_pass"] = trace_back(frames, pb["player_id"], b["first"], a["last"])
        events.append(_event(frames[a["last"]], "pass", pa, sx, sy, outcome="complete", receiver_id=pb["player_id"],
                             receiver_name=pb.get("name"), end_x=round(ex, 3), end_y=round(ey, 3),
                             aerial=bool(hint.get("aerial")), into_space=bool(hint.get("into_space")),
                             p_header=hint.get("p_header"), hinted=bool(hint), play=play, **extra))
    if shot:
        events.append(_event(frames[shot["index"]], "shot", shot["player"], shot["x"], shot["y"],
                             outcome=shot["outcome"], xg=shot["xg"], play=play, attack_right=right,
                             outcome_source=shot["source"]))
        if shot["outcome"] == "goal":
            events.append(_event(frames[shot["goal_index"]], "goal", shot["player"], shot["x"], shot["y"],
                                 play=play, attack_right=right, outcome_source=shot["source"]))
    return events, attack_right, debug


def _hint(hints: dict | None, receiver_id: str, t: float) -> dict:
    if not hints:
        return {}
    from vision.scene_llm import hint_for_pass
    return hint_for_pass(hints, receiver_id, t) or {}


def _event(frame: dict, etype: str, player: dict, x: float, y: float, **extra) -> dict:
    period, t = frame.get("period", 1), frame["match_seconds"]
    return {"match_id": frame.get("match_id", "VISION"), "event_id": 0, "period": period,
            "clock": format_clock(period, t), "minute": format_minute(period, t), "match_seconds": t,
            "type": etype, "team": player["team"], "team_name": player["team"].title(),
            "player_id": player["player_id"], "player_name": player.get("name") or player["player_id"],
            "x": round(x, 3), "y": round(y, 3), "score": "0-0", "synthetic": False, **extra}


def frames_from_tracking_csv(path, t0: float, t1: float, meta: dict) -> list[dict]:
    """tracking.csv rows between t0 and t1 as track_frames dicts (roles and names from the meta squads)."""
    squad = {p["player_id"]: p for t in meta["teams"].values() for p in t["squad"]}
    frames: dict[int, dict] = {}
    with open(Path(path), newline="") as f:
        for row in csv.DictReader(f):
            t = float(row["match_seconds"])
            if not t0 <= t <= t1:
                continue
            fr = frames.setdefault(int(row["frame"]), {"match_id": meta.get("match_id", "VISION"),
                                                       "frame": int(row["frame"]), "period": int(row["period"]),
                                                       "match_seconds": t, "clock": row["clock"],
                                                       "ball": None, "players": []})
            x, y = float(row["x"]), float(row["y"])
            if row["object_id"] == "ball":
                fr["ball"] = {"x": x, "y": y, "confidence": 1.0}
                continue
            q = squad.get(row["object_id"], {})
            fr["players"].append({"player_id": row["object_id"], "team": row["team"], "x": x, "y": y,
                                  "number": q.get("number", 0), "name": q.get("name", row["object_id"]),
                                  "role": "GK" if q.get("role") == "GK" else "OUT", "confidence": 1.0})
    return [frames[k] for k in sorted(frames)]

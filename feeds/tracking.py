"""Tracking frames shared by the live pitch, replay and every feed.

``tracking.csv`` stays the on-disk format the decision engine samples. A live
frame is that file grouped by ``frame``, joined to the squad, and annotated
with the defensive line and the counter-risk zone. The browser draws the
annotation; it does not recompute the model.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from engine.models import W as PITCH_W
from engine.models import counter_risk, to_frame

MAX_HZ = 12.0
POSSESSION_RADIUS_M = 3.5


def keeper_ids(meta: dict) -> set[str]:
    return {p["player_id"] for side in meta.get("teams", {}).values() for p in side.get("squad", []) if p.get("role") == "GK"}


def squad_index(meta: dict) -> dict[str, dict]:
    return {p["player_id"]: p for side in meta.get("teams", {}).values() for p in side.get("squad", [])}


def _own_goal_on_left(team: str, period: int) -> bool:
    """Period 1: home defends the left goal. Period 2: the teams swap ends."""
    return (team == "home") == (period == 1)


def defensive_lines(players: list[dict], period: int, keepers: set[str]) -> dict[str, float | None]:
    """X of the last outfielder: the teammate nearest their own goal, keeper excluded."""
    lines: dict[str, float | None] = {"home": None, "away": None}
    for team in ("home", "away"):
        xs = [p["x"] for p in players if p.get("team") == team and p.get("player_id") not in keepers]
        if not xs:
            continue
        lines[team] = round(min(xs) if _own_goal_on_left(team, period) else max(xs), 2)
    return lines


def _closest_possession(players: list[dict], ball: dict | None) -> str | None:
    if not ball:
        return None
    best, dist = None, POSSESSION_RADIUS_M
    for player in players:
        gap = ((player["x"] - ball["x"]) ** 2 + (player["y"] - ball["y"]) ** 2) ** 0.5
        if gap <= dist and player.get("team") in ("home", "away"):
            best, dist = player["team"], gap
    return best


def annotate_frame(frame: dict, keepers: set[str] | None = None) -> dict:
    """Add defensive lines, possession and the counter-risk zone. Returns a new dict."""
    keepers = keepers or set()
    players = list(frame.get("players") or [])
    period = int(frame.get("period") or 1)
    ball = frame.get("ball")
    lines = defensive_lines(players, period, keepers)
    possession = frame.get("possession")
    if possession not in ("home", "away"):
        possession = _closest_possession(players, ball)
    risk = None
    zone = None
    if ball and possession in ("home", "away"):
        right = (possession == "home") == (period == 1)
        ball_u, _ = to_frame(ball["x"], ball["y"], right)
        behind = 0
        for player in players:
            if player.get("team") != possession or player.get("player_id") in keepers:
                continue
            u, _ = to_frame(player["x"], player["y"], right)
            if u < ball_u - 15:
                behind += 1
        risk = counter_risk(ball["x"], ball["y"], right, behind)
        line = lines.get(possession)
        if line is not None:
            zone = {
                "team": possession,
                "x0": round(min(line, ball["x"]), 2),
                "x1": round(max(line, ball["x"]), 2),
                "y0": 0.0,
                "y1": PITCH_W,
                "risk": risk,
            }
    out = dict(frame)
    out["kind"] = "tracking"
    out["possession"] = possession
    out["defensive_line"] = lines
    out["counter_risk"] = risk
    out["counter_zone"] = zone
    return out


def _rows_to_frame(rows: list[dict], meta: dict, squad: dict[str, dict]) -> dict | None:
    if not rows:
        return None
    head = rows[0]
    players = []
    ball = None
    in_play = False
    possession = None
    for row in rows:
        oid = row["object_id"]
        x, y = float(row["x"]), float(row["y"])
        if row.get("ball_possession") in ("home", "away"):
            possession = row["ball_possession"]
        if str(row.get("ball_in_play", "1")) in ("1", "true", "True"):
            in_play = True
        if oid == "ball":
            ball = {"x": round(x, 2), "y": round(y, 2)}
            continue
        info = squad.get(oid, {})
        players.append({
            "player_id": oid,
            "team": row.get("team") or None,
            "x": round(x, 2),
            "y": round(y, 2),
            "number": info.get("number"),
            "name": info.get("name"),
            "role": info.get("role"),
        })
    return {
        "kind": "tracking",
        "match_id": meta.get("match_id"),
        "frame": int(float(head["frame"])),
        "period": int(float(head["period"])),
        "match_seconds": round(float(head["match_seconds"]), 2),
        "clock": head.get("clock") or "",
        "ball": ball,
        "ball_in_play": bool(in_play and ball),
        "possession": possession,
        "players": players,
    }


def iter_tracking_frames(folder: Path, start: float = 0.0):
    """Yield annotated frames from ``folder/tracking.csv``, in match order.

    Rows at ``match_seconds`` before ``start`` are skipped so a paused live
    view can resume. Missing tracking yields nothing.
    """
    folder = Path(folder)
    path = folder / "tracking.csv"
    meta_path = folder / "match_meta.json"
    if not path.exists() or not meta_path.exists():
        return
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    squad = squad_index(meta)
    keepers = keeper_ids(meta)
    bucket_key = None
    bucket: list[dict] = []

    def flush():
        frame = _rows_to_frame(bucket, meta, squad)
        if frame and frame["match_seconds"] + 1e-6 >= start:
            return annotate_frame(frame, keepers)
        return None

    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = row["frame"]
            if bucket_key is None:
                bucket_key = key
            if key != bucket_key:
                frame = flush()
                if frame:
                    yield frame
                bucket = []
                bucket_key = key
            bucket.append(row)
    if bucket:
        frame = flush()
        if frame:
            yield frame


def iter_paced(frames, speed: float, max_hz: float = MAX_HZ):
    """Yield ``(frame, sleep_seconds)`` capped at ``max_hz`` wall-clock sends.

    ``speed`` is how many match seconds pass per wall-clock second (``1`` is
    real time). Frames that would arrive sooner than the cap are dropped,
    keeping the latest position in that window.
    """
    cap = min(max(float(max_hz or MAX_HZ), 1.0), MAX_HZ)
    min_wall = 1.0 / cap
    rate = float(speed) if speed and speed > 0 else cap
    last_t = None
    pending = None
    accrued = 0.0
    for frame in frames:
        t = float(frame["match_seconds"])
        if last_t is None:
            last_t = t
            yield frame, 0.0
            continue
        accrued += max(0.0, t - last_t) / rate
        last_t = t
        pending = frame
        if accrued + 1e-9 >= min_wall:
            sleep, accrued = accrued, 0.0
            pending = None
            yield frame, sleep
    if pending is not None:
        yield pending, accrued

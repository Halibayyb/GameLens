"""Quality filters for vision tracks: confidence, occlusion, IDs, ball gaps.

These run after detection and before the moment schema is built. Low-confidence
points are dropped rather than guessed. Positions stay on the 105 x 68 pitch.
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np

MIN_CONF = 0.35
MAX_BALL_GAP = 8
REASSOCIATE_M = 4.0
OCCLUSION_IOU = 0.35


def interpolate_ball(positions: list[tuple[float, float] | None], max_gap: int = MAX_BALL_GAP):
    """Fill short holes in a ball track. Long gaps stay None (no guess)."""
    out = list(positions)
    i = 0
    n = len(out)
    while i < n:
        if out[i] is not None:
            i += 1
            continue
        prev = i - 1
        while prev >= 0 and out[prev] is None:
            prev -= 1
        nxt = i
        while nxt < n and out[nxt] is None:
            nxt += 1
        if prev < 0 or nxt >= n or (nxt - prev - 1) > max_gap:
            i = nxt
            continue
        ax, ay = out[prev]
        bx, by = out[nxt]
        span = nxt - prev
        for k in range(prev + 1, nxt):
            t = (k - prev) / span
            out[k] = (ax + (bx - ax) * t, ay + (by - ay) * t)
        i = nxt
    return out


def smooth_homographies(matrices: list[np.ndarray | None], window: int = 5) -> list[np.ndarray | None]:
    """Temporal average of 3x3 homographies. Missing frames reuse the last good one, then smooth."""
    filled: list[np.ndarray | None] = []
    last = None
    for matrix in matrices:
        if matrix is not None:
            last = _unit(matrix)
            filled.append(last)
        else:
            filled.append(last)
    if last is None:
        return filled
    first = next((m for m in filled if m is not None), None)
    for i, matrix in enumerate(filled):
        if matrix is None:
            filled[i] = first
    half = max(1, window // 2)
    out = []
    for i in range(len(filled)):
        chunk = [filled[j] for j in range(max(0, i - half), min(len(filled), i + half + 1)) if filled[j] is not None]
        if not chunk:
            out.append(None)
            continue
        mean = sum(chunk) / len(chunk)
        out.append(_unit(mean))
    return out


def drop_low_confidence(players: Iterable[dict], min_conf: float = MIN_CONF) -> list[dict]:
    kept = []
    for player in players:
        conf = float(player.get("confidence", 1.0))
        if conf < min_conf:
            continue
        kept.append(player)
    return kept


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / area if area else 0.0


def apply_occlusion(detections: list[dict]) -> list[dict]:
    """Lower confidence when two person boxes overlap heavily. Do not invent a position."""
    people = [d for d in detections if d.get("cls") != "ball"]
    out = []
    for det in detections:
        row = dict(det)
        conf = float(row.get("confidence", row.get("conf", 0.7)))
        if row.get("cls") != "ball":
            for other in people:
                if other is det:
                    continue
                if iou(row["xyxy"], other["xyxy"]) >= OCCLUSION_IOU:
                    conf *= 0.55
                    row["occluded"] = True
        row["confidence"] = round(conf, 3)
        out.append(row)
    return out


def reassociate(tracks: list[dict], history: list[dict], max_dist: float = REASSOCIATE_M) -> list[dict]:
    """Reuse a recently lost id when a new track appears nearby with a similar jersey."""
    used = {t["player_id"] for t in tracks}
    assigned = []
    for track in tracks:
        row = dict(track)
        if not row.get("new_id"):
            assigned.append(row)
            continue
        best, best_d = None, max_dist
        for lost in history:
            if lost["player_id"] in used:
                continue
            if lost.get("team") and row.get("team") and lost["team"] != row["team"]:
                continue
            if lost.get("role") in ("GK", "REF") and row.get("role") not in (None, lost["role"]):
                continue
            dist = math.hypot(lost["x"] - row["x"], lost["y"] - row["y"])
            colour_d = _colour_dist(lost.get("color"), row.get("color"))
            if dist < best_d and colour_d < 80:
                best, best_d = lost, dist
        if best:
            row["player_id"] = best["player_id"]
            row["reassociated"] = True
            used.add(best["player_id"])
        assigned.append(row)
    return assigned


def clip_confidence(det: dict, on_pitch: bool, age: int = 2) -> float:
    conf = float(det.get("confidence", det.get("conf", 0.7)))
    if not on_pitch:
        return 0.0
    if det.get("occluded"):
        conf *= 0.7
    if age <= 1:
        conf *= 0.8
    return round(max(0.0, min(1.0, conf)), 3)


def mean_confidence(frames: list[dict]) -> float:
    vals = []
    for frame in frames:
        for player in frame.get("players") or []:
            if player.get("confidence") is not None:
                vals.append(float(player["confidence"]))
        ball = frame.get("ball") or {}
        if isinstance(ball, dict) and ball.get("confidence") is not None:
            vals.append(float(ball["confidence"]))
    if not vals:
        return 0.0
    return round(sum(vals) / len(vals), 3)


def _unit(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=float)
    scale = matrix[2, 2] if abs(matrix[2, 2]) > 1e-9 else 1.0
    return matrix / scale


def _colour_dist(a, b) -> float:
    if not a or not b:
        return 0.0
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))

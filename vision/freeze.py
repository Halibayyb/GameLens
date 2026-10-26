"""Dense freeze frames: the frames the formulas read, re-detected at full resolution.

Tracking runs on downscaled frames and keeps only players it can follow, so a pass frame can hold half the
players on screen, and a missing defender changes PASS (lane defenders) and goal chance (blockers). Only the
pass frame feeds the formulas, so it is worth a slow, careful look: the player model at full resolution, mapped
with that frame's own homography (recovered from the tracked players' foot points and metres), merged with the
tracked players (same box: same id, so passer and receiver keep theirs), new players given a team by kit.
"""
from __future__ import annotations

import numpy as np

from vision.homography import apply
from vision.pipeline import PERSON, _ground, _kit, _on_pitch

DENSE_IMGSZ = 1920
DENSE_CONF = 0.2
SAME_BOX_IOU = 0.15   # a full-resolution box this close to a tracked one is that player
ROLE = {1: "goalkeeper", 2: "player", 3: "referee"}
OFF_PITCH_DOUBT = 0.25   # more on-screen players than this share mapped off the pitch: the positions are doubtful


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _same_person(a, b) -> bool:
    """Boxes of one person from two detector runs: they overlap, or their centres are within half a body width
    (a full-resolution box is often shifted or sized differently from the tracking one)."""
    if _iou(a, b) >= SAME_BOX_IOU:
        return True
    w = max(a[2] - a[0], b[2] - b[0], 1.0)
    return abs((a[0] + a[2]) - (b[0] + b[2])) / 2 < 0.5 * w and abs(a[3] - b[3]) < 0.25 * max(a[3] - a[1], b[3] - b[1], 1.0)


def frame_homography(frame: dict):
    """Image (tracking pixels) -> metres for one tracked frame, refitted from its players' foot points."""
    import cv2

    pairs = [(_ground(p["xyxy"], PERSON), (p["x"], p["y"])) for p in frame.get("players") or [] if p.get("xyxy")]
    if len(pairs) < 4:
        return None
    src = np.float32([a for a, _ in pairs])
    dst = np.float32([b for _, b in pairs])
    matrix, _ = cv2.findHomography(src, dst, 0)
    return None if matrix is None else matrix / matrix[2, 2]


def densify(frame: dict, image_full: np.ndarray, frame_size, detect) -> dict:
    """Add the players full-resolution detection finds to one tracked frame (returns a new frame dict).

    detect(image) -> [(cls_id, xyxy, conf)] in full-resolution pixels; frame_size is the tracking frame size."""
    matrix = (np.asarray(frame["homography"], dtype=float).reshape(3, 3) if frame.get("homography")
              else frame_homography(frame))
    if matrix is None or not frame_size:
        return frame
    sx = frame_size[0] / image_full.shape[1]
    sy = frame_size[1] / image_full.shape[0]
    tracked = [p for p in frame["players"] if p.get("xyxy")]
    kits: dict[str, list] = {}
    added, matched = [], set()
    found = []
    for cls_id, box, conf in detect(image_full):
        small = [box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy]
        best = next((p for p in tracked if _same_person(small, p["xyxy"])), None)
        if best is not None:
            matched.add(best["player_id"])
            if best.get("team") in ("home", "away"):
                kits.setdefault(best["team"], []).append(_kit(image_full, box, (0, 0, 0)))
            continue
        if any(_same_person(small, f[1]) for f in found):
            continue   # the same person twice (overlapping detections)
        found.append((cls_id, small, box, conf))
    centres = {team: np.mean(v, axis=0) for team, v in kits.items() if v}
    suffix = _suffix(frame)
    off = 0
    for n, (cls_id, small, box, conf) in enumerate(found, start=1):
        x, y = apply(matrix, *_ground(small, PERSON))
        if not _on_pitch(x, y):
            off += 1   # a person on screen mapped off the pitch: this frame's homography is suspect
            continue
        role = ROLE.get(int(cls_id), "player")
        if role == "referee":
            team, prefix, short = "ref", "R", "REF"
        else:
            kit = np.asarray(_kit(image_full, box, (0, 0, 0)))
            if role == "goalkeeper" or not centres:
                team = "home" if x < 52.5 else "away"   # keepers defend their half; home starts on the left
            else:
                team = min(centres, key=lambda t: float(np.sum((kit - centres[t]) ** 2)))
            prefix, short = ("H" if team == "home" else "A"), ("GK" if role == "goalkeeper" else "OUT")
        added.append({"player_id": f"{prefix}x{n}{suffix}", "team": team, "x": round(float(x), 2),
                      "y": round(float(y), 2), "number": 0, "name": f"{'Home' if team == 'home' else 'Away' if team == 'away' else 'Referee'} (freeze)",
                      "role": short, "confidence": round(float(conf), 3), "occluded": False, "reassociated": False,
                      "xyxy": [round(float(v), 1) for v in small], "dense": True})
    out = dict(frame)
    out["players"] = list(frame["players"]) + added
    seen = len(added) + len(matched) + off
    out["dense"] = {"added": len(added), "matched": len(matched), "tracked": len(tracked), "off_pitch": off,
                    "mapping": "doubtful" if seen and off / seen > OFF_PITCH_DOUBT else "ok"}
    return out


def _suffix(frame: dict) -> str:
    sid = (frame.get("shot") or {}).get("id", 0)
    return f"s{sid}" if sid else ""


def model_detector():
    """The football player model on a full-resolution frame: [(cls_id, xyxy, conf)] for goalkeepers, players and
    referees."""
    from vision import models

    model = models.load("football-player")

    def detect(image_rgb):
        res = model.predict(image_rgb[:, :, ::-1], imgsz=DENSE_IMGSZ, conf=DENSE_CONF, classes=[1, 2, 3],
                            verbose=False)[0]
        boxes = getattr(res, "boxes", None)
        if boxes is None or boxes.xyxy is None:
            return []
        return [(int(c), [float(v) for v in b], float(s)) for c, b, s in
                zip(boxes.cls.cpu().numpy(), boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy())]
    return detect


def read_frame(video: str, seconds: float):
    """The full-resolution RGB frame at ``seconds`` (None when it cannot be read)."""
    import cv2

    cap = cv2.VideoCapture(video)
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, bgr = cap.read()
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB) if ok else None
    finally:
        cap.release()


def densify_passes(frames: list[dict], events: list[dict], video: str, frame_size, detect=None) -> list[int]:
    """Densify (in place) every frame a detected pass is played on; returns their indexes."""
    times = [f["match_seconds"] for f in frames]
    picks = sorted({min(range(len(frames)), key=lambda i: abs(times[i] - e["match_seconds"]))
                    for e in events if e.get("type") == "pass"})
    if not picks:
        return []
    detect = detect or model_detector()
    for k in picks:
        image = read_frame(video, frames[k]["match_seconds"])
        if image is not None and frames[k].get("mapped"):
            frames[k] = densify(frames[k], image, frame_size, detect)
    return picks

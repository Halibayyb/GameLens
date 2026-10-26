"""Football detector on the prepared models, and the choice between it and the COCO fallback.

``FootballDetector.detect`` has the ``YoloByteTrack`` interface: RGB image in, a list of
``{"cls": "person" | "ball", "track_id", "xyxy", "confidence", "role"}`` out, ``role`` being
player / goalkeeper / referee for people. People come from the player model (1280 px, every detection);
``PanTracker`` gives them ids. ByteTrack only reports confirmed tracks, and with a panning camera sampled
at 5 fps most boxes never confirm: a clip with ~20 players per frame came back with 2-6.
The ball is the player model's ball class or, when that is not confident, the ball model on
overlapping 640 px tiles. Pitch markings (penalty and centre spots) look like a ball, so a candidate
that keeps reappearing at the same pixel is treated as static and dropped; among the rest the one closest
to the last ball wins, else the most confident. Ball boxes carry ``source``: "full" or "tile".
"""
from __future__ import annotations

import logging
import math
import os
import time
from collections import deque

from vision import models

log = logging.getLogger(__name__)

ROLES = {1: "goalkeeper", 2: "player", 3: "referee"}
BALL_CLASS = 0
BALL_ID = 1_000_000          # one ball: a stable track id clear of the player ids
SURE_BALL = 0.5              # a full-frame ball at least this confident skips the tiles
TILE, OVERLAP = 640, 0.2
STATIC_PX, STATIC_FRAMES, HISTORY = 4.0, 6, 24   # a candidate seen this often at one pixel is a pitch marking
MAX_JUMP_PX = 300                                # ball travel between sampled frames still counted as continuity
HANDS_FRACTION, HANDS_WEIGHT = 0.6, 0.4          # a "ball" in the top of a person box is usually gloves / a head
ARMS_PAD = 0.6                                   # arms reach this many box widths beyond each side
MATCH_HEIGHTS, LOST_FRAMES = 1.0, 15             # id match radius (in box heights, after the pan); lost-track memory


def tiles(width: int, height: int, size: int = TILE, overlap: float = OVERLAP) -> list[tuple[int, int, int, int]]:
    """Overlapping (x0, y0, x1, y1) windows covering the frame; edge tiles are shifted inside it."""
    step = max(1, int(size * (1 - overlap)))
    def starts(total):
        if total <= size:
            return [0]
        out = list(range(0, total - size, step))
        return out + [total - size]
    return [(x, y, min(width, x + size), min(height, y + size)) for y in starts(height) for x in starts(width)]


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def nms(boxes: list[dict], iou: float = 0.3) -> list[dict]:
    """Keep the most confident of overlapping boxes (tile seams report one ball twice)."""
    kept: list[dict] = []
    for box in sorted(boxes, key=lambda b: -b["confidence"]):
        if all(_iou(box["xyxy"], k["xyxy"]) < iou for k in kept):
            kept.append(box)
    return kept


def weigh_balls(balls: list[dict], people: list[dict]) -> list[dict]:
    """Down-weight candidates in the upper part of a person box (keeper gloves read as a ball)."""
    for b in balls:
        cx, cy = centre(b)
        def in_reach(p):
            x0, y0, x1, y1 = p["xyxy"]
            pad = ARMS_PAD * (x1 - x0)
            return x0 - pad <= cx <= x1 + pad and y0 <= cy <= y0 + HANDS_FRACTION * (y1 - y0)
        if any(in_reach(p) for p in people):
            b["raw_confidence"] = b["confidence"]
            b["confidence"] = b["confidence"] * HANDS_WEIGHT
    return balls


def centre(box: dict) -> tuple[float, float]:
    x0, y0, x1, y1 = box["xyxy"]
    return (x0 + x1) / 2, (y0 + y1) / 2


class PanTracker:
    """Ids for every detection: compensate the camera pan, then match nearest boxes of the same role."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.tracks: dict[int, dict] = {}   # id -> {"c": centre, "h": height, "role", "age"}
        self.next_id = 1

    def assign(self, people: list[dict]) -> list[dict]:
        prev = list(self.tracks.items())
        cur = [centre(p) for p in people]
        pan = (0.0, 0.0)
        if prev and cur:   # median shift from each box to its nearest previous box
            shifts = [min(((c[0] - t["c"][0], c[1] - t["c"][1]) for _, t in prev), key=lambda d: math.hypot(*d))
                      for c in cur]
            pan = (sorted(d[0] for d in shifts)[len(shifts) // 2], sorted(d[1] for d in shifts)[len(shifts) // 2])
        pairs = []
        for i, (p, c) in enumerate(zip(people, cur)):
            h = max(1.0, p["xyxy"][3] - p["xyxy"][1])
            for tid, t in prev:
                d = math.dist(c, (t["c"][0] + pan[0], t["c"][1] + pan[1]))
                if t["role"] == p.get("role") and d <= MATCH_HEIGHTS * max(h, t["h"]):
                    pairs.append((d, i, tid))
        used_i, used_t = set(), set()
        for _, i, tid in sorted(pairs):
            if i not in used_i and tid not in used_t:
                people[i]["track_id"] = tid
                used_i.add(i); used_t.add(tid)
        for i, p in enumerate(people):
            if i not in used_i:
                p["track_id"] = self.next_id
                self.next_id += 1
        for tid, t in prev:   # lost tracks drift with the pan and expire
            if tid not in used_t:
                t["age"] += 1
                t["c"] = (t["c"][0] + pan[0], t["c"][1] + pan[1])
                if t["age"] > LOST_FRAMES:
                    del self.tracks[tid]
        for p, c in zip(people, cur):
            self.tracks[p["track_id"]] = {"c": c, "h": p["xyxy"][3] - p["xyxy"][1], "role": p.get("role"), "age": 0}
        return people


class BallPicker:
    """Choose one ball per frame from candidates, ignoring spots that never move."""

    def __init__(self):
        self.history: deque[list[tuple[float, float]]] = deque(maxlen=HISTORY)
        self.last: tuple[float, float] | None = None
        self.pending: tuple[float, float] | None = None   # a far candidate waiting for a second sighting

    def is_static(self, point) -> bool:
        return sum(any(math.dist(point, q) <= STATIC_PX for q in frame) for frame in self.history) >= STATIC_FRAMES

    def pick(self, candidates: list[dict]) -> dict | None:
        """Nearest to the last ball; a jump elsewhere must repeat on the next frame before it is believed."""
        moving = [c for c in candidates if not self.is_static(centre(c))]
        if self.last and self.is_static(self.last):   # we were following a pitch marking
            self.last = None
        self.history.append([centre(c) for c in candidates])
        near =[c for c in moving if self.last and math.dist(centre(c), self.last) <= MAX_JUMP_PX]
        if near:
            best = min(near, key=lambda c: math.dist(centre(c), self.last))
            self.last, self.pending = centre(best), None
            return best
        if not moving:
            self.pending = None
            return None
        best = max(moving, key=lambda c: c["confidence"])
        confirmed = self.pending is not None and math.dist(centre(best), self.pending) <= MAX_JUMP_PX
        self.pending = centre(best)
        if self.last is None or confirmed:
            self.last, self.pending = centre(best), None
            return best
        return None


class FootballDetector:
    def __init__(self, device: str | None = None, tiles_enabled: bool = True):
        self.device = device or os.getenv("VISION_DEVICE", "cpu")
        self.tiles_enabled = tiles_enabled
        self.model_name = "football-player"
        self.ids = PanTracker()
        self.balls = BallPicker()
        self._ms: list[float] = []

    def reset(self) -> None:
        """A cut: forget ids and ball history (the pipeline calls this at shot boundaries)."""
        self.ids.reset()
        self.balls = BallPicker()

    def info(self) -> dict:
        """What ran, for the tracking debug: name, weights, input size, device, mean ms per frame."""
        spec = models.MANIFEST["football-player"]
        return {"name": "football", "weights": [f"{models.MANIFEST[n]['file']} {models.MANIFEST[n]['sha256'][:12]}"
                                                for n in ("football-player", "football-ball")],
                "imgsz": spec["imgsz"], "tile": TILE, "device": self.device,
                "ms_per_frame": round(sum(self._ms) / len(self._ms), 1) if self._ms else None}

    def _player_model(self):
        return models.load("football-player")   # predict() keeps no state, so one shared instance

    def detect(self, image) -> list[dict]:
        start = time.perf_counter()
        bgr = image[:, :, ::-1]
        result = self._player_model().predict(
            bgr, imgsz=models.MANIFEST["football-player"]["imgsz"], verbose=False, device=self.device,
        )[0]
        found, balls = self.people_and_balls(result)
        found = self.ids.assign(found)
        balls = weigh_balls(balls, found)
        sure = [b for b in balls if b["confidence"] >= SURE_BALL and not self.balls.is_static(centre(b))]
        if self.tiles_enabled and not sure:
            balls += weigh_balls(self._tile_balls(bgr), found)
        ball = self.balls.pick(nms(balls))
        self._ms.append((time.perf_counter() - start) * 1000)
        return found + ([ball] if ball else [])

    @staticmethod
    def people_and_balls(result) -> tuple[list[dict], list[dict]]:
        boxes = getattr(result, "boxes", None)
        if boxes is None or boxes.xyxy is None or len(boxes) == 0:
            return [], []
        ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else [None] * len(boxes)
        people, balls = [], []
        for track_id, cls, box, conf in zip(ids, boxes.cls.cpu().numpy().astype(int),
                                            boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy()):
            row = {"xyxy": [float(v) for v in box], "confidence": float(conf)}
            if int(cls) == BALL_CLASS:
                balls.append({"cls": "ball", "track_id": BALL_ID, "source": "full", **row})
            elif int(cls) in ROLES:
                people.append({"cls": "person", "track_id": None if track_id is None else int(track_id),
                               "role": ROLES[int(cls)], **row})
        return people, balls

    def _tile_balls(self, bgr) -> list[dict]:
        model = models.load("football-ball")
        h, w = bgr.shape[:2]
        windows = tiles(w, h)
        crops = [bgr[y0:y1, x0:x1] for x0, y0, x1, y1 in windows]
        results = model.predict(crops, imgsz=TILE, verbose=False, device=self.device)
        out = []
        for (x0, y0, _, _), res in zip(windows, results):
            boxes = getattr(res, "boxes", None)
            if boxes is None or len(boxes) == 0:
                continue
            for box, conf in zip(boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy()):
                out.append({"cls": "ball", "track_id": BALL_ID, "source": "tile", "confidence": float(conf),
                            "xyxy": [float(box[0] + x0), float(box[1] + y0), float(box[2] + x0), float(box[3] + y0)]})
        return out


def describe() -> dict:
    """What make_detector() and the pitch mapping would use here, without loading any weights."""
    choice = (os.getenv("VISION_DETECTOR") or "").strip().lower()
    if choice not in ("football", "coco"):
        choice = "football" if models.available("football-player") and models.available("football-ball") else "coco"
    pitch = (models.available("football-pitch")
             and os.getenv("VISION_PITCH_MODEL", "auto").lower() not in ("off", "0", "false"))
    return {"detector": choice, "coco_model": os.getenv("VISION_MODEL") or "yolo11n.pt", "pitch_model": bool(pitch),
            "device": os.getenv("VISION_DEVICE", "cpu")}


def make_detector():
    """VISION_DETECTOR=football|coco. Default: football when its weights are present, else COCO."""
    choice = (os.getenv("VISION_DETECTOR") or "").strip().lower()
    if choice not in ("football", "coco"):
        choice = "football" if models.available("football-player") and models.available("football-ball") else "coco"
    if choice == "football":
        log.info("vision detector: football (roboflow/sports weights)")
        return FootballDetector()
    from vision.pipeline import YoloByteTrack
    log.info("vision detector: coco (%s)", os.getenv("VISION_MODEL") or "yolo11n.pt")
    return YoloByteTrack()


def detector_info(detector) -> dict:
    """``debug["detector"]`` for either detector."""
    if hasattr(detector, "info"):
        return detector.info()
    return {"name": "coco", "weights": [getattr(detector, "model_name", "yolo11n.pt")],
            "imgsz": getattr(detector, "imgsz", None) or 640, "device": getattr(detector, "device", "cpu"),
            "ms_per_frame": None}

"""Broadcast shots: where a clip cuts, and whether each shot can be mapped onto the pitch.

A highlights montage cuts between matches, cameras and close-ups. Tracks, team colours and homography smoothing
must not run across a cut, and a close-up has no pitch to map. Cuts are a jump in the HSV colour histogram
between consecutive sampled frames; a pan or zoom changes it far less than a cut.

Shot types: "wide" (pitch model mapped it, small players), "close" (no pitch mapping and a large player or little
grass), "other" (anything else: crowd, graphics, a wide shot the model could not map).
"""
from __future__ import annotations

import numpy as np

CUT_DIST = 0.45        # Bhattacharyya distance between consecutive frames that marks a cut
CLOSE_BOX = 0.35       # a person box this tall (fraction of the frame) is a close-up
WIDE_BOX = 0.2         # median person box below this fits a wide shot
LOW_GRASS = 0.25       # less grass than this is not a pitch view
WIDE_KEYPOINTS = 4


def histogram(image: np.ndarray) -> np.ndarray:
    """Normalised hue x saturation histogram of an RGB frame."""
    import cv2
    small = cv2.resize(image, (160, 90))
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
    return cv2.normalize(hist, hist).flatten()


def distance(a: np.ndarray, b: np.ndarray) -> float:
    import cv2
    return float(cv2.compareHist(a.astype(np.float32), b.astype(np.float32), cv2.HISTCMP_BHATTACHARYYA))


class CutDetector:
    """Feed frames in order; ``update`` is True on the first frame of a new shot."""

    def __init__(self, threshold: float = CUT_DIST):
        self.threshold = threshold
        self.prev = None
        self.last = 0.0

    def update(self, image: np.ndarray) -> bool:
        hist = histogram(image)
        cut = self.prev is not None and (d := distance(self.prev, hist)) > self.threshold
        self.last = d if self.prev is not None else 0.0
        self.prev = hist
        return bool(cut)


def grass_ratio(image: np.ndarray) -> float:
    rgb = image[:, :, :3].astype(np.int16)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    return float(((g > 60) & (g > r * 1.1) & (g > b * 1.1)).mean())


def shot_type(image: np.ndarray, keypoints: dict | None = None, boxes: list | None = None) -> str:
    """wide / close / other for one frame. keypoints: detect_keypoints output; boxes: person xyxy in pixels."""
    h = image.shape[0]
    heights = sorted((b[3] - b[1]) / h for b in boxes or [])
    tallest = heights[-1] if heights else 0.0
    median = heights[len(heights) // 2] if heights else 0.0
    kp = keypoints or {}
    mapped = bool(kp.get("trusted", kp.get("matrix") is not None) and kp.get("matrix") is not None)
    if mapped and (kp.get("method") != "pitch_model" or (kp.get("n_visible") or 0) >= WIDE_KEYPOINTS) \
            and median < WIDE_BOX and tallest < CLOSE_BOX:
        return "wide"
    if not mapped and (tallest >= CLOSE_BOX or grass_ratio(image) < LOW_GRASS):
        return "close"
    return "other"


def segment(images, types: list[str] | None = None, threshold: float = CUT_DIST) -> list[dict]:
    """Shots as {"id", "start", "end", "type"}: frame indexes inclusive; type is the majority of ``types``."""
    detector = CutDetector(threshold)
    starts = [i for i, image in enumerate(images) if detector.update(image) or i == 0]
    n = len(types) if types is not None else (len(images) if hasattr(images, "__len__") else None)
    if n is None:
        raise ValueError("segment needs a sized sequence of frames")
    out = []
    for k, start in enumerate(starts):
        end = (starts[k + 1] - 1) if k + 1 < len(starts) else n - 1
        span = (types or [])[start:end + 1]
        kind = max(set(span), key=span.count) if span else "other"
        out.append({"id": k, "start": start, "end": end, "type": kind})
    return out

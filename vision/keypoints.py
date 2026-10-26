"""Pitch keypoints for per-frame homography.

Order: a calibration file (fixed camera), the 32-keypoint pitch model (``vision.pitch_keypoints``, a panning
broadcast camera), an optional 4-point model, then the green-quad estimate. ``trusted`` is False for a
green-quad guess on a frame the pitch model could not map: on real footage that box is tens of metres off.
Debug overlays always receive the image points so the admin page can draw them.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from vision import pitch_keypoints
from vision.homography import PITCH_TL_TR_BR_BL, from_image, green_quad, homography

_MODEL = None


def detect_keypoints(image: np.ndarray, calibration: dict | None = None) -> dict:
    """Return image_points, pitch_points, matrix (or None), the method used and whether to trust it."""
    if calibration:
        src = calibration["image_points"]
        if calibration.get("frame_size"):   # points measured on the source video; frames may be downscaled
            sx, sy = image.shape[1] / calibration["frame_size"][0], image.shape[0] / calibration["frame_size"][1]
            src = [(x * sx, y * sy) for x, y in src]
        dst = calibration.get("pitch_points") or PITCH_TL_TR_BR_BL
        return {
            "image_points": [list(map(float, p)) for p in src],
            "pitch_points": [list(map(float, p)) for p in dst],
            "matrix": homography(src, dst),
            "method": "calibration",
            "ok": True,
            "trusted": True,
        }
    pitch_ran = pitch_keypoints.available(image)
    pitch = pitch_keypoints.detect_pitch(image) if pitch_ran else None
    if pitch and pitch["ok"]:
        return {**pitch, "trusted": True}
    model_points = _model_points(image)
    if model_points is not None:
        matrix = homography(model_points, PITCH_TL_TR_BR_BL)
        return {
            "image_points": [list(map(float, p)) for p in model_points],
            "pitch_points": [list(p) for p in PITCH_TL_TR_BR_BL],
            "matrix": matrix,
            "method": "keypoints",
            "ok": True,
            "trusted": True,
        }
    quad = green_quad(image)
    if quad is None:
        return {
            "image_points": [],
            "pitch_points": [list(p) for p in PITCH_TL_TR_BR_BL],
            "matrix": None,
            "method": "none",
            "ok": False,
            "trusted": False,
            **({"pitch_model": _summary(pitch)} if pitch else {}),
        }
    return {
        "image_points": [list(map(float, p)) for p in quad],
        "pitch_points": [list(p) for p in PITCH_TL_TR_BR_BL],
        "matrix": from_image(image),
        "method": "green_quad",
        "ok": True,
        # a rough box guess: only trusted where the pitch model could not run at all
        "trusted": not pitch_ran,
        **({"pitch_model": _summary(pitch)} if pitch else {}),
    }


def _summary(pitch: dict) -> dict:
    return {k: pitch.get(k) for k in ("n_visible", "inliers", "reproj_m", "keypoints", "lines")}


def _model_points(image: np.ndarray):
    """Optional 4-point keypoint model. Missing weights → None (green-quad fallback)."""
    path = os.getenv("PITCH_KEYPOINT_MODEL")
    if not path or not Path(path).exists():
        return None
    global _MODEL
    try:
        if _MODEL is None:
            from ultralytics import YOLO
            _MODEL = YOLO(path)
        result = _MODEL.predict(image[:, :, ::-1], verbose=False)[0]
        kpts = getattr(result, "keypoints", None)
        if kpts is None or kpts.xy is None or len(kpts.xy) == 0:
            return None
        pts = kpts.xy[0].cpu().numpy()
        if len(pts) < 4:
            return None
        return [(float(x), float(y)) for x, y in pts[:4]]
    except Exception:
        return None

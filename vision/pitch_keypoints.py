"""Per-frame pitch homography from a 32-keypoint pitch model (a panning broadcast camera).

The model is roboflow/sports ``football-pitch-detection`` (YOLOv8 pose, MIT repository). Its keypoints follow
``SoccerPitchConfiguration``: x along the length from one goal line, y across from keypoint 1's touchline. Here
they are placed on OUR pitch: 105 x 68 m, FIFA markings, origin bottom-left, y = 68 the far touchline. Keypoint
1's touchline is the far side in a main-camera view, so their y becomes 68 - y. A y-mirror would not change the
formulas (the goal is centred), only the drawing.

Confident keypoints (>= MIN_CONF) feed cv2.findHomography with RANSAC, image pixels -> pitch metres. The model
also returns self-consistent keypoints on a close-up (strung along one painted line), so a homography is only
kept when the pitch lines it projects back onto the frame land on white paint (``line_check``).
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

L, W = 105.0, 68.0
PEN_D, PEN_W = 16.5, 40.32
BOX_D, BOX_W = 5.5, 18.32
SPOT, R = 11.0, 9.15
MIN_CONF = 0.5
RANSAC_M = 1.5        # inlier threshold, metres on the pitch
MAX_REPROJ_M = 1.5    # mean inlier error above this is not trusted
MIN_SPREAD_M = 3.0    # keypoints all on one line give no homography
MIN_WIDTH = 320       # the model is not run on smaller frames (tests, thumbnails)
LINE_SUPPORT = 0.10   # share of projected line samples on white paint
LINE_MEDIAN = 0.035   # median distance to paint, as a fraction of the frame width
LINE_SAMPLES = 100    # fewer projected samples inside the frame: too little pitch to judge
MODEL = "football-pitch"   # vision.models registry name
WEIGHTS = Path(__file__).resolve().parent.parent / "models" / "football-pitch-detection.pt"


def _their_points() -> list[tuple[float, float]]:
    """The 32 keypoints in the model's axes (x along the length, y from keypoint 1's touchline), metres."""
    pl, ph = (W - PEN_W) / 2, (W + PEN_W) / 2
    bl, bh = (W - BOX_W) / 2, (W + BOX_W) / 2
    return [
        (0, 0), (0, pl), (0, bl), (0, bh), (0, ph), (0, W),                        # 1-6 goal line
        (BOX_D, bl), (BOX_D, bh), (SPOT, W / 2),                                   # 7-9
        (PEN_D, pl), (PEN_D, bl), (PEN_D, bh), (PEN_D, ph),                        # 10-13
        (L / 2, 0), (L / 2, W / 2 - R), (L / 2, W / 2 + R), (L / 2, W),            # 14-17 halfway
        (L - PEN_D, pl), (L - PEN_D, bl), (L - PEN_D, bh), (L - PEN_D, ph),        # 18-21
        (L - SPOT, W / 2), (L - BOX_D, bl), (L - BOX_D, bh),                       # 22-24
        (L, 0), (L, pl), (L, bl), (L, bh), (L, ph), (L, W),                        # 25-30 goal line
        (L / 2 - R, W / 2), (L / 2 + R, W / 2),                                    # 31-32 centre circle
    ]


WORLD_KEYPOINTS: list[tuple[float, float]] = [(float(x), float(W - y)) for x, y in _their_points()]

_MODEL = None


def available(image: np.ndarray | None = None) -> bool:
    """Weights present, not switched off, and (for an image) large enough to run on."""
    if os.getenv("VISION_PITCH_MODEL", "auto").lower() in ("off", "0", "false"):
        return False
    if image is not None and image.shape[1] < MIN_WIDTH:
        return False
    try:
        from vision import models
        return models.available(MODEL)
    except (ImportError, AttributeError, KeyError):
        return Path(os.getenv("VISION_PITCH_WEIGHTS") or WEIGHTS).exists()


def _model():
    global _MODEL
    if _MODEL is None:
        try:
            from vision import models
            _MODEL = models.load(MODEL)
        except (ImportError, AttributeError, KeyError):
            from ultralytics import YOLO
            _MODEL = YOLO(str(os.getenv("VISION_PITCH_WEIGHTS") or WEIGHTS))
    return _MODEL


def predict(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(32 x 2 pixel points, 32 confidences) from an RGB frame; empty arrays when no pitch is found."""
    result = _model().predict(image[:, :, ::-1], verbose=False)[0]   # Ultralytics expects BGR arrays
    kpts = getattr(result, "keypoints", None)
    if kpts is None or kpts.xy is None or len(kpts.xy) == 0 or kpts.conf is None:
        return np.zeros((0, 2)), np.zeros(0)
    return kpts.xy[0].cpu().numpy(), kpts.conf[0].cpu().numpy()


def homography_from_keypoints(points: np.ndarray, conf: np.ndarray, min_conf: float = MIN_CONF) -> dict:
    """RANSAC homography image -> metres from the confident keypoints, with its quality.

    ``keypoints`` lists every keypoint the model returned (id 1-32, image x/y, confidence, whether it was used
    and whether RANSAC kept it) so a debug view can draw them.
    """
    import cv2

    n = min(len(points), len(WORLD_KEYPOINTS))
    idx = [i for i in range(n) if conf[i] >= min_conf]
    src = np.float32([points[i] for i in idx]).reshape(-1, 2)
    dst = np.float32([WORLD_KEYPOINTS[i] for i in idx]).reshape(-1, 2)
    detail = [{"id": i + 1, "x": round(float(points[i][0]), 1), "y": round(float(points[i][1]), 1),
               "conf": round(float(conf[i]), 3), "used": i in idx, "inlier": False}
              for i in range(n) if conf[i] > 0.05 and (points[i][0] > 0 or points[i][1] > 0)]
    out = {"image_points": [[float(x), float(y)] for x, y in src],
           "pitch_points": [[float(x), float(y)] for x, y in dst],
           "keypoint_ids": [i + 1 for i in idx], "keypoints": detail, "matrix": None, "method": "pitch_model",
           "ok": False, "n_visible": len(idx), "inliers": 0, "reproj_m": None}
    if len(idx) < 4 or _spread(dst) < MIN_SPREAD_M:
        return out
    matrix, mask = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_M)
    if matrix is None or mask is None:
        return out
    keep = mask.ravel().astype(bool)
    kept_ids = {idx[k] + 1 for k in range(len(idx)) if keep[k]}
    for d in detail:
        d["inlier"] = d["id"] in kept_ids
    if keep.sum() < 4 or _spread(dst[keep]) < MIN_SPREAD_M:
        return out
    proj = cv2.perspectiveTransform(src[keep].reshape(-1, 1, 2), matrix).reshape(-1, 2)
    err = float(np.linalg.norm(proj - dst[keep], axis=1).mean())
    out.update(inliers=int(keep.sum()), reproj_m=round(err, 3))
    if err <= MAX_REPROJ_M:
        out.update(matrix=matrix / matrix[2, 2], ok=True)
    return out


_NAMES = ["corner, far", "box corner on goal line, far", "6-yard box on goal line, far",
          "6-yard box on goal line, near", "box corner on goal line, near", "corner, near",
          "6-yard box corner, far", "6-yard box corner, near", "penalty spot",
          "box edge corner, far", "box edge at 6-yard width, far", "box edge at 6-yard width, near",
          "box edge corner, near"]
LANDMARK_NAMES = ([f"Left {n}" for n in _NAMES] +
                  ["Halfway line, far touchline", "Halfway line x centre circle, far",
                   "Halfway line x centre circle, near", "Halfway line, near touchline"] +
                  [f"Right {n}" for n in _NAMES[9:13]] + ["Right penalty spot"] +
                  [f"Right {n}" for n in _NAMES[6:8]] + [f"Right {n}" for n in _NAMES[0:6]] +
                  ["Centre circle, left of the spot", "Centre circle, right of the spot", "Centre spot"])
LANDMARKS = WORLD_KEYPOINTS + [(L / 2, W / 2)]   # the 32 model keypoints plus the centre spot


def project_landmarks(matrix, size) -> list[dict]:
    """Every landmark (id 1-33, name, world metres) with where ``matrix`` (image -> metres) puts it in a
    ``size`` = (width, height) frame, ``image`` None when it falls off screen. For a person to drag into place."""
    inv = np.linalg.inv(np.asarray(matrix, dtype=float).reshape(3, 3))
    w, h = size
    out = []
    for i, (x, y) in enumerate(LANDMARKS):
        p = inv @ [x, y, 1.0]
        img = None
        if p[2] > 0:
            u, v = p[0] / p[2], p[1] / p[2]
            img = [round(float(u), 1), round(float(v), 1)] if -0.1 * w <= u <= 1.1 * w and -0.1 * h <= v <= 1.1 * h else None
        out.append({"id": i + 1, "name": LANDMARK_NAMES[i], "world": [round(x, 2), round(y, 2)], "image": img})
    return out


def _mirror_index() -> list[int]:
    """Keypoint i's twin in the other half (x -> L - x): the two halves of a pitch look alike to the model."""
    return [min(range(len(WORLD_KEYPOINTS)), key=lambda j: (WORLD_KEYPOINTS[j][0] - (L - x)) ** 2
                + (WORLD_KEYPOINTS[j][1] - y) ** 2) for x, y in WORLD_KEYPOINTS]


MIRROR = _mirror_index()


def side(detail: list[dict]) -> tuple[int, bool]:
    """(-1 left half, +1 right half, 0 unknown, centre keypoints seen) from the RANSAC inliers."""
    xs = [WORLD_KEYPOINTS[d["id"] - 1][0] for d in detail if d.get("inlier")]
    centre = any(abs(x - L / 2) < 1.0 for x in xs)
    left, right = sum(x < L / 2 - 1 for x in xs), sum(x > L / 2 + 1 for x in xs)
    return (0 if left == right else (1 if right > left else -1)), centre


def refit_mirrored(image: np.ndarray, detail: list[dict]) -> dict:
    """The same keypoints read as the other half of the pitch, checked against the painted lines."""
    points, conf = np.zeros((len(WORLD_KEYPOINTS), 2)), np.zeros(len(WORLD_KEYPOINTS))
    for d in detail:
        j = MIRROR[d["id"] - 1]
        points[j], conf[j] = (d["x"], d["y"]), d["conf"]
    out = homography_from_keypoints(points, conf)
    if out["ok"]:
        check = line_check(image, out["matrix"])
        out["lines"] = check
        if not check["ok"]:
            out.update(ok=False, matrix=None)
        else:
            _refine_into(image, out)
    out["method"] = "pitch_model_mirrored"
    return out


def detect_pitch(image: np.ndarray) -> dict:
    points, conf = predict(image)
    out = homography_from_keypoints(points, conf)
    if out["ok"]:
        check = line_check(image, out["matrix"])
        out["lines"] = check
        if not check["ok"]:
            out.update(ok=False, matrix=None)
        else:
            _refine_into(image, out)
    return out


def _refine_into(image: np.ndarray, out: dict) -> None:
    """Replace out["matrix"] with the line-refined homography when it aligns the paint better (in place)."""
    refined = refine_with_lines(image, out["matrix"], out.get("image_points"), out.get("pitch_points"))
    if refined is not None:
        check = line_check(image, refined)
        if check["ok"] and check["median_px"] < out["lines"]["median_px"]:
            out.update(matrix=refined, refined=True, lines_before=out["lines"], lines=check)


def _world_samples(step_m: float = 0.75) -> np.ndarray:
    """Points along every painted pitch line and the centre circle, in metres."""
    pts = []
    for (ax, ay), (bx, by) in LINES:
        n = max(2, int(np.hypot(bx - ax, by - ay) / step_m))
        pts += [(ax + u * (bx - ax), ay + u * (by - ay)) for u in np.linspace(0, 1, n)]
    pts += [(L / 2 + R * np.cos(a), W / 2 + R * np.sin(a)) for a in np.linspace(0, 2 * np.pi, 72)]
    return np.asarray(pts, dtype=np.float64)


WORLD_LINES = None


def refine_with_lines(image: np.ndarray, matrix: np.ndarray, kp_src=None, kp_dst=None, iters: int = 4):
    """Pull the projected pitch markings onto the nearest painted line and refit (image -> metres).

    Keypoints are few and noisy; the painted lines give hundreds of correspondences. Each round matches every
    projected line sample to its nearest paint pixel within a shrinking radius and refits with RANSAC, keypoint
    pairs included so the fit cannot slide along a single line. None when there is too little paint to use."""
    import cv2

    global WORLD_LINES
    if WORLD_LINES is None:
        WORLD_LINES = _world_samples()
    h, w = image.shape[:2]
    hsv = cv2.cvtColor(np.ascontiguousarray(image[:, :, :3]), cv2.COLOR_RGB2HSV)
    paint = ((hsv[:, :, 2] > 160) & (hsv[:, :, 1] < 80)).astype(np.uint8)
    paint[: int(0.15 * h)] = 0   # stands and advertising boards
    if paint.sum() < 200:
        return None
    dist, labels = cv2.distanceTransformWithLabels(1 - paint, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    ys, xs = np.nonzero(paint)
    where = np.zeros((labels.max() + 1, 2), dtype=np.float64)
    where[labels[ys, xs]] = np.c_[xs, ys]
    kp = (np.float64(kp_src).reshape(-1, 2), np.float64(kp_dst).reshape(-1, 2)) if kp_src is not None and len(kp_src) else None
    current = matrix
    for radius in np.linspace(0.025, 0.008, iters) * w:
        inv = np.linalg.inv(current)
        proj = cv2.perspectiveTransform(WORLD_LINES.reshape(-1, 1, 2), inv).reshape(-1, 2)
        inside = (proj[:, 0] > 2) & (proj[:, 0] < w - 3) & (proj[:, 1] > 0.15 * h) & (proj[:, 1] < h - 3)
        px = proj[inside].astype(int)
        near = dist[px[:, 1], px[:, 0]] <= radius
        if near.sum() < 40:
            return None
        src = where[labels[px[near][:, 1], px[near][:, 0]]]
        dst = WORLD_LINES[inside][near]
        if kp is not None:   # keypoints weigh as much as a short stretch of line
            src, dst = np.r_[src, np.repeat(kp[0], 8, axis=0)], np.r_[dst, np.repeat(kp[1], 8, axis=0)]
        fit, _ = cv2.findHomography(src.astype(np.float32), dst.astype(np.float32), cv2.RANSAC, 0.5)
        if fit is None:
            return None
        current = fit / fit[2, 2]
    return current


LINES = [((0, 0), (L, 0)), ((0, W), (L, W)), ((0, 0), (0, W)), ((L, 0), (L, W)), ((L / 2, 0), (L / 2, W)),
         ((PEN_D, (W - PEN_W) / 2), (PEN_D, (W + PEN_W) / 2)), ((L - PEN_D, (W - PEN_W) / 2), (L - PEN_D, (W + PEN_W) / 2)),
         ((0, (W - PEN_W) / 2), (PEN_D, (W - PEN_W) / 2)), ((0, (W + PEN_W) / 2), (PEN_D, (W + PEN_W) / 2)),
         ((L, (W - PEN_W) / 2), (L - PEN_D, (W - PEN_W) / 2)), ((L, (W + PEN_W) / 2), (L - PEN_D, (W + PEN_W) / 2)),
         ((BOX_D, (W - BOX_W) / 2), (BOX_D, (W + BOX_W) / 2)), ((L - BOX_D, (W - BOX_W) / 2), (L - BOX_D, (W + BOX_W) / 2)),
         ((0, (W - BOX_W) / 2), (BOX_D, (W - BOX_W) / 2)), ((0, (W + BOX_W) / 2), (BOX_D, (W + BOX_W) / 2)),
         ((L, (W - BOX_W) / 2), (L - BOX_D, (W - BOX_W) / 2)), ((L, (W + BOX_W) / 2), (L - BOX_D, (W + BOX_W) / 2))]


def line_check(image: np.ndarray, matrix: np.ndarray, steps: int = 80) -> dict:
    """Project the pitch markings onto the frame and measure how far they land from white paint."""
    import cv2

    h, w = image.shape[:2]
    hsv = cv2.cvtColor(np.ascontiguousarray(image[:, :, :3]), cv2.COLOR_RGB2HSV)
    paint = ((hsv[:, :, 2] > 160) & (hsv[:, :, 1] < 80)).astype(np.uint8)
    dist = cv2.distanceTransform(1 - paint, cv2.DIST_L2, 3)
    inv = np.linalg.inv(matrix)
    samples = []
    for (ax, ay), (bx, by) in LINES:
        for u in np.linspace(0, 1, steps):
            p = inv @ [ax + u * (bx - ax), ay + u * (by - ay), 1.0]
            if p[2] <= 0:
                continue
            x, y = p[0] / p[2], p[1] / p[2]
            if 3 < x < w - 3 and 0.15 * h < y < h - 3:   # the top band is stands and boards
                samples.append(dist[int(y), int(x)])
    if len(samples) < LINE_SAMPLES:
        return {"ok": False, "n": len(samples), "support": None, "median_px": None}
    d = np.asarray(samples)
    support, median = float((d <= 0.008 * w).mean()), float(np.median(d))
    return {"ok": support >= LINE_SUPPORT and median <= LINE_MEDIAN * w, "n": len(samples),
            "support": round(support, 3), "median_px": round(median, 1)}


def _spread(points: np.ndarray) -> float:
    """Second singular value of the centred points: ~0 when they lie on one line."""
    if len(points) < 3:
        return 0.0
    centred = np.asarray(points, dtype=float) - np.mean(points, axis=0)
    return float(np.linalg.svd(centred, compute_uv=False)[1] / np.sqrt(len(points)))

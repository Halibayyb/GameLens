"""Pitch homography.

Four image points map onto the 105 x 68 metre pitch. With no calibration file
the four corners of the largest green region are used: top of the image is the
far touchline (y = 68), bottom is the near touchline (y = 0), left is x = 0.
That is only a rough broadcast estimate. A calibration JSON is the accurate path:

    {"image_points": [[x, y], [x, y], [x, y], [x, y]],
     "pitch_points": [[0, 68], [105, 68], [105, 0], [0, 0]]}

``image_points`` order is top-left, top-right, bottom-right, bottom-left.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

PITCH_TL_TR_BR_BL = [(0.0, 68.0), (105.0, 68.0), (105.0, 0.0), (0.0, 0.0)]


def homography(src, dst) -> np.ndarray:
    """3x3 homography mapping ``src`` image points to ``dst`` pitch metres (4 or more pairs, least squares)."""
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    if src.ndim != 2 or src.shape[1] != 2 or src.shape != dst.shape or len(src) < 4:
        raise ValueError("homography needs at least four source points and as many destination points")
    if np.linalg.matrix_rank(np.c_[src, np.ones(len(src))], tol=1e-6 * max(1.0, float(np.abs(src).max()))) < 3:
        raise ValueError("degenerate points: they lie on one line")
    return homography_with_lines(list(zip(map(tuple, src), map(tuple, dst))))


# Painted lines a person can click points on (world metres, a*x + b*y + c = 0): a broadcast midfield frame often
# shows the touchline and the halfway line but too few identifiable points to fix the pitch.
PITCH_LINES = {
    "far_touchline": (0.0, 1.0, -68.0), "near_touchline": (0.0, 1.0, 0.0), "halfway": (1.0, 0.0, -52.5),
    "left_goal_line": (1.0, 0.0, 0.0), "right_goal_line": (1.0, 0.0, -105.0),
    "left_box_edge": (1.0, 0.0, -16.5), "right_box_edge": (1.0, 0.0, -88.5),
    "left_six_yard_edge": (1.0, 0.0, -5.5), "right_six_yard_edge": (1.0, 0.0, -99.5),
    "box_side_far": (0.0, 1.0, -54.16), "box_side_near": (0.0, 1.0, -13.84),
}


def homography_with_lines(points, line_points=()) -> np.ndarray:
    """Image -> metres from point pairs [(image, world)] and points known to lie on a painted line
    [(image, (a, b, c))]. Two equations per point pair, one per line point; at least eight in all."""
    points, line_points = list(points), list(line_points)
    if 2 * len(points) + len(line_points) < 8:
        raise ValueError("need at least eight constraints: four landmarks, or fewer plus points on lines")
    img = np.asarray([p for p, _ in points] + [p for p, _ in line_points], dtype=float)
    ts = _normaliser(img)
    wpts = np.asarray([w for _, w in points], dtype=float) if points else np.zeros((0, 2))
    td = _normaliser(wpts) if len(wpts) >= 2 else np.eye(3)
    td_inv_t = np.linalg.inv(td).T   # a world line transforms with the inverse transpose
    rows = []
    for (x, y), (u, v) in zip(_apply_all(ts, [p for p, _ in points]), _apply_all(td, [w for _, w in points])):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    for (x, y), line in zip(_apply_all(ts, [p for p, _ in line_points]), [l for _, l in line_points]):
        a, b, c = td_inv_t @ np.asarray(line, dtype=float)
        rows.append([a * x, a * y, a, b * x, b * y, b, c * x, c * y, c])
    _, sv, vt = np.linalg.svd(np.asarray(rows, dtype=float))
    if len(sv) >= 8 and sv[7] < 1e-9 * sv[0]:
        raise ValueError("these points do not fix the pitch: spread them over different lines")
    matrix = np.linalg.inv(td) @ vt[-1].reshape(3, 3) @ ts
    if abs(matrix[2, 2]) < 1e-12:
        raise ValueError("degenerate points")
    matrix = matrix / matrix[2, 2]
    if _collapses(matrix, img):
        raise ValueError("these points do not fix the pitch: add a landmark or a point on another line")
    return matrix


def _collapses(matrix: np.ndarray, img: np.ndarray) -> bool:
    """True when the fit squeezes the clicked image region onto (nearly) a line or a point on the pitch.

    Noisy clicks make a rank-deficient system look full rank; the fit then maps the frame onto one line."""
    (x0, y0), (x1, y1) = img.min(axis=0), img.max(axis=0)
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    w = []
    for x, y in corners:
        p = matrix @ [x, y, 1.0]
        if p[2] <= 0:
            return True   # part of the clicked region would map behind the camera
        w.append(p[:2] / p[2])
    w = np.asarray(w)
    area = 0.5 * abs(np.dot(w[:, 0], np.roll(w[:, 1], 1)) - np.dot(w[:, 1], np.roll(w[:, 0], 1)))
    return area < 4.0   # square metres: a real view of the clicked markings covers far more


def _apply_all(t: np.ndarray, pts) -> list:
    return [tuple((t @ [x, y, 1.0])[:2]) for x, y in pts]


def line_error_m(matrix: np.ndarray, line_points) -> float:
    """Mean distance (metres) of the mapped line points from their painted lines (0 when there are none)."""
    errs = [abs(a * x + b * y + c) / np.hypot(a, b) for p, (a, b, c) in line_points for x, y in [apply(matrix, *p)]]
    return float(np.mean(errs)) if errs else 0.0


def _normaliser(points: np.ndarray) -> np.ndarray:
    centre = points.mean(axis=0)
    scale = np.sqrt(2) / (np.linalg.norm(points - centre, axis=1).mean() or 1.0)
    return np.array([[scale, 0, -scale * centre[0]], [0, scale, -scale * centre[1]], [0, 0, 1.0]])


def reprojection_m(matrix: np.ndarray, src, dst) -> float:
    """Mean distance (metres) between where ``matrix`` puts the ``src`` image points and their ``dst`` metres."""
    return float(np.mean([np.hypot(*(np.subtract(apply(matrix, *p), q))) for p, q in zip(src, dst)]))


def apply(matrix: np.ndarray, x: float, y: float) -> tuple[float, float]:
    point = matrix @ np.array([x, y, 1.0])
    return float(point[0] / point[2]), float(point[1] / point[2])


def green_quad(image: np.ndarray):
    """Axis-aligned corners of the green region, or None when the frame is not a pitch."""
    if image.ndim != 3 or image.shape[2] < 3:
        return None
    red = image[:, :, 0].astype(np.int16)
    green = image[:, :, 1].astype(np.int16)
    blue = image[:, :, 2].astype(np.int16)
    mask = (green > 60) & (green > red * 1.15) & (green > blue * 1.15)
    ys, xs = np.nonzero(mask)
    if xs.size < 50:
        return None
    x0, x1 = float(xs.min()), float(xs.max())
    y0, y1 = float(ys.min()), float(ys.max())
    if x1 - x0 < 10 or y1 - y0 < 10:
        return None
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def from_image(image: np.ndarray, calibration: dict | None = None) -> np.ndarray:
    """Homography for one frame. ``calibration`` overrides the green-pitch estimate."""
    if calibration:
        src = calibration["image_points"]
        dst = calibration.get("pitch_points") or PITCH_TL_TR_BR_BL
        return homography(src, dst)
    quad = green_quad(image)
    if quad is None:
        raise ValueError("no pitch visible: pass a calibration JSON or a frame that shows the grass")
    return homography(quad, PITCH_TL_TR_BR_BL)


def load_calibration(path: str | Path | None) -> dict | None:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))

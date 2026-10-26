"""Pitch-keypoint homography without the model: the world table, RANSAC and the paint check."""
import cv2
import numpy as np
import pytest

from vision import pitch_keypoints as pk
from vision.keypoints import detect_keypoints

MIRROR = {**{i: 24 + i for i in range(1, 7)}, 7: 23, 8: 24, 9: 22, 10: 18, 11: 19, 12: 20, 13: 21, 31: 32}


def test_world_keypoints_are_32_points_on_the_pitch_and_mirror_end_to_end():
    w = pk.WORLD_KEYPOINTS
    assert len(w) == 32
    assert all(0 <= x <= 105 and 0 <= y <= 68 for x, y in w)
    for a, b in MIRROR.items():
        (xa, ya), (xb, yb) = w[a - 1], w[b - 1]
        assert xa + xb == pytest.approx(105) and ya == pytest.approx(yb)
    assert w[0] == (0.0, 68.0)                              # keypoint 1: far touchline (y flipped to ours)
    assert all(w[i - 1][0] == 52.5 for i in (14, 15, 16, 17))
    assert w[8] == (11.0, 34.0) and w[21] == (94.0, 34.0)  # penalty spots


def _camera() -> np.ndarray:
    """A plausible broadcast homography world (m) -> image (px), 960 x 540."""
    src = np.float32([[0, 68], [52.5, 68], [52.5, 0], [0, 0]])
    dst = np.float32([[120, 60], [900, 60], [960, 530], [0, 530]])
    return cv2.getPerspectiveTransform(src, dst)


def _to_image(H, pts):
    return cv2.perspectiveTransform(np.float32(pts).reshape(-1, 1, 2), H).reshape(-1, 2)


def test_ransac_recovers_the_homography_and_flags_outliers():
    H = _camera()
    ids = [1, 2, 3, 7, 9, 10, 11, 12, 13, 14, 15, 16]
    points = np.zeros((32, 2))
    conf = np.zeros(32)
    points[[i - 1 for i in ids]] = _to_image(H, [pk.WORLD_KEYPOINTS[i - 1] for i in ids])
    conf[[i - 1 for i in ids]] = 0.9
    points[10] += (80, -60)        # keypoint 11 mislabelled by the model
    points[13] += (-120, 40)       # keypoint 14 too
    out = pk.homography_from_keypoints(points, conf)
    assert out["ok"] and out["inliers"] == len(ids) - 2 and out["reproj_m"] < 0.2
    flags = {d["id"]: d["inlier"] for d in out["keypoints"]}
    assert flags[11] is False and flags[14] is False and flags[9] is True
    back = _to_image(np.linalg.inv(out["matrix"]), [(30, 20)])[0]
    assert np.allclose(back, _to_image(H, [(30, 20)])[0], atol=1.0)


def test_keypoints_on_one_line_give_no_homography():
    H = _camera()
    points, conf = np.zeros((32, 2)), np.zeros(32)
    points[:6] = _to_image(H, pk.WORLD_KEYPOINTS[:6])     # the goal line only
    conf[:6] = 0.95
    out = pk.homography_from_keypoints(points, conf)
    assert not out["ok"] and out["matrix"] is None


def _painted(H, size=(540, 960)) -> np.ndarray:
    img = np.zeros((*size, 3), np.uint8)
    img[:, :] = (40, 140, 50)
    for (ax, ay), (bx, by) in pk.LINES:   # H maps world -> image
        p = _to_image(H, [(ax, ay), (bx, by)])
        cv2.line(img, tuple(int(v) for v in p[0]), tuple(int(v) for v in p[1]), (255, 255, 255), 3)
    return img


def test_line_check_accepts_paint_and_rejects_a_wrong_mapping():
    H = _camera()                      # world -> image
    img = _painted(H)
    assert pk.line_check(img, np.linalg.inv(H))["ok"]
    shifted = np.linalg.inv(H) @ np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1.0]])
    shifted = np.array([[1, 0, 9.0], [0, 1, 6.0], [0, 0, 1]]) @ shifted   # 9 m along, 6 m across
    check = pk.line_check(img, shifted)
    assert not check["ok"] and check["median_px"] > pk.LINE_MEDIAN * 960 * 0.5


def test_small_frames_and_the_off_switch_skip_the_pitch_model(monkeypatch):
    green = np.zeros((48, 80, 3), np.uint8)
    green[:, :, 1] = 140
    kp = detect_keypoints(green)
    assert kp["method"] == "green_quad" and kp["trusted"]      # model never ran: the box is all there is
    monkeypatch.setenv("VISION_PITCH_MODEL", "off")
    big = np.zeros((540, 960, 3), np.uint8)
    big[:, :, 1] = 140
    assert not pk.available(big)
    assert detect_keypoints(big)["trusted"]

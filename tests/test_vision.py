"""Vision pipeline without torch: a fake detector and a green frame."""
import numpy as np

from vision.homography import apply, from_image
from vision.pipeline import FakeDetector, track_frames, write_tracked


def test_fake_detector_lands_on_the_pitch_and_splits_teams(tmp_path):
    image = np.zeros((48, 80, 3), dtype=np.uint8)
    image[:, :, 1] = 140  # green pitch, full frame
    matrix = from_image(image)
    # Foot of the left player and the ball centre, in pixels, then metres.
    left = apply(matrix, 10, 30)
    right = apply(matrix, 70, 30)
    ball = apply(matrix, 40, 24)
    assert 0 <= left[0] < right[0] <= 105
    detector = FakeDetector([[
        {"cls": "person", "track_id": 7, "xyxy": [5, 10, 15, 30], "color": (20, 40, 180)},
        {"cls": "person", "track_id": 8, "xyxy": [65, 10, 75, 30], "color": (220, 120, 30)},
        {"cls": "ball", "track_id": 1, "xyxy": [36, 20, 44, 28], "color": (250, 250, 250)},
    ]])
    frames = list(track_frames([image], detector, fps=5))
    assert len(frames) == 1
    by_team = {p["team"] for p in frames[0]["players"]}
    assert by_team == {"home", "away"}
    home = next(p for p in frames[0]["players"] if p["team"] == "home")
    away = next(p for p in frames[0]["players"] if p["team"] == "away")
    assert home["x"] < away["x"]
    assert abs(frames[0]["ball"]["x"] - ball[0]) < 1
    assert any(p["role"] == "GK" for p in frames[0]["players"])
    meta = write_tracked(tmp_path, frames, fps=5)
    assert meta["source"] == "vision" and meta["synthetic"] is False
    assert (tmp_path / "tracking.csv").exists()
    assert (tmp_path / "events.jsonl").read_text().count("kick_off") == 1
    # left/right expectations from the homography, so a mapping change fails the test loudly
    assert abs(home["x"] - left[0]) < 1
    assert abs(away["x"] - right[0]) < 1

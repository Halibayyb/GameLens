"""Cuts and shot types, and that tracking never carries homographies, ids or the ball across a cut."""
import numpy as np

from vision.pipeline import FakeDetector, smooth_by_shot, track_frames
from vision.shots import CutDetector, segment, shot_type


def _frame(rgb, size=(90, 160), seed=0) -> np.ndarray:
    """A textured frame (real footage spreads over many histogram bins; a flat colour does not)."""
    noise = np.random.default_rng(seed).integers(-25, 26, (*size, 3))
    return np.clip(np.asarray(rgb) + noise, 0, 255).astype(np.uint8)


def test_cut_detector_fires_on_a_new_scene_not_on_a_small_change():
    cuts = CutDetector()
    seq = [_frame((40, 150, 50)), _frame((44, 155, 52), seed=1), _frame((200, 40, 40)), _frame((205, 42, 41), seed=2)]
    assert [cuts.update(f) for f in seq] == [False, False, True, False]


def test_shot_type_wide_close_other():
    img = _frame((40, 150, 50), size=(540, 960))
    mapped = {"matrix": np.eye(3), "trusted": True, "method": "pitch_model", "n_visible": 8}
    assert shot_type(img, mapped, [[0, 0, 10, 40]]) == "wide"
    assert shot_type(img, {"matrix": None, "trusted": False}, [[0, 0, 300, 400]]) == "close"
    crowd = _frame((120, 90, 110), size=(540, 960))
    assert shot_type(crowd, {"matrix": None, "trusted": False}, []) == "close"     # no grass
    assert shot_type(img, {"matrix": None, "trusted": False}, [[0, 0, 10, 40]]) == "other"


def test_segment_majority_type_per_shot():
    seq = [_frame((40, 150, 50))] * 3 + [_frame((200, 40, 40))] * 2
    shots = segment(seq, ["wide", "wide", "other", "close", "close"])
    assert [(s["start"], s["end"], s["type"]) for s in shots] == [(0, 2, "wide"), (3, 4, "close")]


def test_smoothing_stays_inside_a_shot_and_long_gaps_stay_unmapped():
    a, b = np.eye(3), np.diag([2.0, 2.0, 1.0])
    out = smooth_by_shot([a, a, b, None, None, None, b], [0, 0, 1, 1, 1, 1, 1])
    assert np.allclose(out[1], a) and np.allclose(out[2], b)        # no blend across the cut
    assert out[3] is not None and out[4] is not None                 # within MAX_FILL of a good frame
    assert out[5] is not None and out[6] is not None
    out = smooth_by_shot([a, None, None, None, None, None], [0] * 6)
    assert out[3] is None and out[5] is None                         # too far from any mapped frame


def test_track_frames_tags_shots_and_drops_an_unmapped_shot():
    pitch = np.zeros((48, 80, 3), np.uint8)
    pitch[:, :, 1] = 140
    crowd = np.full((48, 80, 3), (150, 60, 90), np.uint8)    # no grass: no homography at all
    people = [{"cls": "person", "track_id": 7, "xyxy": [5, 30, 9, 40], "color": (20, 40, 180)},
              {"cls": "person", "track_id": 8, "xyxy": [65, 30, 69, 40], "color": (220, 120, 30)},
              {"cls": "ball", "track_id": 1, "xyxy": [38, 24, 42, 28]}]

    class Resettable(FakeDetector):
        resets = 0

        def reset(self):
            Resettable.resets += 1

    det = Resettable([people, people, people, people])
    debug: dict = {}
    frames = list(track_frames([pitch, pitch, crowd, crowd], det, fps=5, debug=debug))
    assert [f["shot"]["id"] for f in frames] == [0, 0, 1, 1]
    assert [f["mapped"] for f in frames] == [True, True, False, False]
    assert frames[2]["players"] == [] and frames[2]["ball"] is None
    assert Resettable.resets == 1
    assert [(s["start_t"], s["end_t"]) for s in debug["shots"]] == [(0.0, 0.4), (0.4, 0.8)]
    assert all("trusted" in k and "keypoints" in k for k in debug["keypoints"])

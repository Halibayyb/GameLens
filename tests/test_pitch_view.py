"""Vision quality + Broadcast pitch schema from synthetic tracks. No GPU."""
import numpy as np

from highlights.demo import demo_frames, demo_meta
from highlights.events import derive_events
from highlights.pitch_view import build_pitch_views, caption_moment
from highlights.publish import get_published, list_published, publish_view
from vision.homography import apply, from_image, homography
from vision.pipeline import FakeDetector, track_frames
from vision.quality import drop_low_confidence, interpolate_ball, smooth_homographies


def test_ball_interpolation_fills_short_gaps_only():
    pts = [(10.0, 10.0), None, None, (16.0, 16.0), None, None, None, None, None, None, None, None, None, (40.0, 40.0)]
    out = interpolate_ball(pts, max_gap=3)
    assert out[1] is not None and abs(out[1][0] - 12.0) < 1e-6
    assert out[5] is None  # 8-frame hole after the filled gap is too long


def test_smooth_homographies_reuses_last_good_matrix():
    src = [(0, 0), (10, 0), (10, 10), (0, 10)]
    a = homography(src, [(0, 68), (105, 68), (105, 0), (0, 0)])
    out = smooth_homographies([a, None, a])
    assert out[1] is not None
    assert abs(out[1][2, 2] - 1) < 1e-6


def test_low_confidence_points_are_dropped():
    kept = drop_low_confidence([
        {"player_id": "H1", "confidence": 0.9},
        {"player_id": "H2", "confidence": 0.1},
    ])
    assert [p["player_id"] for p in kept] == ["H1"]


def test_caption_never_uses_coordinates():
    cap = caption_moment(
        {"type": "shot", "outcome": "goal", "x": 99.1, "y": 34.2},
        {"home": "Home", "away": "Away"},
    )
    assert cap["title"] == "Goal"
    assert "99" not in cap["title"]
    assert cap["home"] == "Home"


def test_demo_frames_become_broadcast_schema(tmp_path):
    frames = demo_frames()
    meta = demo_meta()
    events = derive_events(frames, teams={"home": "Home", "away": "Away"},
                           match_id=meta["match_id"])
    pitch = build_pitch_views(
        frames, events, meta, tmp_path / "match",
        reconstructed=False, confidence=0.99,
    )
    assert pitch["views"]
    view = pitch["views"][0]
    assert view["track"]["t"] and "ball" in view["track"]["objects"]
    assert any(oid != "ball" for oid in view["track"]["objects"])
    assert view["moment"]["kind"] in ("goal", "chance")
    assert view["shot"]["from"] and view["shot"]["to"]
    assert view["badge"] == "Schematic demo"
    # Same window shape the existing BroadcastPitch already consumes.
    assert set(view["track"]) >= {"fps", "t", "objects"}


def test_publish_appends_to_match_list(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLISHED_DIR", str(tmp_path))
    frames = demo_frames()
    meta = demo_meta()
    events = derive_events(frames, teams={"home": "Home", "away": "Away"})
    pitch = build_pitch_views(frames, events, meta, tmp_path / "match", reconstructed=True, confidence=0.81)
    summary = publish_view("hl_demo", pitch["views"][0])
    assert summary["id"].startswith("vid_hl_demo_")
    assert summary["reconstructed"] is True
    assert summary["badge"] == "Reconstructed from video · approximate motion"
    assert list_published()[0]["id"] == summary["id"]
    payload = get_published(summary["id"])
    assert payload["track"]["objects"]
    assert payload["moment"]["id"] == summary["id"]


def test_fake_detector_quality_path_still_splits_teams():
    image = np.zeros((48, 80, 3), dtype=np.uint8)
    image[:, :, 1] = 140
    matrix = from_image(image)
    left = apply(matrix, 10, 30)
    right = apply(matrix, 70, 30)
    detector = FakeDetector([[
        {"cls": "person", "track_id": 7, "xyxy": [5, 10, 15, 30], "color": (20, 40, 180), "confidence": 0.9},
        {"cls": "person", "track_id": 8, "xyxy": [65, 10, 75, 30], "color": (220, 120, 30), "confidence": 0.9},
        {"cls": "ball", "track_id": 1, "xyxy": [36, 20, 44, 28], "confidence": 0.8},
    ]])
    debug = {}
    frames = list(track_frames([image], detector, fps=5, debug=debug))
    assert {p["team"] for p in frames[0]["players"]} == {"home", "away"}
    home = next(p for p in frames[0]["players"] if p["team"] == "home")
    away = next(p for p in frames[0]["players"] if p["team"] == "away")
    assert home["x"] < away["x"]
    assert abs(home["x"] - left[0]) < 1
    assert abs(away["x"] - right[0]) < 1
    assert debug["keypoints"][0]["ok"] is True
    assert debug["clusters"]["home"]

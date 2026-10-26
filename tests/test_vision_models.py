"""Prepared-model registry and the football detector's pure parts, with fakes (no weights needed)."""
import hashlib

import numpy as np
import pytest

from vision import detectors, models
from vision.detectors import BallPicker, FootballDetector, PanTracker, make_detector, nms, tiles, weigh_balls


@pytest.fixture
def fake_registry(tmp_path, monkeypatch):
    payload = b"weights"
    spec = {"file": "fake.pt", "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload),
            "source": {"blob": "fake.pt"}, "task": "detect", "imgsz": 640, "classes": {}, "note": ""}
    monkeypatch.setitem(models.MANIFEST, "fake", spec)
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(models, "_verified", set())
    monkeypatch.setattr(models, "_loaded", {})
    return tmp_path, payload


def test_path_verifies_size_and_checksum_once(fake_registry, monkeypatch):
    folder, payload = fake_registry
    assert not models.available("fake")
    (folder / "fake.pt").write_bytes(payload)
    assert models.available("fake") and models.path("fake") == folder / "fake.pt"
    calls = []
    monkeypatch.setattr(models, "_sha256", lambda f: calls.append(f) or "")
    models.path("fake")                      # cached: no second checksum
    assert calls == []


def test_path_rejects_a_corrupt_file(fake_registry):
    folder, payload = fake_registry
    (folder / "fake.pt").write_bytes(payload[::-1])   # same size, wrong bytes
    with pytest.raises(models.ModelUnavailable, match="sha256"):
        models.path("fake")


def test_missing_weights_come_from_azure_blob_and_a_failure_explains_how_to_fetch(fake_registry, monkeypatch):
    import azure.storage.blob as blob
    folder, payload = fake_registry
    monkeypatch.setenv("MODELS_BLOB_URL", "https://acct.blob.core.windows.net/models?sv=sas")
    asked = []

    class Container:
        def download_blob(self, name):
            asked.append(name)
            return type("D", (), {"readinto": lambda self, f: f.write(payload)})()

    monkeypatch.setattr(blob.ContainerClient, "from_container_url", classmethod(lambda cls, url, **k: Container()))
    assert models.path("fake") == folder / "fake.pt" and asked == ["fake.pt"]   # fetched, then checked

    (folder / "fake.pt").unlink()
    models._verified.clear()

    def refused(cls, url, **k):
        raise PermissionError("no access")

    monkeypatch.setattr(blob.ContainerClient, "from_container_url", classmethod(refused))
    with pytest.raises(models.ModelUnavailable, match="az login"):
        models.path("fake")


def test_load_returns_one_instance_per_model(fake_registry, monkeypatch):
    folder, payload = fake_registry
    (folder / "fake.pt").write_bytes(payload)
    made = []

    class FakeYOLO:
        def __init__(self, weights, task=None):
            made.append(weights)

    import ultralytics
    monkeypatch.setattr(ultralytics, "YOLO", FakeYOLO)
    assert models.load("fake") is models.load("fake")
    assert len(made) == 1


def test_make_detector_choice(monkeypatch):
    monkeypatch.setattr(models, "available", lambda name: True)
    monkeypatch.delenv("VISION_DETECTOR", raising=False)
    assert isinstance(make_detector(), FootballDetector)
    monkeypatch.setenv("VISION_DETECTOR", "coco")
    assert type(make_detector()).__name__ == "YoloByteTrack"
    monkeypatch.delenv("VISION_DETECTOR")
    monkeypatch.setattr(models, "available", lambda name: False)
    assert type(make_detector()).__name__ == "YoloByteTrack"


def test_tiles_cover_the_frame_with_overlap():
    windows = tiles(1920, 1080)
    assert all(x1 - x0 == 640 and y1 - y0 == 640 for x0, y0, x1, y1 in windows)
    assert max(x1 for _, _, x1, _ in windows) == 1920 and max(y1 for *_, y1 in windows) == 1080
    assert tiles(500, 400) == [(0, 0, 500, 400)]


def _ball(x, y, conf, source="tile"):
    return {"cls": "ball", "xyxy": [x - 5, y - 5, x + 5, y + 5], "confidence": conf, "source": source}


def test_nms_merges_a_ball_seen_by_two_tiles():
    kept = nms([_ball(100, 100, 0.6), _ball(101, 100, 0.9), _ball(400, 300, 0.5)])
    assert [b["confidence"] for b in kept] == [0.9, 0.5]


class _Tensor:
    def __init__(self, a):
        self.a = np.asarray(a)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class _Boxes:
    def __init__(self, rows):
        self.xyxy = _Tensor([r[1] for r in rows])
        self.cls = _Tensor([r[0] for r in rows])
        self.conf = _Tensor([r[2] for r in rows])
        self.id = None

    def __len__(self):
        return len(self.cls.a)


def test_roles_come_from_the_model_classes():
    result = type("R", (), {"boxes": _Boxes([(0, [5, 5, 9, 9], 0.7), (1, [0, 0, 10, 30], 0.9),
                                             (2, [20, 0, 30, 30], 0.8), (3, [40, 0, 50, 30], 0.6)])})()
    people, balls = FootballDetector.people_and_balls(result)
    assert [p["role"] for p in people] == ["goalkeeper", "player", "referee"]
    assert balls[0]["source"] == "full" and balls[0]["cls"] == "ball"


def _person(x, y, role="player", h=40):
    return {"cls": "person", "xyxy": [x - 8, y - h, x + 8, y], "confidence": 0.9, "role": role}


def test_pan_tracker_keeps_ids_through_a_camera_pan_and_resets():
    t = PanTracker()
    first = t.assign([_person(100, 200), _person(300, 220), _person(500, 210)])
    ids = [p["track_id"] for p in first]
    moved = t.assign([_person(160, 200), _person(360, 220), _person(560, 210)])   # 60 px pan, beyond one box width
    assert [p["track_id"] for p in moved] == ids
    t.reset()
    assert t.assign([_person(100, 200)])[0]["track_id"] == 1


def test_ball_picker_drops_a_static_spot_and_needs_two_sightings_to_jump():
    pick = BallPicker()
    for _ in range(detectors.STATIC_FRAMES):
        pick.pick([_ball(800, 500, 0.9)])                          # penalty spot, every frame
    assert pick.pick([_ball(800, 500, 0.9), _ball(100, 100, 0.4)])["xyxy"][0] == 95   # the moving ball wins
    assert pick.pick([_ball(1500, 900, 0.9)]) is None                                  # far jump: wait
    assert pick.pick([_ball(1505, 902, 0.9)])["xyxy"][0] == 1500                       # seen twice: believed


def test_gloves_in_a_keeper_reach_are_down_weighted():
    keeper = _person(1000, 500, "goalkeeper", h=80)       # box x 992..1008, y 420..500
    balls = weigh_balls([_ball(1012, 440, 0.86), _ball(600, 480, 0.5)], [keeper])
    assert balls[0]["confidence"] == pytest.approx(0.86 * detectors.HANDS_WEIGHT)
    assert balls[0]["raw_confidence"] == 0.86 and balls[1]["confidence"] == 0.5


def test_detector_info_for_both_detectors():
    info = detectors.detector_info(FootballDetector())
    assert info["name"] == "football" and info["imgsz"] == 1280 and "75b09c377fbf" in info["weights"][0]
    from vision.pipeline import YoloByteTrack
    assert detectors.detector_info(YoloByteTrack(imgsz=1280))["imgsz"] == 1280

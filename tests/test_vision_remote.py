"""Uploads tracked on the vision service: the API client and the service endpoints, no YOLO."""
import httpx
import pytest
from fastapi.testclient import TestClient

from highlights import pipeline
from vision import server

FRAMES = [{"frame": 0, "match_seconds": 0.0, "ball": None, "players": []}]


def test_track_remote_retries_a_cold_start_then_polls(tmp_path, monkeypatch):
    clip = tmp_path / "hl_test" / "input.mp4"
    clip.parent.mkdir()
    clip.write_bytes(b"fake")
    monkeypatch.setenv("VISION_URL", "https://vision.test/")
    monkeypatch.setattr(pipeline, "_step", lambda *a: None)
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"post": 0, "poll": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["post"] += 1
            if calls["post"] == 1:
                return httpx.Response(503)          # replica still starting
            assert b'name="fps"' in request.content
            return httpx.Response(200, json={"job_id": "v1", "status": "running"})
        if request.url.path == "/jobs/v1":
            calls["poll"] += 1
            return httpx.Response(200, json={"status": "running" if calls["poll"] == 1 else "done", "frames": 1,
                                             "error": None})
        return httpx.Response(200, json={"frames": FRAMES, "debug": {"model": "yolo11n.pt"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    frames, debug = pipeline._track_remote(clip, 20, 5, client=client)
    assert frames == FRAMES and debug["model"] == "yolo11n.pt"
    assert calls == {"post": 2, "poll": 2}


def test_track_remote_surfaces_a_vision_error(tmp_path, monkeypatch):
    clip = tmp_path / "hl_err" / "input.mp4"
    clip.parent.mkdir()
    clip.write_bytes(b"fake")
    monkeypatch.setenv("VISION_URL", "https://vision.test")
    monkeypatch.setattr(pipeline, "_step", lambda *a: None)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"job_id": "v2"})
        return httpx.Response(200, json={"status": "error", "frames": 0, "error": "Could not open video"})

    with pytest.raises(pipeline.PipelineError, match="Could not open video"):
        pipeline._track_remote(clip, 20, 5, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_vision_service_upload_and_result(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUT", tmp_path)

    def fake_run(job_id, body):
        assert body["fps"] == 5 and body["video_path"].endswith(".mp4")
        job = server.JOBS[job_id]
        job["frames"] = FRAMES
        job["debug"] = {"confidence": __import__("numpy").float32(0.5)}   # numpy survives the JSON
        job["status"] = "done"

    monkeypatch.setattr(server, "_run", fake_run)
    api = TestClient(server.app)
    r = api.post("/track", files={"file": ("clip.mp4", b"fake", "video/mp4")}, data={"seconds": "20", "fps": "5"})
    job_id = r.json()["job_id"]
    for _ in range(50):   # the job thread
        if server.JOBS[job_id]["status"] == "done":
            break
        __import__("time").sleep(0.01)
    assert api.get(f"/jobs/{job_id}").json()["status"] == "done"
    body = api.get(f"/jobs/{job_id}/result").json()
    assert body["frames"] == FRAMES and body["debug"]["confidence"] == 0.5


def test_vision_service_result_before_done_is_409(monkeypatch):
    server.JOBS["busy"] = {"status": "running", "frames": [], "error": None, "folder": ""}
    assert TestClient(server.app).get("/jobs/busy/result").status_code == 409

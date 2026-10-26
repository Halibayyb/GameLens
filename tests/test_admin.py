"""Admin video gate + publish. Demo clip only; no GPU."""
import time

from fastapi.testclient import TestClient

from highlights.pipeline import process_job
from highlights.store import create_job


def test_admin_page_is_served_when_web_is_built():
    from api.main import app
    client = TestClient(app)
    page = client.get("/admin/video")
    assert page.status_code in (200, 404)
    if page.status_code == 200:
        assert "text/html" in page.headers.get("content-type", "")


def test_admin_status_hidden_when_flag_off(monkeypatch):
    monkeypatch.delenv("ADMIN_VIDEO", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    from api.main import app
    client = TestClient(app)
    body = client.get("/api/admin/status").json()
    assert body["enabled"] is False
    assert client.get("/api/admin/video").status_code == 404


def test_admin_requires_token(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_VIDEO", "1")
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    monkeypatch.setenv("HIGHLIGHTS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("PUBLISHED_DIR", str(tmp_path / "pub"))
    from api.main import app
    client = TestClient(app)
    assert client.get("/api/admin/status").json()["enabled"] is True
    assert client.get("/api/admin/video").status_code == 401
    ok = client.get("/api/admin/video", headers={"X-Admin-Token": "secret-token"})
    assert ok.status_code == 200
    denied = client.post("/api/admin/video/jobs", data={"source": "demo"})
    assert denied.status_code == 401


def test_admin_demo_job_builds_pitch_view_and_publishes(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_VIDEO", "1")
    monkeypatch.setenv("ADMIN_TOKEN", "demo")
    monkeypatch.setenv("HIGHLIGHTS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("PUBLISHED_DIR", str(tmp_path / "pub"))
    from api.main import app
    client = TestClient(app)
    created = client.post(
        "/api/admin/video/jobs",
        data={"source": "demo"},
        headers={"X-Admin-Token": "demo"},
    )
    assert created.status_code == 200
    job_id = created.json()["job_id"]
    body = None
    for _ in range(80):
        body = client.get(f"/api/admin/video/jobs/{job_id}", headers={"X-Admin-Token": "demo"}).json()
        if body["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert body and body["status"] == "done", body
    result = client.get(f"/api/admin/video/jobs/{job_id}/result", headers={"X-Admin-Token": "demo"}).json()
    assert result["pitch_view"]["views"]
    assert result["debug"]["keypoints"]
    assert result["steps"]
    view = result["pitch_view"]["views"][0]
    assert view["track"]["objects"]
    pub = client.post(f"/api/admin/video/jobs/{job_id}/publish", headers={"X-Admin-Token": "demo"})
    assert pub.status_code == 200
    published_id = pub.json()["published"][0]["id"]
    match = client.get("/api/match").json()
    assert any(m["id"] == published_id for m in match["moments"])
    detail = client.get(f"/api/moments/{published_id}").json()
    assert detail["track"]["t"]
    assert "ball" in detail["track"]["objects"]
    assert detail["moment"]["badge"]


def test_highlights_demo_still_writes_pitch_view(tmp_path, monkeypatch):
    monkeypatch.setenv("HIGHLIGHTS_DIR", str(tmp_path))
    job = create_job("demo", filename="demo")
    result = process_job(job["job_id"])
    assert result["pitch_view"]["views"]
    assert result["badge"] == "Schematic demo"
    assert result["debug"]["clusters"]["home"]


def _admin(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_VIDEO", "1")
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    monkeypatch.setenv("HIGHLIGHTS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("PUBLISHED_DIR", str(tmp_path / "pub"))
    from api.main import app
    return TestClient(app)


def test_admin_video_reports_the_vision_service_it_will_use(monkeypatch, tmp_path):
    import httpx
    client = _admin(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_URL", "https://vision.test")
    seen = {"detector": "football", "pitch_model": True, "device": "cpu",
            "scene": {"model": "scene-vision", "api": "openai", "provider": "foundry"}}
    monkeypatch.setattr(httpx, "get", lambda url, timeout: httpx.Response(
        200, json={"ok": True, **seen}, request=httpx.Request("GET", url)))
    v = client.get("/api/admin/video", headers={"X-Admin-Token": "secret-token"}).json()["vision"]
    assert v["where"] == "service" and v["status"] == "ok" and v["detector"] == "football"
    assert v["scene"]["model"] == "scene-vision" and "api_key" not in v["scene"]


def test_admin_video_says_a_sleeping_vision_service_is_starting(monkeypatch, tmp_path):
    import httpx
    client = _admin(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_URL", "https://vision.test")

    def asleep(url, timeout):
        raise httpx.ConnectTimeout("cold start")

    monkeypatch.setattr(httpx, "get", asleep)
    v = client.get("/api/admin/video", headers={"X-Admin-Token": "secret-token"}).json()["vision"]
    assert v == {"where": "service", "status": "starting"}


def test_vision_health_names_detector_and_scene_model_without_keys(monkeypatch):
    from vision import server
    monkeypatch.setenv("SCENE_LLM_ENDPOINT", "https://r.openai.azure.com/openai/v1/")
    monkeypatch.setenv("SCENE_LLM_MODEL", "scene-vision")
    monkeypatch.setenv("SCENE_LLM_API_KEY", "not-for-the-page")
    monkeypatch.setenv("SCENE_LLM_AUTH", "key")
    body = TestClient(server.app).get("/health").json()
    assert body["ok"] and body["detector"] in ("football", "coco") and "pitch_model" in body
    assert body["scene"]["model"] == "scene-vision" and "not-for-the-page" not in str(body)

"""Commentary voice: SSML style by situation, Azure call and cache, the browser fallback."""
import io
import urllib.request

import pytest
from fastapi.testclient import TestClient

from engine import speech


def test_ssml_is_excited_and_stronger_for_a_big_late_goal():
    calm = speech.ssml("What a save!", [], voice="en-US-GuyNeural", style="excited")
    big = speech.ssml("Late, late drama!", ["go_ahead", "late"], voice="en-US-GuyNeural", style="excited")
    assert "style='excited'" in calm and "styledegree='1.0'" in calm
    assert "styledegree='1.8'" in big and "rate='+12%'" in big and "xml:lang='en-US'" in big
    assert "&lt;b&gt;" in speech.ssml("<b>", [], voice="en-US-GuyNeural")   # text is escaped, never markup
    assert "express-as" not in speech.ssml("x", [], voice="en-US-GuyNeural", style="none")


@pytest.fixture()
def api(monkeypatch, tmp_path, match):
    monkeypatch.setenv("HIGHLIGHTS_DIR", str(tmp_path / "jobs"))
    for k in ("AZURE_SPEECH_REGION", "AZURE_SPEECH_KEY", "AZURE_SPEECH_RESOURCE_ID"):
        monkeypatch.delenv(k, raising=False)
    from api.main import app
    return TestClient(app), next(m.id for m in match.moments if m.kind == "goal")


def test_without_azure_the_page_gets_the_line_for_the_browser_voice(api):
    client, goal = api
    body = client.get(f"/api/moments/{goal}/speech").json()
    assert body["speech"] == "browser" and body["text"]
    assert client.get("/api/moments/nope/speech").status_code == 404


def test_with_azure_the_line_is_spoken_once_and_cached(api, monkeypatch):
    client, goal = api
    monkeypatch.setenv("AZURE_SPEECH_REGION", "northcentralus")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "test-key")
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req)
        return io.BytesIO(b"ID3-fake-mp3")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    first = client.get(f"/api/moments/{goal}/speech")
    second = client.get(f"/api/moments/{goal}/speech")
    assert first.headers["content-type"] == "audio/mpeg" and first.content == b"ID3-fake-mp3" == second.content
    assert len(calls) == 1   # the second play comes from the cache
    req = calls[0]
    assert req.full_url == "https://northcentralus.tts.speech.microsoft.com/cognitiveservices/v1"
    assert req.headers["Ocp-apim-subscription-key"] == "test-key" and b"express-as" in req.data


def test_a_failed_azure_call_still_lets_the_page_speak(api, monkeypatch):
    client, goal = api
    monkeypatch.setenv("AZURE_SPEECH_REGION", "northcentralus")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "test-key")

    def refused(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", refused)
    body = client.get(f"/api/moments/{goal}/speech").json()
    assert body["speech"] == "browser" and "HTTPError" in body["note"]

from fastapi.testclient import TestClient


def test_match_and_moment_endpoints():
    from api.main import app
    client = TestClient(app)
    r = client.get("/api/match")
    assert r.status_code == 200
    body = r.json()
    assert body["synthetic"] is True and body["source"] == "synthetic" and body["moments"]
    series = body["pressure"]   # the timeline chart: one value per minute, home positive, away negative
    assert series["step"] == 1 and len(series["values"]) >= body["total_minutes"] and all(-1 <= v <= 1 for v in series["values"])
    mid = body["moments"][0]["id"]
    d = client.get(f"/api/moments/{mid}")
    assert d.status_code == 200
    assert "text" in d.json() and d.json()["text"]["caption"]
    assert "appetites" not in body and "options" not in d.json()   # the reward/risk appetite model is gone from the page
    assert client.get("/api/moments/nope").status_code == 404


def test_ordinary_matches_carry_no_video():
    from api.main import app
    client = TestClient(app)
    assert all(m["video"] is None for m in client.get("/api/match").json()["moments"])


def test_shot_moments_include_a_replay_track():
    from api.main import app
    client = TestClient(app)
    moments = client.get("/api/match").json()["moments"]
    shot = next(m for m in moments if m["kind"] in ("goal", "chance"))
    d = client.get(f"/api/moments/{shot['id']}").json()
    assert d["track"]["t"] and "ball" in d["track"]["objects"]
    assert any(oid != "ball" for oid in d["track"]["objects"])
    other = next((m for m in moments if m["kind"] not in ("goal", "chance")), None)
    if other:
        assert client.get(f"/api/moments/{other['id']}").json()["track"] is None


def test_live_event_channel_is_unchanged_and_tracking_is_separate():
    from api.main import app
    client = TestClient(app)
    with client.websocket_connect("/api/live?channel=events&speed=100000") as ws:
        event = ws.receive_json()
    assert event["type"] and "player_id" in event
    with client.websocket_connect("/api/live?channel=tracking&speed=100000&hz=12") as ws:
        frame = ws.receive_json()
    assert frame["kind"] == "tracking"
    assert frame["players"]
    assert "defensive_line" in frame and "counter_risk" in frame
    assert frame.get("ball") is None or {"x", "y"} <= set(frame["ball"])


def test_a_goal_with_an_assist_is_described_by_its_pass():
    from api.main import app
    client = TestClient(app)
    moments = client.get("/api/match").json()["moments"]
    detail = [client.get(f"/api/moments/{m['id']}").json() for m in moments if m["kind"] in ("goal", "chance")]
    with_assist = next(d for d in detail if d["assist"])
    assert with_assist["available"] is True
    assert with_assist["assist"]["verdict"]["label"] and with_assist["assist"]["sentence"]
    text = with_assist["text"]
    assert with_assist["assist"]["pass"]["passer"] in text["caption"]
    assert with_assist["assist"]["sentence"] in text["audio_description"]
    if with_assist["moment"]["kind"] == "goal":
        assert text["easy_read"].startswith("Goal for ") and "%" not in text["easy_read"]
    without = next((d for d in detail if not d["assist"]), None)
    other = next((m for m in moments if m["kind"] not in ("goal", "chance")), None)
    for m in ([without["moment"]] if without else []) + ([other] if other else []):
        d = client.get(f"/api/moments/{m['id']}").json()
        assert d["available"] is False and d["assist"] is None and d["text"]["caption"]


def test_ai_sentence_is_only_worded_on_request(monkeypatch):
    """The moment carries the template; the model is asked only through /ai-sentence."""
    import api.main as api_main
    client = TestClient(api_main.app)
    moment = next(m for m in client.get("/api/match").json()["moments"] if client.get(f"/api/moments/{m['id']}").json()["assist"])
    got = client.get(f"/api/moments/{moment['id']}").json()["assist"]
    assert got["source"] == "template"
    monkeypatch.setattr(api_main, "cached_verdict_sentence", lambda mid, payload: ("A bold ball.", "azure-openai"))
    ai = client.get(f"/api/moments/{moment['id']}/ai-sentence").json()
    assert ai == {"sentence": "A bold ball.", "source": "azure-openai", "ai": True}
    monkeypatch.setattr(api_main, "cached_verdict_sentence", lambda mid, payload: ("T.", "template"))
    assert client.get(f"/api/moments/{moment['id']}/ai-sentence").json()["ai"] is False
    assert client.get("/api/moments/nope/ai-sentence").status_code == 404

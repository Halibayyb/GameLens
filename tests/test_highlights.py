"""Track → pitch events, plus the demo job. No video, no network, no GPU."""
import time

from highlights.demo import demo_frames, demo_meta
from highlights.events import ball_speed, closest_player, derive_events, frames_to_rows, in_box, moving_toward_goal, pressing, toward_goal
from highlights.pipeline import process_job
from highlights.storage import save_upload
from highlights.store import create_job, get_job


def _player(pid, team, x, y):
    return {"player_id": pid, "team": team, "x": x, "y": y, "name": pid, "role": "OUT"}


def _frame(t, ball, players, possession="home", period=1):
    return {
        "kind": "tracking",
        "match_id": "T",
        "frame": int(t * 5),
        "period": period,
        "match_seconds": t,
        "clock": f"00:{int(t):02d}",
        "ball": ball,
        "ball_in_play": True,
        "possession": possession,
        "players": players,
    }


def test_closest_player_and_pressing():
    carrier = _player("H09", "home", 80, 34)
    frame = _frame(1, {"x": 80, "y": 34}, [
        carrier,
        _player("A03", "away", 82, 34),
        _player("A04", "away", 78, 36),
        _player("A05", "away", 81, 30),
        _player("H01", "home", 8, 34),
    ])
    assert closest_player(frame)["player_id"] == "H09"
    assert pressing(frame, carrier) is True
    alone = _frame(1, {"x": 80, "y": 34}, [carrier, _player("A03", "away", 82, 34)])
    assert pressing(alone, carrier) is False


def test_toward_goal_uses_attacking_third():
    assert toward_goal({"x": 80, "y": 34}, "home", 1) is True
    assert toward_goal({"x": 40, "y": 34}, "home", 1) is False
    assert toward_goal({"x": 20, "y": 34}, "away", 1) is True
    assert toward_goal({"x": 20, "y": 34}, "home", 2) is True
    assert moving_toward_goal({"x": 80, "y": 34}, {"x": 90, "y": 34}, "home", 1) is True
    assert moving_toward_goal({"x": 80, "y": 34}, {"x": 70, "y": 34}, "home", 1) is False
    assert in_box({"x": 90, "y": 34}, "home", 1) is True
    assert in_box({"x": 80, "y": 34}, "home", 1) is False


def test_ball_speed():
    a = _frame(0.0, {"x": 70, "y": 34}, [])
    b = _frame(0.2, {"x": 76, "y": 34}, [])
    assert abs(ball_speed(a, b) - 30.0) < 1e-6


def test_derive_events_pass_shot_goal_and_possession():
    frames = [
        _frame(0.0, {"x": 70, "y": 34}, [_player("H10", "home", 70, 34), _player("H09", "home", 88, 34),
                                         _player("A04", "away", 92, 30)], "home"),
        _frame(0.4, {"x": 76, "y": 34}, [_player("H10", "home", 70, 34), _player("H09", "home", 88, 34),
                                         _player("A04", "away", 92, 30)], "home"),
        _frame(0.8, {"x": 88, "y": 34}, [_player("H10", "home", 70, 34), _player("H09", "home", 88, 34),
                                         _player("A04", "away", 92, 30)], "home"),
        _frame(1.2, {"x": 90, "y": 34}, [_player("H10", "home", 68, 34), _player("H09", "home", 90, 34),
                                         _player("A04", "away", 92, 30)], "home"),
        _frame(1.4, {"x": 103, "y": 34}, [_player("H10", "home", 68, 34), _player("H09", "home", 90, 34),
                                          _player("A04", "away", 94, 30)], "home"),
        _frame(2.0, {"x": 52, "y": 34}, [_player("H10", "home", 60, 34), _player("A04", "away", 52, 34)], "away"),
    ]
    events = derive_events(frames, teams={"home": "Home", "away": "Away"}, match_id="T")
    types = [e["type"] for e in events]
    assert types.count("pass") == 1
    assert types.count("shot") == 1
    assert types.count("goal") == 1
    assert "possession" in types
    assert types[-1] == "period_end"
    shot = next(e for e in events if e["type"] == "shot")
    assert shot["outcome"] == "goal" and shot["team"] == "home" and shot["xg"] > 0
    pas = next(e for e in events if e["type"] == "pass")
    assert pas["outcome"] == "complete" and pas["receiver_id"] == "H09" and pas["length_m"] >= 6
    rows = frames_to_rows(frames)
    assert {r["object_id"] for r in rows} >= {"ball", "H10", "H09"}
    assert all("x" in r and "y" in r for r in rows)


def test_demo_frames_are_live_tracking_schema():
    frames = demo_frames()
    assert len(frames) == 61
    first = frames[0]
    assert first["kind"] == "tracking"
    assert first["ball"] and first["players"]
    assert "defensive_line" in first and "counter_risk" in first
    assert {p["team"] for p in first["players"]} == {"home", "away"}
    events = derive_events(frames, teams={"home": "Home", "away": "Away"})
    assert {e["type"] for e in events} >= {"pass", "shot", "goal", "pressing"}


def test_demo_pipeline_writes_analysis(tmp_path, monkeypatch):
    monkeypatch.setenv("HIGHLIGHTS_DIR", str(tmp_path))
    job = create_job("demo", filename="demo")
    result = process_job(job["job_id"])
    assert result["video_kind"] == "schematic" and result["synthetic"] is True
    assert result["has_video"] is False
    assert result["frames"][0]["kind"] == "tracking"
    assert any(e["type"] == "goal" for e in result["events"])
    assert result["analyses"]
    analysis = result["analyses"][0]
    assert analysis["available"] is True
    assert analysis["text"]["provider"] == "templates"
    assert result["explanations"]
    assert any(c["kind"] == "assist" for c in result["explanations"])
    assert (tmp_path / job["job_id"] / "result.json").exists()
    assert get_job(job["job_id"])["status"] == "done"
    # meta for the decision engine
    assert demo_meta()["teams"]["home"]["name"] == "Home"


def test_upload_without_vision_does_not_fake_tracks(tmp_path, monkeypatch):
    monkeypatch.setenv("HIGHLIGHTS_DIR", str(tmp_path))
    job = create_job("upload", filename="clip.mp4")
    save_upload(job["job_id"], b"not-a-real-clip", "clip.mp4")
    try:
        process_job(job["job_id"])
        finished = get_job(job["job_id"])
        assert finished["status"] == "error"
    except Exception:
        finished = get_job(job["job_id"])
        assert finished["status"] == "error"
    assert finished["error"]
    err = finished["error"].lower()
    assert any(word in err for word in ("demo", "vision", "yolo", "frame", "cv2", "module", "torch", "ultralytics"))
    # Must not have written a schematic result over a real upload
    from highlights.store import load_result
    assert load_result(job["job_id"]) is None


def test_highlights_api_demo_and_event_grid_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("HIGHLIGHTS_DIR", str(tmp_path))
    from fastapi.testclient import TestClient
    from api.main import app
    client = TestClient(app)
    created = client.post("/api/highlights/jobs", data={"source": "demo"})
    assert created.status_code == 200
    job_id = created.json()["job_id"]
    body = None
    for _ in range(80):
        body = client.get(f"/api/highlights/jobs/{job_id}").json()
        if body["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert body and body["status"] == "done", body
    result = client.get(f"/api/highlights/jobs/{job_id}/result").json()
    assert result["frames"][0]["kind"] == "tracking"
    assert result["explanations"]
    assist = next(c for c in result["explanations"] if c["kind"] == "assist")
    exp = client.get(f"/api/highlights/jobs/{job_id}/explain", params={"moment_id": assist["id"], "depth": "analyst"})
    assert exp.status_code == 200
    assert exp.json()["explanation"]["label"] == "AI-generated from event data"
    assert client.get(f"/api/highlights/jobs/{job_id}/video").status_code == 404
    hook = client.post("/api/highlights/hooks/blob", json=[{
        "eventType": "Microsoft.EventGrid.SubscriptionValidationEvent",
        "data": {"validationCode": "abc"},
    }])
    assert hook.status_code == 200
    assert hook.json()["validationResponse"] == "abc"


def test_derive_events_when_the_clip_ends_without_a_ball():
    frames = demo_frames()[:20]
    frames[-1] = {**frames[-1], "ball": None}   # YOLO lost the ball on the last frame
    events = derive_events(frames, teams={"home": "Home", "away": "Away"}, match_id="T")
    end = events[-1]
    assert end["type"] == "period_end" and (end["x"], end["y"]) == (52.5, 34.0)


def test_freeze_frame_is_the_tracked_frame_at_the_pass():
    from highlights.pipeline import _freeze
    frames = [{"match_seconds": t, "ball": {"x": 1, "y": 2, "xyxy": [5, 5, 7, 7]} if t == 0.4 else None,
               "players": [{"player_id": "A1", "team": "away", "role": "OUT", "xyxy": [1, 2, 3, 4]},
                           {"player_id": "H2", "team": "home", "role": "OUT", "xyxy": None}]} for t in (0.2, 0.4, 0.6)]
    fz = _freeze(frames, {"match_seconds": 0.45, "player_id": "A9", "receiver_id": "A3", "receiver_at_pass": "A1"},
                 [960, 540])
    assert fz["t"] == 0.4 and fz["receiver_id"] == "A1" and fz["ball"] == [5, 5, 7, 7]
    assert [b["player_id"] for b in fz["boxes"]] == ["A1"]           # no box, no overlay
    assert _freeze(frames, {"match_seconds": 0.4, "player_id": "A9", "receiver_id": "A3"}, None) is None

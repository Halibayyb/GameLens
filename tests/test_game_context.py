"""Context builder and grounded explanations. No Azure, no network."""
from engine.game_context import (
    build_context, defenders_bypassed, passing_lanes, pick_key_moments,
    principles_from, team_shape, voronoi_control, xt,
)
from engine.game_explain import build_explanations, template_explain
from highlights.demo import TEAMS, demo_frames
from highlights.events import derive_events
from highlights.pipeline import process_job
from highlights.store import create_job


def _frames():
    return demo_frames()


def _events(frames=None):
    frames = frames or _frames()
    return derive_events(frames, teams=TEAMS, match_id="T"), frames


def test_voronoi_shares_sum_to_one():
    players = [
        {"player_id": "H1", "team": "home", "x": 20, "y": 20},
        {"player_id": "H2", "team": "home", "x": 30, "y": 50},
        {"player_id": "A1", "team": "away", "x": 80, "y": 34},
    ]
    share = voronoi_control(players)
    assert abs(share["home"] + share["away"] - 1.0) < 1e-9
    assert share["home"] > 0.4  # two home players on the left
    assert share["cells"] == 21 * 14


def test_team_shape_compactness_and_width():
    players = [
        {"team": "home", "x": 40, "y": 20, "role": "OUT"},
        {"team": "home", "x": 50, "y": 40, "role": "OUT"},
        {"team": "home", "x": 8, "y": 34, "role": "GK"},
    ]
    shape = team_shape(players, "home", 1)
    assert shape["n"] == 3
    assert shape["width_m"] == 20.0
    assert shape["length_m"] == 10.0
    assert 8 < shape["compactness_m"] < 16
    assert shape["line"] == 40


def test_xt_and_lanes_use_engine_models():
    assert xt(90, 34, True) > xt(40, 34, True)
    frame = {
        "period": 1,
        "ball": {"x": 70, "y": 34},
        "players": [
            {"player_id": "H10", "team": "home", "x": 70, "y": 34, "name": "Vos", "role": "OUT"},
            {"player_id": "H09", "team": "home", "x": 88, "y": 38, "name": "Bennet", "role": "OUT"},
            {"player_id": "A04", "team": "away", "x": 80, "y": 34, "name": "Fiel", "role": "OUT"},
        ],
    }
    carrier = frame["players"][0]
    lanes = passing_lanes(frame, carrier, 1)
    assert lanes
    assert all(0.05 <= row["success"] <= 0.97 for row in lanes)
    assert lanes[0]["receiver"] == "Bennet"
    assert defenders_bypassed((70, 34), (88, 38), [(80, 34)], True) == 1


def test_pick_moments_finds_build_up_assist_shot_goal():
    events, frames = _events()
    picks = pick_key_moments(events, frames)
    kinds = {p["kind"] for p in picks}
    assert {"build_up", "assist", "shot", "goal"} <= kinds
    assist = next(p for p in picks if p["kind"] == "assist")
    assert assist["player_name"]
    assert assist["match_seconds"] < next(p["match_seconds"] for p in picks if p["kind"] == "shot")


def test_context_has_required_fields_and_no_quotes():
    events, frames = _events()
    assist = next(p for p in pick_key_moments(events, frames) if p["kind"] == "assist")
    ctx = build_context(frames, events, TEAMS, assist["match_seconds"],
                        event=assist["event"], kind="assist", synthetic=True)
    assert ctx["carrier"]["name"] == assist["event"]["player_name"]
    if ctx["lanes"]:
        assert ctx["lanes"][0]["receiver"] != ctx["carrier"]["name"]
    for key in ("score", "game_state", "shapes", "pitch_control", "pressing",
                "lanes", "chain", "momentum", "attribution", "principles"):
        assert key in ctx
    assert ctx["synthetic"] is True
    assert ctx["pitch_control"]["home"] + ctx["pitch_control"]["away"] == 1
    text = template_explain(ctx, "analyst")
    assert text["provider"] == "templates"
    assert "AI-generated from event data" in text["label"]
    blob = __import__("json").dumps(ctx)
    for name in (p["name"] for p in text["key_players"]):
        assert name in blob
    if ctx["event"].get("length_m") is not None:
        assert str(ctx["event"]["length_m"]) in text["what_happened"] or str(ctx["event"]["length_m"]) in text["why"]
    for principle in text["principles"]:
        assert principle in (ctx["principles"] or []) or principle in principles_from(ctx)
    assert "said" not in text["what_happened"].lower()


def test_pipeline_batches_explanations(tmp_path, monkeypatch):
    monkeypatch.setenv("HIGHLIGHTS_DIR", str(tmp_path))
    job = create_job("demo", filename="demo")
    result = process_job(job["job_id"])
    cards = result["explanations"]
    assert cards
    kinds = {c["kind"] for c in cards}
    assert "assist" in kinds and "goal" in kinds
    assist = next(c for c in cards if c["kind"] == "assist")
    assert assist["by_depth"]["fan"]["headline"]
    assert assist["by_depth"]["analyst"]["provider"] == "templates"
    assert assist["by_depth"]["coach"]["coaches_takeaway"]
    assert assist["label"] == "AI-generated from event data"
    assert "tracking" in assist["context"]["attribution"].lower() or "event" in assist["context"]["attribution"].lower()


def test_build_explanations_helper_matches_picker():
    events, frames = _events()
    cards = build_explanations(frames, events, TEAMS, synthetic=True)
    assert [c["kind"] for c in cards] == [p["kind"] for p in pick_key_moments(events, frames)]

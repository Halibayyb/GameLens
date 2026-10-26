"""Vision events on the true proto tracking: the adapter alone, with no detector error."""
import json

import pytest

from engine.assist import build_assist
from engine.moments import Match
from vision.events import detect_events, frames_from_tracking_csv
from vision.pipeline import write_tracked

GOALS = [(1, "H08", "H11"), (2, "A07", "A09"), (3, "H17", "H20")]   # (goal, passer, receiver) in match_proto
BEFORE_S, AFTER_S = 10.0, 4.0   # true tracking window around the killer pass


@pytest.fixture(scope="module")
def proto(proto_dir):
    match = Match(proto_dir)
    passes = [m.assist_pass["match_seconds"] for m in match.moments if m.kind == "goal"]
    return proto_dir, json.loads((proto_dir / "match_meta.json").read_text()), passes


def _frames(proto, goal):
    folder, meta, passes = proto
    t = passes[goal - 1]
    return frames_from_tracking_csv(folder / "tracking.csv", t - BEFORE_S, t + AFTER_S, meta)


@pytest.mark.parametrize("goal,passer,receiver", GOALS)
def test_assist_pass_shot_and_goal_found(proto, goal, passer, receiver):
    t = proto[2][goal - 1]
    found = detect_events(_frames(proto, goal), fps=5)
    passes = [e for e in found["events"] if e["type"] == "pass"]
    shot = next(e for e in found["events"] if e["type"] == "shot")
    assist = passes[-1]
    assert (assist["player_id"], assist["receiver_id"]) == (passer, receiver)
    assert abs(assist["match_seconds"] - t) <= 0.4
    assert shot["player_id"] == receiver and shot["outcome"] == "goal"
    assert any(e["type"] == "goal" for e in found["events"])
    # all three goals are scored at the x = 0 end except the away goal at x = 105
    assert found["attack_right"][assist["team"]] == (assist["team"] == "away")


def test_ball_flying_past_a_defender_is_not_a_touch(proto):
    found = detect_events(_frames(proto, 1), fps=5)
    assert "H09" not in [pid for _, pid in found["debug"]["touches"]]   # 0.9 m from the ball in flight


def test_detected_events_reproduce_the_ground_pass_verdict(proto, tmp_path):
    folder = proto[0]
    truth_match = Match(folder)
    truth = build_assist(truth_match, next(m for m in truth_match.moments if m.kind == "goal"))
    frames = _frames(proto, 1)
    found = detect_events(frames, 5)
    # goal 1 is a through ball into space; the detector can't tell that yet, so the flag is supplied by hand
    next(e for e in reversed(found["events"]) if e["type"] == "pass")["into_space"] = True
    write_tracked(tmp_path, frames, 5, events=found)
    clip = Match(tmp_path)
    got = build_assist(clip, next(m for m in clip.moments if m.kind == "goal"))
    assert got["verdict"] == truth["verdict"]
    expected = {o["player_id"]: o for o in truth["options"]}
    for o in got["options"]:
        # the played pass is scored at its end, which the detector takes from the receiver's first touched frame
        # (here 1.7 m from the proto's target), so it gets a looser bound than the alternatives
        tol = 0.05 if o["played"] else 0.01
        assert o["arrives"] == pytest.approx(expected[o["player_id"]]["arrives"], abs=tol)
        assert o["goal"] == pytest.approx(expected[o["player_id"]]["goal"], abs=tol)
        assert o["cut_out"] == expected[o["player_id"]]["cut_out"]


@pytest.mark.parametrize("goal,passer,receiver", GOALS)
def test_scene_hints_close_the_lofted_and_into_space_gaps(proto, tmp_path, goal, passer, receiver):
    """A ground track can't see a lofted pass or one played into space; the scene model's hint can.

    The hint carries the proto pass's own flags, and with it the detected pass gets the true verdict."""
    folder, _, passes = proto
    truth_match = Match(folder)
    moment = [m for m in truth_match.moments if m.kind == "goal"][goal - 1]
    truth = build_assist(truth_match, moment)
    flags = {"aerial": bool(moment.assist_pass.get("aerial")), "into_space": bool(moment.assist_pass.get("into_space"))}
    frames = _frames(proto, goal)
    hints = {"passes": [{"receiver_id": receiver, "t": passes[goal - 1], **flags, "p_header": None}]}
    found = detect_events(frames, 5, hints=hints)
    assist = [e for e in found["events"] if e["type"] == "pass"][-1]
    assert assist["hinted"] and {k: assist[k] for k in flags} == flags
    write_tracked(tmp_path, frames, 5, events=found)
    clip = Match(tmp_path)
    got = build_assist(clip, next(m for m in clip.moments if m.kind == "goal"))
    assert got["verdict"] == truth["verdict"]

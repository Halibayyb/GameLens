from engine import models
from engine.decisions import APPETITE, analyse
from engine.explain import explain


def test_xg_is_higher_close_to_goal():
    assert models.xg(100, 34, True) > models.xg(80, 34, True) > models.xg(60, 10, True)
    assert abs(models.xg(100, 34, True) - models.xg(5, 34, False)) < 1e-9  # mirrored for the other team


def test_pass_success_drops_with_defender_in_lane():
    clear = models.pass_success((50, 34), (70, 34), [])
    blocked = models.pass_success((50, 34), (70, 34), [(60, 34.5)])
    assert blocked < clear


def test_moments_are_found_and_continuous(match):
    assert match.moments, "expected at least one key moment"
    elapsed = [m.elapsed for m in match.moments]
    assert elapsed == sorted(elapsed)
    segs = match.score_segments()
    assert segs[0]["from"] == 0 and segs[-1]["score"].count("-") == 1


def test_scores_follow_reward_minus_weighted_risk(match):
    shot = next((m for m in match.moments if m.kind in ("goal", "chance")), None)
    if shot is None:
        return
    for name, w in APPETITE.items():
        a = analyse(match, shot.id, name)
        assert a["available"]
        for o in a["options"]:
            assert abs(o["score"] - round(o["reward"] - w * o["risk"], 3)) < 1e-6
        assert sum(o["best"] for o in a["options"]) == 1
        assert any(o["high_risk"] for o in a["options"])


def test_non_shot_moments_explain_without_options(match):
    other = next((m for m in match.moments if m.kind not in ("goal", "chance")), None)
    if other is None:
        return
    a = analyse(match, other.id)
    assert a["available"] is False and a["options"] == []
    text = explain(a)
    assert text["provider"] == "templates" and text["caption"]


def test_goal_and_chance_moments_carry_a_replay_track(match):
    shots = [m for m in match.moments if m.kind in ("goal", "chance")]
    assert shots, "expected at least one shot moment"
    for m in shots:
        tr = m.track
        assert tr and "ball" in tr["objects"]
        n = len(tr["t"])
        assert n >= 2 and 0.0 in tr["t"] and tr["t"] == sorted(tr["t"])
        assert all(len(o["xy"]) == n for o in tr["objects"].values())
        i = tr["t"].index(0.0)   # the replay's tau = 0 frame is the scored frame
        for oid, o in m.frame.items():
            assert o["x"] == tr["objects"][oid]["xy"][i][0] and o["y"] == tr["objects"][oid]["xy"][i][1]
    assert all(not m.track for m in match.moments if m.kind not in ("goal", "chance"))


def test_summary_flags_important_moments(match):
    from engine.moments import HIGH_CHANCE_XG
    for m in match.moments:
        s = m.summary()
        assert isinstance(s["important"], bool)
        assert s["xg"] is None or s["xg"] == round(m.event["xg"], 3)
        if m.kind == "goal":
            assert s["important"]
        if m.kind in ("sub",) or m.title == "Yellow card":
            assert not s["important"]
        if m.kind == "chance":
            assert s["important"] == (m.event["xg"] >= HIGH_CHANCE_XG)

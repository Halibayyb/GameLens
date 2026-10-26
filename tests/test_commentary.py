"""Commentary lines: the situation from the data, the momentum, the pass, and no result given away."""
import re

import pytest

from engine.commentary import commentary, momentum, situation

TEAMS = {"home": "Home", "away": "Away"}


def goal(mid, minute, team, score, player="Wes Marby", xg=0.2):
    return {"id": mid, "kind": "goal", "title": "Goal", "minute": f"{minute}'", "elapsed": minute, "team": team,
            "team_name": TEAMS[team], "player": player, "score": score, "xg": xg}


LABELS = {"best": "Best option on the pitch", "brave": "The brave pass", "line": "Line-breaker", "smart": "Smart pass"}


def assist(passer="Goran Pelldane", key="line", cut=5, aerial=False, into_space=False, arrives=None):
    """arrives=None is an analysis without the odds (the older commentary wording); a number adds the verdict label and odds."""
    played = {"cut_out": cut} if arrives is None else {"cut_out": cut, "arrives": arrives}
    return {"pass": {"passer": passer, "aerial": aerial, "into_space": into_space},
            "verdict": {"key": key, **({"label": LABELS[key]} if arrives is not None else {})}, "payload": {"played": played}}


@pytest.mark.parametrize("score,team,before,swing", [
    ("1-0", "home", [], "opener"),
    ("1-1", "away", [goal("g1", 10, "home", "1-0")], "equaliser"),
    ("2-1", "home", [goal("g1", 10, "home", "1-0"), goal("g2", 20, "away", "1-1")], "go_ahead"),
    ("2-1", "home", [goal("g1", 10, "away", "0-1"), goal("g2", 20, "home", "1-1")], "comeback"),
    ("2-0", "home", [goal("g1", 10, "home", "1-0")], "extends"),
    ("1-2", "home", [goal("g1", 10, "away", "0-1"), goal("g2", 20, "away", "0-2")], "pulls_back"),
])
def test_what_the_goal_does_to_the_score(score, team, before, swing):
    assert situation(goal("g9", 60, team, score), before, None)["swing"] == swing


def test_momentum_with_against_and_swing():
    on_top_home = {"values": [0.6] * 90}
    assert momentum(on_top_home, 70, "home") == "with" and momentum(on_top_home, 70, "away") == "against"
    turning = {"values": [-0.6] * 66 + [0.7] * 24}   # away on top, then home take over just before 70'
    assert momentum(turning, 70, "home") == "swing"
    assert momentum({"values": [0.0] * 90}, 70, "home") is None and momentum(None, 70, "home") is None


def test_a_late_go_ahead_goal_asks_rather_than_tells_the_result():
    before = [goal("g1", 10, "home", "1-0"), goal("g2", 82, "away", "1-1")]
    out = commentary(goal("e22", 87, "home", "2-1", player="Jonah Wylworth"), assist("Soren Keststead", "smart", 2),
                     before, None, TEAMS)
    assert "late" in out["tags"] and "go_ahead" in out["tags"]
    assert not re.search(r"\b(wins|won|winner)\b(?![^.!]*\?)", out["line"], re.I)   # never "the winner" as a fact


def test_the_line_uses_only_the_datas_names_and_numbers():
    out = commentary(goal("e11", 71, "home", "1-0"), assist(), [], {"values": [0.6] * 90}, TEAMS)
    line = out["line"]
    assert "Goran Pelldane" in line and "Wes Marby" in line and "5 defenders" in line
    assert set(re.findall(r"\d+", line)) <= {"5"}
    assert out["tags"] == ["opener", "momentum_with"]


def test_the_voice_keeps_the_easy_explanation_from_the_analytics():
    out = commentary(goal("e11", 71, "home", "1-0"), assist(arrives=0.33), [], {"values": [0.6] * 90}, TEAMS)["line"]
    assert "Line-breaker from Goran Pelldane" in out and "only one in three get through" in out and "five defenders cut out" in out
    assert not re.search(r"\d", out)                  # numbers are words, so the voice reads them cleanly
    assert out == "Line-breaker from Goran Pelldane: only one in three get through, five defenders cut out."   # straight to the facts
    easy = commentary(goal("e12", 40, "home", "1-0"), assist(key="best", cut=1, arrives=0.66), [], None, TEAMS)["line"]
    assert easy.startswith("Best option on the pitch from") and "two in three get through" in easy and "one defender cut out" in easy
    safe = commentary(goal("e13", 40, "home", "1-0"), assist(key="smart", cut=0, arrives=0.97), [], None, TEAMS)["line"]
    assert "almost always gets there" in safe and "defender" not in safe


def test_pass_flavours_and_a_goal_from_nothing():
    lofted = commentary(goal("a", 30, "home", "1-0"), assist(aerial=True, into_space=True), [], None, TEAMS)["line"]
    assert "over the top" in lofted and "runs onto it" in lofted
    solo = commentary(goal("b", 30, "home", "1-0", xg=0.03), None, [], None, TEAMS)["line"]
    assert "Wes Marby" in solo and "Out of almost nothing!" in solo


def test_the_same_moment_always_reads_the_same_and_other_moments_have_a_voice():
    one = commentary(goal("e17", 82, "away", "1-1", player="Oskar Elvdane"), assist("Bram Torstead", "best", 1, aerial=True),
                     [goal("g1", 70, "home", "1-0")], None, TEAMS)
    two = commentary(goal("e17", 82, "away", "1-1", player="Oskar Elvdane"), assist("Bram Torstead", "best", 1, aerial=True),
                     [goal("g1", 70, "home", "1-0")], None, TEAMS)
    assert one == two and one["line"].startswith(("Late drama", "Away rescue"))
    saved = {"id": "s1", "kind": "chance", "title": "Shot saved", "minute": "13'", "elapsed": 13, "team": "home",
             "team_name": TEAMS["home"], "player": "Wes Marby", "score": "0-0", "xg": 0.3}
    assert "Wes Marby" in commentary(saved, None, [], None, TEAMS)["line"]


def test_the_moment_api_serves_the_commentary(match):
    from fastapi.testclient import TestClient
    from api.main import app
    goal_id = next(m.id for m in match.moments if m.kind == "goal")
    body = TestClient(app).get(f"/api/moments/{goal_id}").json()
    assert body["text"]["commentary"] == body["commentary"]["line"] and body["commentary"]["line"].endswith(("!", "."))


def test_an_analysed_goal_has_three_captions_from_the_server():
    a = assist(arrives=0.3183)
    a["options"] = [{"played": False, "arrives": 0.79, "goal": 0.22}, {"played": True, "arrives": 0.3183, "goal": 0.0586}]
    out = commentary(goal("e11", 71, "home", "1-0"), a, [], None, TEAMS)
    assert [c["part"] for c in out["captions"]] == ["line", "smart", "finish"] and out["captions"][0]["text"] == out["line"]
    assert out["captions"][1]["text"] == "32% arrived, 5.9% score." and out["captions"][2]["text"] == "Incredible vision!"
    assert [c["part"] for c in commentary(goal("b", 30, "home", "1-0"), None, [], None, TEAMS)["captions"]] == ["line"]

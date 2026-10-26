import pytest
from fastapi.testclient import TestClient

from engine import assist as A
from engine.explain import VERDICT_MAX_WORDS, valid_sentence, verdict_sentence
from engine.moments import Match

# Hand-built freeze frames in proto units (120 x 80, attacking x = 120).
PASSER = (50.0, 40.0)


def opp(x, y, gk=False):
    return {"loc": (x, y), "gk": gk}


def mate(pid, x, y):
    return {"id": pid, "loc": (x, y)}


def verdict_of(mates, opponents, played, passer=PASSER):
    opts = A.score_options(passer, mates, opponents, played)
    return A.pick_verdict(opts)["key"], opts


GK = opp(119, 40, gk=True)


def test_verdict_best():
    key, _ = verdict_of([mate("R", 100, 40), mate("B", 60, 20)], [GK], "R")
    assert key == "best"


def test_verdict_brave():
    # R has the biggest goal chance but a defender on the lane makes the safe ball worth more
    key, opts = verdict_of([mate("R", 114, 40), mate("S", 100, 30)], [GK, opp(80, 40)], "R")
    assert key == "brave"
    r, s = opts
    assert r["goal"] > s["goal"] and r["ev"] < s["ev"]


def test_verdict_line_breaker():
    # R cuts out the two defenders (as does B), but B is better on value and goal chance
    key, _ = verdict_of([mate("R", 70, 40), mate("B", 100, 40)], [GK, opp(60, 10), opp(62, 70)], "R")
    assert key == "line"


def test_verdict_smart():
    # the defender is 8 units off B's pass, so it is involved (within 12) and B cuts it out
    key, opts = verdict_of([mate("R", 60, 40), mate("B", 100, 40)], [GK, opp(75, 32)], "R")
    assert key == "smart"
    assert opts[0]["cut_out"] < opts[1]["cut_out"] and opts[0]["ev"] < opts[1]["ev"] and opts[0]["goal"] < opts[1]["goal"]


def test_ties_go_to_the_played_pass():
    mates = lambda played: [mate("U", 90, 30), mate("D", 90, 50)]
    for played in ("U", "D"):
        key, opts = verdict_of(mates(played), [GK], played)
        assert opts[0]["ev"] == pytest.approx(opts[1]["ev"]) and key == "best"


def test_clamps():
    far = A.chance_arrives((0, 40), (110, 40), [opp(50, 40), opp(30, 40)])
    near = A.chance_arrives((50, 40), (50.5, 40), [opp(200, 200)])
    assert far == 0.05 and near == pytest.approx(0.98 - 0.012 * 0.5)
    assert A.chance_arrives((0, 0), (0, 0), [opp(100, 100)]) == 0.98
    assert A.chance_goal((119, 40), [GK]) == 0.7
    assert A.chance_goal((5, 5), [GK] + [opp(60, 30)] * 3) == 0.01


def test_lofted_pass_skips_the_lane_but_pays_a_penalty():
    lane = [opp(60, 40)]
    ground = A.chance_arrives((50, 40), (70, 40), lane)
    lofted = A.chance_arrives((50, 40), (70, 40), lane, lofted=True)
    assert lofted == pytest.approx(ground + 0.25 - 0.10)


def test_blockers_change_the_goal_chance_smoothly():
    spot = (105, 40)
    # at x = 112 the receiver-post-post triangle spans y 38.13 .. 41.87, so y 37.4 is about 0.7 units outside it
    inside, edge, outside = opp(112, 40), opp(112, 37.4), opp(112, 20)
    # no blocker: x keeperOnly; one full blocker: x perBlocker; half way out of the triangle: in between
    assert A.blocker_factor(A.blocker_weight(spot, [outside])) == pytest.approx(1.6)
    assert A.blocker_factor(A.blocker_weight(spot, [inside])) == pytest.approx(0.8)
    assert 0.8 < A.blocker_factor(A.blocker_weight(spot, [edge])) < 1.6
    assert A.blocker_factor(1 - 1e-9) == pytest.approx(A.blocker_factor(1.0))   # no cliff at one blocker
    assert A.blocker_weight(spot, [opp(112, 40, gk=True)]) == 0   # the keeper never blocks


def test_reception_points_meet_the_ball():
    # a ball of 20 units/s: a teammate standing still is scored where they stand, a runner where they will be
    # (the proto's 4-step fixed point, so close to the exact meeting point rather than on it)
    mates = [mate("R", 70, 40), mate("S", 60, 30), mate("M", 60, 50)]
    run = {"S": (0, 0), "M": (5, 0)}
    at = lambda pid, secs: (next(t["loc"] for t in mates if t["id"] == pid)[0] + run[pid][0] * secs,
                            next(t["loc"] for t in mates if t["id"] == pid)[1] + run[pid][1] * secs)
    pts = A.reception_points(PASSER, PASSER, (90, 40), 2.0, mates, "R", at)
    assert pts["R"] == (90, 40) and pts["S"] == (60, 30)
    arrival = A.dist(PASSER, pts["M"]) / 20
    assert pts["M"][0] == pytest.approx(60 + 5 * arrival, abs=0.01)


def test_goal_chance_is_the_better_of_shot_xg_and_xt_and_xt_is_off_for_now():
    # docs/how-it-works.md: goalChance = max(shotXG, xT); EV = arrives x goalChance. No trained xT yet, so xT is 0.
    assert A.XT_GRID is None and A.xt_value((100, 40)) == 0.0
    _, opts = verdict_of([mate("R", 100, 40), mate("B", 60, 20)], [GK], "R")
    for o in opts:
        assert o["xt"] == 0.0 and o["goal"] == o["shot_xg"] and o["ev"] == pytest.approx(o["arrives"] * o["goal"])
    assert A.goal_chance(0.01, 0.04) == 0.04 and A.goal_chance(0.30, 0.12) == 0.30   # the PDF's far-from-goal and near-goal cases


def test_xt_grid_lookup_and_its_effect_on_the_verdict(monkeypatch):
    # a 16 x 12 grid over 120 x 80: a far receiver gets a build-up value that shot xG alone would give it as ~0
    grid = [[0.0] * A.XT_COLS for _ in range(A.XT_ROWS)]
    grid[A.XT_ROWS // 2][4] = 0.12                     # the cell around x 30-37.5, y 40-46.7
    monkeypatch.setattr(A, "XT_GRID", grid)
    assert A.xt_value((33, 42)) == 0.12 and A.xt_value((100, 5)) == 0.0
    assert A.xt_value((-5, 200)) == 0.0                # clamped to the edge cells
    _, opts = verdict_of([mate("R", 33, 42), mate("B", 100, 40)], [GK], "R")
    far = next(o for o in opts if o["id"] == "R")
    assert far["xt"] == 0.12 and far["goal"] == max(far["shot_xg"], 0.12) == 0.12


def test_easy_read_names_the_score_before_the_goal_and_the_pass():
    for verdict in ("best_option", "brave_pass", "line_breaker", "smart_pass"):
        payload = {"verdict": verdict, "passer": "Jack", "played": {"arrives": 0.4, "cut_out": 2}}
        text = A.easy_read(payload, "Home", "Jack", "Home 1, Away 1")
        assert text.startswith("Goal for Home, Home 1, Away 1. Jack's pass: ")
        assert "%" not in text


def test_mirrored_attack_left_scores_the_same():
    right = A.to_proto(80, 20, True), A.to_proto(95, 30, True)
    left = A.to_proto(105 - 80, 68 - 20, False), A.to_proto(105 - 95, 68 - 30, False)
    assert [c for p in left for c in p] == pytest.approx([c for p in right for c in p])
    mk = lambda pts: A.score_options(pts[0], [mate("R", *pts[1])], [GK], "R")
    assert mk(left)[0]["ev"] == pytest.approx(mk(right)[0]["ev"])


def test_payload_carries_the_template_line():
    _, opts = verdict_of([mate("R", 100, 40), mate("B", 60, 20)], [GK], "R")
    verdict = A.pick_verdict(opts)
    payload = A.build_verdict_payload(opts, verdict, {"R": "Rita", "B": "Bo"}, "Pat")
    assert payload["verdict"] == "best_option" and payload["played"]["label"] == "Pass to Rita"
    assert payload["safe"]["label"] == "Safe pass to Bo" and payload["receiver"] == "Rita" and payload["passer"] == "Pat"
    assert payload["line"] == A.verdict_template(payload) and payload["line"].endswith("best ball on the pitch.")


def test_odds():
    assert [A.odds(p) for p in (0.25, 0.34, 0.4, 0.5, 0.81, 0.682, 0.598)] == ["1/4", "1/3", "2/5", "1/2", "4/5", "2/3", "3/5"]


def test_verdict_line_your_three_goals():
    assert A.verdict_line("line_breaker", 0.318, 5, "Jack") == "2/3 never make it. This one sliced through 5 defenders."
    assert A.verdict_line("brave_pass", 0.435, 1, "Kyle") == "3/5 get picked off. Kyle went for it anyway, and it landed."
    assert A.verdict_line("best_option", 0.402, 4, "Nick") == "Only 2/5 get through. Still the best ball on the pitch."


def test_verdict_line_other_cases():
    assert A.verdict_line("line_breaker", 0.3, 1) == "7/10 never make it. This one sliced through 1 defender."
    assert A.verdict_line("best_option", 0.786, 0) == "4/5 get through. And the best ball on the pitch."
    assert A.verdict_line("brave_pass", 0.6, 0, "Kyle") == "3/5 get through. Kyle went for it, and it landed."
    assert A.verdict_line("brave_pass", 0.4, 0).endswith("The passer went for it anyway, and it landed.")
    assert A.verdict_line("smart_pass", 0.3, 0) == "7/10 never make it. This one found the scorer."
    assert A.verdict_line("smart_pass", 0.03, 0) == "A long shot. This one found the scorer."
    assert A.verdict_line("best_option", 0.97, 0) == "Almost always gets there. And the best ball on the pitch."


# --- LLM guard ------------------------------------------------------------

PAYLOAD = {"verdict": "best_option", "line": "2/3 never make it. This one sliced through 2 defenders.",
           "played": {"label": "Pass to A", "arrives": 0.34, "goal": 0.12, "value": 0.041, "cut_out": 2},
           "next_best": {"label": "Pass to B", "arrives": 0.5, "goal": 0.05, "value": 0.025, "cut_out": 0},
           "safe": {"label": "Safe pass to C", "arrives": 0.9, "goal": 0.02, "value": 0.018, "cut_out": 1},
           "value_ratio_vs_next_best": 1.64}


def test_guard_accepts_payload_numbers_and_percent_forms():
    assert valid_sentence("A bold pass with a 34% chance to arrive.", PAYLOAD)
    assert valid_sentence("It took 2 defenders out of the game.", PAYLOAD)
    assert valid_sentence("Worth 1.64 times the next best.", PAYLOAD)
    assert valid_sentence("2/3 never make it. This one got through.", PAYLOAD)   # "one" here is not a number
    assert valid_sentence("Two in three never make it.", PAYLOAD)                 # words from the template line
    assert valid_sentence("No numbers at all here, just a lovely ball.", PAYLOAD)


def test_guard_rejects_invented_numbers_and_long_replies():
    assert not valid_sentence("A 77% chance of arriving.", PAYLOAD)
    assert not valid_sentence("It beat 7 defenders.", PAYLOAD)
    assert not valid_sentence("It beat seven defenders.", PAYLOAD) and not valid_sentence("Only one in nine get through.", PAYLOAD)
    assert not valid_sentence(" ".join(["word"] * (VERDICT_MAX_WORDS + 1)), PAYLOAD)
    assert valid_sentence(" ".join(["word"] * VERDICT_MAX_WORDS), PAYLOAD)
    assert not valid_sentence("", PAYLOAD)


def test_sentence_falls_back_to_template_without_config(monkeypatch):
    for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"):
        monkeypatch.delenv(k, raising=False)
    assert verdict_sentence(PAYLOAD) == (A.verdict_template(PAYLOAD), "template")


def test_sentence_uses_a_valid_llm_reply_and_rejects_a_bad_one(monkeypatch):
    import engine.explain as ex
    monkeypatch.setattr(ex, "_verdict_azure", lambda p: "What a ball, a 34% chance made it brilliant.")
    assert verdict_sentence(PAYLOAD)[1] == "azure-openai"
    monkeypatch.setattr(ex, "_verdict_azure", lambda p: "It beat 9 defenders.")
    assert verdict_sentence(PAYLOAD)[1] == "template"


# --- real moments ----------------------------------------------------------

@pytest.fixture(scope="module")
def analysed(match: Match):
    return [(m, A.build_assist(match, m)) for m in match.moments if m.kind in ("goal", "chance")]


def test_assist_found_for_some_moments_only_for_shooters_team(analysed):
    for m, a in analysed:
        if m.assist_pass:
            p = m.assist_pass
            assert p["receiver_id"] == m.event["player_id"] and p["team"] == m.event["team"]
            assert 0 < m.event["match_seconds"] - p["match_seconds"] <= 10
        else:
            assert a is None


def test_assist_options_and_sentence(analysed):
    for m, a in analysed:
        if not a:
            continue
        assert sum(o["played"] for o in a["options"]) == 1
        assert a["pass"]["receiver_id"] == next(o["player_id"] for o in a["options"] if o["played"])
        assert [o["ev"] for o in a["options"]] == sorted((o["ev"] for o in a["options"]), reverse=True)
        s = A.verdict_template(a["payload"])
        assert len(s.split()) <= 20
        assert valid_sentence(s, a["payload"])


def test_track_covers_the_pass_and_the_shot(analysed):
    for m, a in analysed:
        if a and m.track:
            fps, t = m.track["fps"], m.track["t"]
            assert t[0] <= a["pass"]["tau"] + 1 / fps and t[-1] >= 0 and 0 in t
            assert t[0] >= -12 and a["pass"]["tau"] < 0


# --- API -------------------------------------------------------------------

def test_api_roles_numbers_and_assist():
    from api.main import app
    client = TestClient(app)
    body = client.get("/api/match").json()
    assert body["roles"]["H01"] == "GK" and isinstance(body["numbers"]["H01"], int)
    assert body["names"].keys() == body["roles"].keys() and all(body["names"].values())
    for mom in body["moments"]:
        d = client.get(f"/api/moments/{mom['id']}").json()
        assert "assist" in d
        a = d["assist"]
        if mom["kind"] not in ("goal", "chance"):
            assert a is None
        elif a:
            assert a["source"] in ("azure-openai", "template") and a["sentence"]
            assert a["verdict"]["label"] and set(a["payload"]) >= {"verdict", "played", "next_best", "safe"}


def test_ball_motion_fields_are_sane_on_a_generated_match(analysed):
    """flight / bend / lift / z are derived for generated matches; the shot aims into the goal mouth."""
    seen = 0
    for m, a in analysed:
        if not a:
            continue
        seen += 1
        p = a["pass"]
        assert 0.3 <= p["flight"] <= 3.0 and p["bend"] == 0 and 0 <= p["lift"] <= 2.5
        assert p["start_z"] == 0 and p["arrive_z"] == 0
        assert p["tau"] <= p["reception_tau"] <= 0 and p["carry"] == pytest.approx(-p["reception_tau"])
        shot = A.build_shot(m)
        assert shot["tau"] == 0 and shot["to"][0] in (0.0, 105.0) and 30.3 <= shot["to"][1] <= 37.7
        assert shot["s"] > 0 and shot["z"] >= 0 and shot["lift"] >= 0
    assert seen

import csv
import json
import math
from pathlib import Path

import pytest

from data_gen import proto_demo
from engine import assist as A
from engine.decisions import _attack_right
from engine.moments import Match

PROTO = json.loads((Path(proto_demo.__file__).parent / "proto_moments.json").read_text())["moments"]
INTENDED = ["line_breaker", "brave_pass", "best_option"]
LABELS = ["Line-breaker", "The brave pass", "Best option on the pitch"]


@pytest.fixture(scope="module")
def demo(proto_dir):
    return Match(proto_dir)


@pytest.fixture(scope="module")
def frames(proto_dir):
    """{frame: {object_id: (x, y)}} for the whole match."""
    out = {}
    with open(proto_dir / "tracking.csv", newline="") as f:
        for row in csv.DictReader(f):
            out.setdefault(int(row["frame"]), {})[row["object_id"]] = (float(row["x"]), float(row["y"]))
    return out


def proto_analysis(m):
    """The proto's own numbers: scoring.js run on the exported moment (proto units, no coordinate round trip)."""
    ff = m["freeze_frame"]
    passer = next(p for p in ff if p["player"] == m["passer"])["location"]
    opponents = [{"loc": p["location"], "gk": p["gk"]} for p in ff if not p["teammate"]]
    mates = [{"id": p["player"], "loc": p["location"]} for p in ff if p["teammate"] and not p["gk"] and p["player"] != m["passer"]]
    targets = None
    if m.get("intoSpace"):
        loc = {p["player"]: p["location"] for p in ff}
        run = {n: proto_demo.normalize_run(m.get("run", {}).get(n)) for n in loc}
        targets = A.reception_points(passer, m["start"], m["end"], m.get("flight", 1.0), mates, m["receiver"],
                                     lambda n, secs: proto_demo.position_at(loc[n], run[n], secs))
    opts = A.score_options(passer, mates, opponents, m["receiver"], lofted=bool(m.get("aerial")), targets=targets)
    return {o["id"]: o for o in opts}, A.pick_verdict(opts)


def goals(match):
    return [x for x in match.moments if x.kind == "goal"]


def fmt_pct(p):
    """scoring.js fmtPct: one decimal below 10%, else a whole percent (Math.round)."""
    return f"{p * 100:.1f}%" if p < 0.1 else f"{math.floor(p * 100 + 0.5)}%"


def test_a_full_match_ending_two_one(demo):
    assert demo.meta["final_score"] == {"home": 2, "away": 1}
    g = goals(demo)
    assert [x.event["team"] for x in g] == ["home", "away", "home"]
    assert [x.event["score"] for x in g] == ["1-0", "1-1", "2-1"]
    assert [x.event["minute"] for x in g] == ["71'", "82'", "87'"]
    assert all(x.assist_pass and x.assist_frame and x.track for x in g)
    assert sum(e["type"] == "goal" for e in demo.events) == 3   # the simulation scores none of its own
    assert len(demo.events) >= 1500
    ends = [e for e in demo.events if e["type"] == "period_end"]
    assert [e["period_name"] for e in ends] == ["half time", "full time"]
    assert not any(e["type"] == "red_card" for e in demo.events)


def test_options_match_the_prototype_exports(demo):
    """Every option row of the proto's "Export frames" frames.txt, at the precision it prints."""
    squad = {q["player_id"]: q for t in demo.meta["teams"].values() for q in t["squad"]}
    for i, x in enumerate(goals(demo)):
        rows = proto_demo.parse_frames_txt((proto_demo.PROTO_FRAMES / f"goal{i + 1}.txt").read_text())
        a = A.build_assist(demo, x)
        shown = [o for o in a["options"] if o["shown"]]   # already ranked by expected value, as the letters are
        assert a["verdict"]["label"] == LABELS[i]
        assert len(shown) == len(rows)
        for row, o in zip(rows, shown):
            assert squad[o["player_id"]]["number"] == row["number"], (i + 1, row["option"])
            assert o["played"] == row["played"]
            assert fmt_pct(o["arrives"]) == fmt_pct(row["arrives"]), (i + 1, row["option"], "arrives")
            assert fmt_pct(o["goal"]) == fmt_pct(row["goal"]), (i + 1, row["option"], "goal")
            assert o["cut_out"] == row["cut_out"], (i + 1, row["option"], "cut out")


def test_verdicts_are_line_brave_best(demo):
    ids = [A.build_assist(demo, x)["payload"]["verdict"] for x in goals(demo)]
    assert ids == INTENDED


def test_options_match_the_proto_numbers(demo):
    for x, pm in zip(goals(demo), PROTO):
        expected, verdict = proto_analysis(pm)
        got = A.build_assist(demo, x)
        assert got["verdict"]["key"] == verdict["key"]
        assert len(got["options"]) == len(expected)
        for o in got["options"]:
            name = next(n for n in expected if proto_demo.player_id(pm, n) == o["player_id"])
            e = expected[name]
            assert o["played"] == e["played"] and o["cut_out"] == e["cut_out"], (pm["minute"], name)
            for k in ("arrives", "goal", "ev"):
                assert o[k] == pytest.approx(e[k], abs=0.005), (pm["minute"], name, k)


def test_freeze_frame_equals_the_proto(demo):
    for x, pm in zip(goals(demo), PROTO):
        right = _attack_right(x.event["team"], x.event["period"])
        assert len(x.assist_frame) == 23   # 22 players + ball
        for p in pm["freeze_frame"]:
            o = x.assist_frame[proto_demo.player_id(pm, p["player"])]
            u, v = A.to_proto(o["x"], o["y"], right)
            assert (u, v) == pytest.approx(tuple(p["location"]), abs=0.01)
        assert x.assist_tau < 0


def test_tracking_covers_the_whole_match(demo, frames):
    keys = sorted(frames)
    assert keys == list(range(keys[0], keys[-1] + 1))   # no gaps
    assert all(len(objs) == 23 for objs in frames.values())   # 22 players + ball, kick-off to full time
    minutes = (keys[-1] - keys[0]) / demo.meta["fps"] / 60
    assert minutes >= 90


def test_no_jumps_where_the_scenes_join_the_play(demo, frames):
    """From 25 s before each killer pass to 12 s after its goal: the blend in, the scene and the hand-back."""
    fps = demo.meta["fps"]
    for x in goals(demo):
        first = round((x.assist_pass["match_seconds"] - 25) * fps)
        last = round((x.event["match_seconds"] + 12) * fps)
        for k in range(first + 1, last + 1):
            for oid, p in frames[k].items():
                if oid != "ball" and oid in frames[k - 1]:
                    speed = math.dist(p, frames[k - 1][oid]) * fps
                    assert speed <= 9.0, (x.event["minute"], k, oid, speed)


def test_line_ups_follow_the_scenes(demo):
    g = goals(demo)
    on_pitch = [{oid for oid in x.assist_frame if oid != "ball"} for x in g]
    assert on_pitch[0] == on_pitch[1]
    home = lambda ids: {i for i in ids if i[0] == "H"}
    assert home(on_pitch[2]) - home(on_pitch[1]) == {"H16", "H17", "H20"}
    assert home(on_pitch[1]) - home(on_pitch[2]) == {"H04", "H07", "H10"}
    subs = [e for e in demo.events if e["type"] == "substitution"]
    home_subs = [e for e in subs if e["team"] == "home"]
    assert {e["player_on_id"] for e in home_subs} == {"H16", "H17", "H20"}
    assert all(g[1].event["match_seconds"] < e["match_seconds"] < g[2].event["match_seconds"] for e in home_subs)
    assert all(e["match_seconds"] > g[2].event["match_seconds"] for e in subs if e["team"] == "away")


def test_players_have_easy_names_numbers_and_positions(demo):
    squads = {t: demo.meta["teams"][t]["squad"] for t in ("home", "away")}
    names = [p["name"] for t in squads.values() for p in t]
    assert len(names) == len(set(names)) == 28
    for team, squad in squads.items():
        assert [p["name"] for p in sorted(squad, key=lambda p: p["number"])] == proto_demo.EASY_NAMES[team][:len(squad)]
        assert all(p["name"].isalpha() and " " not in p["name"] for p in squad)
        assert {p["role"] for p in squad} <= {"GK", "RB", "CB", "LB", "DM", "CM", "AM", "RW", "LW", "RM", "LM", "ST"}
    assert [(p["number"], p["role"], p["name"]) for p in squads["home"][:2]] == [(1, "GK", "John"), (6, "CM", "Tom")]
    by_id = {p["player_id"]: p for t in squads.values() for p in t}
    for e in demo.events:   # away's substitutes play the position of the player they replace (home's have their own)
        if e["type"] == "substitution" and e["team"] == "away":
            assert by_id[e["player_on_id"]]["role"] == by_id[e["player_id"]]["role"]
    assert demo.names["H02"] == "Josh" and by_id["H02"]["role"] == "RB"


def test_goal_moments_link_their_videos(demo):
    """The three goals carry the URL of their clip (the web app shows it if the file is installed); nothing else does."""
    summaries = [m.summary() for m in demo.moments]
    assert [m["video"] for m in summaries if m["kind"] == "goal"] == ["/videos/goal1_full.mp4", "/videos/goal2_full.mp4", "/videos/goal3_full.mp4"]
    assert all(m["video"] is None for m in summaries if m["kind"] != "goal")


def test_sentences_are_short_and_say_what_the_pass_did(demo):
    want = ["2/3 never make it. This one sliced through 5 defenders.",
            "3/5 get picked off. Kyle went for it anyway, and it landed.",
            "Only 2/5 get through. Still the best ball on the pitch."]
    got = [A.verdict_template(A.build_assist(demo, x)["payload"]) for x in goals(demo)]
    assert got == want and all(len(s.split()) <= 20 for s in got)


def test_goal_events_carry_the_pass_flags(demo):
    passes = [x.assist_pass for x in goals(demo)]
    assert [p["aerial"] for p in passes] == [False, True, False]
    assert [p["into_space"] for p in passes] == [True, False, True]
    assert [len(p.get("shown_ids", [])) for p in passes] == [0, 2, 3]   # goal 1 shows the top three
    assert all(p["show_landing"] for p in passes)   # the pitch draws each played pass to where the ball lands
    block = [A.build_assist(demo, x)["pass"] for x in goals(demo)]   # the API block carries the same flags
    assert [b["aerial"] for b in block] == [False, True, False]
    assert [b["into_space"] for b in block] == [True, False, True]
    assert all(b["show_landing"] for b in block)


def test_pass_and_shot_carry_the_balls_choreography(demo):
    """assist.pass and the shot expose what the web replay needs to move the ball; the demo equals the proto values."""
    for x, proto in zip(goals(demo), PROTO):
        a = A.build_assist(demo, x)["pass"]
        assert a["flight"] == proto["flight"] and a["bend"] == proto["bend"] and a["lift"] == proto["lift"]
        assert a["start_z"] == proto.get("startZ", 0) and a["arrive_z"] == proto.get("arriveZ", 0)
        assert a["tau"] <= a["reception_tau"] <= 0 and a["carry"] == pytest.approx(-a["reception_tau"])
        shot = A.build_shot(x)
        right = _attack_right(x.event["team"], x.event["period"])
        assert shot["tau"] == 0 and shot["s"] == proto["shot"].get("s", 0.65)
        assert shot["z"] == proto["shot"].get("z", 0) and shot["lift"] == proto["shot"].get("lift", 0.3)
        assert shot["to"][0] == (105.0 if right else 0.0) and 30.3 < shot["to"][1] < 37.7   # in the goal mouth
        assert shot["from"] == [x.event["x"], x.event["y"]]

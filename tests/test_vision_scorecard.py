"""Scorecard comparison logic on tiny hand-made inputs (no YOLO)."""
import pytest

from scripts.vision_scorecard import assign, compare, match_players, norm, parse_frames_txt, percentile


def test_assign_pairs_nearest_points_one_to_one():
    pairs, d = assign([(0, 0), (10, 0)], [(10.5, 0), (0, 1)])
    assert sorted(pairs) == [(0, 1), (1, 0)]
    assert sorted(d) == pytest.approx([0.5, 1.0])


def test_match_players_handles_attack_direction_and_y_mirror():
    truth = {"H1": {"x": 10, "y": 20}, "H2": {"x": 30, "y": 50}, "ball": {"x": 0, "y": 0}}
    # the clip attacks the other way and is drawn upside down: x -> 105 - x only (y mirrored twice)
    seen = [{"player_id": "p7", "x": 95, "y": 20}, {"player_id": "p8", "x": 75, "y": 50}]
    m = match_players(seen, truth, right=True, seen_right=False)
    assert m["ids"] == {"p7": "H1", "p8": "H2"}
    assert max(m["errors"]) < 1e-9 and m["mirror"]


def _assist(passer, receiver, verdict, options):
    return {"pass": {"passer_id": passer, "receiver_id": receiver}, "verdict": {"key": verdict, "label": verdict},
            "options": [{"player_id": i, "arrives": a, "goal": g, "ev": a * g, "cut_out": c} for i, a, g, c in options]}


def test_compare_maps_ids_and_reports_worst_option_error():
    truth = _assist("H8", "H11", "line", [("H11", 0.34, 0.10, 6), ("H9", 0.79, 0.23, 5)])
    got = _assist("t3", "t5", "line", [("t5", 0.30, 0.10, 6), ("t4", 0.79, 0.25, 4)])
    c = compare(got, truth, {"t3": "H8", "t5": "H11", "t4": "H9"})
    assert c["passer_ok"] and c["receiver_ok"] and c["verdict_ok"]
    assert c["max_err_arrives"] == pytest.approx(0.04)
    assert c["max_err_goal"] == pytest.approx(0.02)
    assert c["cut_mismatches"] == 1 and c["options_matched"] == "2/2"
    assert compare(None, truth) == {"pass_found": False}


def test_parse_frames_txt_and_helpers():
    text = "A  Player 9  arrives 79%  goal 22%  cut out 4\nC  Player 11 (the pass played)  arrives 32%  goal 16%  cut out 5\n"
    rows = parse_frames_txt(text)
    assert [(r["number"], r["played"], r["arrives"], r["cut_out"]) for r in rows] == [(9, False, 0.79, 4), (11, True, 0.32, 5)]
    assert norm(5, 10, False) == (100, 58)
    assert percentile([3, 1, 2], 0.5) == 2 and percentile([], 0.5) is None

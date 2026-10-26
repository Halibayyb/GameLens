"""Live tracking frames: annotation and the browser send-rate cap."""
from feeds.tracking import annotate_frame, iter_paced
from engine.models import counter_risk


def test_annotate_frame_draws_the_defensive_line_and_counter_zone():
    frame = annotate_frame({
        "period": 1,
        "match_seconds": 12.0,
        "ball": {"x": 80.0, "y": 34.0},
        "possession": "home",
        "players": [
            {"player_id": "H01", "team": "home", "x": 5, "y": 34},
            {"player_id": "H02", "team": "home", "x": 40, "y": 20},
            {"player_id": "H09", "team": "home", "x": 78, "y": 34},
            {"player_id": "A01", "team": "away", "x": 100, "y": 34},
            {"player_id": "A05", "team": "away", "x": 70, "y": 30},
        ],
    }, keepers={"H01", "A01"})
    assert frame["kind"] == "tracking"
    assert frame["defensive_line"]["home"] == 40
    assert frame["defensive_line"]["away"] == 70
    assert frame["counter_risk"] == counter_risk(80, 34, True, 1)
    zone = frame["counter_zone"]
    assert zone["team"] == "home" and zone["x0"] == 40 and zone["x1"] == 80


def test_period_two_defensive_line_swaps_ends():
    frame = annotate_frame({
        "period": 2,
        "ball": {"x": 20, "y": 34},
        "possession": "home",
        "players": [
            {"player_id": "H01", "team": "home", "x": 100, "y": 34},
            {"player_id": "H02", "team": "home", "x": 60, "y": 20},
        ],
    }, keepers={"H01"})
    # Period 2 home defends the right-hand goal, so the last outfielder is the greatest x.
    assert frame["defensive_line"]["home"] == 60


def test_pace_caps_the_wall_clock_rate():
    frames = [{"match_seconds": i * 0.1, "players": []} for i in range(300)]
    paced = list(iter_paced(frames, speed=1, max_hz=12))
    assert len(paced) == 300  # 10 Hz is already under the 12 Hz cap
    assert paced[0][1] == 0
    fast = list(iter_paced(frames, speed=100, max_hz=10))
    assert 2 <= len(fast) <= 5
    assert all(sleep >= 0 for _, sleep in fast)

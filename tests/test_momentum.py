import math

import pytest

from engine import momentum as M


def ev(t, team, kind="pass", zone="central, attacking third", **kw):
    return {"type": kind, "team": team, "match_seconds": t * 60.0, "zone": zone, **kw}


def spell(team, start, n=6, kind="pass", zone="central, attacking third"):
    return [ev(start + i * 0.4, team, kind, zone) for i in range(n * 2)]


def test_home_pressure_is_positive_and_away_pressure_negative():
    events = spell("home", 10) + spell("away", 40)
    v = M.pressure(events, 60)["values"]
    assert v[10] > 0.5 and v[40] < -0.5
    assert max(v[:25]) > 0 and min(v[:25]) >= -0.2   # the away spell does not leak into the first half hour


def test_swapping_the_teams_flips_the_sign():
    a = spell("home", 10) + spell("away", 40)
    b = spell("away", 10) + spell("home", 40)
    va, vb = M.pressure(a, 60)["values"], M.pressure(b, 60)["values"]
    assert va == [pytest.approx(-x) for x in vb]


def test_values_are_bounded_and_one_per_minute():
    events = spell("home", 5, 30, "shot", "inside the opposition box")
    out = M.pressure(events, 90.5)
    assert out["step"] == 1 and len(out["values"]) == 92   # minutes 0 .. 91
    assert all(-1.0 <= x <= 1.0 for x in out["values"]) and out["scale"] > 0


def test_no_events_is_a_flat_line():
    out = M.pressure([], 90)
    assert out["values"] == [0.0] * 91 and out["scale"] == 0.0
    assert M.pressure([ev(5, "home", "tackle", "central, midfield")], 90)["values"] == [0.0] * 91   # nothing that counts


def test_weights_follow_the_zone_and_the_event():
    assert M.event_weight(ev(1, "home", zone="inside the opposition box")) == M.W_BOX
    assert M.event_weight(ev(1, "home", zone="left wing, attacking third")) == M.W_THIRD
    assert M.event_weight(ev(1, "home", zone="central, midfield")) == 0.0
    assert M.event_weight(ev(1, "home", "shot", xg=0.25)) == pytest.approx(M.W_SHOT + 0.25 * M.W_SHOT_XG)
    assert M.event_weight(ev(1, "home", "goal")) == M.W_GOAL
    assert M.event_weight(ev(1, "home", "corner", zone="")) == M.W_CORNER
    assert M.event_weight(ev(1, "home", "free_kick", "inside own box")) == 0.0


def test_a_goal_makes_a_visible_peak():
    quiet = [ev(m, "home", zone="central, midfield") for m in range(60)]
    with_goal = quiet + [ev(30, "home", "shot", "inside the opposition box", xg=0.4), ev(30.1, "home", "goal", "inside the opposition box")]
    v = M.pressure(with_goal + [ev(10, "away"), ev(50, "away")], 60)["values"]
    assert v[30] == max(v) and v[30] > 0.9


def test_deterministic():
    events = spell("home", 3) + spell("away", 20)
    assert M.pressure(events, 45) == M.pressure(events, 45)
    assert all(math.isfinite(x) for x in M.pressure(events, 45)["values"])

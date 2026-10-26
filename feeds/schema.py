"""Internal match-event schema.

The decision engine (``engine/moments.py``, ``engine/decisions.py``,
``engine/assist.py``), the live WebSocket and ``stream/replay.py`` all read
events in this shape: one JSON object per line in ``events.jsonl``, plus
``match_meta.json``. Synthetic matches, the prototype demo and every feed
adapter produce these records. Do not add a second event dialect.

Pitch
-----
Metres. ``x`` is in ``[0, 105]`` (length), ``y`` in ``[0, 68]`` (width),
origin at the bottom-left corner. In period 1 the home team attacks towards
``x = 105``; the teams swap ends in period 2. That is the same rule as
``engine.decisions._attack_right`` and ``engine.models.to_frame``, which the
xG and pass models use to recover an attacking frame.

``tracking.csv`` is what the pitch replay reads. The synthetic generator
writes every player and the ball on every frame, and that file is the default
source for the broadcast, analyst and live pitches. The vision
service writes the same columns when it is selected. The browser
never fetches a provider file itself.

``match_meta.json``
-------------------
Required by ``engine.moments.Match``:

- ``match_id`` (str)
- ``synthetic`` (bool)
- ``disclaimer`` (str)
- ``teams.home`` / ``teams.away``: ``name``, ``squad``
- each squad player: ``player_id``, ``name``, ``number``, ``role``
  (``role == "GK"`` marks the keeper; do not rely on the id ending in ``01``)
- ``half_minutes`` (regulation half, usually 45)
- ``added_time_minutes`` (list, index 0 is the first-half addition)
- ``final_score``: ``{"home": int, "away": int}``
- ``fps``: tracking frames per second, or null when there is no ``tracking.csv``

Optional and used by the API when present: ``source``, ``data_label``,
``provider``, ``provider_match_id``.

Event record
------------
Every event has these keys (values may be null when the concept does not
apply, for example ``team`` on a period end):

================  ============================================================
key               meaning
================  ============================================================
match_id          same id as the meta
event_id          1-based position in the file (kick-off first)
period            1, 2, 3 (extra time), 4, 5 (penalty shootout)
clock             referee clock ``MM:SS`` from the nominal kick-off
minute            display minute, ``46'`` or ``45+2'``
match_seconds     seconds from kick-off, continuous across halves (first-half
                  added time is included, so the second half starts later
                  than 45:00 on this axis)
type              see event types below
team              ``home``, ``away``, or null
team_name         display name, or null
player_id         squad id (string), or null
player_name       display name, or null
x, y              pitch metres
score             ``"{home}-{away}"`` *after* this event is applied
synthetic         false for a real feed
================  ============================================================

``match_seconds`` is what ``/api/live`` and ``stream/replay.py`` sleep on.
Equal timestamps are fine; the file order is the order of the match.

Event types the engine and the ticker understand
------------------------------------------------
- ``shot`` — ``outcome`` is ``goal``, ``saved``, ``blocked`` or ``off_target``.
  ``xg`` (provider value when the feed has one, otherwise the engine falls
  back to ``engine.models.xg``), ``shot_type`` (``open_play``, ``penalty``,
  ``free_kick``, ``from_corner``, ``penalty_shootout``), ``body_part``
  (``foot`` or ``head``), ``distance_m``. A ``penalty_shootout`` shot is
  streamed but is not a key moment and does not change ``score``.
- ``goal`` — logged immediately after a scoring ``shot`` (not a shootout).
  ``score`` on this row includes the goal. Moments copy it onto the shot.
- ``pass`` — ``outcome`` ``complete``, ``incomplete``, ``out_of_play``,
  ``offside`` or ``unknown``. A complete pass that creates an assist also has
  ``end_x``, ``end_y``, ``receiver_id``, ``receiver_name``. ``pass_type``
  (``open_play``, ``corner``, ``free_kick``, ``throw_in``, ``goal_kick``,
  ``kick_off``, …), ``length_m``, ``aerial``.
- A ``shot`` may also carry ``video``: the URL (under the web app) of a clip of that goal, which the page shows in the
  video frame. Only the prototype match sets it; the field is optional and free to ignore.
- ``carry`` — ``end_x``, ``end_y``.
- ``tackle``, ``interception``, ``clearance``, ``dribble``, ``duel``, ``foul``.
- ``yellow_card``, ``red_card`` — optional ``reason``.
- ``substitution`` — ``player_on_id``, ``player_on_name`` (the player coming on).
  ``player_*`` is the player going off.
- ``penalty_awarded``, ``corner_awarded``, ``save``, ``kick_off``.
- ``period_end`` — ``period_name`` (``half time``, ``full time``, …).
- ``var_check``, ``var_decision``, ``added_time``, ``free_kick``, ``throw_in``,
  ``goal_kick``, ``ball_out_of_play``, ``commentary`` — produced by the
  synthetic generator; a real feed emits them when the provider has them.

``tracking.csv`` (optional, present on the default synthetic path)
-----------------------------------------------------------------
Same columns as ``data_gen/synthetic_match.py``: ``frame, period,
match_seconds, clock, object_id, team, x, y, ball_possession, ball_in_play``.
``object_id`` is ``ball`` or a ``player_id``. The engine samples the row whose
``match_seconds`` rounds to the event time at ``fps``, and builds the replay
window the broadcast and analyst pitches draw. The synthetic generator writes
every player and the ball at ``fps`` for the whole match, which is what the
pitch uses by default. Without a window of frames the moment UI falls back to
the static pitch map.

Live tracking frame (``/api/live?channel=tracking``)
----------------------------------------------------
One JSON object per frame, joined to events on ``match_id`` and
``match_seconds`` (the same clock as ``events.jsonl``). Pitch metres, same
origin as events. ``feeds.tracking`` builds these from ``tracking.csv`` and
adds the defensive line and counter-risk zone the live pitch draws.

================  ============================================================
key               meaning
================  ============================================================
kind              ``tracking``
match_id          same id as the events
frame             frame index (``round(match_seconds * fps)``)
period            1 or 2
match_seconds     seconds from kick-off, same axis as events
clock             display clock
ball              ``{"x", "y"}`` in metres, or null when the ball is off camera
ball_in_play      bool
possession        ``home``, ``away``, or null
players           list of ``player_id``, ``team``, ``x``, ``y``, plus
                  ``number``, ``name``, ``role`` when the squad has them
defensive_line    ``{"home", "away"}`` x of the last outfielder (keeper excluded)
counter_risk      ``engine.models.counter_risk`` at the ball for the team in
                  possession, or null
counter_zone      rectangle from that team's defensive line to the ball, or null
================  ============================================================
"""
from engine.models import L as PITCH_L
from engine.models import W as PITCH_W

REQUIRED_KEYS = (
    "match_id", "event_id", "period", "clock", "minute", "match_seconds",
    "type", "team", "team_name", "player_id", "player_name", "x", "y",
    "score", "synthetic",
)

# Nominal referee clock. Periods 3 and 4 are extra time; 5 is a shootout.
PERIOD_BASE_MIN = {1: 0, 2: 45, 3: 90, 4: 105, 5: 120}
PERIOD_NOMINAL_MIN = {1: 45, 2: 45, 3: 15, 4: 15, 5: 0}


def format_clock(period: int, timestamp_s: float) -> str:
    """Referee clock ``MM:SS`` (minutes may exceed 59)."""
    base = PERIOD_BASE_MIN.get(period, max(0, period - 1) * 45)
    total = base * 60 + timestamp_s
    return f"{int(total // 60):02d}:{int(total % 60):02d}"


def format_minute(period: int, timestamp_s: float) -> str:
    """Display minute: ``23'`` during the half, ``45+2'`` in added time."""
    base = PERIOD_BASE_MIN.get(period, max(0, period - 1) * 45)
    nominal_min = PERIOD_NOMINAL_MIN.get(period, 45)
    nominal_s = nominal_min * 60
    if nominal_s and timestamp_s >= nominal_s:
        added = int((timestamp_s - nominal_s) // 60) + 1
        return f"{base + nominal_min}+{added}'"
    return f"{int((base * 60 + timestamp_s) // 60) + 1}'"


def zone(x: float, y: float, attacking_right: bool) -> str:
    """Coarse pitch zone in the team's attacking frame. Same bands as the generator."""
    u, v = (x, y) if attacking_right else (PITCH_L - x, PITCH_W - y)
    if u > 88.5 and abs(v - 34) < 20.16:
        return "inside the opposition box"
    if u < 16.5 and abs(v - 34) < 20.16:
        return "inside own box"
    third = "own third" if u < 35 else "midfield" if u < 70 else "attacking third"
    side = "left wing" if v > 45.3 else "right wing" if v < 22.7 else "central"
    return f"{side}, {third}"


def validate_event(event: dict) -> None:
    """Raise ``ValueError`` when an event cannot be replayed by the engine."""
    missing = [key for key in REQUIRED_KEYS if key not in event]
    if missing:
        raise ValueError(f"event missing {missing}")
    if event["type"] == "shot" and "outcome" not in event:
        raise ValueError("shot event needs an outcome")
    if event["type"] == "pass" and event.get("outcome") == "complete":
        if "end_x" not in event or "end_y" not in event:
            raise ValueError("a complete pass needs end_x and end_y")

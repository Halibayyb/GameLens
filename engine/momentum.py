"""Match pressure over time: who was threatening, minute by minute, for the timeline chart.

Pure functions over the event list, so every source (synthetic, proto, vision) gets the same chart. Each event
is worth something to the team that did it, in that team's own attacking frame (an event's ``zone`` is already relative
to the acting team). Weights are per event; they are a first reading of "pressure", kept in one place so they are easy
to tune.
"""
import math

BOX, THIRD = "inside the opposition box", "attacking third"
W_BOX, W_THIRD = 2.5, 1.0       # a pass or carry inside the opposition box / in the attacking third
W_FREE_KICK = 1.5               # a free kick in the attacking third
W_CORNER = 2.0
W_SHOT, W_SHOT_XG = 3.0, 12.0   # a shot: 3 + 12 x xG
W_GOAL = 4.0
SIGMA_MIN = 1.5                 # Gaussian smoothing, about a 5 minute window
SCALE_QUANTILE = 0.95           # the 95th percentile of |home - away| is full height; the rest is clipped to [-1, 1]


def event_weight(e: dict) -> float:
    t, zone = e.get("type"), e.get("zone") or ""
    in_box, attacking = zone == BOX, zone == BOX or THIRD in zone
    if t in ("pass", "carry"):
        return W_BOX if in_box else W_THIRD if attacking else 0.0
    if t == "free_kick":
        return W_FREE_KICK if attacking else 0.0
    if t in ("corner", "corner_awarded"):
        return W_CORNER
    if t == "shot":
        return W_SHOT + W_SHOT_XG * (e.get("xg") or 0.0)
    if t == "goal":
        return W_GOAL
    return 0.0


def _smooth(values: list[float]) -> list[float]:
    radius = max(1, int(math.ceil(3 * SIGMA_MIN)))
    kernel = [math.exp(-0.5 * (d / SIGMA_MIN) ** 2) for d in range(-radius, radius + 1)]
    total = sum(kernel)
    kernel = [k / total for k in kernel]
    n = len(values)
    return [sum(kernel[j] * values[min(n - 1, max(0, i + j - radius))] for j in range(len(kernel))) for i in range(n)]


def pressure(events: list[dict], total_minutes: float) -> dict:
    """{"step": 1, "values": [...], "scale": s}: one value per minute, home pressure minus away pressure.

    Positive = home on top, negative = away on top, 0 = balanced; always within [-1, 1].
    total_minutes: the match length on the same clock as ``match_seconds / 60``."""
    n = max(2, int(math.ceil(total_minutes)) + 1)
    raw = {"home": [0.0] * n, "away": [0.0] * n}
    for e in events:
        team = e.get("team")
        if team in raw and e.get("match_seconds") is not None:
            raw[team][min(n - 1, max(0, int(e["match_seconds"] // 60)))] += event_weight(e)
    diff = [h - a for h, a in zip(_smooth(raw["home"]), _smooth(raw["away"]))]
    ranked = sorted(abs(d) for d in diff)
    scale = ranked[min(n - 1, int(SCALE_QUANTILE * n))]
    if scale < 1e-9:   # nothing happened: a flat line, not a division by zero
        return {"step": 1, "values": [0.0] * n, "scale": 0.0}
    return {"step": 1, "values": [round(max(-1.0, min(1.0, d / scale)), 3) for d in diff], "scale": round(scale, 3)}

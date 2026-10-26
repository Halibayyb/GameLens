"""Assist analysis: was the pass that set up a shot the right one?

A Python port of good-assist-proto/src/scoring.js (commit e2242b1). The proto scores on a 120 x 80 pitch attacking
x = 120 (goal y 36..44), so world points are mirrored to attack right and scaled before scoring, and the proto
coefficients hold unchanged. Every teammate of the passer (own keeper and the passer excluded) is scored where they
would receive the ball: their position when the pass was played, or, for a ball into space, where their own run takes
them by the time a ball of the same pace arrives. The played pass is compared with them.

The formula is the one in docs/how-it-works.md: PASS (``chance_arrives``), goal chance = max(shot xG, xT), EV = arrives x
goal chance, CUT (``defenders_cut_out``), then the verdict rules in order with ties to the played pass. The trained XG AI
and the xT grid need possession data to train on, which is set aside for now: shot xG is the geometry baseline (``chance_goal``) and
xT stays 0 until ``XT_GRID`` is filled, so the numbers do not change.
"""
import math

from .decisions import _attack_right
from .moments import Match, Moment

PITCH_L, PITCH_W = 105.0, 68.0
PW, PH = 120.0, 80.0
GOAL_X, GOAL_Y1, GOAL_Y2 = 120.0, 36.0, 44.0
XG = {"a": -0.2, "angle": 0.5, "dist": 0.095, "perBlocker": 0.8, "keeperOnly": 1.6, "soft": 1.5, "min": 0.01, "max": 0.7}
LOFTED_PENALTY = 0.10       # a cross or lofted ball is harder to deliver and control
CUT_INVOLVED = 12.0         # a defender counts as cut out only within this distance of the pass
XT_GRID = None              # expected threat per cell of a 16 x 12 grid over the 120 x 80 pitch (row = y cell, column = x cell);
                            # None until it is built from real possession data, so xT contributes nothing yet
XT_COLS, XT_ROWS = 16, 12
VERDICTS = {"best": "Best option on the pitch", "brave": "The brave pass",
            "line": "Line-breaker", "smart": "Smart pass"}
VERDICT_IDS = {"best": "best_option", "brave": "brave_pass", "line": "line_breaker", "smart": "smart_pass"}
EPS = 1e-9


def clamp(v, lo, hi):
    return min(hi, max(lo, v))


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def dist_to_segment(p, a, b) -> float:
    """Distance from p to the segment a-b (defenders behind the passer don't count)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    len2 = dx * dx + dy * dy
    t = 0 if len2 == 0 else clamp(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2, 0, 1)
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def goal_angle(spot) -> float:
    a1 = math.atan2(GOAL_Y1 - spot[1], GOAL_X - spot[0])
    a2 = math.atan2(GOAL_Y2 - spot[1], GOAL_X - spot[0])
    return abs(a2 - a1)


def in_triangle(p, a, b, c) -> bool:
    def side(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
    d1, d2, d3 = side(p, a, b), side(p, b, c), side(p, c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def dist_to_triangle(p, a, b, c) -> float:
    """0 inside the triangle abc, otherwise the distance to its nearest edge."""
    if in_triangle(p, a, b, c):
        return 0.0
    return min(dist_to_segment(p, a, b), dist_to_segment(p, b, c), dist_to_segment(p, c, a))


def chance_arrives(src, to, opponents, lofted=False) -> float:
    """Chance the pass arrives (0.05 - 0.98). opponents: [{"loc", "gk"}], goalkeeper included.

    lofted: a cross goes over the lane defenders, so they don't count, but it pays LOFTED_PENALTY."""
    lane = 0 if lofted else sum(dist_to_segment(o["loc"], src, to) < 2.5 for o in opponents)
    nearest = min((dist(o["loc"], to) for o in opponents), default=math.inf)
    return clamp(0.98 - 0.012 * dist(src, to) - 0.25 * lane - 0.15 * (nearest < 3) - LOFTED_PENALTY * lofted,
                 0.05, 0.98)


def base_xg(spot) -> float:
    z = XG["a"] + XG["angle"] * goal_angle(spot) - XG["dist"] * dist(spot, (GOAL_X, 40))
    return 1 / (1 + math.exp(-z))


def blocker_weight(spot, opponents) -> float:
    """Summed partial blockers: each outfield opponent weighs 1 inside the receiver-post-post triangle, fading to 0
    at XG["soft"] units outside it. The keeper is never a blocker (the base xG already assumes one)."""
    p1, p2 = (GOAL_X, GOAL_Y1), (GOAL_X, GOAL_Y2)
    return sum(max(0.0, 1 - dist_to_triangle(o["loc"], spot, p1, p2) / XG["soft"]) for o in opponents if not o["gk"])


def blocker_factor(b) -> float:
    """x keeperOnly with no blocker, x perBlocker per full blocker, continuous in between."""
    return XG["perBlocker"] ** b if b >= 1 else XG["keeperOnly"] ** (1 - b) * XG["perBlocker"] ** b


def chance_goal(spot, opponents) -> float:
    """Chance of a goal from the receiver's spot (0.01 - 0.7)."""
    return clamp(base_xg(spot) * blocker_factor(blocker_weight(spot, opponents)), XG["min"], XG["max"])


def xt_value(spot) -> float:
    """Expected threat of having the ball at spot (proto units): the grid cell's value, 0 while XT_GRID is None."""
    if XT_GRID is None:
        return 0.0
    col = int(clamp(spot[0] / PW * XT_COLS, 0, XT_COLS - 1))
    row = int(clamp(spot[1] / PH * XT_ROWS, 0, XT_ROWS - 1))
    return XT_GRID[row][col]


def goal_chance(shot_xg, xt) -> float:
    """What a reception is worth if it arrives: the better of the shot and the location (the PDF's max rule)."""
    return max(shot_xg, xt)


def defenders_cut_out(src, to, opponents) -> int:
    """Outfield opponents strictly between passer and receiver in x and within CUT_INVOLVED of the pass."""
    return sum(not o["gk"] and src[0] < o["loc"][0] < to[0] and dist_to_segment(o["loc"], src, to) <= CUT_INVOLVED
               for o in opponents)


def score_options(passer, mates, opponents, receiver_id, lofted=False, targets=None) -> list[dict]:
    """Score each teammate {"id", "loc"} (proto units) at targets[id], else at their freeze-frame spot.

    lofted: a cross, lane defenders don't count. targets: reception points for a ball into space (see
    reception_points); "loc" stays the player's position at the pass."""
    out = []
    for t in mates:
        played = t["id"] == receiver_id
        target = (targets or {}).get(t["id"], t["loc"])
        arrives = chance_arrives(passer, target, opponents, lofted)
        shot_xg, xt = chance_goal(target, opponents), xt_value(target)
        goal = goal_chance(shot_xg, xt)
        out.append({"id": t["id"], "loc": t["loc"], "target": target, "played": played, "arrives": arrives,
                    "shot_xg": shot_xg, "xt": xt, "goal": goal, "ev": arrives * goal,
                    "cut_out": defenders_cut_out(passer, target, opponents)})
    return out


def reception_points(passer, start, end, flight, mates, receiver_id, position_at) -> dict:
    """Where each option receives a ball into space (proto units), as receptionPoint in scoring.js.

    The played pass is scored at its end. Every other teammate is scored where their own run takes them by the time
    a ball of the same pace (|start - end| / flight) arrives. position_at(id, seconds after the pass) -> spot."""
    speed = dist(start, end) / (flight or 1)
    out = {}
    for t in mates:
        if t["id"] == receiver_id:
            out[t["id"]] = tuple(end)
            continue
        spot = t["loc"]
        for _ in range(4):
            spot = position_at(t["id"], dist(passer, spot) / speed)
        out[t["id"]] = tuple(spot)
    return out


def pick_verdict(options) -> dict:
    """Numbers decide, in priority order. Ties go to the played pass."""
    played = next(o for o in options if o["played"])
    top = lambda k: max(o[k] for o in options)
    key = "smart"
    if played["ev"] >= top("ev") - EPS:
        key = "best"
    elif played["goal"] >= top("goal") - EPS:
        key = "brave"
    elif played["cut_out"] >= top("cut_out"):
        key = "line"
    return {"key": key, "label": VERDICTS[key]}


SHOWN_DEFAULT = 3


def shown_options(options, shown_ids=None) -> set:
    """The options the prototype draws: the ringed ones (shown_ids) or the top SHOWN_DEFAULT by value, plus the
    played pass. The verdict is still judged over every option."""
    others = sorted((o for o in options if not o["played"]), key=lambda o: -o["ev"])
    picked = [o for o in others if o["id"] in shown_ids] if shown_ids else others[:SHOWN_DEFAULT]
    return {o["id"] for o in picked} | {o["id"] for o in options if o["played"]}


def _facts(o, label) -> dict:
    return {"label": label, "arrives": round(o["arrives"], 2), "goal": round(o["goal"], 2),
            "value": round(o["ev"], 3), "cut_out": o["cut_out"]}


def build_verdict_payload(options, verdict, names, passer=None) -> dict:
    """The facts the verdict sentence may use (names: player id -> name; passer: the passer's name, for the brave pass)."""
    played = next(o for o in options if o["played"])
    others = [o for o in options if not o["played"]]
    nxt = max(others, key=lambda o: o["ev"])
    safe = max(others, key=lambda o: o["arrives"])
    v = VERDICT_IDS[verdict["key"]]
    return {"verdict": v, "verdict_label": verdict["label"],
            "line": verdict_line(v, played["arrives"], played["cut_out"], passer),
            "passer": passer, "receiver": names[played["id"]], "cut_out": played["cut_out"],
            "played": _facts(played, f"Pass to {names[played['id']]}"),
            "next_best": _facts(nxt, f"Pass to {names[nxt['id']]}"),
            "safe": _facts(safe, f"Safe pass to {names[safe['id']]}"),
            "value_ratio_vs_next_best": round(played["ev"] / nxt["ev"], 2)}


def odds(p) -> str:
    """A chance as "N/M": the nearest of halves, thirds, quarters, fifths and tenths (a tie takes the smaller denominator)."""
    n, d = min(((max(1, min(d - 1, round(p * d))), d) for d in (2, 3, 4, 5, 10)), key=lambda f: abs(p - f[0] / f[1]))
    return f"{n}/{d}"


def verdict_line(verdict_id, arrives, cut_out, passer=None) -> str:
    """The short line under the verdict label: how often a pass like this fails (or arrives), then what this one did.

    Under 50% the line is framed from the failing side ("2/3 never make it"); from 50% it says how many get through."""
    p = arrives
    if p < 0.05:
        lead = "A long shot."
    elif p >= 0.95:
        lead = "Almost always gets there."
    elif verdict_id == "best_option":
        lead = f"Only {odds(p)} get through." if p < 0.5 else f"{odds(p)} get through."
    elif p < 0.5:
        lead = f"{odds(1 - p)} get picked off." if verdict_id == "brave_pass" else f"{odds(1 - p)} never make it."
    else:
        lead = f"{odds(p)} get through."
    name = passer or "The passer"
    tail = {"best_option": "Still the best ball on the pitch." if p < 0.5 else "And the best ball on the pitch.",
            "brave_pass": f"{name} went for it anyway, and it landed." if p < 0.5 else f"{name} went for it, and it landed.",
            "line_breaker": f"This one sliced through {cut_out} defender{'' if cut_out == 1 else 's'}.",
            }.get(verdict_id, "This one found the scorer.")
    return f"{lead} {tail}"


def verdict_template(payload) -> str:
    """Template sentence, built only from the payload (the verdict label shows apart)."""
    played = payload["played"]
    return verdict_line(payload["verdict"], played["arrives"], played["cut_out"], payload.get("passer"))


def easy_read(payload, team, passer, score_line) -> str:
    """Goal line for screen readers, no percentages: who scored, the score before the goal, then the pass in the verdict's words.

    score_line: the score before the goal as words, e.g. "Home 1, Away 1" (the match is live)."""
    return f"Goal for {team}, {score_line}. {passer}'s pass: {verdict_template(payload)}"


def to_proto(x, y, attack_right) -> tuple[float, float]:
    """World metres -> proto units, attacking x = 120."""
    u, v = (x, y) if attack_right else (PITCH_L - x, PITCH_W - y)
    return u * PW / PITCH_L, v * PH / PITCH_W


PASS_SPEED_MS = 20.0        # a generated pass travels at about this speed
RECEIVE_M = 1.5             # the ball counts as received when it is this close to the receiver
GOAL_HALF_M = 3.66


def _reception_from_track(m: Moment, p: dict) -> float | None:
    """First tracked time after the pass (and its first fifth of a second) when the ball is within RECEIVE_M of the receiver."""
    t, objs = m.track.get("t"), m.track.get("objects", {})
    ball, rec = objs.get("ball", {}).get("xy"), objs.get(p["receiver_id"], {}).get("xy")
    if not t or not ball or not rec:
        return None
    for k, tau in enumerate(t):
        if tau >= m.assist_tau + 0.2 and ball[k] and rec[k] and dist(ball[k], rec[k]) <= RECEIVE_M:
            return tau
    return None


def pass_motion(m: Moment, p: dict) -> dict:
    """How the ball travels on the pass, for the replay: flight (s), bend / lift / start_z / arrive_z (proto units,
    as in good-assist-proto) and reception_tau (s relative to the shot). The demo match carries the proto's own values
    on the pass event; for any other match they are derived: flight from the tracked ball or distance / 20 m/s."""
    length = dist((p["x"], p["y"]), (p["end_x"], p["end_y"]))
    if "flight" in p:
        flight, bend, lift = p["flight"], p.get("bend", 0), p.get("lift", 0)
        start_z, arrive_z = p.get("start_z", 0), p.get("arrive_z", 0)
    else:
        seen = _reception_from_track(m, p)
        flight = seen - m.assist_tau if seen is not None else length / PASS_SPEED_MS
        bend, start_z, arrive_z = 0, 0, 0
        lift = round(min(2.5, 0.1 * (length - 25)), 2) if length > 25 else 0   # a long ball gets a little air
    flight = round(clamp(flight, 0.3, 3.0), 3)
    out = {"flight": flight, "bend": bend, "lift": lift, "start_z": start_z, "arrive_z": arrive_z,
           "reception_tau": round(min(0.0, m.assist_tau + flight), 3)}
    for key in ("jumps", "slides"):   # who leaves the ground or goes down, seconds after the pass (the demo match only)
        if p.get(key):
            out[key] = p[key]
    return out


def track_position(m: Moment, oid: str, tau: float) -> list | None:
    """Where object oid is tau seconds relative to the moment (world metres), linear between tracked frames."""
    t, xy = m.track.get("t"), m.track.get("objects", {}).get(oid, {}).get("xy")
    if not t or not xy:
        return None
    pts = [(tt, p) for tt, p in zip(t, xy) if p]
    if not pts:
        return None
    if tau <= pts[0][0]:
        return list(pts[0][1])
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        if t0 <= tau <= t1:
            f = (tau - t0) / (t1 - t0) if t1 > t0 else 0.0
            return [p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f]
    return list(pts[-1][1])


def build_shot(m: Moment) -> dict | None:
    """The shot the replay finishes with: from (shooter, world m), to (a point in the goal mouth, world m), tau 0 (the
    moment), s (seconds to the goal line), z (height at the end) and lift (arc), the last two in proto units."""
    e = m.event
    if e.get("type") != "shot":
        return None
    if "target_x" in e:
        to = [e["target_x"], e["target_y"]]
    else:   # the goal centre plus a small, repeatable offset
        to = [PITCH_L if _attack_right(e["team"], e["period"]) else 0.0, round(PITCH_W / 2 + ((e["event_id"] * 37) % 5 - 2) * 0.8, 3)]
        to[1] = clamp(to[1], PITCH_W / 2 - GOAL_HALF_M + 0.5, PITCH_W / 2 + GOAL_HALF_M - 0.5)
    return {"from": [e["x"], e["y"]], "to": to, "tau": 0.0, "s": e.get("shot_s", 0.45),
            "z": e.get("shot_z", 0.3), "lift": e.get("shot_lift", 0.2)}


def assist_from_frame(frame: dict, passer_id: str, receiver_id: str, team: str, attack_right: bool, *,
                      is_gk=lambda oid: False, lofted: bool = False, targets_for=None, names: dict | None = None,
                      roles: dict | None = None, shown_ids=None) -> dict | None:
    """Score every option of one pass from one freeze frame: {"options", "verdict", "payload"}, or None.

    frame: {player_id: {"team", "x", "y"}} in world metres. targets_for(passer, mates, pt) may return where each
    option is scored (an into-space pass); None scores everyone where they stand. Both build_assist (tracked
    moments) and the upload page's verified freeze frame (hand-corrected positions) go through here."""
    if passer_id not in frame or receiver_id not in frame:
        return None
    pt = lambda o: to_proto(o["x"], o["y"], attack_right)
    opponents = [{"loc": pt(o), "gk": is_gk(oid)} for oid, o in frame.items()
                 if o["team"] not in (team, "", "ref") and oid != "ball"]
    mates = [{"id": oid, "loc": pt(o)} for oid, o in frame.items()
             if o["team"] == team and oid != passer_id and not is_gk(oid)]
    if not opponents or not any(t["id"] == receiver_id for t in mates) or len(mates) < 2:
        return None
    passer = pt(frame[passer_id])
    targets = targets_for(passer, mates, pt) if targets_for else None
    scored = score_options(passer, mates, opponents, receiver_id, lofted=lofted, targets=targets)
    verdict = pick_verdict(scored)
    names = {oid: oid for oid in frame} | (names or {})
    roles = roles or {}
    payload = build_verdict_payload(scored, verdict, names, names[passer_id])
    shown = shown_options(scored, shown_ids)
    options = sorted(({"player_id": o["id"], "player": names[o["id"]], "role": roles.get(o["id"], "OUT"),
                       "location": [frame[o["id"]]["x"], frame[o["id"]]["y"]], "played": o["played"],
                       "arrives": round(o["arrives"], 4), "shot_xg": round(o["shot_xg"], 4), "xt": round(o["xt"], 4),
                       "goal": round(o["goal"], 4), "ev": round(o["ev"], 4),
                       "cut_out": o["cut_out"], "shown": o["id"] in shown} for o in scored), key=lambda o: -o["ev"])
    return {"options": options, "verdict": verdict, "payload": payload}


def build_assist(match: Match, m: Moment) -> dict | None:
    """Analyse the pass that set up this shot (everything but the sentence), or None if there is no assist."""
    p, frame = m.assist_pass, m.assist_frame
    if not p or not frame:
        return None
    # vision re-numbers players during a pan: receiver_at_pass is who the receiver was in this freeze frame
    rid = p["receiver_id"] if p["receiver_id"] in frame else p.get("receiver_at_pass")
    if p["player_id"] not in frame or rid not in frame:
        return None
    team = m.event["team"]
    right = p.get("attack_right")   # vision: the pass's own camera play (a montage can hold several matches)
    if right is None:
        right = match.meta.get("attack_right", {}).get(team, _attack_right(team, m.event["period"]))
    squad = {q["player_id"]: q for t in match.meta["teams"].values() for q in t["squad"]}
    motion = pass_motion(m, p)

    def targets_for(passer, mates, pt):
        if not p.get("into_space"):
            return None
        def position_at(oid, secs):
            at = track_position(m, oid, m.assist_tau + secs)
            return pt({"x": at[0], "y": at[1]}) if at else next(t["loc"] for t in mates if t["id"] == oid)
        return reception_points(passer, pt(p), pt({"x": p["end_x"], "y": p["end_y"]}), motion["flight"], mates,
                                rid, position_at)

    scored = assist_from_frame(frame, p["player_id"], rid, team, right,
                               is_gk=lambda oid: squad.get(oid, {}).get("role") == "GK", lofted=bool(p.get("aerial")),
                               targets_for=targets_for, names={**match.names, **{k: v["name"] for k, v in squad.items()}},
                               roles={k: v["role"] for k, v in squad.items()}, shown_ids=p.get("shown_ids"))
    if scored is None:
        return None
    options, verdict, payload = scored["options"], scored["verdict"], scored["payload"]
    tau = m.assist_tau
    return {"pass": {"event_id": p["event_id"], "passer_id": p["player_id"], "passer": p["player_name"],
                     "receiver_id": p["receiver_id"], "receiver": p["receiver_name"],
                     "start": [p["x"], p["y"]], "end": [p["end_x"], p["end_y"]], "tau": tau, **motion,
                     "aerial": bool(p.get("aerial")), "into_space": bool(p.get("into_space")),
                     "show_landing": bool(p.get("show_landing")),
                     "carry": round(-motion["reception_tau"], 3) + 0.0},
            "attack_right": right, "options": options, "verdict": verdict, "payload": payload}

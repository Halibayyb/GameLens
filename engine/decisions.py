"""Decision options for a key moment, scored as reward minus risk.

score = reward - weight * risk, where weight comes from the risk appetite:
    safe = 2.0, balanced = 1.0, bold = 0.5
"""
import math
from dataclasses import asdict, dataclass

from . import models
from .moments import Match, Moment

APPETITE = {"safe": 2.0, "balanced": 1.0, "bold": 0.5}


@dataclass
class Option:
    key: str
    label: str
    action: str            # shoot | pass | recycle | through_ball
    p: float               # chance it works
    value: float           # goal chance if it works (1.0 for a shot)
    reward: float          # expected goals created
    risk: float            # expected goals conceded if it goes wrong
    target: tuple[float, float]
    receiver: str | None = None
    high_risk: bool = False
    chosen: bool = False
    score: float = 0.0


def _attack_right(team: str, period: int) -> bool:
    return (team == "home") == (period == 1)


def _cover_behind(frame: dict, attacking: str, ball_x: float, attack_right: bool) -> int:
    """How many attacking players are well behind the ball (cover if the ball is lost)."""
    bu, _ = models.to_frame(ball_x, 34, attack_right)
    n = 0
    for obj in frame.values():
        if obj["team"] == attacking:
            u, _ = models.to_frame(obj["x"], obj["y"], attack_right)
            if u < bu - 15:
                n += 1
    return n


def options_for(match: Match, m: Moment) -> list[Option]:
    """Build the alternatives open to the player at a shot moment."""
    e = m.event
    team, opp = e["team"], ("away" if e["team"] == "home" else "home")
    right = _attack_right(team, e["period"])
    bx, by = e["x"], e["y"]
    frame = m.frame
    defenders = [(o["x"], o["y"]) for o in frame.values() if o["team"] == opp]
    mates = {oid: o for oid, o in frame.items() if o["team"] == team and oid != e["player_id"]}
    behind = _cover_behind(frame, team, bx, right)
    risk_here = models.counter_risk(bx, by, right, behind)

    opts: list[Option] = []
    p_goal = e.get("xg") or models.xg(bx, by, right)
    opts.append(Option("shoot", "Shoot", "shoot", p_goal, 1.0, p_goal,
                       round((1 - p_goal) * risk_here * 0.3, 3), (105.0 if right else 0.0, 34.0)))

    bu = models.to_frame(bx, by, right)[0]
    keepers = {p["player_id"] for side in match.meta["teams"].values() for p in side["squad"] if p.get("role") == "GK"}
    # Synthetic keepers are also numbered 01. A feed that tags role GK uses the squad.
    outfield = {oid: o for oid, o in mates.items() if oid not in keepers and (keepers or not oid.endswith("01"))}

    def world(u: float, w: float) -> tuple[float, float]:
        return (u, w) if right else (105 - u, 68 - w)

    # Safe pass: the teammate who offers the most after receiving and driving ~6 m towards goal.
    scored = []
    for oid, o in outfield.items():
        tu, tw = models.to_frame(o["x"], o["y"], right)
        if tu < bu - 15 or math.dist((bx, by), (o["x"], o["y"])) > 35:
            continue
        ps = models.pass_success((bx, by), (o["x"], o["y"]), defenders)
        nu, nw = min(tu + 6, 100.0), tw + (34 - tw) * 0.2
        v = models.xg(*world(nu, nw), right) * 0.9
        scored.append((ps * v, oid, o, ps, v))
    scored.sort(key=lambda s: s[0], reverse=True)
    if scored:
        _, oid, o, ps, v = scored[0]
        mid = ((bx + o["x"]) / 2, (by + o["y"]) / 2)
        opts.append(Option("pass", f"Pass to {match.names.get(oid, oid)}", "pass", ps, v, ps * v,
                           round((1 - ps) * models.counter_risk(*mid, right, behind), 3),
                           (o["x"], o["y"]), receiver=oid))

    # High-risk assist: a ball into the box, ahead of the most advanced runner.
    if outfield:
        oid, o = max(outfield.items(), key=lambda kv: models.to_frame(kv[1]["x"], kv[1]["y"], right)[0])
        u, w = models.to_frame(o["x"], o["y"], right)
        bw = models.to_frame(bx, by, right)[1]
        tu, tw = min(max(u + 10, bu + 4), 99.0), w + (34 - w) * 0.6
        if math.hypot(tu - bu, tw - bw) < 8:   # runner is level with the ball: square it across to the far post
            tu, tw = 99.0, max(26.0, min(42.0, 68 - bw))
        tx, ty = world(tu, tw)
        ps2 = models.pass_success((bx, by), (tx, ty), defenders) * 0.55
        v2 = models.xg(tx, ty, right) * 0.95
        opts.append(Option("through", f"High-risk assist to {match.names.get(oid, oid)}", "through_ball",
                           ps2, v2, ps2 * v2, round((1 - ps2) * risk_here * 1.4, 3), (tx, ty),
                           receiver=oid, high_risk=True))

    used = {o.receiver for o in opts}
    back = [(math.dist((bx, by), (o["x"], o["y"])), oid, o) for oid, o in mates.items()
            if oid not in used and models.to_frame(o["x"], o["y"], right)[0] < bu - 5]
    if back:
        _, oid, o = min(back)
        ps = models.pass_success((bx, by), (o["x"], o["y"]), defenders)
        opts.append(Option("recycle", f"Recycle to {match.names.get(oid, oid)}", "recycle", ps, 0.03,
                           ps * 0.03, round((1 - ps) * models.counter_risk(o["x"], o["y"], right, behind), 3),
                           (o["x"], o["y"]), receiver=oid))
    opts[0].chosen = True  # the synthetic player actually shot
    return opts


def analyse(match: Match, moment_id: str, appetite: str = "balanced") -> dict:
    m = match.get(moment_id)
    if m.kind not in ("goal", "chance"):
        return {"moment": m.summary(), "options": [], "available": False,
                "note": "Decision options are calculated for shots in this sample."}
    w = APPETITE.get(appetite, 1.0)
    opts = options_for(match, m)
    for o in opts:
        o.score = round(o.reward - w * o.risk, 3)
    best = max(opts, key=lambda o: o.score)
    chosen = next(o for o in opts if o.chosen)
    gap = round(best.score - chosen.score, 3)
    verdict = ("The chosen option was the best available." if best is chosen or gap < 0.005
               else f"Better option: {best.label} (+{gap:.2f}).")
    return {"moment": m.summary(), "available": True, "appetite": appetite, "weight": w,
            "options": [{**asdict(o), "p": round(o.p, 3), "value": round(o.value, 3),
                         "reward": round(o.reward, 3), "best": o is best} for o in opts],
            "best": best.key, "chosen": chosen.key, "gap": gap, "verdict": verdict}

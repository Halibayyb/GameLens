#!/usr/bin/env python3
"""
proto_demo.py - build match_proto: a full simulated match around the prototype's three hand-tuned goals.

The default match is ``synthetic_match.py`` (``MATCH_SOURCE=synthetic``). Select
this one with ``MATCH_SOURCE=proto``.

Reads data_gen/proto_moments.json (exported from good-assist-proto by scripts/export_proto_moments.mjs) and
writes a standard match folder (match_meta.json, events.jsonl, tracking.csv) in the same format as
synthetic_match.py, so the engine, API and web app read it unchanged.

Home (home) 2-1 Away (away), goals at 71', 82' and 87'. The 90 minutes are played by the
synthetic_match.py simulation with its own goals and red cards switched off. Each prototype scene takes over the
tracking 3 s before its killer pass, after a short blend from the simulated positions to the scene's first frame,
and hands back after the goal for the usual celebration and kick-off. The line-ups follow the scenes: home's #16,
#17 and #20 come on after the equaliser.

The proto pitch is 120 x 80 attacking +x. It maps to the 105 x 68 world by a scale, mirrored (x -> 105 - x,
y -> 68 - y) when the attacking team attacks left in that period. Players and the ball follow a Python port of the
proto's motion model (positionAt, passPoint, easeBall). The ball's height is not in the tracking: the pass and shot events carry flight, bend, lift and z for the web
replay to choreograph the ball. Jumps and slides are not in the tracking: they ride on the pass event (`jumps` / `slides`).

Run:  python data_gen/proto_demo.py --out data/match_proto
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import models  # noqa: E402
from engine.decisions import _attack_right  # noqa: E402

try:
    from .synthetic_match import BASE, CENTRE, TEAM_NAMES, VERSION, Match as SimMatch
except ImportError:   # run as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from synthetic_match import BASE, CENTRE, TEAM_NAMES, VERSION, Match as SimMatch  # noqa: E402

L, W = 105.0, 68.0
PW, PH = 120.0, 80.0
FPS = 5
HALF_MINUTES = 45
LEAD_S, SHOT_S = 0.8, 0.65            # motion.js LEAD_S; main.js SHOT_S
TRACK_BEFORE_S, TRACK_AFTER_S = 3.0, 2.0
PASS_SECOND = 40.0                    # the pass is played this many seconds into the goal's minute
MATCH_ID = "SYN-PROTO"
SEED = 2026                           # the simulation around the scenes
PROTO_MOMENTS = Path(__file__).resolve().parent / "proto_moments.json"
PROTO_FRAMES = Path(__file__).resolve().parent / "proto_frames"   # the proto's "Export frames" frames.txt per goal
ROLE_BY_NUMBER = {1: "GK", 2: "RB", 3: "CB", 4: "CB", 5: "CB", 6: "DM", 7: "RW", 8: "CM", 9: "ST", 10: "AM", 11: "LW"}
# Easy to read and say aloud: a squad gets its names in shirt-number order, home first. Fictional first names only.
EASY_NAMES = {
    "home": ["John", "Josh", "Dean", "Mark", "Sam", "Tom", "Ben", "Jack", "Luke", "Matt", "Paul", "Rob", "Nick", "Joe"],
    "away": ["Adam", "Ryan", "Sean", "Leo", "Max", "Tim", "Kyle", "Ross", "Alex", "Owen", "Neil", "Carl", "Greg", "Ian"],
}
AWAY_BENCH = (12, 14, 15)             # home's bench is the scene-3 players who don't start
BLEND_MIN_S, BLEND_MAX_S = 4.0, 20.0  # the ease from the simulation into a scene
BLEND_SPEED = 5.0                     # m/s on average over the blend (its smooth step peaks at 1.5x that)
STEER_S = 20.0                        # before the blend, the scoring team is helped to the ball
CELEBRATION_S = (40.0, 55.0)          # goal to kick-off, as in synthetic_match.py
CHANGE_GAP_S = 5.0                    # substitutions this long into the celebration, one second apart


# ---------- the proto's motion model (src/motion.js) ----------

def normalize_run(raw) -> dict:
    zero = [0, 0]
    if not raw:
        return {"v0": zero, "v": zero, "delay": 0, "ramp": 1, "then": None, "jump": None, "slide": None}
    if isinstance(raw, list):
        return {"v0": raw, "v": raw, "delay": 0, "ramp": 1, "then": None, "jump": None, "slide": None}
    v = raw.get("v", zero)
    return {"v0": raw.get("v0", v), "v": v, "delay": raw.get("delay", 0), "ramp": raw.get("ramp", 1),
            "then": raw.get("then"), "jump": raw.get("jump"), "slide": raw.get("slide")}


def position_at(loc, run, tau):
    """Where a player stands tau seconds after the pass (negative = before it), proto units."""
    if tau <= 0:
        t, r = max(tau, -LEAD_S), run["ramp"]
        p = -(r * -t + ((1 - r) / (2 * LEAD_S)) * (LEAD_S ** 2 - (t + LEAD_S) ** 2)) + r * (tau - t)
        dx, dy = run["v0"][0] * p, run["v0"][1] * p
    else:
        then = run["then"]
        t2, v2 = (then[0], then[1]) if then else (math.inf, run["v"])
        a = min(tau, run["delay"])
        b = max(0, min(tau, t2) - run["delay"])
        c = max(0, tau - t2)
        k = then[2] if then and len(then) > 2 else 0
        if k:
            c = k * (1 - math.exp(-c / k))
        dx = run["v0"][0] * a + run["v"][0] * b + v2[0] * c
        dy = run["v0"][1] * a + run["v"][1] * b + v2[1] * c
    return [loc[0] + dx, loc[1] + dy]


def ease_out(t):
    return 1 - (1 - t) * (1 - t)


def ease_ball(t):
    return 0.5 * t + 0.5 * ease_out(t)


def ease_driven(t):
    return 1.2 * t - 0.2 * t * t


def pass_point(start, end, bend, lift, u):
    """Ground position of the curved ball at progress u (height is not tracked)."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    ln = math.hypot(dx, dy) or 1
    nx, ny = -dy / ln, dx / ln
    cx, cy = (start[0] + end[0]) / 2 + nx * 2 * bend, (start[1] + end[1]) / 2 + ny * 2 * bend
    a, b, c = (1 - u) ** 2, 2 * (1 - u) * u, u * u
    return [a * start[0] + b * cx + c * end[0], a * start[1] + b * cy + c * end[1]]


def lerp(a, b, t):
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]


def smooth_step(f):
    return f * f * (3 - 2 * f)


def parse_frames_txt(text: str) -> list[dict]:
    """The options table of a proto frames.txt: letter, player number, played?, arrives, goal, cut out."""
    out = []
    for m in re.finditer(r"^([A-Z])\s+Player (\d+)( \(the pass played\))?\s+arrives (\d+)%\s+goal ([\d.]+)%\s+cut out (\d+)",
                         text, re.M):
        out.append({"option": m[1], "number": int(m[2]), "played": bool(m[3]), "arrives": int(m[4]) / 100,
                    "goal": float(m[5]) / 100, "cut_out": int(m[6])})
    return out


# ---------- naming ----------

def prefix_of(moment, attacking: bool) -> str:
    """Team letter (H / A) of the attackers ("Player N") or the defenders ("Defender N", "Goalkeeper")."""
    home_attacks = moment["team"] == "Home"
    return "H" if home_attacks == attacking else "A"


def player_id(moment, name: str) -> str:
    if name == "Goalkeeper":
        return f"{prefix_of(moment, False)}01"
    kind, n = name.split()
    return f"{prefix_of(moment, kind == 'Player')}{int(n):02d}"


def build_squads(moments) -> dict:
    """Every player seen in a freeze frame, per team. Role: first moment's `positions` wins, else by number."""
    seen: dict[str, dict[int, str]] = {"H": {}, "A": {}}
    for m in moments:
        for p in m["freeze_frame"]:
            pid = player_id(m, p["player"])
            num = int(pid[1:])
            role = m.get("positions", {}).get(p["player"]) or ROLE_BY_NUMBER.get(num, "CM")
            seen[pid[0]].setdefault(num, role)
    for num in AWAY_BENCH:   # numbered after the starters, so the earlier names don't change
        seen["A"].setdefault(num, "SUB")
    squads = {}
    for letter, team in (("H", "home"), ("A", "away")):
        nums = sorted(seen[letter])
        if len(nums) > len(EASY_NAMES[team]):
            raise ValueError(f"{team} needs {len(nums)} names, EASY_NAMES has {len(EASY_NAMES[team])}")
        squads[team] = [{"player_id": f"{letter}{num:02d}", "name": EASY_NAMES[team][i], "number": num,
                         "role": seen[letter][num]} for i, num in enumerate(nums)]
    return squads


def lineup(moment, letter: str) -> dict:
    """{player id: spot} of one team in a scene: own attack frame, metres."""
    attacking = prefix_of(moment, True) == letter
    out = {}
    for p in moment["freeze_frame"]:
        pid = player_id(moment, p["player"])
        if pid[0] == letter:
            u, v = p["location"][0] * L / PW, p["location"][1] * W / PH
            out[pid] = (u, v) if attacking else (L - u, W - v)
    return out


def starting_order(moments, letter: str) -> list[str]:
    """The starting XI's ids in synthetic_match.py's 4-3-3 slot order (BASE), each player in the slot nearest
    their average spot in the scenes (relative to the team's centre), so the simulation keeps everyone near where
    the scenes need them. The first scene holds both starting XIs."""
    spots: dict[str, list] = {}
    for m in moments:
        for pid, spot in lineup(m, letter).items():
            spots.setdefault(pid, []).append(spot)
    mean = {pid: np.mean(spots[pid], axis=0) for pid in lineup(moments[0], letter)}
    keeper = f"{letter}01"
    field = sorted(pid for pid in mean if pid != keeper)
    centre_u = np.mean([mean[pid][0] for pid in field])
    base_u = BASE[1:, 0].mean()
    cost = sorted((math.dist((mean[pid][0] - centre_u, mean[pid][1]), (BASE[i, 0] - base_u, BASE[i, 1])), pid, i)
                  for pid in field for i in range(1, 11))
    slot, taken = {}, set()
    for _, pid, i in cost:   # greedy: the closest pairs first
        if pid not in slot and i not in taken:
            slot[pid] = i
            taken.add(i)
    return [keeper] + sorted(field, key=slot.get)


# ---------- coordinates ----------

def to_world(u, v, right):
    x, y = u * L / PW, v * W / PH
    return (x, y) if right else (L - x, W - y)


# ---------- one goal: tracking + events ----------

def video_url(number: int, minute: int) -> str:
    """Where the web app serves the video of goal `number`: the match clip, its analytics and its smart view as one file
    (scripts/merge_goal_videos.py builds it from the three clips in media/goal_clips)."""
    return f"/videos/goal{number}_full.mp4"


def goal_sequence(m, pass_secs: float, ids: dict, video: str | None = None):
    """Frames [(s, {object_id: (x, y)}, ball, possession, in_play)] and the event specs for one goal.

    pass_secs: when the killer pass is played, in the clock the frames and specs use. video: the clip of this goal, if any."""
    team = "home" if m["team"] == "Home" else "away"
    period = 2
    right = _attack_right(team, period)
    wd = lambda p: to_world(p[0], p[1], right)
    ff = {p["player"]: p for p in m["freeze_frame"]}
    run = {n: normalize_run(m.get("run", {}).get(n)) for n in ff}
    at = lambda name, tau: position_at(ff[name]["location"], run[name], tau)
    flight, carry = m.get("flight", 1.0), m.get("carry", 0.0)
    shot_s = m["shot"].get("s", SHOT_S)
    bend, lift = m.get("bend", 0), m.get("lift", 0)
    start, pre = m["start"], m.get("prelude")
    passer, receiver = m["passer"], m["receiver"]
    arrive = at(receiver, flight)
    shot_from = at(receiver, flight + carry)
    shot_to = m["shot"]["to"]
    tau_land = flight + carry + shot_s

    def ball(tau):
        if tau < 0:
            if not pre or tau >= pre["touch"]:
                return list(start) if pre else at(passer, tau)
            if tau < pre["pass"]:
                return at(pre["from"], tau)
            if tau < pre["recv"]:
                return pass_point(at(pre["from"], pre["pass"]), at(passer, pre["recv"]), pre.get("bend", 0), 0.1,
                                  ease_driven((tau - pre["pass"]) / (pre["recv"] - pre["pass"])))
            return lerp(at(passer, pre["recv"]), start, ease_out((tau - pre["recv"]) / (pre["touch"] - pre["recv"])))
        if tau <= flight:
            return pass_point(start, arrive, bend, lift, ease_ball(tau / flight))
        if tau <= flight + carry:
            return at(receiver, tau)
        if tau <= tau_land:
            return pass_point(shot_from, shot_to, 0, m["shot"].get("lift", 0.3), ease_out((tau - flight - carry) / shot_s))
        return list(shot_to)

    frames, n = [], round((TRACK_BEFORE_S + tau_land + TRACK_AFTER_S) * FPS)
    for k in range(n + 1):
        tau = round(k / FPS - TRACK_BEFORE_S, 6)
        objs = {ids[name]: wd(at(name, tau)) for name in ff}
        live = tau <= tau_land
        frames.append((round(pass_secs + tau, 2), objs, wd(ball(tau)), team if live else "", int(live)))

    pid = ids[passer], ids[receiver]
    sx, sy = wd(start)
    ex, ey = wd(m["end"])
    shx, shy = wd(shot_from)
    aerial = bool(m.get("aerial"))
    specs = [
        (pass_secs, "pass", dict(player=pid[0], receiver=pid[1], x=sx, y=sy, outcome="complete", pass_type="open_play",
                                 length_m=round(math.dist((sx, sy), (ex, ey)), 1), end_x=ex, end_y=ey,
                                 aerial=aerial, into_space=bool(m.get("intoSpace")), show_landing=bool(m.get("showLanding")),
                                 # the options the proto draws (its `ringed`); without them, the top three
                                 **({"shown_ids": [ids[n] for n in m["ringed"]]} if m.get("ringed") else {}),
                                 # the proto's choreography of the ball, in proto units (see web/src/motion.js ballAt)
                                 flight=flight, bend=bend, lift=lift,
                                 start_z=m.get("startZ", 0), arrive_z=m.get("arriveZ", 0),
                                 # jumps and slides (the proto's `run.jump` / `run.slide`, seconds after the pass), by object id
                                 **{key: {ids[n]: run[n][field] for n in ff if run[n][field]}
                                    for key, field in (("jumps", "jump"), ("slides", "slide")) if any(run[n][field] for n in ff)})),
        (pass_secs + flight + carry, "shot", dict(player=pid[1], x=shx, y=shy, shot_type="open_play",
                                                  body_part="head" if aerial else "foot",
                                                  xg=round(models.xg(shx, shy, right), 3), outcome="goal",
                                                  distance_m=round(math.hypot((L if right else 0) - shx, 34 - shy), 1),
                                                  target_x=round(wd(shot_to)[0], 3), target_y=round(wd(shot_to)[1], 3),
                                                  shot_s=shot_s, shot_z=m["shot"].get("z", 0),
                                                  shot_lift=m["shot"].get("lift", 0.3),
                                                  **({"video": video} if video else {}))),
        (pass_secs + tau_land, "goal", dict(player=pid[1], x=shx, y=shy)),
    ]
    if pre:   # the ball played in to the passer before the killer pass
        fx, fy = wd(at(pre["from"], pre["pass"]))
        tx, ty = wd(at(passer, pre["recv"]))
        specs.insert(0, (pass_secs + pre["pass"], "pass", dict(
            player=ids[pre["from"]], receiver=pid[0], x=fx, y=fy, outcome="complete", pass_type="open_play",
            length_m=round(math.dist((fx, fy), (tx, ty)), 1), end_x=tx, end_y=ty, aerial=False, into_space=False)))
    return team, frames, specs


# ---------- the full match ----------

class ProtoMatch(SimMatch):
    """synthetic_match.py's 90 minutes with the proto scenes played in at their minutes (all in the second half)."""

    POS_DECIMALS = 3   # so the scenes round-trip to proto units to within 0.01

    def __init__(self, moments):
        if any(m["minute"] <= HALF_MINUTES for m in moments):
            raise ValueError("every proto goal must be in the second half")
        squads = build_squads(moments)
        for letter, team in (("H", "home"), ("A", "away")):
            order = starting_order(moments, letter)
            by_id = {p["player_id"]: p for p in squads[team]}
            squads[team] = [by_id[pid] for pid in order] + [p for p in squads[team] if p["player_id"] not in order]
        super().__init__(SEED, FPS, HALF_MINUTES, team_names=TEAM_NAMES, squads=squads, match_id=MATCH_ID,
                         allow_goals=False, allow_red_cards=False, sub_plan=[])
        self.scenes = [self._scene(m, n) for n, m in enumerate(moments, 1)]
        self.sub_plan = sorted(self._changes(moments))
        for _, team, off, on in self.sub_plan:   # a substitute plays the position of the player they replace
            roles = {p["player_id"]: p for p in self.squads[team]}
            if roles[on]["role"] == "SUB":
                roles[on]["role"] = roles[off]["role"]

    @staticmethod
    def _scene(m, number: int) -> dict:
        ids = {p["player"]: player_id(m, p["player"]) for p in m["freeze_frame"]}
        pass_t = (m["minute"] - 1) * 60 + PASS_SECOND - HALF_MINUTES * 60   # second-half clock, as self.t
        team, frames, specs = goal_sequence(m, pass_t, ids, video=video_url(number, m["minute"]))
        return {"team": team, "frames": frames, "specs": sorted(specs, key=lambda s: s[0]),
                "k0": round(frames[0][0] * FPS), "end": frames[-1][0], "state": "pending"}

    def _changes(self, moments) -> list[tuple]:
        """Substitutions [(t, team, off, on)] that make the line-ups match the scenes: each change in the celebration
        after the scene before the one the new player appears in, paired with the player whose spot is nearest.
        Away's bench comes on for its forwards after the last scene."""
        out = []
        for i in range(1, len(moments)):
            for letter, team in (("H", "home"), ("A", "away")):
                before, now = lineup(moments[i - 1], letter), lineup(moments[i], letter)
                off, on = [p for p in before if p not in now], [p for p in now if p not in before]
                pairs = sorted((math.dist(before[a], now[b]), a, b) for a in off for b in on)
                done = set()
                for _, a, b in pairs:
                    if a not in done and b not in done:
                        done.update((a, b))
                        out.append((self.scenes[i - 1]["end"] + CHANGE_GAP_S + len(out), team, a, b))
        forwards = [self.slot_pid[s] for s in list(self.slots("away"))[8:]]
        t = self.scenes[-1]["end"] + CHANGE_GAP_S
        out += [(t + j, "away", a, f"A{n:02d}") for j, (a, n) in enumerate(zip(forwards, AWAY_BENCH))]
        return out

    # ---------- the scenes ----------
    def scripted(self):
        """Blend into the next scene, then play its frames and events; hand back after the goal."""
        if self.period != 2:
            return False
        k = round(self.t * self.fps)
        sc = next((s for s in self.scenes if s["state"] != "done"), None)
        if sc is None or k < sc["k0"] - BLEND_MAX_S * self.fps:
            return False
        if sc["state"] == "pending":
            if sc["k0"] - k > self._blend_frames(sc):
                return False
            sc.update(state="blend", kb=k, p0=self.pos.copy(), b0=self.ball.copy())
            self.setpiece = None
            self.carrier = self.receiver = self.taker = self.restart_fn = None
        self.poss = sc["team"]
        if k < sc["k0"]:
            f = smooth_step((k - sc["kb"]) / (sc["k0"] - sc["kb"]))
            _, objs, ball, _, _ = sc["frames"][0]
            for s in range(22):
                self.pos[s] = sc["p0"][s] + (np.array(objs[self.slot_pid[s]]) - sc["p0"][s]) * f
            self.ball = sc["b0"] + (np.array(ball) - sc["b0"]) * f
            self.mode = "scripted"
            return True
        j = k - sc["k0"]
        _, objs, ball, _, live = sc["frames"][j]
        for s in range(22):   # the proto's runners carry on into the net: stop them where the simulation would
            self.pos[s] = np.clip(objs[self.slot_pid[s]], [-1, -1], [L + 1, W + 1])
        self.ball = np.array(ball, dtype=float)
        self.mode = "scripted" if live else "dead"
        while sc["specs"] and sc["specs"][0][0] < self.t + self.dt - 1e-6:
            self._log_scene(sc["team"], *sc["specs"].pop(0))
        if j == len(sc["frames"]) - 1:
            sc["state"] = "done"
            conceding = self.opp(sc["team"])
            self.kickoff_shape = True
            self.dead(self.rng.uniform(*CELEBRATION_S), self.kick_off, conceding,
                      taker=self.slots(conceding)[9], spot=CENTRE)
        return True

    def _blend_frames(self, sc) -> int:
        """Frames to ease from here into the scene's first frame, at BLEND_SPEED for whoever is furthest away."""
        objs = sc["frames"][0][1]
        far = max(math.dist(self.pos[s], objs[self.slot_pid[s]]) for s in range(22))
        return math.ceil(min(BLEND_MAX_S, max(BLEND_MIN_S, far / BLEND_SPEED)) * self.fps)

    def _log_scene(self, team, t, etype, kw):
        """Log a scene event at its exact time t (second half), keeping the stats and the score."""
        extras = {k: (round(v, 3) if k in ("end_x", "end_y") else v) for k, v in kw.items()
                  if k not in ("player", "receiver", "x", "y")}
        if "receiver" in kw:
            extras = {"receiver_id": kw["receiver"], "receiver_name": self.names[kw["receiver"]], **extras}
        st = self.stats[team]
        if etype == "pass":
            st["passes"] += 1
            st["passes_completed"] += 1
        elif etype == "shot":
            st["shots"] += 1
            st["shots_on_target"] += 1
            st["xg"] += kw["xg"]
        elif etype == "goal":
            self.score[team] += 1
            st["goals"] += 1
        ev = self.log(etype, team, self.slot_pid.index(kw["player"]), at=(kw["x"], kw["y"]), **extras)
        clock, minute = self.clock_parts(t)
        ev.update(clock=clock, minute=minute, match_seconds=round(self.match_seconds(t), 2),
                  x=round(kw["x"], 3), y=round(kw["y"], 3), zone=self.zone(team, (kw["x"], kw["y"])))

    # ---------- the build-up ----------
    def _steering(self):
        """The next scene, while its scoring team is being helped to the ball."""
        if self.period != 2:
            return None
        k = round(self.t * self.fps)
        sc = next((s for s in self.scenes if s["state"] == "pending"), None)
        return sc if sc and sc["k0"] - (STEER_S + BLEND_MAX_S) * self.fps <= k < sc["k0"] else None

    def decide(self):
        sc = self._steering()
        if sc is None:
            return super().decide()
        c = self.carrier
        if self.team_of(c) == sc["team"]:   # keep the ball: no shots, no duels
            return self.carry(c) if c % 11 and self.rng.random() < 0.2 else self.pass_ball(c)
        o = self.nearest(sc["team"], self.ball)
        if o is not None and math.dist(self.pos[o], self.ball) <= 6:
            self.stats[sc["team"]]["tackles_won"] += 1
            self.log("tackle", sc["team"], o, outcome="won",
                     opponent_id=self.slot_pid[c], opponent_name=self.names[self.slot_pid[c]])
            return self.gain(o, (0.8, 1.8))
        return self.pass_ball(c)

    def meta(self, write_tracking):
        meta = super().meta(write_tracking)
        meta["generator"] = f"proto_demo.py (synthetic_match.py v{VERSION} around the prototype's goals)"
        return meta


def build(out_dir) -> dict:
    """Write the match folder and return its meta. Raises if the score isn't the scenes' score."""
    moments = json.loads(PROTO_MOMENTS.read_text())["moments"]
    meta = ProtoMatch(moments).run(out_dir)
    want = {t: sum(m["team"].lower() == t for m in moments) for t in ("home", "away")}
    if meta["final_score"] != want:
        raise RuntimeError(f"match_proto ended {meta['final_score']}, expected {want}")
    return meta


def main():
    ap = argparse.ArgumentParser(description="Build match_proto, the full match around the prototype's goals.")
    ap.add_argument("--out", default=str(ROOT / "data" / "match_proto"), help="output folder")
    a = ap.parse_args()
    meta = build(a.out)
    h, aw = meta["summary"]["home"], meta["summary"]["away"]
    print(f'{meta["teams"]["home"]["name"]} {meta["final_score"]["home"]} - '
          f'{meta["final_score"]["away"]} {meta["teams"]["away"]["name"]}  (synthetic, from the prototype)')
    for k in ("possession_pct", "shots", "shots_on_target", "xg", "passes", "pass_accuracy_pct",
              "corners", "fouls", "yellow_cards", "substitutions"):
        print(f"  {k:<18} {h[k]:>6}  {aw[k]:>6}")


if __name__ == "__main__":
    main()

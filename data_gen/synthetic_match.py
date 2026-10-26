#!/usr/bin/env python3
"""
synthetic_match.py - generate a SYNTHETIC, football-realistic match dataset.

Outputs (in the --out folder):
  events.jsonl     one JSON event per line: passes, carries, tackles, shots, goals,
                   fouls, cards, corners, throw-ins, VAR checks, substitutions ...
  tracking.csv     x/y of every player on the pitch + the ball, at --fps frames/second
  match_meta.json  data card: fictional teams and squads, settings, summary stats

Everything is simulated. Teams, players and matches are fictional.
No real Premier League (or any other real) match data is used.

Pitch: metres, x = 0..105 (length), y = 0..68 (width), origin bottom-left.
In period 1 the home team attacks towards x = 105; teams swap ends at half time.

Examples:
  python synthetic_match.py --seed 7 --out match_007
  python synthetic_match.py --seed 7 --half-minutes 10 --out quick_test
  python synthetic_match.py --seed 7 --no-tracking --out events_only
"""
import argparse
import csv
import json
import math
import random
from collections import deque
from pathlib import Path

import numpy as np

VERSION = "1.0"
L, W = 105.0, 68.0
CENTRE = (L / 2, W / 2)

ROLES = ["GK", "RB", "RCB", "LCB", "LB", "DM", "RCM", "LCM", "RW", "ST", "LW"]
# 4-3-3 shape in an "attacking towards +x" frame (so the LEFT flank is high y).
BASE = np.array([(5, 34), (24, 8), (20, 25), (20, 43), (24, 60), (33, 34),
                 (40, 22), (40, 46), (56, 9), (60, 34), (56, 59)], dtype=float)
CORNER_RUNNERS = [2, 3, 9, 6, 7]  # RCB, LCB, ST, RCM, LCM attack the box at corners

FIRST = ["Aron", "Bastien", "Caio", "Dario", "Elian", "Femi", "Goran", "Hugo", "Ilyas",
         "Jonah", "Kofi", "Luca", "Mateo", "Niko", "Oskar", "Pavel", "Quinn", "Rafa",
         "Soren", "Tiago", "Umar", "Viktor", "Wes", "Yanis", "Zeno", "Ansel", "Bram",
         "Cyril", "Dorian", "Emre", "Somsak", "Khamla", "Vilay", "Anou"]
LAST_A = ["Ash", "Bel", "Cor", "Dun", "Elv", "Fen", "Gar", "Hal", "Ivor", "Kest", "Lom",
          "Mar", "Nor", "Ost", "Pell", "Quar", "Rov", "Sel", "Tor", "Vann", "Wyl", "Yar"]
LAST_B = ["dane", "ford", "mere", "wick", "ton", "holt", "by", "stead", "more", "croft",
          "ley", "worth"]
TEAM_NAMES = {"home": "Home", "away": "Away"}


def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-z))


def make_squad(rng, team, used, prefix=None):
    squad = []
    numbers = rng.sample(range(2, 40), 15)
    code = prefix or team[0].upper()
    for i in range(16):
        name = None
        while name is None or name in used:
            name = f"{rng.choice(FIRST)} {rng.choice(LAST_A)}{rng.choice(LAST_B)}"
        used.add(name)
        num = 1 if i == 0 else numbers[i - 1]
        squad.append({"player_id": f"{code}{num:02d}", "name": name,
                      "number": num, "role": ROLES[i] if i < 11 else "SUB"})
    return squad


class Match:
    POS_DECIMALS = 2   # tracking.csv positions

    def __init__(self, seed, fps, half_minutes, team_names=None, squads=None,
                 snapshot_goals=False, match_id=None, allow_goals=True, allow_red_cards=True, sub_plan=None):
        # allow_goals / allow_red_cards False: a shot that would score is saved, blocked or missed instead, and a red
        # card is not shown. sub_plan: [(t in the second half (s), team, off_id, on_id)] replaces the random changes.
        self.allow_goals, self.allow_red_cards = allow_goals, allow_red_cards
        self.seed = seed
        self.rng = random.Random(seed)
        self.nrng = np.random.default_rng(seed)
        self.match_id = match_id or f"SYN-{seed:05d}"
        self.fps, self.dt = fps, 1.0 / fps
        self.half_len = half_minutes * 60.0
        self.team_names = dict(team_names) if team_names else dict(TEAM_NAMES)
        self.snapshot_goals = bool(snapshot_goals)
        self._recent = deque()
        self._pending_window = None
        self._flush_until = None
        self.goal_windows = []
        used = set()
        if squads:
            self.squads = {t: [dict(p) for p in squads[t]] for t in ("home", "away")}
        else:
            self.squads = {t: make_squad(self.rng, t, used) for t in ("home", "away")}
        self.names = {p["player_id"]: p["name"] for s in self.squads.values() for p in s}
        self.slot_pid = ([p["player_id"] for p in self.squads["home"][:11]] +
                         [p["player_id"] for p in self.squads["away"][:11]])
        self.bench = {t: [p["player_id"] for p in self.squads[t][11:]] for t in ("home", "away")}
        self.active = [True] * 22
        self.pos = np.zeros((22, 2))
        self.ball = np.array(CENTRE, dtype=float)
        self.period, self.t = 1, 0.0
        scale = half_minutes / 45.0
        self.added = [max(1, round(self.rng.randint(1, 4) * scale)),
                      max(1, round(self.rng.randint(3, 7) * scale))]
        self.added_announced = [False, False]
        self.score = {"home": 0, "away": 0}
        self.yellows = set()
        self.events, self.timed = [], []
        self.poss = "home"
        self.mode = "dead"
        self.carrier = self.receiver = self.taker = None
        self.restart_fn = None
        self.spot = np.array(CENTRE, dtype=float)
        self.dead_until = 0.0
        self.next_decision = 0.0
        self.kickoff_shape = True
        self.setpiece = None
        self.frame_no = 0
        self.poss_frames = {"home": 0, "away": 0}
        self.stats = {t: dict(goals=0, shots=0, shots_on_target=0, xg=0.0, passes=0,
                              passes_completed=0, corners=0, fouls=0, yellow_cards=0,
                              red_cards=0, tackles_won=0, interceptions=0, substitutions=0)
                      for t in ("home", "away")}
        # substitutions: second half, roughly 58'-85' (scaled for short matches)
        self.sub_plan = sorted((self.rng.uniform(13, 40) * 60 * scale, team)
                               for team in ("home", "away")
                               for _ in range(self.rng.randint(3, 5)))
        if sub_plan is not None:
            self.sub_plan = sorted(sub_plan)

    # ---------- geometry helpers ----------
    @staticmethod
    def team_of(s):
        return "home" if s < 11 else "away"

    @staticmethod
    def opp(team):
        return "away" if team == "home" else "home"

    @staticmethod
    def slots(team):
        return range(0, 11) if team == "home" else range(11, 22)

    def direction(self, team):
        d = 1 if team == "home" else -1
        return d if self.period == 1 else -d

    def to_frame(self, p, team):
        """World -> team frame (team attacks +x, its left flank is high y). Self-inverse."""
        p = np.asarray(p, dtype=float)
        return p.copy() if self.direction(team) == 1 else np.array([L - p[0], W - p[1]])

    from_frame = to_frame

    def zone(self, team, p):
        u, y = self.to_frame(p, team)
        if u > 88.5 and abs(y - 34) < 20.16:
            return "inside the opposition box"
        if u < 16.5 and abs(y - 34) < 20.16:
            return "inside own box"
        third = "own third" if u < 35 else "midfield" if u < 70 else "attacking third"
        side = "left wing" if y > 45.3 else "right wing" if y < 22.7 else "central"
        return f"{side}, {third}"

    def nearest(self, team, p, exclude_gk=True, exclude=()):
        best, bd = None, 1e9
        for s in self.slots(team):
            if not self.active[s] or s in exclude or (exclude_gk and s % 11 == 0):
                continue
            d = math.dist(self.pos[s], p)
            if d < bd:
                best, bd = s, d
        return best

    def slot_or_nearest(self, slot, team, p):
        if slot is not None and self.active[slot]:
            return slot
        return self.nearest(team, p, exclude_gk=False)

    def match_seconds(self, t=None):
        t = self.t if t is None else t
        return t if self.period == 1 else self.half_len + self.added[0] * 60 + t

    def clock_parts(self, t=None):
        secs = (0.0 if self.period == 1 else self.half_len) + (self.t if t is None else t)
        reg = self.half_len * self.period
        clock = f"{int(secs // 60):02d}:{int(secs % 60):02d}"
        if secs >= reg:
            minute = f"{int(reg // 60)}+{int((secs - reg) // 60) + 1}'"
        else:
            minute = f"{int(secs // 60) + 1}'"
        return clock, minute

    # ---------- logging ----------
    def log(self, etype, team=None, slot=None, **kw):
        at = kw.pop("at", None)
        at = self.ball if at is None else at
        pid = self.slot_pid[slot] if slot is not None else None
        clock, minute = self.clock_parts()
        ev = {"match_id": self.match_id, "event_id": len(self.events) + 1,
              "period": self.period, "clock": clock, "minute": minute,
              "match_seconds": round(self.match_seconds(), 2), "type": etype,
              "team": team, "team_name": self.team_names.get(team),
              "player_id": pid, "player_name": self.names.get(pid),
              "x": round(float(at[0]), 1), "y": round(float(at[1]), 1)}
        ev.update(kw)
        if team:
            ev["zone"] = self.zone(team, (ev["x"], ev["y"]))
        ev["score"] = f'{self.score["home"]}-{self.score["away"]}'
        ev["synthetic"] = True
        self.events.append(ev)
        return ev

    # ---------- state transitions ----------
    def gain(self, s, delay=(0.6, 2.0)):
        self.carrier, self.receiver = s, None
        self.poss = self.team_of(s)
        self.mode = "hold"
        self.next_decision = self.t + self.rng.uniform(*delay)

    def fly(self, to, speed, on_arrive, receiver=None):
        self.mode = "flight"
        self.fl_to = np.array(to, dtype=float)
        self.fl_speed, self.fl_cb = speed, on_arrive
        self.receiver, self.carrier = receiver, None

    def chase(self, s, cb):
        self.mode, self.receiver, self.loose_cb, self.carrier = "loose", s, cb, None

    def dead(self, seconds, restart, team, taker=None, spot=None):
        self.mode = "dead"
        self.dead_until = self.t + seconds
        self.restart_fn = restart
        self.spot = np.array(self.ball if spot is None else spot, dtype=float)
        self.taker = self.slot_or_nearest(taker, team, self.spot)
        self.carrier = self.receiver = None

    def do_restart(self):
        self.ball = self.spot.copy()
        self.pos[self.taker] = self.spot.copy()
        self.carrier, self.poss = self.taker, self.team_of(self.taker)
        self.mode, self.kickoff_shape = "hold", False
        fn, self.restart_fn, self.taker = self.restart_fn, None, None
        fn()

    # ---------- on-ball decisions ----------
    def decide(self):
        c = self.carrier
        team = self.team_of(c)
        u, y = self.to_frame(self.ball, team)
        dist = math.hypot(L - u, 34 - y)
        if c % 11 != 0:  # outfield player
            p_shot = (0.06 + 0.30 * max(0.0, (28 - dist) / 28) if dist < 30
                      else (0.012 if dist < 34 else 0.0))
            if self.rng.random() < p_shot:
                return self.shoot(c)
            r = self.rng.random()
            p_lose = 0.07 + (0.06 if u > 70 else 0.0)
            if r < p_lose:
                return self.duel(c)
            if r < p_lose + 0.17:
                return self.carry(c)
        return self.pass_ball(c)

    def carry(self, c):
        team = self.team_of(c)
        u, y = self.to_frame(self.pos[c], team)
        nu = min(u + self.rng.uniform(5, 13), 102.0)
        ny = float(np.clip(y + self.rng.uniform(-6, 6), 3, W - 3))
        self.carry_target = self.from_frame((nu, ny), team)
        self.mode = "carry"
        self.log("carry", team, c, end_x=round(float(self.carry_target[0]), 1),
                 end_y=round(float(self.carry_target[1]), 1))

    def duel(self, c):
        team = self.team_of(c)
        o = self.nearest(self.opp(team), self.ball)
        if o is None or math.dist(self.pos[o], self.ball) > 4:
            return self.pass_ball(c)
        if self.rng.random() < 0.27:
            return self.foul(o, c)
        self.stats[self.opp(team)]["tackles_won"] += 1
        self.log("tackle", self.opp(team), o, outcome="won",
                 opponent_id=self.slot_pid[c], opponent_name=self.names[self.slot_pid[c]])
        self.gain(o, (0.8, 1.8))

    def pass_ball(self, c, kind="open_play"):
        team = self.team_of(c)
        cu = self.to_frame(self.pos[c], team)[0]
        mates, weights = [], []
        for s in self.slots(team):
            if s == c or not self.active[s]:
                continue
            d = math.dist(self.pos[s], self.pos[c])
            gain = self.to_frame(self.pos[s], team)[0] - cu
            w = math.exp(-((d - 16) / 14) ** 2) * max(0.3, 1 + gain / 25)
            if s % 11 == 0:
                w *= 0.15 if cu < 40 else 0.01
            if kind == "kick_off":
                w = 1.0 if (gain < 0 and d < 25) else 0.01
            if kind == "throw_in":
                w *= 2.0 if d < 20 else 0.3
            mates.append(s)
            weights.append(max(w, 1e-4))
        r = self.rng.choices(mates, weights)[0]
        lead = self.to_frame(self.pos[r], team) + np.array([1.5, 0.0])
        target = np.clip(self.from_frame(lead, team), [0.5, 0.5], [L - 0.5, W - 0.5])
        length = math.dist(self.ball, target)
        p_ok = (0.91 if cu < 35 else 0.83 if cu < 70 else 0.70) - 0.006 * max(0, length - 20)
        if kind == "kick_off":
            p_ok = 0.99
        elif kind == "throw_in":
            p_ok = max(p_ok, 0.85)
        speed = 13.0 if length < 25 else 18.0
        st = self.stats[team]
        st["passes"] += 1
        base = dict(pass_type=kind, length_m=round(length, 1),
                    end_x=round(float(target[0]), 1), end_y=round(float(target[1]), 1),
                    receiver_id=self.slot_pid[r], receiver_name=self.names[self.slot_pid[r]])
        if self.rng.random() < p_ok:
            st["passes_completed"] += 1
            self.log("pass", team, c, outcome="complete", **base)
            return self.fly(target, speed, lambda: self.gain(r), receiver=r)
        other = self.opp(team)
        if self.rng.random() < 0.18:  # overhit / misplaced out of play
            side = -0.5 if target[1] < 34 else W + 0.5
            out = np.array([float(np.clip(target[0], 1, L - 1)), side])
            self.log("pass", team, c, outcome="out_of_play", **base)
            return self.fly(out, speed, lambda: self.ball_out(other))
        p = self.ball + self.rng.uniform(0.45, 0.95) * (target - self.ball)
        o = self.nearest(other, p, exclude_gk=False)
        self.log("pass", team, c, outcome="intercepted", **base)

        def intercepted():
            self.stats[other]["interceptions"] += 1
            self.log("interception", other, o)
            self.gain(o)
        return self.fly(p, speed, intercepted, receiver=o)

    def shoot(self, c, xg_factor=1.0, kind="open_play", body="foot"):
        team, other = self.team_of(c), self.opp(self.team_of(c))
        u, y = self.to_frame(self.ball, team)
        dist = math.hypot(L - u, 34 - y)
        angle = abs(math.atan2(37.66 - y, L - u) - math.atan2(30.34 - y, L - u))
        xg = 0.76 if kind == "penalty" else sigmoid(-1.5 + 1.9 * angle - 0.07 * dist) * xg_factor
        st = self.stats[team]
        st["shots"] += 1
        st["xg"] += xg
        if self.rng.random() < xg and self.allow_goals:
            outcome = "goal"
        else:
            r2 = self.rng.random()
            outcome = ("blocked" if r2 < 0.25 and kind != "penalty"
                       else "saved" if r2 < 0.58 else "off_target")
        if outcome in ("goal", "saved"):
            st["shots_on_target"] += 1
        if outcome == "blocked":
            end_f = np.array([u + self.rng.uniform(1.5, 4), y + self.rng.uniform(-1.5, 1.5)])
        elif outcome == "off_target":
            end_f = np.array([104.9, 34 + self.rng.choice([-1, 1]) * self.rng.uniform(4.2, 9)])
        else:
            end_f = np.array([105.4 if outcome == "goal" else 104.6, 34 + self.rng.uniform(-3.2, 3.2)])
        end = self.from_frame(end_f, team)
        self.log("shot", team, c, shot_type=kind, body_part=body, xg=round(xg, 3),
                 outcome=outcome, distance_m=round(dist, 1))
        callbacks = {"goal": lambda: self.goal(team, c),
                     "saved": lambda: self.after_save(other, team),
                     "blocked": lambda: self.after_block(team),
                     "off_target": lambda: self.goal_kick(other)}
        self.fly(end, 16.0 if body == "head" else 26.0, callbacks[outcome])

    # ---------- outcomes & restarts ----------
    def goal(self, team, s):
        self.score[team] += 1
        self.stats[team]["goals"] += 1
        ev = self.log("goal", team, s)
        if self.snapshot_goals:
            self._pending_window = {
                "goal_event_id": ev["event_id"],
                "frames": list(self._recent),
            }
            self._flush_until = self.match_seconds() + 1.2
        conceding = self.opp(team)
        if self.rng.random() < 0.3:  # VAR checks the goal
            overturn = self.rng.random() < 0.15
            self.timed.append((self.t + 25, lambda: self.log(
                "var_check", None, None, check="goal - possible offside in the build-up")))

            def decision():
                if overturn:
                    self.score[team] -= 1
                    self.stats[team]["goals"] -= 1
                self.log("var_decision", None, None, check="goal",
                         outcome="goal disallowed - offside" if overturn else "goal stands")
            self.timed.append((self.t + 70, decision))
            if overturn:
                self.kickoff_shape = False
                spot = self.from_frame((22, self.rng.uniform(15, 53)), conceding)
                return self.dead(85, lambda: self.restart_pass("free_kick"), conceding, spot=spot)
            self.kickoff_shape = True
            return self.dead(85, self.kick_off, conceding, taker=self.slots(conceding)[9], spot=CENTRE)
        self.kickoff_shape = True
        self.dead(self.rng.uniform(40, 55), self.kick_off, conceding,
                  taker=self.slots(conceding)[9], spot=CENTRE)

    def kick_off(self):
        self.log("kick_off", self.poss, self.carrier)
        self.pass_ball(self.carrier, "kick_off")

    def restart_pass(self, kind):
        self.log(kind, self.poss, self.carrier)
        self.pass_ball(self.carrier, kind)

    def after_save(self, keeper_team, shooting_team):
        gk = self.slots(keeper_team)[0]
        if self.active[gk] and self.rng.random() < 0.6:
            self.log("save", keeper_team, gk, outcome="held")
            return self.chase(gk, lambda: self.gain(gk, (2.0, 5.0)))
        self.log("save", keeper_team, gk if self.active[gk] else None, outcome="parried for a corner")
        self.corner(shooting_team)

    def after_block(self, shooting_team):
        if self.rng.random() < 0.35:
            return self.corner(shooting_team)
        self.loose_ball()

    def loose_ball(self):
        cands = [s for s in (self.nearest("home", self.ball), self.nearest("away", self.ball)) if s is not None]
        s = min(cands, key=lambda k: math.dist(self.pos[k], self.ball))
        self.chase(s, lambda: self.gain(s))

    def ball_out(self, team):
        self.log("ball_out_of_play", None, None, restart="throw-in")
        spot = (float(np.clip(self.ball[0], 1, L - 1)), 0.3 if self.ball[1] < 34 else W - 0.3)
        self.dead(self.rng.uniform(6, 11), lambda: self.restart_pass("throw_in"), team, spot=spot)

    def goal_kick(self, team):
        self.log("ball_out_of_play", None, None, restart="goal kick")
        spot = self.from_frame((5.5, 34 + self.rng.choice([-1, 1]) * self.rng.uniform(0, 9)), team)
        self.dead(self.rng.uniform(10, 18), lambda: self.restart_pass("goal_kick"), team,
                  taker=self.slots(team)[0], spot=spot)

    def corner(self, team):
        self.stats[team]["corners"] += 1
        u, y = self.to_frame(self.ball, team)
        right_side = y < 34
        spot = self.from_frame((104.5, 0.5 if right_side else W - 0.5), team)
        taker = self.slots(team)[8 if right_side else 10]
        sp = {}
        for i in CORNER_RUNNERS:
            sp[self.slots(team)[i]] = self.from_frame(
                (self.rng.uniform(93, 101), self.rng.uniform(27, 41)), team)
        for s in list(self.slots(self.opp(team)))[1:]:
            sp[s] = self.from_frame((self.rng.uniform(95, 103.5), self.rng.uniform(26, 42)), team)
        self.setpiece = sp
        self.log("corner_awarded", team, None)
        self.dead(self.rng.uniform(18, 28), self.corner_kick, team, taker=taker, spot=spot)

    def corner_kick(self):
        team, c = self.poss, self.carrier
        self.log("corner", team, c)
        tgt = self.from_frame((self.rng.uniform(94, 101), self.rng.uniform(27, 41)), team)

        def land():
            self.setpiece = None
            att = self.nearest(team, self.ball)
            if att is not None and math.dist(self.pos[att], self.ball) < 6 and self.rng.random() < 0.3:
                self.pos[att] = self.ball.copy()
                return self.shoot(att, xg_factor=0.4, kind="from_corner", body="head")
            dfn = self.nearest(self.opp(team), self.ball, exclude_gk=False)
            self.pos[dfn] = self.ball + self.nrng.normal(0, 0.3, 2)
            self.clearance(dfn)
        self.fly(tgt, 20.0, land)

    def clearance(self, s):
        team = self.team_of(s)
        self.log("clearance", team, s)
        u, y = self.to_frame(self.ball, team)
        tgt = self.from_frame((min(u + self.rng.uniform(20, 40), 100.0),
                               float(np.clip(y + self.rng.uniform(-15, 15), 3, W - 3))), team)
        self.fly(tgt, 20.0, self.loose_ball)

    def foul(self, fouler, victim):
        ft, vt = self.team_of(fouler), self.team_of(victim)
        self.stats[ft]["fouls"] += 1
        self.log("foul", ft, fouler, on_player_id=self.slot_pid[victim],
                 on_player_name=self.names[self.slot_pid[victim]])
        extra = self.card(fouler)
        u, y = self.to_frame(self.ball, vt)
        if u > 88.5 and abs(y - 34) < 20.16:
            self.log("penalty_awarded", vt, victim)
            self.timed.append((self.t + 20, lambda: self.log(
                "var_check", None, None, check="penalty", outcome="on-field decision stands")))
            return self.dead(55 + extra, lambda: self.shoot(self.carrier, kind="penalty"), vt,
                             taker=self.slots(vt)[9], spot=self.from_frame((94, 34), vt))
        self.dead(self.rng.uniform(8, 14) + extra, self.free_kick, vt, taker=victim)

    def free_kick(self):
        c = self.carrier
        team = self.team_of(c)
        u, y = self.to_frame(self.ball, team)
        if math.hypot(L - u, 34 - y) < 30 and self.rng.random() < 0.35:
            self.log("free_kick", team, c, attempt="direct shot")
            return self.shoot(c, xg_factor=0.7, kind="free_kick")
        self.restart_pass("free_kick")

    def card(self, s):
        team, pid = self.team_of(s), self.slot_pid[s]
        r = self.rng.random()
        if r < 0.004 or (r < 0.13 and pid in self.yellows):
            if not self.allow_red_cards:
                return 0
            self.log("red_card", team, s, reason="second yellow" if r >= 0.004 else "serious foul play")
            self.stats[team]["red_cards"] += 1
            self.active[s] = False
            return 20
        if r < 0.13:
            self.yellows.add(pid)
            self.stats[team]["yellow_cards"] += 1
            self.log("yellow_card", team, s)
            return 10
        return 0

    def substitute(self, team, off=None, on=None):
        """A random change, or the scripted one when off / on (player ids) are given."""
        if not self.bench[team]:
            return
        if off is not None:
            s = self.slot_pid.index(off)
            self.bench[team].remove(on)
        else:
            cands = [s for s in self.slots(team) if self.active[s] and s % 11 != 0 and s != self.taker]
            if not cands:
                return
            s = self.rng.choice(cands)
            on = self.bench[team].pop(0)
        self.log("substitution", team, s, player_on_id=on, player_on_name=self.names[on])
        self.stats[team]["substitutions"] += 1
        self.slot_pid[s] = on
        self.pos[s] = (L / 2 + self.rng.uniform(-3, 3), 0.5)  # new player enters at halfway line

    def place_kickoff(self):
        self.kickoff_shape = True
        self.ball = np.array(CENTRE, dtype=float)
        for team in ("home", "away"):
            for i, p in enumerate(self.shape(team, False)):
                self.pos[self.slots(team)[i]] = p

    # ---------- per-frame simulation ----------
    def scripted(self):
        """True when a subclass has set this frame's positions itself, so step() is skipped."""
        return False

    def shape(self, team, in_poss):
        bu, by = (52.5, 34.0) if self.kickoff_shape else self.to_frame(self.ball, team)
        shift = 0.32 * (bu - 52.5) + (7 if in_poss else -5)
        u = BASE[:, 0] + shift
        y = BASE[:, 1] + 0.22 * (by - 34)
        if in_poss:
            y = 34 + (y - 34) * 1.12
        u[0] = np.clip(5 + 0.08 * (bu - 30), 2, 18)
        u[1:] = np.clip(u[1:], 6, 99)
        if self.kickoff_shape:
            u = np.minimum(u, 51.0)
        y = np.clip(y, 2, W - 2)
        return [self.from_frame((u[i], y[i]), team) for i in range(11)]

    def move(self, s, target, vmax):
        d = np.asarray(target, dtype=float) - self.pos[s]
        dist = math.hypot(d[0], d[1])
        if dist > 1e-6:
            self.pos[s] += d / dist * min(dist, vmax * self.dt)
        self.pos[s] += self.nrng.normal(0, 0.02, 2)
        self.pos[s] = np.clip(self.pos[s], [-1, -1], [L + 1, W + 1])

    def step(self):
        due = [f for (tt, f) in self.timed if tt <= self.t]
        self.timed = [(tt, f) for (tt, f) in self.timed if tt > self.t]
        for f in due:
            f()
        busy = set()
        if self.mode == "hold":
            c = self.carrier
            team = self.team_of(c)
            fwd = self.from_frame(self.to_frame(self.pos[c], team) + np.array([3.0, 0.0]), team)
            self.move(c, np.clip(fwd, [1, 1], [L - 1, W - 1]), 1.2)
            self.ball = self.pos[c].copy()
            busy.add(c)
            if self.t >= self.next_decision:
                self.decide()
        elif self.mode == "carry":
            c = self.carrier
            self.move(c, self.carry_target, 5.5)
            self.ball = self.pos[c].copy()
            busy.add(c)
            if math.dist(self.pos[c], self.carry_target) < 0.5:
                self.mode = "hold"
                self.next_decision = self.t + self.rng.uniform(0.3, 1.0)
        elif self.mode == "flight":
            vec = self.fl_to - self.ball
            d = math.hypot(vec[0], vec[1])
            stepd = self.fl_speed * self.dt
            if self.receiver is not None:
                self.move(self.receiver, self.fl_to, 7.0)
                busy.add(self.receiver)
            if d <= stepd:
                self.ball = self.fl_to.copy()
                cb, r = self.fl_cb, self.receiver
                if r is not None and math.dist(self.pos[r], self.ball) > 1.5:
                    self.chase(r, cb)
                else:
                    cb()
            else:
                self.ball = self.ball + vec / d * stepd
        elif self.mode == "loose":
            r = self.receiver
            self.move(r, self.ball, 7.0)
            busy.add(r)
            if math.dist(self.pos[r], self.ball) < 1.2:
                cb, self.receiver = self.loose_cb, None
                cb()
        elif self.mode == "dead":
            while self.period == 2 and self.sub_plan and self.sub_plan[0][0] <= self.t:
                self.substitute(*self.sub_plan.pop(0)[1:])
            if self.taker is not None:
                self.move(self.taker, self.spot, 6.0)
                busy.add(self.taker)
            if self.t >= self.dead_until:
                self.do_restart()

        if self.mode in ("hold", "carry") and self.carrier is not None:
            p = self.nearest(self.opp(self.poss), self.ball, exclude=busy)
            if p is not None:
                d = math.dist(self.pos[p], self.ball)
                tgt = self.ball + (self.pos[p] - self.ball) / max(d, 1e-6) * 1.5 if d > 1.5 else self.pos[p]
                self.move(p, tgt, 6.0)
                busy.add(p)

        for team in ("home", "away"):
            tg = self.shape(team, team == self.poss)
            for i, s in enumerate(self.slots(team)):
                if s in busy or not self.active[s]:
                    continue
                target = self.setpiece.get(s, tg[i]) if self.setpiece else tg[i]
                self.move(s, target, 4.5)

    def _snapshot_frame(self):
        in_play = self.mode != "dead"
        players = []
        for s in range(22):
            if self.active[s]:
                players.append({
                    "player_id": self.slot_pid[s],
                    "team": self.team_of(s),
                    "x": round(float(self.pos[s][0]), 2),
                    "y": round(float(self.pos[s][1]), 2),
                })
        return {
            "frame": self.frame_no,
            "period": self.period,
            "match_seconds": round(self.match_seconds(), 2),
            "clock": self.clock_parts()[0],
            "ball": {"x": round(float(self.ball[0]), 2), "y": round(float(self.ball[1]), 2)},
            "ball_in_play": in_play,
            "possession": self.poss if in_play else "",
            "players": players,
        }

    def _keep_goal_window(self):
        if not self.snapshot_goals:
            return
        snap = self._snapshot_frame()
        self._recent.append(snap)
        cutoff = snap["match_seconds"] - 26.0
        while self._recent and self._recent[0]["match_seconds"] < cutoff:
            self._recent.popleft()
        if self._pending_window is not None:
            self._pending_window["frames"].append(snap)
            if snap["match_seconds"] >= self._flush_until:
                self.goal_windows.append(self._pending_window)
                self._pending_window = None
                self._flush_until = None

    def record(self, w):
        in_play = self.mode != "dead"
        if in_play:
            self.poss_frames[self.poss] += 1
        self._keep_goal_window()
        if w is None:
            return
        ms = f"{self.match_seconds():.2f}"
        clock = self.clock_parts()[0]
        d = self.POS_DECIMALS
        w.writerow([self.frame_no, self.period, ms, clock, "ball", "",
                    f"{self.ball[0]:.{d}f}", f"{self.ball[1]:.{d}f}",
                    self.poss if in_play else "", int(in_play)])
        for s in range(22):
            if self.active[s]:
                w.writerow([self.frame_no, self.period, ms, clock, self.slot_pid[s],
                            self.team_of(s), f"{self.pos[s][0]:.{d}f}", f"{self.pos[s][1]:.{d}f}", "", ""])

    def end_period(self):
        self.log("period_end", None, None, period_name="half time" if self.period == 1 else "full time")
        if self.period == 2:
            return False
        self.period, self.t = 2, 0.0
        self.timed.clear()
        self.setpiece = None
        self.place_kickoff()
        self.dead(0.5, self.kick_off, "away", taker=self.slots("away")[9], spot=CENTRE)
        return True

    def run(self, out_dir, write_tracking=True):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        f = open(out / "tracking.csv", "w", newline="") if write_tracking else None
        w = csv.writer(f) if f else None
        if w:
            w.writerow(["frame", "period", "match_seconds", "clock", "object_id", "team",
                        "x", "y", "ball_possession", "ball_in_play"])
        self.place_kickoff()
        self.dead(0.5, self.kick_off, "home", taker=self.slots("home")[9], spot=CENTRE)
        max_frames = int((2 * self.half_len + 3600) * self.fps)
        while self.frame_no < max_frames:
            end = self.half_len + self.added[self.period - 1] * 60
            if self.t >= self.half_len and not self.added_announced[self.period - 1]:
                self.added_announced[self.period - 1] = True
                self.log("added_time", None, None, minutes=self.added[self.period - 1])
            if self.t >= end and self.mode in ("hold", "carry", "dead") and self.restart_fn is None \
                    or (self.t >= end + 120 and self.mode in ("hold", "carry", "dead")):
                if not self.end_period():
                    break
            if not self.scripted():
                self.step()
            self.record(w)
            self.t += self.dt
            self.frame_no += 1
        if f:
            f.close()
        if self._pending_window is not None:
            self.goal_windows.append(self._pending_window)
            self._pending_window = None
        with open(out / "events.jsonl", "w") as fe:
            for ev in self.events:
                fe.write(json.dumps(ev) + "\n")
        meta = self.meta(write_tracking)
        (out / "match_meta.json").write_text(json.dumps(meta, indent=2))
        return meta

    def meta(self, write_tracking):
        total = max(1, sum(self.poss_frames.values()))
        summary = {}
        for t in ("home", "away"):
            s = dict(self.stats[t])
            s["xg"] = round(s["xg"], 2)
            s["possession_pct"] = round(100 * self.poss_frames[t] / total, 1)
            s["pass_accuracy_pct"] = round(100 * s["passes_completed"] / max(1, s["passes"]), 1)
            summary[t] = s
        return {
            "match_id": self.match_id,
            "synthetic": True,
            "disclaimer": "Simulated data. All teams, players and events are fictional. "
                          "No real Premier League or other real match data was used.",
            "generator": f"synthetic_match.py v{VERSION}",
            "seed": self.seed,
            "pitch": {"length_m": L, "width_m": W, "origin": "bottom-left corner",
                      "home_attacks_in_period_1": "towards x = 105"},
            "fps": self.fps if write_tracking else None,
            "half_minutes": self.half_len / 60,
            "added_time_minutes": self.added,
            "teams": {t: {"name": self.team_names[t], "formation": "4-3-3", "squad": self.squads[t]}
                      for t in ("home", "away")},
            "final_score": dict(self.score),
            "summary": summary,
            "event_counts": {k: sum(1 for e in self.events if e["type"] == k)
                             for k in sorted({e["type"] for e in self.events})},
        }


def main():
    ap = argparse.ArgumentParser(description="Generate a synthetic, football-realistic match.")
    ap.add_argument("--seed", type=int, default=1, help="random seed (same seed = same match)")
    ap.add_argument("--out", default=None, help="output folder (default: match_<seed>)")
    ap.add_argument("--fps", type=int, default=5, help="tracking frames per second")
    ap.add_argument("--half-minutes", type=float, default=45, help="length of each half")
    ap.add_argument("--no-tracking", action="store_true", help="only write events + meta")
    a = ap.parse_args()
    m = Match(a.seed, a.fps, a.half_minutes)
    meta = m.run(a.out or f"match_{a.seed:03d}", write_tracking=not a.no_tracking)
    h, aw = meta["summary"]["home"], meta["summary"]["away"]
    print(f'{meta["teams"]["home"]["name"]} {meta["final_score"]["home"]} - '
          f'{meta["final_score"]["away"]} {meta["teams"]["away"]["name"]}  (synthetic, seed {a.seed})')
    for k in ("possession_pct", "shots", "shots_on_target", "xg", "passes", "pass_accuracy_pct",
              "corners", "fouls", "yellow_cards"):
        print(f"  {k:<18} {h[k]:>6}  {aw[k]:>6}")


if __name__ == "__main__":
    main()

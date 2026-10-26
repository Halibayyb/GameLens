"""Load a match folder (events plus optional tracking) and pick out the key moments.

The default folder is produced by ``data_gen/synthetic_match.py``. Tracking rows
for the players and the ball become the pitch replay.
"""
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

KEY_TYPES = {"yellow_card": "Yellow card", "red_card": "Red card", "substitution": "Substitution",
             "penalty_awarded": "Penalty awarded", "var_decision": "VAR decision"}
BIG_CHANCE_XG = 0.12
HIGH_CHANCE_XG = 0.25   # a chance at least this good counts as an important moment
TRACK_BEFORE_S, TRACK_AFTER_S = 4.0, 1.5   # replay window around a moment (seconds)
ASSIST_WINDOW_S, PASS_LEAD_S, TRACK_MAX_BEFORE_S = 10.0, 1.0, 12.0   # assist lookback; replay starts this long before the pass; window cap


@dataclass
class Moment:
    id: str
    event: dict
    title: str
    kind: str                     # goal | chance | card | var | sub | penalty
    elapsed: float                # continuous match minute (first-half added time pushes the second half on)
    frame: dict = field(default_factory=dict)   # object_id -> {"team", "x", "y"} at that instant
    track: dict = field(default_factory=dict)   # replay window: {"t": [tau s], "objects": {id: {"team", "xy": [[x, y] | None]}}}
    assist_pass: dict | None = None   # completed pass that set up the shot (event), if any
    assist_frame: dict = field(default_factory=dict)   # tracking frame at that pass: the freeze frame
    assist_tau: float = 0.0           # pass time relative to the shot (s, negative)

    @property
    def important(self) -> bool:
        """Goals, red cards, penalties, VAR decisions and high-xG chances: what the default view keeps."""
        if self.kind in ("goal", "penalty", "var"):
            return True
        if self.kind == "card":
            return self.title == "Red card"
        return self.kind == "chance" and (self.event.get("xg") or 0) >= HIGH_CHANCE_XG

    def summary(self) -> dict:
        e = self.event
        xg = e.get("xg")
        return {"id": self.id, "title": self.title, "kind": self.kind, "minute": e["minute"],
                "elapsed": round(self.elapsed, 2), "team": e["team"], "team_name": e["team_name"],
                "player": e.get("player_name"), "player_id": e.get("player_id"), "score": e["score"], "x": e["x"], "y": e["y"],
                "xg": round(xg, 3) if xg is not None else None, "important": self.important,
                "video": e.get("video")}   # a clip of this moment (a URL under the web app), or null


class Match:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.meta = json.loads((self.folder / "match_meta.json").read_text())
        self.events = [json.loads(line) for line in (self.folder / "events.jsonl").read_text().splitlines() if line]
        self.names = {p["player_id"]: p["name"] for t in self.meta["teams"].values() for p in t["squad"]}
        first_half_secs = self.meta["half_minutes"] * 60 + self.meta["added_time_minutes"][0] * 60
        self._half2_offset = first_half_secs
        self.moments = self._find_moments()
        self._attach_frames()

    def elapsed_minutes(self, e: dict) -> float:
        return e["match_seconds"] / 60.0

    @property
    def total_minutes(self) -> float:
        return max(e["match_seconds"] for e in self.events) / 60.0

    def _find_moments(self) -> list[Moment]:
        out = []
        for i, e in enumerate(self.events):
            t = e["type"]
            if t == "shot" and (e.get("outcome") == "goal" or e.get("xg", 0) >= BIG_CHANCE_XG):
                title = "Goal" if e["outcome"] == "goal" else {"saved": "Shot saved", "blocked": "Shot blocked",
                                                               "off_target": "Big chance missed"}.get(e["outcome"], "Chance")
                kind = "goal" if e["outcome"] == "goal" else "chance"
                if kind == "goal":  # show the score after the goal
                    nxt = next((g for g in self.events[i + 1:i + 4] if g["type"] == "goal"), None)
                    if nxt:
                        e = {**e, "score": nxt["score"]}
            elif t in KEY_TYPES:
                title = KEY_TYPES[t]
                kind = {"yellow_card": "card", "red_card": "card", "substitution": "sub",
                        "penalty_awarded": "penalty", "var_decision": "var"}[t]
                if t == "var_decision":
                    title = "VAR: " + e.get("outcome", "decision")
            else:
                continue
            out.append(Moment(id=f"e{e['event_id']}", event=e, title=title, kind=kind,
                              elapsed=self.elapsed_minutes(e),
                              assist_pass=self._find_assist(i) if kind in ("goal", "chance") else None))
        return out

    def _find_assist(self, i: int) -> dict | None:
        """The latest completed pass by the shooter's team to the shooter within ASSIST_WINDOW_S before the shot."""
        shot = self.events[i]
        for p in reversed(self.events[:i]):
            if shot["match_seconds"] - p["match_seconds"] > ASSIST_WINDOW_S:
                break
            if (p["type"] == "pass" and p.get("outcome") == "complete" and p["team"] == shot["team"]
                    and p.get("receiver_id") == shot["player_id"]):
                return p
        return None

    def _attach_frames(self) -> None:
        """Read tracking.csv once, keeping the frame at each moment (and at its assist pass) and a window for the replay."""
        path = self.folder / "tracking.csv"
        if not path.exists():
            return
        fps = self.meta.get("fps") or 5
        after = round(TRACK_AFTER_S * fps)
        centre = {m.id: round(m.event["match_seconds"] * fps) for m in self.moments}
        pass_key = {m.id: round(m.assist_pass["match_seconds"] * fps) for m in self.moments if m.assist_pass}
        # replay window start (frames before the shot): the usual lead, or back to PASS_LEAD_S before the pass, capped
        before = {m.id: min(round(TRACK_MAX_BEFORE_S * fps),
                            centre[m.id] - pass_key[m.id] + round(PASS_LEAD_S * fps)
                            if m.id in pass_key else round(TRACK_BEFORE_S * fps))
                  for m in self.moments if m.kind in ("goal", "chance")}
        need = set(centre.values()) | set(pass_key.values())
        for mid, b in before.items():
            need.update(range(centre[mid] - b, centre[mid] + after + 1))
        frames: dict[int, dict] = {}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                key = round(float(row["match_seconds"]) * fps)
                if key in need:
                    frames.setdefault(key, {})[row["object_id"]] = {"team": row["team"], "x": float(row["x"]),
                                                                    "y": float(row["y"])}
        for m in self.moments:
            m.frame = frames.get(centre[m.id], {})
            if m.id in pass_key:
                m.assist_frame = frames.get(pass_key[m.id], {})
                m.assist_tau = round((pass_key[m.id] - centre[m.id]) / fps, 3)
            if m.id in before:
                m.track = self._build_track(frames, centre[m.id], before[m.id], after, fps)

    @staticmethod
    def _build_track(window: dict, c: int, before: int, after: int, fps: int) -> dict:
        keys = [k for k in range(c - before, c + after + 1) if k in window]
        if c not in window or len(keys) < 2:
            return {}
        ids = {oid: o["team"] for k in keys for oid, o in window[k].items()}
        objects = {oid: {"team": team,
                         "xy": [[window[k][oid]["x"], window[k][oid]["y"]] if oid in window[k] else None for k in keys]}
                   for oid, team in ids.items()}
        return {"fps": fps, "t": [round((k - c) / fps, 3) for k in keys], "objects": objects}

    def get(self, moment_id: str) -> Moment:
        for m in self.moments:
            if m.id == moment_id:
                return m
        raise KeyError(moment_id)

    def score_segments(self) -> list[dict]:
        """Score through the match as continuous segments (for the score strip)."""
        segs, start, score = [], 0.0, "0-0"
        for e in self.events:
            if e["type"] in ("goal", "var_decision") and e["score"] != score:
                t = self.elapsed_minutes(e)
                segs.append({"from": round(start, 2), "to": round(t, 2), "score": score})
                start, score = t, e["score"]
        segs.append({"from": round(start, 2), "to": round(self.total_minutes, 2), "score": score})
        return segs

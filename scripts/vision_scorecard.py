"""Scorecard: how far the vision pipeline is from the passing formulas, on the three synthetic goals.

  Level 1  truth: the proto match's own events and tracking -> engine.assist.build_assist
  Level 2  adapter: vision.events.detect_events on the true tracking (no detector error)
  Level 3  vision: YOLO + ByteTrack + homography on docs/goal_0N/*.mp4, then the level 2 path

Video time is not match time. Level 3 is aligned to the true pass frame by the video frame whose players best
match it (optimal assignment, attack-right normalised, y-mirror allowed), so position error is measurable even
when no pass is detected.

  python scripts/vision_scorecard.py [--goals 1 2 3] [--levels 1 2 3] [--fps 8] [--imgsz 1280] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_gen.proto_demo import PROTO_FRAMES, parse_frames_txt  # noqa: E402,F401 (parse_frames_txt: re-exported)
from engine.assist import build_assist  # noqa: E402
from engine.moments import Match  # noqa: E402
from vision.events import detect_events, frames_from_tracking_csv  # noqa: E402
from vision.pipeline import write_tracked  # noqa: E402

PROTO = ROOT / "data" / "match_proto"
VIDEOS = {  # the rendered clip of each goal
    1: "docs/goal_01/goal1_home_throughball_71min_v2_final.mp4",
    2: "docs/goal_02/goal2_away_header_82min_v3_final.mp4",
    3: "docs/goal_03/football_goal_8s_dataplay_v2.mp4",
}
WINDOW_S = (10.0, 4.0)   # true tracking from this long before the killer pass to this long after it
CACHE = Path(tempfile.gettempdir()) / "gamelens_scorecard"


# ---------- comparison (pure, unit tested) ----------

def norm(x: float, y: float, right: bool) -> tuple[float, float]:
    """World metres -> attack-right frame."""
    return (x, y) if right else (105 - x, 68 - y)


def assign(a: list[tuple], b: list[tuple]) -> tuple[list[tuple[int, int]], list[float]]:
    """Minimum-cost one-to-one pairs between point lists a and b, and their distances."""
    if not a or not b:
        return [], []
    cost = [[math.dist(p, q) for q in b] for p in a]
    try:
        from scipy.optimize import linear_sum_assignment
        rows, cols = linear_sum_assignment(cost)
        pairs = list(zip(rows.tolist(), cols.tolist()))
    except ImportError:   # greedy fallback
        pairs, used_a, used_b = [], set(), set()
        for d, i, j in sorted((cost[i][j], i, j) for i in range(len(a)) for j in range(len(b))):
            if i not in used_a and j not in used_b:
                pairs.append((i, j)); used_a.add(i); used_b.add(j)
    return pairs, [cost[i][j] for i, j in pairs]


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def match_players(seen: list[dict], truth: dict, right: bool, seen_right: bool) -> dict:
    """Map tracked players to true ids at one instant. Tries the y-mirror too; returns ids, errors and the mirror used."""
    t_ids = [k for k, o in truth.items() if k != "ball"]
    t_pts = [norm(truth[k]["x"], truth[k]["y"], right) for k in t_ids]
    best = None
    for mirror in (False, True):
        pts = []
        for p in seen:
            x, y = norm(p["x"], p["y"], seen_right)
            pts.append((x, 68 - y) if mirror else (x, y))
        pairs, d = assign(pts, t_pts)
        cost = sum(d) / max(1, len(d))
        if best is None or cost < best["mean"]:
            best = {"mean": cost, "mirror": mirror, "errors": d,
                    "ids": {seen[i]["player_id"]: t_ids[j] for i, j in pairs}}
    return best


def compare(got: dict | None, truth: dict, id_map: dict | None = None) -> dict:
    """Option-level differences between two build_assist results (id_map: got id -> truth id)."""
    if not got:
        return {"pass_found": False}
    id_map = id_map or {}
    tv = {o["player_id"]: o for o in truth["options"]}
    rows = []
    for o in got["options"]:
        t = tv.get(id_map.get(o["player_id"], o["player_id"]))
        if t:
            rows.append({k: abs(o[k] - t[k]) for k in ("arrives", "goal", "ev")} | {"cut": o["cut_out"] - t["cut_out"]})
    passer = id_map.get(got["pass"]["passer_id"], got["pass"]["passer_id"])
    receiver = id_map.get(got["pass"]["receiver_id"], got["pass"]["receiver_id"])
    worst = lambda k: round(max((r[k] for r in rows), default=0.0), 4)
    return {"pass_found": True,
            "passer_ok": passer == truth["pass"]["passer_id"], "receiver_ok": receiver == truth["pass"]["receiver_id"],
            "verdict": got["verdict"]["label"], "verdict_ok": got["verdict"]["key"] == truth["verdict"]["key"],
            "options_matched": f"{len(rows)}/{len(truth['options'])}",
            "max_err_arrives": worst("arrives"), "max_err_goal": worst("goal"), "max_err_ev": worst("ev"),
            "cut_mismatches": sum(1 for r in rows if r["cut"])}


# ---------- the three levels ----------

def _assist_from_frames(frames: list[dict], fps: float) -> tuple[dict | None, dict]:
    found = detect_events(frames, fps)
    folder = Path(tempfile.mkdtemp(prefix="scorecard_"))
    write_tracked(folder, frames, fps, events=found)
    match = Match(folder)
    goal = next((m for m in match.moments if m.kind == "goal"), None)
    return (build_assist(match, goal) if goal else None), found


def proto_goals(truth_match: Match) -> list[tuple]:
    """The match's goal moments in order, each with its true tracking window around the killer pass."""
    goals = [m for m in truth_match.moments if m.kind == "goal"]
    return [(m, (m.assist_pass["match_seconds"] - WINDOW_S[0], m.assist_pass["match_seconds"] + WINDOW_S[1]))
            for m in goals]


def level1(truth_match: Match, moment, goal: int) -> dict:
    a = build_assist(truth_match, moment)
    txt = PROTO_FRAMES / f"goal{goal}.txt"
    squad = {q["player_id"]: q for t in truth_match.meta["teams"].values() for q in t["squad"]}
    doc = parse_frames_txt(txt.read_text()) if txt.exists() else []
    by_number = {squad[o["player_id"]]["number"]: o for o in a["options"]}
    diffs = [{"number": d["number"], "played": d["played"],
              "frames_txt": [d["arrives"], d["goal"], d["cut_out"]],
              "engine": [round(by_number[d["number"]]["arrives"], 2), round(by_number[d["number"]]["goal"], 3),
                         by_number[d["number"]]["cut_out"]] if d["number"] in by_number else None} for d in doc]
    return {"assist": a, "frames_txt": diffs}


def level2(meta: dict, window: tuple, truth: dict) -> dict:
    frames = frames_from_tracking_csv(PROTO / "tracking.csv", *window, meta)
    got, found = _assist_from_frames(frames, meta.get("fps") or 5)
    return compare(got, truth) | {"touches": found["debug"]["touches"]}


def track_video(video: Path, calibration: Path | None, fps: float, imgsz: int | None, cache: Path) -> list[dict]:
    key = cache / f"{video.stem}_{fps}_{imgsz or 640}_{'cal' if calibration else 'green'}.json"
    if key.exists():
        return json.loads(key.read_text())
    from vision.homography import load_calibration
    from vision.pipeline import YoloByteTrack, read_video, track_frames
    frames = read_video(str(video), seconds=8, sample_fps=fps, max_width=1920)
    tracked = list(track_frames(frames, YoloByteTrack(model=str(ROOT / "yolo11n.pt"), imgsz=imgsz), fps=fps,
                                calibration=load_calibration(calibration) if calibration else None))
    cache.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps(tracked))
    return tracked


def level3(tracked: list[dict], fps: float, truth: dict, truth_frame: dict, truth_right: bool) -> dict:
    balls = [f["ball"] for f in tracked if f.get("ball")]
    static = len(balls) > 3 and max(math.dist((b["x"], b["y"]), (balls[0]["x"], balls[0]["y"])) for b in balls) < 0.5
    # align: the video frame whose players best fit the true pass frame (either attack direction for the clip)
    best = None
    for k, f in enumerate(tracked):
        for seen_right in (True, False):
            m = match_players(f["players"], truth_frame, truth_right, seen_right)
            if m["errors"] and (best is None or m["mean"] < best[1]["mean"]):
                best = (k, m, seen_right)
    out = {"frames": len(tracked), "players_per_frame": round(sum(len(f["players"]) for f in tracked) / max(1, len(tracked)), 1),
           "true_players": sum(1 for k in truth_frame if k != "ball"),
           "ball_seen": round(len(balls) / max(1, len(tracked)), 2), "ball_static": static}
    if best:
        k, m, _ = best
        out |= {"aligned_t": tracked[k]["match_seconds"], "pos_err_median_m": round(percentile(m["errors"], 0.5), 2),
                "pos_err_p90_m": round(percentile(m["errors"], 0.9), 2), "y_mirror": m["mirror"]}
    got, found = _assist_from_frames(tracked, fps)
    id_map = best[1]["ids"] if best else {}
    return out | compare(got, truth, id_map) | {"touches": found["debug"]["touches"][:12]}


# ---------- report ----------

def _fmt(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    return "-" if v is None else str(v)


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--goals", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--levels", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--fps", type=float, default=8)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--cache", type=Path, default=CACHE)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    if not (PROTO / "match_meta.json").exists():
        from data_gen import proto_demo
        proto_demo.build(PROTO)
    truth_match = Match(PROTO)
    meta = truth_match.meta
    goals = proto_goals(truth_match)
    report = {}
    for g in args.goals:
        moment, window = goals[g - 1]
        video = VIDEOS[g]
        l1 = level1(truth_match, moment, g)
        truth = l1["assist"]
        row = {"truth": {"verdict": truth["verdict"]["label"], "passer": truth["pass"]["passer_id"],
                         "receiver": truth["pass"]["receiver_id"], "frames_txt_vs_engine": l1["frames_txt"]}}
        if 2 in args.levels:
            row["level2"] = level2(meta, window, truth)
        if 3 in args.levels:
            cal = ROOT / f"vision/calibrations/goal_0{g}.json"
            for name, c in (("level3_calibrated", cal if cal.exists() else None), ("level3_green_quad", None)):
                tracked = track_video(ROOT / video, c, args.fps, args.imgsz, args.cache)
                row[name] = level3(tracked, args.fps, truth, moment.assist_frame, truth["attack_right"])
        report[f"goal_{g}"] = row
    _print(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
    return report


def _print(report: dict) -> None:
    keys = ["pass_found", "passer_ok", "receiver_ok", "verdict", "verdict_ok", "options_matched", "max_err_arrives",
            "max_err_goal", "max_err_ev", "cut_mismatches", "players_per_frame", "true_players", "ball_seen",
            "ball_static", "pos_err_median_m", "pos_err_p90_m"]
    for goal, row in report.items():
        t = row["truth"]
        print(f"\n## {goal}: truth {t['passer']} -> {t['receiver']}, verdict {t['verdict']}")
        levels = [k for k in row if k.startswith("level")]
        print("| metric | " + " | ".join(levels) + " |")
        print("|---|" + "---|" * len(levels))
        for k in keys:
            if any(k in row[lv] for lv in levels):
                print(f"| {k} | " + " | ".join(_fmt(row[lv].get(k)) for lv in levels) + " |")
        for d in t["frames_txt_vs_engine"]:
            print(f"  frames.txt #{d['number']}{' (played)' if d['played'] else ''}: {d['frames_txt']}  engine: {d['engine']}")


if __name__ == "__main__":
    main()

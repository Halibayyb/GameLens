"""Verified freeze frames: a person's corrections to the one frame the pass formulas read.

The upload page proposes each pass frame (players, teams, pitch landmarks from the automatic mapping). A person
drags at least four landmarks onto the painted pitch, fixes a wrong team, picks the passer and the receiver and
the pass flags. Positions are then recomputed from that exact mapping and the formulas re-run. numpy only, so
the API image (no OpenCV, no torch) can do it.
"""
from __future__ import annotations

import json
import time

from engine.assist import assist_from_frame
from highlights.store import _job_dir
from vision.homography import PITCH_LINES, apply, homography_with_lines, line_error_m, reprojection_m
from vision.pipeline import PERSON, _ground, _on_pitch
from vision.pitch_keypoints import LANDMARKS

TEAMS = ("home", "away", "ref", "ignore")


class VerifyError(ValueError):
    pass


def verify_freeze(freeze: dict, body: dict) -> dict:
    """Corrections (``body``) applied to one proposed freeze frame -> the verified assist (raises VerifyError)."""
    marks = [m for m in body.get("landmarks") or [] if m.get("image")]
    on_lines = [m for m in body.get("line_points") or [] if m.get("image")]
    if 2 * len(marks) + len(on_lines) < 8:
        raise VerifyError("Place at least four pitch landmarks, or fewer plus points on painted lines.")
    try:
        src = [tuple(map(float, m["image"])) for m in marks]
        dst = [LANDMARKS[int(m["id"]) - 1] for m in marks]
        lines = [(tuple(map(float, m["image"])), PITCH_LINES[m["line"]]) for m in on_lines]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise VerifyError(f"Unknown landmark or line: {exc}") from exc
    try:
        matrix = homography_with_lines(list(zip(src, dst)), lines)
    except ValueError as exc:
        raise VerifyError(f"These points do not fix the pitch ({exc}). Spread them over the frame, and add points "
                          "on the touchline or halfway line.") from exc
    fit = max(reprojection_m(matrix, src, dst) if src else 0.0, line_error_m(matrix, lines))

    team_of = {o["player_id"]: o.get("team") for o in body.get("players") or []}
    if any(t not in TEAMS for t in team_of.values()):
        raise VerifyError(f"A team must be one of {', '.join(TEAMS)}.")
    boxes = [dict(b) for b in freeze.get("boxes") or []]
    for n, extra in enumerate(body.get("add") or [], start=1):
        team = extra.get("team")
        if team not in TEAMS[:3] or len(extra.get("xyxy") or []) != 4:
            raise VerifyError("An added player needs a box and a team.")
        prefix = {"home": "H", "away": "A", "ref": "R"}[team]
        boxes.append({"player_id": f"{prefix}m{n}", "team": team, "role": extra.get("role") or "OUT",
                      "xyxy": [float(v) for v in extra["xyxy"]], "manual": True})
    frame, players, off = {}, [], []
    for b in boxes:
        team = team_of.get(b["player_id"], b.get("team"))
        if team == "ignore":
            continue
        x, y = apply(matrix, *_ground(b["xyxy"], PERSON))
        row = {"player_id": b["player_id"], "team": team, "role": b.get("role") or "OUT", "x": round(x, 2),
               "y": round(y, 2), "xyxy": b["xyxy"]}
        if not _on_pitch(x, y):
            off.append(b["player_id"])
            continue
        frame[b["player_id"]] = {"team": team, "x": x, "y": y}
        players.append(row)

    passer = body.get("passer_id") or freeze.get("passer_id")
    receiver = body.get("receiver_id") or freeze.get("receiver_id")
    for who, pid in (("passer", passer), ("receiver", receiver)):
        if pid not in frame:
            raise VerifyError(f"The {who} ({pid}) is not on the pitch with these landmarks.")
    team = frame[passer]["team"]
    if frame[receiver]["team"] != team or team not in ("home", "away"):
        raise VerifyError("Passer and receiver must be teammates.")
    right = body.get("attack_right", freeze.get("attack_right"))
    if right is None:
        raise VerifyError("Say which way the passer's team attacks.")
    lofted = bool(body.get("lofted", freeze.get("lofted")))
    into_space = bool(body.get("into_space", freeze.get("into_space")))
    end = body.get("end_image") or _auto_end_image(freeze)
    roles = {p["player_id"]: p["role"] for p in players}

    def targets_for(_passer, _mates, pt):   # into space: the receiver is scored where the ball arrives
        if not (into_space and end):
            return None
        x, y = apply(matrix, *map(float, end))
        return {receiver: pt({"x": x, "y": y})}

    scored = assist_from_frame(frame, passer, receiver, team, bool(right), is_gk=lambda oid: roles.get(oid) == "GK",
                               lofted=lofted, targets_for=targets_for, roles=roles)
    if scored is None:
        raise VerifyError("Need opponents and at least two teammates of the passer on the pitch.")
    return {**scored, "verified": True, "passer_id": passer, "receiver_id": receiver, "attack_right": bool(right),
            "lofted": lofted, "into_space": into_space, "fit_m": round(fit, 3), "off_pitch": off,
            "homography": [round(float(v), 9) for v in matrix.ravel()], "players": players,
            "landmarks": marks, "line_points": on_lines, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _auto_end_image(freeze: dict):
    """Where the automatic pipeline put the ball's arrival, back in image pixels (via its own mapping)."""
    import numpy as np

    h, end = freeze.get("homography"), freeze.get("end") or [None, None]
    if not h or end[0] is None:
        return None
    p = np.linalg.inv(np.asarray(h, dtype=float).reshape(3, 3)) @ [end[0], end[1], 1.0]
    return [float(p[0] / p[2]), float(p[1] / p[2])] if p[2] > 0 else None


def save_truth(job_id: str, moment_id: str, freeze: dict, body: dict, verified: dict) -> None:
    """Append the correction to the job's freeze_truth.json: ground truth for scoring the automatic pipeline."""
    path = _job_dir(job_id) / "freeze_truth.json"
    rows = json.loads(path.read_text()) if path.exists() else []
    rows.append({"moment_id": moment_id, "t": freeze.get("t"), "frame_size": freeze.get("frame_size"),
                 "auto": {"passer_id": freeze.get("passer_id"), "receiver_id": freeze.get("receiver_id"),
                          "homography": freeze.get("homography"), "lofted": freeze.get("lofted"),
                          "into_space": freeze.get("into_space")},
                 "corrections": body, "verified": {k: verified[k] for k in
                                                   ("passer_id", "receiver_id", "attack_right", "lofted", "into_space",
                                                    "fit_m", "homography", "players")}})
    path.write_text(json.dumps(rows, indent=1))

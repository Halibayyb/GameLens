"""YOLO + ByteTrack + homography, as a library the vision service calls.

Importing this module does not import torch or ultralytics. The detector is
built on the first real video, and tests pass a ``FakeDetector`` plus raw
frames so the default API image never downloads weights.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from feeds.schema import format_clock, format_minute
from feeds.store import write_match
from vision.homography import apply, load_calibration
from vision.keypoints import detect_keypoints
from vision.shots import CutDetector, shot_type
from vision.quality import (
    MIN_CONF,
    apply_occlusion,
    clip_confidence,
    drop_low_confidence,
    interpolate_ball,
    mean_confidence,
    reassociate,
    smooth_homographies,
)

PERSON, BALL, REF = "person", "ball", "ref"


class FakeDetector:
    """One list of detections per frame. Used by tests and by ``--fake``."""

    def __init__(self, frames: list[list[dict]]):
        self.frames = frames
        self.index = 0

    def detect(self, image) -> list[dict]:
        row = self.frames[min(self.index, len(self.frames) - 1)] if self.frames else []
        self.index += 1
        return row


class YoloByteTrack:
    """Ultralytics YOLO with the bundled ByteTrack tracker. Loaded on first use."""

    def __init__(self, model: str | None = None, device: str | None = None, imgsz: int | None = None):
        self.device = device or os.getenv("VISION_DEVICE", "cpu")
        # 640 (the Ultralytics default) shrinks a broadcast ball to a few pixels; 1280 finds it
        self.imgsz = imgsz or int(os.getenv("VISION_IMGSZ") or 0) or None
        self.model_name = model or os.getenv("VISION_MODEL") or _default_model(self.device)
        self._model = None

    def detect(self, image) -> list[dict]:
        model = self._load()
        # Ultralytics expects BGR when given an array. Our pipeline uses RGB.
        bgr = image[:, :, ::-1]
        result = model.track(
            bgr, persist=True, tracker="bytetrack.yaml", classes=[0, 32],
            verbose=False, device=self.device, **({"imgsz": self.imgsz} if self.imgsz else {}),
        )[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or boxes.xyxy is None or len(boxes) == 0:
            return []
        ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else range(len(boxes))
        classes = boxes.cls.cpu().numpy().astype(int)
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy() if getattr(boxes, "conf", None) is not None else [0.7] * len(boxes)
        found = []
        for track_id, cls, box, conf in zip(ids, classes, xyxy, confs):
            kind = BALL if int(cls) == 32 else PERSON
            found.append({
                "cls": kind, "track_id": int(track_id),
                "xyxy": [float(v) for v in box], "confidence": float(conf),
            })
        return found

    def _load(self):
        if self._model is None:
            from ultralytics import YOLO  # local to the vision image; not the API
            self._model = YOLO(self.model_name)
        return self._model


def _default_model(device: str) -> str:
    """CPU defaults to the nano weights; GPU can override with VISION_MODEL."""
    if (device or "cpu").startswith("cuda"):
        return os.getenv("VISION_GPU_MODEL", "yolo11n.pt")
    return os.getenv("VISION_CPU_MODEL", "yolo11n.pt")


def track_frames(frames, detector, fps: float = 5.0, calibration: dict | None = None, debug: dict | None = None):
    """Turn RGB frames into live tracking-frame dicts (pitch metres, period 1).

    A cut starts a new shot (``vision.shots``): the detector's tracker is reset, homographies are smoothed only
    within a shot, and team colours are clustered per shot (a montage mixes matches). Frames without a trusted
    homography (close-ups, a green-quad guess the pitch model rejected) keep ``players: []`` and
    ``mapped: False`` rather than wrong metres. Team colours are clustered; the left-starting cluster is home.
    A leftover jersey colour is the referee. The goalkeeper on each side is the extreme median x.
    """
    pending = []
    raw_h = []
    keypoint_rows = []
    detection_rows = []
    cutter, shot_ids, kinds, shot = CutDetector(), [], [], -1
    for index, image in enumerate(frames):
        if cutter.update(image) or index == 0:
            shot += 1
            if index and hasattr(detector, "reset"):
                detector.reset()   # track ids must not carry across a cut
        kpts = detect_keypoints(image, calibration)
        detections = apply_occlusion(detector.detect(image))
        kind = shot_type(image, kpts, [d["xyxy"] for d in detections if d.get("cls", PERSON) == PERSON])
        trusted = kpts.get("trusted", kpts.get("matrix") is not None) and kind != "close"
        raw_h.append(kpts.get("matrix") if trusted else None)
        shot_ids.append(shot)
        kinds.append(kind)
        pending.append((index, image, detections, None))
        keypoint_rows.append({
            "frame": index, "t": round(index / fps, 2), "shot": shot, "shot_type": kind,
            "image_points": kpts.get("image_points") or [],
            "method": kpts.get("method"), "ok": bool(kpts.get("ok")), "trusted": bool(trusted),
            "keypoints": kpts.get("keypoints") or (kpts.get("pitch_model") or {}).get("keypoints") or [],
            "n_visible": kpts.get("n_visible", (kpts.get("pitch_model") or {}).get("n_visible")),
            "inliers": kpts.get("inliers", (kpts.get("pitch_model") or {}).get("inliers")),
            "reproj_m": kpts.get("reproj_m", (kpts.get("pitch_model") or {}).get("reproj_m")),
            "lines": kpts.get("lines") or (kpts.get("pitch_model") or {}).get("lines"),
        })
        detection_rows.append({
            "frame": index, "t": round(index / fps, 2),
            "boxes": [
                {"cls": d.get("cls"), "track_id": d.get("track_id"),
                 "xyxy": d.get("xyxy"), "confidence": d.get("confidence", 0.7),
                 "occluded": bool(d.get("occluded")), "role": d.get("role"), "source": d.get("source")}
                for d in detections
            ],
        })
    one_side_per_shot(raw_h, keypoint_rows, [img for _, img, _, _ in pending], shot_ids)
    matrices = smooth_by_shot(raw_h, shot_ids)
    pending = [(i, img, dets, matrices[n]) for n, (i, img, dets, _) in enumerate(pending)]
    shot_of = {i: shot_ids[n] for n, (i, *_) in enumerate(pending)}
    labelled, clusters = _label_teams(pending, shot_of)
    keepers = _keepers(labelled)
    history: list[dict] = []
    ages: dict[str, int] = {}
    built = []
    for index, players, ball, extras in labelled:
        t = round(index / fps, 2)
        if index and shot_of[index] != shot_of.get(index - 1, shot_of[index]):
            history = []   # no re-association across a cut
        tagged = []
        for player in players:
            pid = player["player_id"]
            ages[pid] = ages.get(pid, 0) + 1
            player = dict(player)
            player["confidence"] = clip_confidence(player, True, ages[pid])
            if player["player_id"] in keepers:
                player["role"] = "GK"
            tagged.append(player)
        tagged = reassociate(tagged, history)
        tagged = drop_low_confidence(tagged, MIN_CONF)
        history = [
            {"player_id": p["player_id"], "team": p.get("team"), "x": p["x"], "y": p["y"],
             "color": p.get("color"), "role": p.get("role")}
            for p in tagged
        ] + [h for h in history if h["player_id"] not in {p["player_id"] for p in tagged}][:24]
        built.append((index, tagged, ball, extras, t))
    balls: list = []
    for sid in dict.fromkeys(shot_ids):   # a ball gap is never bridged across a cut
        balls += interpolate_ball([row[2] for n, row in enumerate(built) if shot_ids[n] == sid])
    out = []
    for n, ((index, players, _ball, extras, t), ball) in enumerate(zip(built, balls)):
        if matrices[n] is None:
            ball = None
        frame = {
            "kind": "tracking",
            "match_id": "VISION",
            "frame": index,
            "shot": {"id": shot_ids[n], "type": kinds[n]},
            "mapped": matrices[n] is not None,
            # image (tracking pixels) -> metres, so a later step can map new boxes on this frame exactly
            "homography": None if matrices[n] is None else [round(float(v), 9) for v in np.asarray(matrices[n]).ravel()],
            "period": 1,
            "match_seconds": t,
            "clock": format_clock(1, t),
            "ball": None if ball is None else {
                "x": round(ball[0], 2), "y": round(ball[1], 2),
                "confidence": extras.get("ball_confidence", 0.7 if _ball else 0.45),
                "xyxy": extras.get("ball_xyxy") if _ball else None,   # None when interpolated
            },
            "ball_in_play": ball is not None,
            "possession": None,
            "players": [
                {
                    "player_id": player["player_id"],
                    "team": player["team"],
                    "x": round(player["x"], 2),
                    "y": round(player["y"], 2),
                    "number": player["number"],
                    "name": player["name"],
                    "role": player.get("role") or ("GK" if player["player_id"] in keepers else "OUT"),
                    "confidence": player.get("confidence", 0.7),
                    "occluded": bool(player.get("occluded")),
                    "reassociated": bool(player.get("reassociated")),
                    "xyxy": player.get("xyxy"),
                }
                for player in players
            ],
        }
        out.append(frame)
        yield frame
    if debug is not None:
        debug["detections"] = detection_rows
        debug["keypoints"] = keypoint_rows
        debug["shots"] = shots_from(shot_ids, kinds, fps)
        if pending:
            debug["frame_size"] = [int(pending[0][1].shape[1]), int(pending[0][1].shape[0])]
        debug["clusters"] = clusters
        debug["confidence"] = mean_confidence(out)
        debug["device"] = os.getenv("VISION_DEVICE", "cpu")
        debug["model"] = os.getenv("VISION_MODEL") or _default_model(os.getenv("VISION_DEVICE", "cpu"))


MAX_FILL = 2   # frames a shot may borrow a neighbour's homography for


def one_side_per_shot(raw: list, rows: list[dict], images: list, shot_ids: list[int]) -> None:
    """Keep each camera shot on one half of the pitch (in place).

    The pitch model can read the right penalty box as the left one; positions then jump some 60 m between frames
    of one play. The shot's majority half wins; a frame on the other half with no centre-line keypoint (so the
    camera did not pan across halfway) is refitted on the mirrored keypoints, or left unmapped if that fails."""
    from vision.pitch_keypoints import refit_mirrored, side
    for sid in dict.fromkeys(shot_ids):
        idx = [i for i, s in enumerate(shot_ids) if s == sid and raw[i] is not None
               and rows[i].get("method") == "pitch_model"]
        votes = {i: side(rows[i].get("keypoints") or []) for i in idx}
        majority = sum(v * (rows[i].get("inliers") or 1) for i, (v, _) in votes.items())
        if not majority:
            continue
        want = 1 if majority > 0 else -1
        for i, (v, centre) in votes.items():
            if v and v != want and not centre:
                fit = refit_mirrored(images[i], rows[i]["keypoints"])
                raw[i] = fit["matrix"] if fit["ok"] else None
                rows[i].update(method=fit["method"] if fit["ok"] else "pitch_model_flipped_dropped",
                               trusted=bool(fit["ok"]), inliers=fit.get("inliers"), reproj_m=fit.get("reproj_m"))


def _same_view(a, b, tol_m: float = 2.0) -> bool:
    """Two homographies map the image centre region to nearly the same place: the camera did not move."""
    pts = np.array([[[0.25, 0.5]], [[0.5, 0.5]], [[0.75, 0.5]]], dtype=float)
    pa, pb = [(m @ np.c_[pts.reshape(-1, 2), np.ones(3)].T) for m in (a, b)]
    pa, pb = pa[:2] / pa[2], pb[:2] / pb[2]
    return float(np.abs(pa - pb).max()) <= tol_m


def smooth_by_shot(raw: list, shot_ids: list[int], window: int = 3) -> list:
    """Smooth homographies within each shot. A gap of up to MAX_FILL frames borrows its neighbours' mapping only
    when it lies between two mapped frames that agree (a still camera); during a pan it stays None (unmapped)."""
    out: list = [None] * len(raw)
    for sid in dict.fromkeys(shot_ids):
        idx = [i for i, s in enumerate(shot_ids) if s == sid]
        good = [i for i in idx if raw[i] is not None]
        if not good:
            continue
        smoothed = smooth_homographies([raw[i] for i in idx], window=window)
        for k, i in enumerate(idx):
            if raw[i] is not None:
                out[i] = smoothed[k]
                continue
            before = [g for g in good if g < i and i - g <= MAX_FILL + 1]
            after = [g for g in good if g > i and g - i <= MAX_FILL + 1]
            if before and after and _same_view(raw[before[-1]], raw[after[0]]):
                out[i] = smoothed[k]
    return out


def shots_from(shot_ids: list[int], kinds: list[str], fps: float) -> list[dict]:
    """Shot segments {"id", "start", "end", "type", "start_t", "end_t"}; type is the majority frame type."""
    out = []
    for sid in dict.fromkeys(shot_ids):
        idx = [i for i, s in enumerate(shot_ids) if s == sid]
        span = [kinds[i] for i in idx]
        out.append({"id": sid, "start": idx[0], "end": idx[-1], "type": max(set(span), key=span.count),
                    "start_t": round(idx[0] / fps, 2), "end_t": round((idx[-1] + 1) / fps, 2)})
    return out


def materialize_video(video: str, folder: Path, seconds: float = 8.0, fps: float = 5.0,
                      calibration: str | None = None, detector=None) -> dict:
    """Read a local video and write the internal match folder. Downloads weights on first YOLO use."""
    folder = Path(folder)
    frames = read_video(video, seconds=seconds, sample_fps=fps)
    det = detector or YoloByteTrack()
    tracked = list(track_frames(frames, det, fps=fps, calibration=load_calibration(calibration or os.getenv("VISION_CALIBRATION"))))
    if not tracked:
        raise RuntimeError(f"No frames read from {video}.")
    from vision.events import detect_events
    return write_tracked(folder, tracked, fps=fps, events=detect_events(tracked, fps))


def write_tracked(folder: Path, frames: list[dict], fps: float, events: dict | None = None) -> dict:
    """Write the match folder. ``events``: ``vision.events.detect_events`` output (passes, shot, goal)."""
    folder = Path(folder)
    players: dict[str, dict] = {}
    rows = []
    for frame in frames:
        poss = frame.get("possession") or ""
        in_play = 1 if frame.get("ball") else 0
        if frame.get("ball"):
            rows.append(_row(frame, "ball", "", frame["ball"]["x"], frame["ball"]["y"], poss, in_play))
        for player in frame["players"]:
            players[player["player_id"]] = player
            rows.append(_row(frame, player["player_id"], player["team"], player["x"], player["y"], poss, in_play))
    if not rows:
        raise RuntimeError("The vision pipeline produced no players or ball.")
    last = frames[-1]["match_seconds"]
    end = last if last > 0 else 0.2
    home = [_squad(p) for p in players.values() if p["team"] == "home"]
    away = [_squad(p) for p in players.values() if p["team"] == "away"]
    # Referee / officials stay on the pitch replay but not in either squad.
    kick_x, kick_y = _kick(frames)
    found, attack_right = (events or {}).get("events", []), (events or {}).get("attack_right") or {}
    events = sorted([
        _event(0.0, "kick_off", kick_x, kick_y, "0-0"),
        *found,
        _event(end, "period_end", kick_x, kick_y, "0-0", period_name="clip end"),
    ], key=lambda e: (e["match_seconds"], e["type"] == "period_end"))
    for i, e in enumerate(events):
        e["event_id"] = i
    meta = {
        "match_id": "VISION",
        "synthetic": False,
        "source": "vision",
        "provider": "vision",
        "data_label": "Computer-vision tracking",
        "disclaimer": (
            "Player and ball positions estimated from match video (YOLO, ByteTrack, pitch homography). "
            "Coordinates are approximate unless a four-point calibration file was supplied. "
            "Not a licensed tracking feed."
        ),
        "teams": {
            "home": {"name": "Home", "formation": "tracked", "squad": home or [_placeholder("H0", "Home")]},
            "away": {"name": "Away", "formation": "tracked", "squad": away or [_placeholder("A0", "Away")]},
        },
        "half_minutes": 45,
        "added_time_minutes": [0, 0],
        "final_score": {"home": 0, "away": 0},
        "fps": fps,
    }
    if attack_right:
        meta["attack_right"] = attack_right   # engine.assist reads it instead of the home / period rule
    write_match(folder, events, meta, rows=rows)
    return meta


def read_video(path: str, seconds: float, sample_fps: float, max_width: int | None = None):
    """Yield RGB frames. Imports OpenCV only when a real file is opened.

    CPU path downsamples wide frames (VISION_MAX_WIDTH, default 960) so
    yolo11n/yolov8n stay interactive without a GPU.
    """
    import cv2
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video {path}.")
    native = capture.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(native / sample_fps)))
    limit = int(seconds * native) if seconds else 10 ** 9
    if max_width is None:
        raw = os.getenv("VISION_MAX_WIDTH", "960")
        max_width = int(raw) if str(raw).isdigit() else 960
        if (os.getenv("VISION_DEVICE") or "cpu").startswith("cuda"):
            max_width = int(os.getenv("VISION_GPU_MAX_WIDTH") or max_width or 1280)
    index = 0
    try:
        while index < limit:
            ok, frame = capture.read()
            if not ok:
                break
            if index % step == 0:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                if max_width and rgb.shape[1] > max_width:
                    scale = max_width / rgb.shape[1]
                    rgb = cv2.resize(rgb, (max_width, max(1, int(round(rgb.shape[0] * scale)))))
                yield rgb
            index += 1
    finally:
        capture.release()


MIN_SHOT_SAMPLES = 6   # fewer jersey samples in a shot: cluster it with the whole clip


def _label_teams(pending, shot_of: dict | None = None):
    """Return (rows, clusters) with home/away/referee assigned.

    Colours are clustered per shot when ``shot_of`` is given (a montage mixes matches and kits); grass-coloured
    crops are left out of the clustering. Player ids carry the shot after the first (``H7s2``) because a reset
    tracker reuses ids after a cut.
    """
    shot_of = shot_of or {}
    groups: dict[int, list] = {}
    for row in pending:
        groups.setdefault(shot_of.get(row[0], 0), []).append(row)
    samples_by_shot = {sid: _samples(rows) for sid, rows in groups.items()}
    pooled = [x for sid in samples_by_shot for x in samples_by_shot[sid]]
    rows_out, legend, per_shot = {}, None, {}
    for sid, rows in groups.items():
        samples = samples_by_shot[sid] if len(samples_by_shot[sid]) >= MIN_SHOT_SAMPLES else pooled
        out, clusters = _label_group(rows, samples, sid)
        rows_out.update({r[0]: r for r in out})
        per_shot[sid] = clusters
        if legend is None or len(samples_by_shot[sid]) > legend[0]:
            legend = (len(samples_by_shot[sid]), clusters)
    clusters = dict(legend[1]) if legend else _cluster_legend([], [], 0, [])
    if len(per_shot) > 1:
        clusters["by_shot"] = per_shot
    return [rows_out[index] for index, *_ in pending], clusters


def _samples(rows) -> list:
    """(det, colour, foot) for each person on a mapped frame whose crop is not grass."""
    out = []
    for index, image, detections, matrix in rows:
        if matrix is None:
            continue
        for det in detections:
            if det.get("cls") != PERSON or det.get("role") in ("referee", "goalkeeper"):
                continue   # the football detector names them; they would skew the two kit colours
            foot = _ground(det["xyxy"], PERSON)
            if not _on_pitch(*apply(matrix, *foot)):
                continue   # stewards, staff and fans in the stands would form a "team" of their own
            color = det.get("color") or _jersey_color(image, det["xyxy"])
            det["color"] = color
            det["kit"] = det.get("kit") or _kit(image, det["xyxy"], color)
            if not _grassy(color):
                out.append((det, color, foot))
    return out


def _label_group(rows, samples, shot_id: int):
    colours = [c for _, c, _ in samples]
    kits = [det["kit"] for det, _, _ in samples]
    labels = _two_means(kits) if kits else []
    centers = _centers(colours, labels) if colours else []
    kit_centers = [tuple(sum(k[d] for k, lab in zip(kits, labels) if lab == want) / max(1, labels.count(want))
                         for d in range(len(kits[0]))) for want in (0, 1)] if kits else []
    label_of = {id(det): lab for (det, _, _), lab in zip(samples, labels)}
    left = {0: [], 1: []}
    for (_, _, foot), label in zip(samples, labels):
        if label in left:
            left[label].append(foot[0])
    mean0 = sum(left[0]) / len(left[0]) if left[0] else 1e9
    mean1 = sum(left[1]) / len(left[1]) if left[1] else 1e9
    home_label = 0 if mean0 <= mean1 else 1
    suffix = f"s{shot_id}" if shot_id else ""
    out = []
    for index, image, detections, matrix in rows:
        players, ball = [], None
        extras = {"ball_confidence": None}
        if matrix is not None:
            for det in detections:
                kind = det.get("cls", PERSON)
                if kind == BALL:
                    x, y = apply(matrix, *_ground(det["xyxy"], BALL))
                    if _on_pitch(x, y):
                        ball = (x, y)
                        extras["ball_confidence"] = float(det.get("confidence", 0.7))
                        extras["ball_xyxy"] = [round(float(v), 1) for v in det["xyxy"]]
                    continue
                color = det.get("color") or _jersey_color(image, det["xyxy"])
                label = label_of.get(id(det))
                if label is None:   # grass-coloured crop or from another shot's pool: nearest kit centre
                    f = det.get("kit") or _kit(image, det["xyxy"], color)
                    label = min(range(len(kit_centers)), key=lambda k: sum((a - b) ** 2 for a, b in zip(f, kit_centers[k]))) if kit_centers else 0
                x, y = apply(matrix, *_ground(det["xyxy"], PERSON))
                if not _on_pitch(x, y):
                    continue
                detected = det.get("role")
                if detected == "referee" or (detected is None and _is_referee(color, centers)):
                    team, role, prefix, name = REF, "REF", "R", "Referee"
                elif detected == "goalkeeper":   # home starts on the left, so it defends x = 0
                    team = "home" if x < 52.5 else "away"
                    role, prefix = "GK", "H" if team == "home" else "A"
                    name = "Home" if team == "home" else "Away"
                else:
                    team = "home" if label == home_label else "away"
                    role = "OUT"
                    prefix = "H" if team == "home" else "A"
                    name = "Home" if team == "home" else "Away"
                track = int(det.get("track_id") or len(players) + 1)
                pid = f"{prefix}{track}{suffix}"
                players.append({
                    "player_id": pid, "team": team, "x": x, "y": y,
                    "number": track, "name": f"{name} {track}",
                    "role": role, "color": color,
                    "confidence": float(det.get("confidence", 0.7)),
                    "occluded": bool(det.get("occluded")),
                    "new_id": det.get("track_id") is None,
                    "xyxy": [round(float(v), 1) for v in det["xyxy"]],   # image box: touches and the freeze frame
                })
        out.append((index, players, ball, extras))
    return out, _cluster_legend(colors=colours, labels=labels, home_label=home_label, centers=centers)


def _grassy(color) -> bool:
    """A mean crop colour that is mostly pitch (the real-clip "home" kit was grass, rgb(141, 160, 134))."""
    if not color:
        return False
    r, g, b = color[:3]
    return g > r + 10 and g > b + 10


def _keepers(labelled) -> set[str]:
    """The deepest player per team, unless the detector already labelled goalkeepers."""
    if any(p.get("role") == "GK" for row in labelled for p in row[1]):
        return set()
    xs: dict[str, list[float]] = {}
    teams: dict[str, str] = {}
    for row in labelled:
        players = row[1]
        for player in players:
            if player.get("role") == "REF" or player.get("team") == REF:
                continue
            xs.setdefault(player["player_id"], []).append(player["x"])
            teams[player["player_id"]] = player["team"]
    keepers = set()
    for team, pick in (("home", min), ("away", max)):
        side = {pid: sum(values) / len(values) for pid, values in xs.items() if teams.get(pid) == team and values}
        if side:
            keepers.add(pick(side, key=side.get))
    return keepers


def _centers(colours, labels):
    groups = {0: [], 1: []}
    for colour, label in zip(colours, labels):
        groups.setdefault(label, []).append(colour)
    out = []
    for lab in (0, 1):
        rows = groups.get(lab) or []
        if rows:
            out.append(tuple(sum(c[ch] for c in rows) / len(rows) for ch in range(3)))
        else:
            out.append((0.0, 0.0, 0.0))
    return out


def _is_referee(color, centers) -> bool:
    """A leftover kit (black / neon / far from both team centres) is the referee."""
    if not color or len(centers) < 2:
        return False
    dists = [sum((a - b) ** 2 for a, b in zip(color, c)) ** 0.5 for c in centers]
    nearest = min(dists)
    r, g, b = color
    dark = r < 45 and g < 45 and b < 45
    neon = (abs(r - g) < 25 and b < 80 and r > 140) or (g > 180 and r > 180 and b < 80)
    return nearest > 90 and (dark or neon or nearest > 140)


def _cluster_legend(colors, labels, home_label, centers):
    home = centers[home_label] if centers else (43, 85, 208)
    away = centers[1 - home_label] if len(centers) > 1 else (224, 122, 31)
    return {
        "home": [round(v, 1) for v in home],
        "away": [round(v, 1) for v in away],
        "referee": [196.0, 160.0, 0.0],
    }


def _kit(image, xyxy, color) -> tuple:
    """_kit_hist of the box, or of the mean colour alone when the box has no kit pixels (tiny or synthetic)."""
    hist = _kit_hist(image, xyxy)
    if sum(hist):
        return hist
    return _pixel_hist(np.array([color[:3]] * 4, dtype=float)) if sum(color[:3]) else hist


def _kit_hist(image, xyxy) -> tuple:
    """What a kit is made of: shares of white, black, red, yellow, blue and magenta pixels on the torso.

    Mean colour fails on stripes (Newcastle's black and white average to the grey of a pale blue shirt) and
    on light (one team splits into sunlit and shadowed players). Grass pixels are left out."""
    x1, y1, x2, y2 = [int(v) for v in xyxy]
    h, w = image.shape[:2]
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w - 1, x2), min(h - 1, y2)
    top = y1 + max(1, int((y2 - y1) * 0.15))              # below the head
    crop = image[top:y1 + max(2, int((y2 - y1) * 0.55)), x1:x2]
    px = crop.reshape(-1, crop.shape[-1])[:, :3].astype(float) if crop.size else np.zeros((0, 3))
    if len(px):
        r, g, b = px[:, 0], px[:, 1], px[:, 2]
        px = px[~((g > r + 15) & (g > b + 15))]
    return _pixel_hist(px)


def _pixel_hist(px) -> tuple:
    if len(px) < 4:
        return (0.0,) * 6
    mx, mn = px.max(axis=1), px.min(axis=1)
    v, s = mx / 255.0, np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    r, g, b = px[:, 0], px[:, 1], px[:, 2]
    hue = (np.degrees(np.arctan2(np.sqrt(3) * (g - b), 2 * r - g - b)) + 360) % 360
    colour = (s >= 0.15) & (v >= 0.3)   # a pale sky-blue shirt has saturation about 0.2: colour, not white
    bins = [(v > 0.65) & (s < 0.15), v < 0.3,
            colour & ((hue < 25) | (hue >= 335)), colour & (hue >= 25) & (hue < 75),
            colour & (hue >= 165) & (hue < 255), colour & (hue >= 255) & (hue < 335)]
    return tuple(255.0 * float(m.mean()) for m in bins)


def _two_means(colours: list[tuple]) -> list[int]:
    if len(set(colours)) < 2:
        return [0] * len(colours)
    dim = len(colours[0])
    dist = lambda a, b: sum((x - y) ** 2 for x, y in zip(a, b))
    first = colours[0]   # start from the two kits furthest apart, not from whichever came first
    second = max(colours, key=lambda c: dist(c, first))
    first = max(colours, key=lambda c: dist(c, second))
    center = [first, second]
    labels = [0] * len(colours)
    for _ in range(8):
        groups = [[], []]
        for i, colour in enumerate(colours):
            dist = [sum((a - b) ** 2 for a, b in zip(colour, c)) for c in center]
            labels[i] = 0 if dist[0] <= dist[1] else 1
            groups[labels[i]].append(colour)
        for lab in (0, 1):
            if groups[lab]:
                center[lab] = tuple(sum(c[ch] for c in groups[lab]) / len(groups[lab]) for ch in range(dim))
    return labels


def _jersey_color(image, xyxy) -> tuple:
    x1, y1, x2, y2 = [int(v) for v in xyxy]
    h, w = image.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w - 1, x2), min(h - 1, y2)
    if x2 <= x1 or y2 <= y1:
        return (0, 0, 0)
    crop = image[y1:y1 + max(1, (y2 - y1) // 2), x1:x2]
    if crop.size == 0:
        return (0, 0, 0)
    pixels = crop.reshape(-1, crop.shape[-1])[:, :3].astype(float)
    r, g, b = pixels[:, 0], pixels[:, 1], pixels[:, 2]
    grass = (g > r + 15) & (g > b + 15)            # small players: the box is mostly grass
    dark = pixels.max(axis=1) < 70                  # hair, shadow and socks say nothing about the kit
    kit = pixels[~grass & ~dark]
    mean = (kit if len(kit) >= max(4, len(pixels) // 10) else pixels).mean(axis=0)
    return tuple(float(v) for v in mean)


def _ground(xyxy, kind: str) -> tuple[float, float]:
    x1, y1, x2, y2 = xyxy
    if kind == BALL:
        return (x1 + x2) / 2, (y1 + y2) / 2
    return (x1 + x2) / 2, y2


def _on_pitch(x: float, y: float) -> bool:
    return -5 <= x <= 110 and -5 <= y <= 73


def _row(frame, oid, team, x, y, poss, in_play) -> dict:
    return {
        "frame": frame["frame"], "period": frame["period"], "match_seconds": frame["match_seconds"],
        "clock": frame["clock"], "object_id": oid, "team": team, "x": x, "y": y,
        "ball_possession": poss, "ball_in_play": in_play,
    }


def _squad(player: dict) -> dict:
    return {
        "player_id": player["player_id"],
        "name": player["name"],
        "number": player["number"],
        "role": player.get("role") or "OUT",
    }


def _placeholder(pid: str, side: str) -> dict:
    return {"player_id": pid, "name": side, "number": 0, "role": "OUT"}


def _kick(frames: list[dict]) -> tuple[float, float]:
    for frame in frames:
        if frame.get("ball"):
            return frame["ball"]["x"], frame["ball"]["y"]
    return 52.5, 34.0


def _event(seconds: float, etype: str, x: float, y: float, score: str, **extra) -> dict:
    return {
        "match_id": "VISION", "event_id": 0, "period": 1,
        "clock": format_clock(1, seconds), "minute": format_minute(1, seconds),
        "match_seconds": seconds, "type": etype, "team": None, "team_name": None,
        "player_id": None, "player_name": None, "x": x, "y": y, "score": score,
        "synthetic": False, **extra,
    }

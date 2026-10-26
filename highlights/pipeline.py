"""Run one highlight job: ingest → track → events → analyse → store.

The API thread and ``python -m highlights.worker`` both call ``process_job``.
Demo jobs never import torch. A real upload without the vision extras fails
with a clear error instead of drawing synthetic tracks on someone else's clip.
"""
from __future__ import annotations

import os
from pathlib import Path

from engine.decisions import analyse
from engine.explain import explain
from engine.game_explain import build_explanations
from engine.moments import Match
from feeds.store import write_match
from highlights.demo import demo_frames, demo_meta
from highlights.events import derive_events, frames_to_rows
from highlights.notify import emit
from highlights.pitch_view import build_pitch_views, caption_moment
from highlights.store import _job_dir, get_job, load_result, save_result, update, video_path


class PipelineError(RuntimeError):
    pass


def process_job(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise PipelineError(f"No highlight job {job_id}")
    try:
        _step(job_id, 5, "ingest", "Loading the clip.")
        debug = None
        if job.get("source") == "demo":
            frames, meta = _demo()
            video_kind = "schematic"
            has_video = False
        else:
            path = video_path(job_id)
            if path is None:
                raise PipelineError("No video file on this job. Upload a clip or run the demo.")
            frames, meta, debug = _track_video(path)
            video_kind = "upload"
            has_video = True

        _step(job_id, 45, "events", "Deriving events from the tracks.")
        events = derive_events(frames, teams={
            "home": meta["teams"]["home"]["name"],
            "away": meta["teams"]["away"]["name"],
        }, match_id=meta["match_id"])
        if debug and debug.get("vision_events") is not None:
            events = _merge_vision_events(events, debug.pop("vision_events"))

        teams = {k: v["name"] for k, v in meta["teams"].items()}

        _step(job_id, 70, "analyse", "Scoring decisions on the clip.")
        moments, analyses = _analyse(job_id, events, frames, meta)

        _step(job_id, 78, "pitch_view", "Building the Broadcast / Analyst replay.")
        reconstructed = video_kind == "upload"
        confidence = _mean_confidence(frames)
        if not reconstructed:
            confidence = max(confidence, 0.99)
        pitch_view = build_pitch_views(
            frames, events, meta, _job_dir(job_id) / "match",
            reconstructed=reconstructed, confidence=confidence,
        )
        if pitch_view.get("views"):
            for view in pitch_view["views"]:
                ev = next((e for e in events if e.get("type") == "shot"), None)
                cap = caption_moment(ev or {}, teams)
                if cap.get("title"):
                    view["moment"]["title"] = cap["title"]
                if cap.get("home"):
                    teams["home"] = cap["home"]
                    meta["teams"]["home"]["name"] = cap["home"]
                    view["teams"]["home"] = cap["home"]
                if cap.get("away"):
                    teams["away"] = cap["away"]
                    meta["teams"]["away"]["name"] = cap["away"]
                    view["teams"]["away"] = cap["away"]
            pitch_view["moments"] = [v["moment"] for v in pitch_view["views"]]
        debug = debug or _demo_debug(frames, teams)

        _step(job_id, 80, "explain", "Writing moment-by-moment explanations.")
        explanations = build_explanations(
            frames, events, teams, analyses=analyses,
            source=meta.get("source"), synthetic=bool(meta.get("synthetic")),
            job_id=job_id,
        )

        _step(job_id, 90, "store", "Saving the replay.")
        duration = float(frames[-1]["match_seconds"]) if frames else 0.0
        steps = [
            {"name": "ingest", "ok": True},
            {"name": "detect", "ok": True},
            {"name": "track", "ok": True},
            {"name": "homography", "ok": True},
            {"name": "teams", "ok": True},
            {"name": "events", "ok": True},
            {"name": "pitch_view", "ok": bool(pitch_view.get("views"))},
            {"name": "explain", "ok": True},
        ]
        result = {
            "job_id": job_id,
            "synthetic": bool(meta.get("synthetic")),
            "source": meta.get("source"),
            "badge": "Reconstructed from video · approximate motion" if reconstructed else "Schematic demo",
            "reconstructed": reconstructed,
            "confidence": confidence,
            "video_kind": video_kind,
            "has_video": has_video,
            "fps": meta.get("fps") or 5,
            "duration": duration,
            "teams": teams,
            "disclaimer": meta["disclaimer"],
            "frames": frames,
            "events": events,
            "moments": moments or pitch_view.get("moments") or [],
            "analyses": analyses,
            "explanations": explanations,
            # what the upload page's "Extracted data" panel reads: scene model output and the passing formulas
            "scene": (debug or {}).pop("scene", None),
            "scene_hints": (debug or {}).pop("scene_hints", None),
            "assist": _assist(_job_dir(job_id) / "match", frames, (debug or {}).get("frame_size"))
            if video_kind == "upload" else None,
            "pitch_view": pitch_view,
            "debug": debug,
            "steps": steps,
        }
        save_result(job_id, result)
        update(job_id, status="done", step="done", progress=100, message="Ready.",
               video_kind=video_kind, has_video=has_video, error=None)
        emit(job_id, {"status": "done", "step": "done", "progress": 100, "message": "Ready."})
        return result
    except Exception as exc:
        update(job_id, status="error", step="error", progress=100, message=str(exc), error=str(exc))
        emit(job_id, {"status": "error", "step": "error", "progress": 100, "message": str(exc)})
        raise


def _step(job_id: str, progress: int, step: str, message: str) -> None:
    update(job_id, status="running", step=step, progress=progress, message=message)
    emit(job_id, {"status": "running", "step": step, "progress": progress, "message": message})


def _demo() -> tuple[list[dict], dict]:
    return demo_frames(), demo_meta()


def _track_video(path: Path) -> tuple[list[dict], dict, dict]:
    job_id = path.parent.name
    _step(job_id, 15, "detect", "Detecting players and the ball.")
    seconds = float(os.getenv("HIGHLIGHT_SECONDS") or 20)
    fps = float(os.getenv("HIGHLIGHT_FPS") or 5)
    if os.getenv("VISION_URL") and not _local_vision():
        tracked, debug = _track_remote(path, seconds, fps)
    else:
        tracked, debug = _track_local(path, seconds, fps)
    if not tracked:
        raise PipelineError("The vision pipeline produced no frames.")
    from vision.events import detect_events
    from vision.pipeline import write_tracked
    if not any(f.get("mapped") and f.get("players") for f in tracked):
        raise PipelineError("No frame of this clip could be mapped onto the pitch: it shows close-ups or a crop "
                            "(for example a vertical phone video) without enough pitch lines. Upload a wide "
                            "broadcast shot of the play.")
    found = detect_events(tracked, fps, hints=(debug or {}).get("scene_hints"))
    if _local_vision() and not debug.get("dense"):   # the vision service densifies its own frames
        from vision.freeze import densify_passes
        _step(path.parent.name, 42, "freeze", "Re-reading each pass frame at full resolution.")
        debug["dense"] = densify_passes(tracked, found["events"], str(path), debug.get("frame_size"))
    meta = write_tracked(path.parent / "match", tracked, fps=fps, events=found)
    debug["vision_events"] = found["events"]   # per camera play; replaces derive_events' pass / shot / goal
    debug["plays"] = found["debug"].get("plays", [])
    return tracked, meta, debug


def _local_vision() -> bool:
    """OpenCV and Ultralytics importable here (the vision image, or a local install)."""
    from importlib.util import find_spec
    return bool(find_spec("cv2") and find_spec("ultralytics"))


def _track_local(path: Path, seconds: float, fps: float) -> tuple[list[dict], dict]:
    if not _local_vision():
        raise PipelineError(
            "This clip needs the vision worker (YOLO + ByteTrack). "
            "Use “Use demo clip” for a GPU-free walkthrough, set VISION_URL to the vision service, or run "
            "`pip install -r vision/requirements.txt` and `python -m highlights.worker`."
        )
    from vision.homography import load_calibration
    from vision.detectors import detector_info, make_detector
    from vision.pipeline import read_video, track_frames
    frames_rgb = list(read_video(str(path), seconds=seconds, sample_fps=fps))
    if not frames_rgb:
        raise PipelineError(f"Could not read any frames from {path.name}.")
    _step(path.parent.name, 30, "track", "Tracking players and mapping them onto the pitch.")
    debug: dict = {}
    detector = make_detector()
    tracked = list(track_frames(frames_rgb, detector, fps=fps,
                                calibration=load_calibration(os.getenv("VISION_CALIBRATION")), debug=debug))
    debug["detector"] = detector_info(detector)
    _step(path.parent.name, 40, "scene", "Reading the scene (shot types, posture, lofted passes).")
    from vision.scene_llm import scene_for
    scene = scene_for(frames_rgb, tracked, fps, debug=debug)
    debug["scene"], debug["scene_hints"] = scene["scene"], scene["scene_hints"]
    return tracked, debug


def _track_remote(path: Path, seconds: float, fps: float, client=None) -> tuple[list[dict], dict]:
    """Send the clip to the vision service (VISION_URL) and poll until its tracking is ready.

    The service scales to zero, so the first upload waits for a cold start; it is retried until VISION_TIMEOUT_S.
    """
    import time

    import httpx

    base = os.environ["VISION_URL"].rstrip("/")
    job_id = path.parent.name
    deadline = time.monotonic() + float(os.getenv("VISION_TIMEOUT_S") or 900)
    http = client or httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0))
    try:
        remote = None
        while remote is None:
            try:
                with path.open("rb") as handle:
                    r = http.post(f"{base}/track", files={"file": (path.name, handle, "video/mp4")},
                                  data={"seconds": str(seconds), "fps": str(fps)})
                if r.status_code < 500:
                    r.raise_for_status()
                    remote = r.json()["job_id"]
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                raise PipelineError("The vision service did not start in time. Try again in a minute.")
            _step(job_id, 20, "detect", "Waking the vision service.")
            time.sleep(5)
        while True:
            s = http.get(f"{base}/jobs/{remote}").json()
            if s["status"] == "error":
                raise PipelineError(f"Vision service: {s['error']}")
            if s["status"] == "done":
                break
            if time.monotonic() > deadline:
                raise PipelineError("The vision service timed out on this clip.")
            _step(job_id, 30, "track", f"Tracking players on the vision service ({s['frames']} frames).")
            time.sleep(2)
        result = http.get(f"{base}/jobs/{remote}/result")
        result.raise_for_status()
        body = result.json()
        return body["frames"], body.get("debug") or {}
    finally:
        if client is None:
            http.close()


KEY_TYPES = ("pass", "shot", "goal")


def _merge_vision_events(derived: list[dict], vision: list[dict]) -> list[dict]:
    """derive_events reads a clip as one play, so a montage gets one attack direction and goals at the wrong end.
    vision.events reads each camera play on its own: take its passes, shots and goals, keep the rest
    (possession, pressing, interceptions) for the explanations, and keep the score running across plays."""
    out = [e for e in derived if e.get("type") not in KEY_TYPES]
    for e in vision:
        e = dict(e)
        if e["type"] == "pass":
            e.setdefault("length_m", round(((e["end_x"] - e["x"]) ** 2 + (e["end_y"] - e["y"]) ** 2) ** 0.5, 1))
            e.setdefault("pass_type", "open_play")
        if e["type"] == "shot":
            e.setdefault("body_part", None)
            e.setdefault("shot_type", "open_play")
            goal_x = 105.0 if e.get("attack_right") else 0.0
            e.setdefault("distance_m", round(((goal_x - e["x"]) ** 2 + (34 - e["y"]) ** 2) ** 0.5, 1))
        out.append(e)
    out.sort(key=lambda e: (e["match_seconds"], e.get("type") == "period_end"))
    home = away = 0
    for e in out:
        if e.get("type") == "goal":
            home, away = (home + 1, away) if e.get("team") == "home" else (home, away + 1)
        e["score"] = f"{home}-{away}"
    return out


def _assist(folder: Path, frames: list[dict] | None = None, frame_size=None) -> list[dict]:
    """engine.assist (the formulas in docs/how-it-works.md) for each goal or chance with a detected assist.

    Each carries ``freeze``: the real frame at the pass (time, image boxes of every tracked player and the ball)
    so the upload page can show the percentages on the broadcast image instead of a reconstructed replay."""
    if not (folder / "match_meta.json").exists():
        return []
    from engine.assist import build_assist
    match = Match(folder)
    out = []
    for m in match.moments:
        if m.kind in ("goal", "chance") and m.assist_pass:
            try:
                a = build_assist(match, m)
            except Exception:   # a partial freeze frame from video: show nothing rather than fail the job
                a = None
            if a:
                freeze = _freeze(frames or [], m.assist_pass, frame_size)
                if freeze:
                    freeze.update(team=m.event["team"], attack_right=a["attack_right"])
                out.append({"moment_id": m.id, "title": m.title, **a, "freeze": freeze})
    return out


def _freeze(frames: list[dict], p: dict, frame_size) -> dict | None:
    if not frames or not frame_size:
        return None
    k = min(range(len(frames)), key=lambda i: abs(frames[i]["match_seconds"] - p["match_seconds"]))
    f = frames[k]
    from vision.pitch_keypoints import project_landmarks
    h = f.get("homography")
    return {"t": f["match_seconds"], "frame_size": frame_size, "passer_id": p["player_id"],
            "receiver_id": p.get("receiver_at_pass") or p["receiver_id"],
            "ball": (f.get("ball") or {}).get("xyxy"),
            "homography": h,   # image -> metres; the editor drags these landmarks to correct it
            "landmarks": project_landmarks(h, frame_size) if h else [],
            "lofted": bool(p.get("aerial")), "into_space": bool(p.get("into_space")),
            "end": [p.get("end_x"), p.get("end_y")],
            "dense": f.get("dense"),   # full-resolution re-read: players added, and whether the mapping holds
            "boxes": [{"player_id": q["player_id"], "team": q["team"], "role": q.get("role"), "xyxy": q["xyxy"]}
                      for q in f["players"] if q.get("xyxy")]}


def _mean_confidence(frames: list[dict]) -> float:
    vals = []
    for frame in frames:
        for player in frame.get("players") or []:
            if player.get("confidence") is not None:
                vals.append(float(player["confidence"]))
        ball = frame.get("ball") or {}
        if isinstance(ball, dict) and ball.get("confidence") is not None:
            vals.append(float(ball["confidence"]))
    if not vals:
        return 0.86
    return round(sum(vals) / len(vals), 3)


def _demo_debug(frames: list[dict], teams: dict) -> dict:
    keypoints = []
    detections = []
    for frame in frames[::5]:
        t = float(frame.get("match_seconds") or 0)
        keypoints.append({
            "frame": frame.get("frame", 0), "t": t,
            "image_points": [[40, 20], [200, 20], [220, 140], [20, 140]],
            "method": "schematic", "ok": True,
        })
        boxes = []
        for player in frame.get("players") or []:
            boxes.append({
                "cls": "person", "track_id": player.get("number") or 0,
                "xyxy": [player["x"], 68 - player["y"], player["x"] + 4, 72 - player["y"]],
                "confidence": player.get("confidence", 0.99),
                "team": player.get("team"),
            })
        detections.append({"frame": frame.get("frame", 0), "t": t, "boxes": boxes})
    return {
        "detections": detections,
        "keypoints": keypoints,
        "clusters": {
            "home": [43, 85, 208],
            "away": [224, 122, 31],
            "referee": [196, 160, 0],
        },
        "confidence": _mean_confidence(frames),
        "teams": teams,
        "method": "schematic",
    }


def _analyse(job_id: str, events: list[dict], frames: list[dict], meta: dict) -> tuple[list, list]:
    folder = _job_dir(job_id) / "match"
    rows = frames_to_rows(frames)
    # write_match assigns event_id; work on a copy so the stored result stays readable.
    written = [dict(event) for event in events]
    if written:
        write_match(folder, written, meta, rows=rows)
        match = Match(folder)
        moments = [m.summary() for m in match.moments]
        analyses = []
        for moment in match.moments:
            if moment.kind not in ("goal", "chance"):
                continue
            result = analyse(match, moment.id, "balanced")
            result["text"] = explain(result)
            analyses.append(result)
        return moments, analyses
    write_match(folder, [], meta, rows=rows)
    return [], []


def result_or_none(job_id: str) -> dict | None:
    return load_result(job_id)



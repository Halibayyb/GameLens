"""Vision HTTP service for a local CPU run or an Azure Container Apps GPU replica.

    uvicorn vision.server:app --host 0.0.0.0 --port 8000

POST /jobs with ``{"video_path": "clip.mp4"}`` or ``{"blob_url": "https://....blob.core.windows.net/..."}``,
or POST /track with the clip as a multipart upload (the API sends uploads this way).
The job runs in the background. ``GET /jobs/{id}`` reports status, ``GET /jobs/{id}/result`` returns
the tracking frames and the debug overlay once it is done. ``WS /ws?job_id=``
streams tracking frames as they are produced. Scale-to-zero is a property of the
container app (min replicas 0); this process itself just serves one clip at a time.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile, WebSocket, WebSocketDisconnect

app = FastAPI(title="Matchday vision", version="0.1.0")
JOBS: dict[str, dict] = {}
OUT = Path(os.getenv("VISION_OUT", "/tmp/vision"))


@app.get("/health")
def health():
    """Liveness plus what a job would run: detector, pitch mapping and scene model (name only, never keys)."""
    from vision.detectors import describe
    from vision.scene_llm import config
    cfg, why = config()
    scene = {"model": cfg["model"], "api": cfg["api"], "provider": cfg.get("provider")} if cfg else {"skipped": why}
    return {"ok": True, **describe(), "scene": scene}


@app.post("/jobs")
def create_job(body: dict):
    video_path = body.get("video_path")
    blob_url = body.get("blob_url")
    if not video_path and not blob_url:
        raise HTTPException(400, "Send video_path or blob_url.")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "running", "frames": [], "error": None, "folder": str(OUT / job_id)}
    thread = threading.Thread(target=_run, args=(job_id, body), daemon=True)
    thread.start()
    return {"job_id": job_id, "status": "running"}


@app.post("/track")
def track_upload(file: UploadFile = File(...), seconds: float = Form(20), fps: float = Form(5)):
    job_id = uuid.uuid4().hex[:12]
    folder = OUT / job_id
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / ("input" + (Path(file.filename or "").suffix or ".mp4"))
    with dest.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    JOBS[job_id] = {"status": "running", "frames": [], "error": None, "folder": str(folder / "match")}
    body = {"video_path": str(dest), "seconds": seconds, "fps": fps}
    threading.Thread(target=_run, args=(job_id, body), daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@app.get("/jobs/{job_id}/result")
def job_result(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "No such job")
    if job["status"] != "done":
        raise HTTPException(409, f"Job is {job['status']}")
    payload = {"frames": job["frames"], "debug": job.get("debug") or {}}
    # debug carries numpy scalars (team colours, confidences)
    return Response(json.dumps(payload, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)),
                    media_type="application/json")


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "No such job")
    return {"job_id": job_id, "status": job["status"], "frames": len(job["frames"]), "error": job["error"], "folder": job["folder"]}


@app.websocket("/ws")
async def stream(ws: WebSocket, job_id: str):
    await ws.accept()
    job = JOBS.get(job_id)
    if not job:
        await ws.send_json({"kind": "error", "error": "No such job"})
        await ws.close()
        return
    sent = 0
    try:
        while True:
            frames = job["frames"]
            while sent < len(frames):
                await ws.send_json(frames[sent])
                sent += 1
            if job["status"] != "running":
                if job["error"]:
                    await ws.send_json({"kind": "error", "error": job["error"]})
                await ws.close()
                return
            await _sleep()
    except WebSocketDisconnect:
        return


async def _sleep():
    import asyncio
    await asyncio.sleep(0.2)


def _run(job_id: str, body: dict) -> None:
    job = JOBS[job_id]
    try:
        video = body.get("video_path")
        if body.get("blob_url"):
            video = str(_download_blob(body["blob_url"], OUT / job_id / "input"))
        seconds = float(body.get("seconds") or os.getenv("VISION_SECONDS", "8"))
        folder = Path(job["folder"])
        # Stream frames into the job list, then also write the match folder.
        from vision.detectors import detector_info, make_detector
        from vision.pipeline import read_video, track_frames, write_tracked
        from vision.homography import load_calibration
        detector = make_detector()
        fps = float(body.get("fps") or 5)
        from vision.events import detect_events
        from vision.scene_llm import scene_for
        frames, debug = [], {}
        images = list(read_video(video, seconds=seconds, sample_fps=fps))   # kept for the scene model's keyframes
        for frame in track_frames(
            images,
            detector,
            fps=fps,
            calibration=load_calibration(body.get("calibration")),
            debug=debug,
        ):
            frames.append(frame)
            job["frames"].append(frame)
        debug["detector"] = detector_info(detector)
        job["status_detail"] = "scene"
        scene = scene_for(images, frames, fps, debug=debug)
        debug["scene"], debug["scene_hints"] = scene["scene"], scene["scene_hints"]
        found = detect_events(frames, fps, hints=scene["scene_hints"])
        from vision.freeze import densify_passes
        debug["dense"] = densify_passes(frames, found["events"], video, debug.get("frame_size"))
        job["frames"] = frames   # densified pass frames replace their tracked copies
        write_tracked(folder, frames, fps=fps, events=found)
        job["debug"] = debug
        job["status"] = "done"
    except Exception as exc:  # noqa: BLE001 - surface the failure on the job
        job["error"] = str(exc)
        job["status"] = "error"


def _download_blob(url: str, dest: Path) -> Path:
    """Download one video. Managed identity when the account is set and there is no connection string."""
    from urllib.parse import urlparse

    dest.parent.mkdir(parents=True, exist_ok=True)
    target = dest if dest.suffix else dest.with_suffix(".mp4")
    connection = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    account = os.getenv("AZURE_STORAGE_ACCOUNT")
    if connection or account:
        from azure.storage.blob import BlobClient
        if connection:
            parsed = urlparse(url)
            container, _, blob = parsed.path.lstrip("/").partition("/")
            client = BlobClient.from_connection_string(connection, container_name=container, blob_name=blob)
        else:
            from azure.identity import DefaultAzureCredential
            client = BlobClient.from_blob_url(url, credential=DefaultAzureCredential())
        with target.open("wb") as handle:
            handle.write(client.download_blob().readall())
        return target
    import urllib.request
    urllib.request.urlretrieve(url, target)
    return target

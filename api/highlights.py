"""Highlight-clip jobs. Mounted at ``/api/highlights``.

The API is the orchestrator. A local upload starts a background thread. On
Azure, Event Grid can POST blob-created events here; the same path then
runs the job (or leaves it queued for ``python -m highlights.worker``).
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from urllib.parse import urlparse, unquote

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, Response, StreamingResponse

from engine.game_context import build_context
from engine.game_explain import DEPTHS, azure_speech, explain_moment, narration_text, speech_configured
from highlights.notify import emit, subscribe, unsubscribe
from highlights.pipeline import process_job
from highlights.storage import save_from_url, save_upload
from highlights.store import create_job, get_job, list_jobs, load_result, video_path

router = APIRouter(prefix="/api/highlights", tags=["highlights"])
MAX_UPLOAD = 80 * 1024 * 1024
REMOTE = lambda: os.getenv("HIGHLIGHTS_REMOTE_WORKER", "").strip().lower() in ("1", "true", "yes")


def kick(job_id: str) -> None:
    if REMOTE():
        emit(job_id, {"status": "queued", "step": "queued", "progress": 0,
                      "message": "Queued for the highlight worker."})
        return
    threading.Thread(target=_run, args=(job_id,), daemon=True).start()


def _run(job_id: str) -> None:
    try:
        process_job(job_id)
    except Exception:
        return


@router.get("/jobs")
def jobs(limit: int = 20):
    return {"jobs": list_jobs(limit)}


@router.get("/jobs/{job_id}")
def job(job_id: str):
    found = get_job(job_id)
    if not found:
        raise HTTPException(404, "No such highlight job")
    return found


@router.get("/jobs/{job_id}/result")
def result(job_id: str):
    found = get_job(job_id)
    if not found:
        raise HTTPException(404, "No such highlight job")
    if found.get("status") != "done":
        raise HTTPException(409, found.get("message") or "Job is not finished")
    body = load_result(job_id)
    if not body:
        raise HTTPException(404, "No result stored for this job")
    return body


@router.get("/jobs/{job_id}/video")
def video(job_id: str):
    path = video_path(job_id)
    if not path:
        raise HTTPException(404, "This job has no video file (the demo is schematic).")
    media = {".webm": "video/webm", ".mov": "video/quicktime", ".m4v": "video/mp4"}.get(path.suffix.lower(), "video/mp4")
    return FileResponse(path, media_type=media, filename=path.name)


@router.post("/jobs")
async def create(
    source: str = Form("upload"),
    url: str | None = Form(None),
    file: UploadFile | None = File(None),
):
    source = (source or "upload").strip().lower()
    if source == "demo":
        job = create_job("demo", filename="demo")
        kick(job["job_id"])
        return job
    if url:
        job = create_job("url", filename=url.rsplit("/", 1)[-1])
        try:
            save_from_url(job["job_id"], url)
        except Exception as exc:
            raise HTTPException(400, f"Could not fetch that URL: {exc}") from exc
        kick(job["job_id"])
        return job
    if file is not None:
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "Clip is larger than 80 MB.")
        if not data:
            raise HTTPException(400, "Empty file.")
        job = create_job("upload", filename=file.filename or "clip.mp4")
        save_upload(job["job_id"], data, file.filename or "clip.mp4")
        kick(job["job_id"])
        return job
    raise HTTPException(400, "Provide a file, a url, or source=demo.")


@router.get("/jobs/{job_id}/explain")
def explain_at(
    job_id: str,
    t: float | None = Query(None),
    moment_id: str | None = None,
    depth: str = "analyst",
    lang: str = "en",
    refresh: bool = False,
):
    """Look up or build a grounded explanation for a highlight timestamp."""
    result = _ready_result(job_id)
    card = _pick_card(result, t=t, moment_id=moment_id)
    if card is None:
        # On-demand for a raw timestamp (scrub).
        if t is None:
            raise HTTPException(404, "No explanation for this moment")
        event = _event_near(result.get("events") or [], t)
        ctx = build_context(
            result.get("frames") or [], result.get("events") or [], result.get("teams") or {},
            t, event=event, kind=(event or {}).get("type"),
            source=result.get("source"), synthetic=bool(result.get("synthetic")),
        )
        body = explain_moment(ctx, depth, lang, job_id=job_id, use_cache=not refresh)
        return {"id": None, "kind": ctx.get("kind"), "match_seconds": t, "explanation": body,
                "label": "AI-generated from event data", "attribution": ctx.get("attribution")}
    depth = depth if depth in DEPTHS else "analyst"
    body = (card.get("by_depth") or {}).get(depth)
    if body is None or refresh:
        body = explain_moment(card["context"], depth, lang, job_id=job_id, use_cache=not refresh)
        card.setdefault("by_depth", {})[depth] = body
    return {**{k: card.get(k) for k in ("id", "kind", "title", "match_seconds", "clock", "player_name", "team_name")},
            "explanation": body, "label": card.get("label"), "attribution": (card.get("context") or {}).get("attribution")}


@router.get("/jobs/{job_id}/explain/stream")
def explain_stream(job_id: str, t: float = 0, depth: str = "analyst", lang: str = "en"):
    """SSE of one explanation. Templates arrive in one chunk; Azure can stream later."""
    payload = explain_at(job_id, t=t, depth=depth, lang=lang)

    def chunks():
        body = payload.get("explanation") or {}
        # One JSON event so the UI can swap in tokens later without a protocol change.
        yield f"data: {json.dumps(payload)}\n\n"
        yield "data: [DONE]\n\n"
        _ = body

    return StreamingResponse(chunks(), media_type="text/event-stream")


@router.get("/jobs/{job_id}/explain/{moment_id}/speech")
def explain_speech(job_id: str, moment_id: str, depth: str = "analyst"):
    result = _ready_result(job_id)
    card = _pick_card(result, moment_id=moment_id)
    if not card:
        raise HTTPException(404, "No such explanation")
    text = narration_text(card, depth)
    audio = azure_speech(text) if speech_configured() else None
    if audio:
        return Response(audio, media_type="audio/mpeg")
    return {"text": text, "speech": "browser", "note": "Azure Speech is optional; the browser can read this aloud."}


@router.post("/jobs/{job_id}/run")
def run(job_id: str):
    found = get_job(job_id)
    if not found:
        raise HTTPException(404, "No such highlight job")
    kick(job_id)
    return get_job(job_id)


@router.post("/hooks/blob")
async def blob_hook(request: Request):
    """Event Grid webhook: BlobCreated on the ``highlights`` container.

    Handles ``SubscriptionValidationEvent``. Jobs the API already started are
    left alone so an upload-then-replicate does not run twice.
    """
    body = await request.json()
    events = body if isinstance(body, list) else [body]
    if events and events[0].get("eventType") == "Microsoft.EventGrid.SubscriptionValidationEvent":
        return {"validationResponse": events[0]["data"]["validationCode"]}
    started = []
    for event in events:
        if event.get("eventType") != "Microsoft.Storage.BlobCreated":
            continue
        data = event.get("data") or {}
        url = data.get("url") or data.get("blobUrl")
        if not url:
            continue
        job_id, filename = _blob_parts(url)
        existing = get_job(job_id) if job_id else None
        if existing:
            if existing.get("status") == "queued":
                kick(existing["job_id"])
                started.append(existing["job_id"])
            continue
        job = create_job("blob", filename=filename, job_id=job_id)
        try:
            save_from_url(job["job_id"], url)
        except Exception:
            pass
        kick(job["job_id"])
        started.append(job["job_id"])
    return {"ok": True, "started": started}


@router.websocket("/jobs/{job_id}/live")
async def live(ws: WebSocket, job_id: str):
    await ws.accept()
    found = get_job(job_id)
    if not found:
        await ws.send_json({"kind": "error", "error": "No such highlight job"})
        await ws.close()
        return
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_progress(payload: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, payload)

    subscribe(job_id, on_progress)
    await ws.send_json({"kind": "highlight", "job_id": job_id, **found})
    if found.get("status") in ("done", "error"):
        unsubscribe(job_id, on_progress)
        await ws.close()
        return
    try:
        while True:
            message = await queue.get()
            await ws.send_json(message)
            if message.get("status") in ("done", "error"):
                break
    except Exception:
        pass
    finally:
        unsubscribe(job_id, on_progress)
        try:
            await ws.close()
        except Exception:
            pass


def _ready_result(job_id: str) -> dict:
    found = get_job(job_id)
    if not found:
        raise HTTPException(404, "No such highlight job")
    if found.get("status") != "done":
        raise HTTPException(409, found.get("message") or "Job is not finished")
    body = load_result(job_id)
    if not body:
        raise HTTPException(404, "No result stored for this job")
    return body


def _pick_card(result: dict, t: float | None = None, moment_id: str | None = None) -> dict | None:
    cards = result.get("explanations") or []
    if moment_id:
        return next((c for c in cards if c.get("id") == moment_id), None)
    if t is None:
        return cards[0] if cards else None
    reached = [c for c in cards if c["match_seconds"] <= t + 0.35]
    return reached[-1] if reached else (cards[0] if cards else None)


def _event_near(events: list[dict], t: float) -> dict | None:
    best = None
    for event in events:
        if event.get("type") == "period_end":
            continue
        if event["match_seconds"] <= t + 0.4:
            best = event
    return best


def _blob_parts(url: str) -> tuple[str | None, str]:
    path = unquote(urlparse(url).path)
    parts = [p for p in path.split("/") if p]
    # .../highlights/<job_id>/<filename>
    if "highlights" in parts:
        i = parts.index("highlights")
        rest = parts[i + 1:]
        if len(rest) >= 2:
            return rest[0], rest[-1]
        if rest:
            return None, rest[-1]
    return None, Path(path).name

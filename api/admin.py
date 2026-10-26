"""Gated admin video workshop. Mounted at ``/api/admin``.

Off unless ADMIN_VIDEO=1 and ADMIN_TOKEN is set. The public fan page never
links here. Token is checked on every mutating or job-data route.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile

from api.highlights import kick
from highlights.pipeline import result_or_none
from highlights.publish import get_published, list_published, publish_view
from highlights.storage import save_upload
from highlights.store import create_job, get_job, list_jobs, load_result

router = APIRouter(prefix="/api/admin", tags=["admin"])
MAX_UPLOAD = 80 * 1024 * 1024


def admin_enabled() -> bool:
    flag = os.getenv("ADMIN_VIDEO", "").strip().lower() in ("1", "true", "yes")
    return flag and bool(os.getenv("ADMIN_TOKEN", "").strip())


def _bearer(authorization: str | None) -> str:
    kind, _, value = (authorization or "").partition(" ")
    return value.strip() if kind.lower() == "bearer" else ""


def require_admin(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    token: str | None = Query(default=None),
) -> str:
    """A signed-in session whose password has been changed; the shared ADMIN_TOKEN only until one has."""
    from api.auth import AuthError, legacy_token_ok, session_user
    if not admin_enabled():
        raise HTTPException(404, "Not found")
    session = _bearer(authorization)
    if session:
        try:
            username, _, must_change = session_user(session)
        except AuthError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        if must_change:
            raise HTTPException(403, "Change the first-time password before using the workshop.")
        return username
    if legacy_token_ok((x_admin_token or token or "").strip()):
        return "admin"
    raise HTTPException(401, "Sign in to use the admin workshop.")


def _auth_call(fn, *args):
    from api.auth import AuthError
    if not admin_enabled():
        raise HTTPException(404, "Not found")
    try:
        return fn(*args)
    except AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.post("/login")
def login(body: dict, request: Request):
    """Username and password -> a session. The first sign-in uses the deployment's ADMIN_TOKEN and must change it."""
    from api.auth import login as do_login
    client = request.client.host if request.client else ""
    return _auth_call(do_login, body.get("username", ""), body.get("password", ""), client)


@router.post("/password")
def change_password(body: dict, authorization: str | None = Header(default=None)):
    """Change the signed-in person's password (required on the first sign-in); returns a fresh session."""
    from api.auth import change_password as do_change
    return _auth_call(do_change, _bearer(authorization), body.get("current", ""), body.get("new", ""))


@router.get("/me")
def me(authorization: str | None = Header(default=None)):
    from api.auth import session_user
    username, _, must_change = _auth_call(session_user, _bearer(authorization))
    return {"username": username, "must_change": must_change}


@router.get("/status")
def status():
    """Safe to call from the fan app. Never returns the token."""
    on = admin_enabled()
    return {"enabled": on, "token_required": on, "path": "/admin/video" if on else None}


def _vision_info() -> dict:
    """What tracks an upload: the vision service when VISION_URL is set (asked, which also wakes it), else here."""
    url = os.getenv("VISION_URL", "").strip().rstrip("/")
    if not url:
        try:
            from vision.detectors import describe
            from vision.scene_llm import config
        except ImportError:
            return {"where": "local", "status": "no vision extras"}
        cfg, why = config()
        return {"where": "local", "status": "ok", **describe(),
                "scene": {"model": cfg["model"], "api": cfg["api"]} if cfg else {"skipped": why}}
    import httpx
    try:
        r = httpx.get(f"{url}/health", timeout=4.0)
        r.raise_for_status()
        return {"where": "service", "status": "ok", **r.json()}
    except Exception:   # scaled to zero: the request above starts it; the page asks again next load
        return {"where": "service", "status": "starting"}


@router.get("/video")
def video_info(_token: str = Depends(require_admin)):
    return {
        "vision": _vision_info(),
        "enabled": True,
        "cpu_model": os.getenv("VISION_CPU_MODEL") or os.getenv("VISION_MODEL") or "yolo11n.pt",
        "device": os.getenv("VISION_DEVICE", "cpu"),
        "jobs": list_jobs(20),
        "published": list_published(),
    }


@router.get("/video/jobs")
def jobs(_token: str = Depends(require_admin), limit: int = 20):
    return {"jobs": list_jobs(limit)}


@router.get("/video/jobs/{job_id}")
def job(job_id: str, _token: str = Depends(require_admin)):
    found = get_job(job_id)
    if not found:
        raise HTTPException(404, "No such job")
    return found


@router.get("/video/jobs/{job_id}/result")
def result(job_id: str, _token: str = Depends(require_admin)):
    found = get_job(job_id)
    if not found:
        raise HTTPException(404, "No such job")
    if found.get("status") != "done":
        raise HTTPException(409, found.get("message") or "Job is not finished")
    body = load_result(job_id)
    if not body:
        raise HTTPException(404, "No result stored")
    return body


@router.post("/video/jobs")
async def create(
    source: str = Form("demo"),
    file: UploadFile | None = File(None),
    _token: str = Depends(require_admin),
):
    source = (source or "demo").strip().lower()
    extra = {"admin": True, "pitch_view": True}
    if source == "demo" and file is None:
        job = create_job("demo", filename="demo", extra=extra)
        kick(job["job_id"])
        return job
    if file is None:
        raise HTTPException(400, "Provide a file or source=demo.")
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Clip is larger than 80 MB.")
    if not data:
        raise HTTPException(400, "Empty file.")
    job = create_job("upload", filename=file.filename or "clip.mp4", extra=extra)
    save_upload(job["job_id"], data, file.filename or "clip.mp4")
    kick(job["job_id"])
    return job


@router.post("/video/jobs/{job_id}/freeze/{moment_id}")
def verify_freeze_frame(job_id: str, moment_id: str, body: dict, _token: str = Depends(require_admin)):
    """A person's corrections to one pass frame: exact pitch mapping, teams, passer and receiver, pass flags."""
    from highlights.store import save_result
    from highlights.verify import VerifyError, save_truth, verify_freeze
    result = load_result(job_id)
    if not result:
        raise HTTPException(404, "No result stored")
    assist = next((a for a in result.get("assist") or [] if a.get("moment_id") == moment_id), None)
    if not assist or not assist.get("freeze"):
        raise HTTPException(404, "No freeze frame for this moment")
    try:
        verified = verify_freeze(assist["freeze"], body)
    except VerifyError as exc:
        raise HTTPException(400, str(exc)) from exc
    assist["verified"] = verified
    save_result(job_id, result)
    save_truth(job_id, moment_id, assist["freeze"], body, verified)
    return assist


@router.post("/video/jobs/{job_id}/publish")
def publish(job_id: str, _token: str = Depends(require_admin)):
    found = get_job(job_id)
    if not found:
        raise HTTPException(404, "No such job")
    body = result_or_none(job_id)
    if not body:
        raise HTTPException(409, "Job has no result to publish")
    views = (body.get("pitch_view") or {}).get("views") or []
    if not views:
        raise HTTPException(409, "No shot moment to publish")
    published = [publish_view(job_id, view) for view in views]
    return {"ok": True, "published": published}


@router.get("/video/published")
def published(_token: str = Depends(require_admin)):
    return {"moments": list_published()}


@router.get("/video/published/{moment_id}")
def published_one(moment_id: str, _token: str = Depends(require_admin)):
    found = get_published(moment_id)
    if not found:
        raise HTTPException(404, "No such published moment")
    return found

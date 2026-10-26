"""Job records. Files on disk by default; Cosmos when COSMOS_ENDPOINT is set."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def highlights_dir() -> Path:
    return Path(os.getenv("HIGHLIGHTS_DIR") or (ROOT / "data" / "highlights"))


HIGHLIGHTS_DIR = highlights_dir()  # resolved at import; helpers re-read the env


def _job_dir(job_id: str) -> Path:
    return highlights_dir() / job_id


def new_id() -> str:
    return "hl_" + uuid.uuid4().hex[:10]


def create_job(source: str, filename: str = "", extra: dict | None = None, job_id: str | None = None) -> dict:
    extra = dict(extra or {})
    job_id = job_id or extra.pop("job_id", None) or new_id()
    folder = _job_dir(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    job = {
        "job_id": job_id,
        "status": "queued",
        "step": "queued",
        "progress": 0,
        "message": "Waiting to start.",
        "source": source,
        "filename": filename,
        "synthetic": source == "demo",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "error": None,
        "video_kind": None,
        "has_video": False,
    }
    if extra:
        job.update(extra)
    save_job(job)
    return job


def save_job(job: dict) -> dict:
    folder = _job_dir(job["job_id"])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "job.json"
    _atomic_write(path, json.dumps(job, indent=2) + "\n")
    _cosmos_upsert(job)
    return job


def get_job(job_id: str) -> dict | None:
    path = _job_dir(job_id) / "job.json"
    if not path.exists():
        return _cosmos_get(job_id)
    return json.loads(path.read_text(encoding="utf-8"))


def list_jobs(limit: int = 20) -> list[dict]:
    root = highlights_dir()
    root.mkdir(parents=True, exist_ok=True)
    jobs = []
    for folder in root.iterdir():
        path = folder / "job.json"
        if path.exists():
            jobs.append(json.loads(path.read_text(encoding="utf-8")))
    jobs.sort(key=lambda j: j.get("created_at") or "", reverse=True)
    return jobs[:limit]


def save_result(job_id: str, result: dict) -> Path:
    path = _job_dir(job_id) / "result.json"
    _atomic_write(path, json.dumps(result) + "\n")
    return path


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_result(job_id: str) -> dict | None:
    path = _job_dir(job_id) / "result.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def video_path(job_id: str) -> Path | None:
    folder = _job_dir(job_id)
    for path in sorted(folder.glob("input.*")):
        if path.stat().st_size > 0:
            return path
    for name in ("demo.mp4",):
        path = folder / name
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def update(job_id: str, **fields) -> dict:
    job = get_job(job_id)
    if not job:
        raise KeyError(job_id)
    job.update(fields)
    return save_job(job)


def _cosmos_upsert(job: dict) -> None:
    client = _cosmos()
    if client is None:
        return
    try:
        client.upsert_item({**job, "id": job["job_id"]})
    except Exception:
        return


def _cosmos_get(job_id: str) -> dict | None:
    client = _cosmos()
    if client is None:
        return None
    try:
        return client.read_item(job_id, partition_key=job_id)
    except Exception:
        return None


def _cosmos():
    url = os.getenv("COSMOS_ENDPOINT")
    key = os.getenv("COSMOS_KEY")
    if not url:
        return None
    try:
        from azure.cosmos import CosmosClient
        if key:
            client = CosmosClient(url, credential=key)
        else:
            from azure.identity import DefaultAzureCredential
            client = CosmosClient(url, credential=DefaultAzureCredential())
        db = client.get_database_client(os.getenv("COSMOS_DATABASE", "gamelens"))
        return db.get_container_client(os.getenv("COSMOS_CONTAINER", "highlights"))
    except Exception:
        return None

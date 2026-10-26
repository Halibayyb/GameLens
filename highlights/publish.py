"""Published reconstructed moments, overlaid on the synthetic match list.

Synthetic remains the default match. Publishing appends extra moments that
the existing Broadcast / Analyst views already know how to draw.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def published_dir() -> Path:
    return Path(os.getenv("PUBLISHED_DIR") or (ROOT / "data" / "published"))


def _index_path() -> Path:
    folder = published_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "index.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _load_index() -> list[dict]:
    path = _index_path()
    if not path.exists():
        return []
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return body if isinstance(body, list) else body.get("moments") or []


def _save_index(rows: list[dict]) -> None:
    _atomic_write(_index_path(), json.dumps(rows, indent=2) + "\n")


def list_published() -> list[dict]:
    return list(_load_index())


def get_published(moment_id: str) -> dict | None:
    for row in _load_index():
        if row.get("id") == moment_id:
            payload = _payload_path(moment_id)
            if payload.exists():
                return json.loads(payload.read_text(encoding="utf-8"))
            return row
    return None


def publish_view(job_id: str, view: dict, extra: dict | None = None) -> dict:
    moment = dict(view.get("moment") or {})
    pub_id = f"vid_{job_id}_{moment.get('id') or 'shot'}"
    moment["id"] = pub_id
    moment["job_id"] = job_id
    moment["source"] = "video" if view.get("reconstructed") else "highlight-demo"
    summary = {
        "id": pub_id,
        "title": moment.get("title"),
        "kind": moment.get("kind"),
        "minute": moment.get("minute"),
        "elapsed": moment.get("elapsed"),
        "team": moment.get("team"),
        "team_name": moment.get("team_name"),
        "player": moment.get("player"),
        "score": moment.get("score"),
        "x": moment.get("x"),
        "y": moment.get("y"),
        "xg": moment.get("xg"),
        "important": True,
        "reconstructed": bool(view.get("reconstructed")),
        "confidence": view.get("confidence"),
        "badge": view.get("badge") or moment.get("badge"),
        "job_id": job_id,
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    payload = {
        **view,
        "moment": moment,
        "id": pub_id,
        "job_id": job_id,
        "published_at": summary["published_at"],
    }
    if extra:
        payload.update(extra)
    _atomic_write(_payload_path(pub_id), json.dumps(payload) + "\n")
    rows = [row for row in _load_index() if row.get("id") != pub_id]
    rows.append(summary)
    _save_index(rows)
    return summary


def _payload_path(moment_id: str) -> Path:
    return published_dir() / f"{moment_id}.json"

"""Highlight video bytes: local disk, or Azure Blob when an account is set."""
from __future__ import annotations

import os
from pathlib import Path

from highlights.store import _job_dir


def save_upload(job_id: str, data: bytes, filename: str = "clip.mp4") -> Path:
    suffix = Path(filename).suffix.lower() or ".mp4"
    if suffix not in (".mp4", ".webm", ".mov", ".m4v"):
        suffix = ".mp4"
    dest = _job_dir(job_id) / f"input{suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    _maybe_upload_blob(job_id, dest)
    return dest


def save_from_url(job_id: str, url: str) -> Path:
    dest = _job_dir(job_id) / "input.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("file://"):
        src = Path(url[7:])
        dest.write_bytes(src.read_bytes())
        return dest
    if os.getenv("AZURE_STORAGE_ACCOUNT") and "blob.core.windows.net" in url:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient
        client = BlobClient.from_blob_url(url, credential=DefaultAzureCredential())
        dest.write_bytes(client.download_blob().readall())
        return dest
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": "gamelens-highlights/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        dest.write_bytes(response.read())
    return dest


def _maybe_upload_blob(job_id: str, path: Path) -> None:
    account = os.getenv("AZURE_STORAGE_ACCOUNT")
    connection = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not (account or connection):
        return
    container = os.getenv("HIGHLIGHTS_CONTAINER", "highlights")
    blob = f"{job_id}/{path.name}"
    try:
        from azure.storage.blob import BlobClient, BlobServiceClient
        if connection:
            service = BlobServiceClient.from_connection_string(connection)
            client = service.get_blob_client(container, blob)
        else:
            from azure.identity import DefaultAzureCredential
            client = BlobClient(
                f"https://{account}.blob.core.windows.net", container, blob,
                credential=DefaultAzureCredential(),
            )
        with path.open("rb") as handle:
            client.upload_blob(handle, overwrite=True)
    except Exception:
        return



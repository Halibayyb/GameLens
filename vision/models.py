"""Prepared models: one manifest, verified files, one loaded instance per process.

The football weights are roboflow/sports' Ultralytics YOLOv8 models (AGPL-3.0 terms apply to shipping
them). This project keeps a copy in Azure Blob Storage (MODELS_BLOB_URL, default the `models` container of
the GameLens storage account), read with Microsoft Entra ID: a managed identity or your `az login`, with the
Storage Blob Data Reader role. A SAS URL works too. The vision image bakes them into /app/models; a local
run keeps them in <repo>/models (gitignored). ``python -m vision.models`` fetches anything missing, and every
file is checked against its size and sha256 before use.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOB_URL = "https://st6zxezlomabtuo.blob.core.windows.net/models"   # override with MODELS_BLOB_URL

MANIFEST: dict[str, dict] = {
    "football-player": {
        "file": "football-player-detection.pt",
        "sha256": "75b09c377fbf9d0791d23f6cfb689f5aed6eaa43a6818bd1fb884cf7507fffaf",
        "size": 136802409,
        "source": {"blob": "football-player-detection.pt"},
        "task": "detect", "imgsz": 1280,
        "classes": {0: "ball", 1: "goalkeeper", 2: "player", 3: "referee"},
        "note": "Players, goalkeepers, referees and the ball on broadcast footage.",
    },
    "football-ball": {
        "file": "football-ball-detection.pt",
        "sha256": "678fbad05134f19c5094cb8d273812ec9c6691228180d46832551ecf99ed2912",
        "size": 136786099,
        "source": {"blob": "football-ball-detection.pt"},
        "task": "detect", "imgsz": 640,
        "classes": {0: "ball"},
        "note": "Ball only; run on 640 px tiles of the full frame.",
    },
    "football-pitch": {
        "file": "football-pitch-detection.pt",
        "sha256": "28f68f7c4056d6d9b137efd2e7ab5f3c494039380c63831649126ced25628b36",
        "size": 140212306,
        "source": {"blob": "football-pitch-detection.pt"},
        "task": "pose", "imgsz": 640,
        "classes": {0: "pitch"},
        "note": "32 pitch keypoints in the roboflow/sports SoccerPitchConfiguration order.",
    },
    "coco": {
        "file": "yolo11n.pt", "sha256": None, "size": None,
        "source": {"ultralytics": "yolo11n.pt"}, "task": "detect", "imgsz": 640,
        "classes": {0: "person", 32: "sports ball"},
        "note": "Generic COCO fallback.",
    },
    "pose": {
        "file": "yolo11n-pose.pt", "sha256": None, "size": None,
        "source": {"ultralytics": "yolo11n-pose.pt"}, "task": "pose", "imgsz": 640,
        "classes": {0: "person"},
        "note": "17 COCO body keypoints for posture (header vs foot).",
    },
}

_lock = threading.Lock()
_verified: set[Path] = set()
_loaded: dict[str, object] = {}


class ModelUnavailable(RuntimeError):
    pass


def model_dir() -> Path:
    return Path(os.getenv("MODEL_DIR") or ROOT / "models")


def _spec(name: str) -> dict:
    if name not in MANIFEST:
        raise KeyError(f"Unknown model {name!r}; known: {', '.join(MANIFEST)}")
    return MANIFEST[name]


def _sha256(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(file: Path, spec: dict) -> None:
    """Size and checksum, once per process per file."""
    if file in _verified:
        return
    if spec.get("size") and file.stat().st_size != spec["size"]:
        raise ModelUnavailable(f"{file} is {file.stat().st_size} bytes, expected {spec['size']}: re-download it.")
    if spec.get("sha256") and _sha256(file) != spec["sha256"]:
        raise ModelUnavailable(f"{file} fails its sha256 check: re-download it.")
    _verified.add(file)


def available(name: str) -> bool:
    """The weights are on disk (no download, no checksum)."""
    spec = _spec(name)
    return (model_dir() / spec["file"]).is_file()


def _download(spec: dict, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = spec["source"]
    if "ultralytics" in source:
        from ultralytics.utils.downloads import attempt_download_asset
        got = Path(attempt_download_asset(str(dest)))
        if got != dest and got.is_file():
            got.replace(dest)
        return
    base = (os.getenv("MODELS_BLOB_URL") or BLOB_URL).rstrip("/")
    try:
        from azure.storage.blob import ContainerClient
        if "?" in base:   # a SAS URL carries its own permission
            container = ContainerClient.from_container_url(base)
        else:
            from azure.identity import DefaultAzureCredential
            container = ContainerClient.from_container_url(base, credential=DefaultAzureCredential())
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as handle:
            container.download_blob(source["blob"]).readinto(handle)
        tmp.replace(dest)
    except Exception as exc:
        raise ModelUnavailable(
            f"{dest.name} is missing and could not be fetched from {base.split('?')[0]}/{source['blob']} "
            f"({type(exc).__name__}). Sign in with `az login` (Storage Blob Data Reader on the storage account), "
            f"set MODELS_BLOB_URL, or copy the file to {dest}."
        ) from exc


def path(name: str) -> Path:
    """Local, verified weights for ``name``; downloads them if missing."""
    spec = _spec(name)
    file = model_dir() / spec["file"]
    with _lock:
        if not file.is_file():
            _download(spec, file)
        if not file.is_file():
            raise ModelUnavailable(f"Could not fetch {file.name}.")
        _check(file, spec)
    return file


def load(name: str):
    """One Ultralytics YOLO instance per model per process."""
    with _lock:
        if name in _loaded:
            return _loaded[name]
    weights = path(name)
    from ultralytics import YOLO
    with _lock:
        if name not in _loaded:
            _loaded[name] = YOLO(str(weights), task=_spec(name)["task"])
        return _loaded[name]


def main() -> None:
    for name in MANIFEST:
        print(f"{name:16} {path(name)}")


if __name__ == "__main__":
    main()

"""Write a match folder the engine can open without knowing the source."""
import csv
import json
from pathlib import Path

from feeds.schema import validate_event

TRACKING_FIELDS = ["frame", "period", "match_seconds", "clock", "object_id", "team",
                   "x", "y", "ball_possession", "ball_in_play"]


def write_tracking(path: Path, rows, fps: float = 5) -> None:
    """Stream tracking rows to ``path``. ``rows`` is a list or any iterable of dicts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRACKING_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            ms = float(row["match_seconds"])
            writer.writerow({
                "frame": row.get("frame", round(ms * fps)),
                "period": row["period"],
                "match_seconds": f"{ms:.2f}",
                "clock": row.get("clock", ""),
                "object_id": row["object_id"],
                "team": row.get("team", ""),
                "x": f"{float(row['x']):.2f}",
                "y": f"{float(row['y']):.2f}",
                "ball_possession": row.get("ball_possession", ""),
                "ball_in_play": row.get("ball_in_play", 1),
            })


def write_match(folder: Path, events: list[dict], meta: dict, rows: list[dict] | None = None) -> dict:
    """Write ``events.jsonl``, ``match_meta.json`` and, when ``rows`` is non-empty, ``tracking.csv``.

    Event ids are assigned here, in file order. ``rows is None`` leaves an
    existing ``tracking.csv`` in place (a feed that streams the file itself).
    An empty ``rows`` list deletes a stale file so a reused folder cannot keep
    another source's frames.
    """
    folder.mkdir(parents=True, exist_ok=True)
    for i, event in enumerate(events, start=1):
        event["event_id"] = i
        validate_event(event)
    payload = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
    (folder / "events.jsonl").write_text(payload, encoding="utf-8")
    tracking = folder / "tracking.csv"
    if rows:
        write_tracking(tracking, rows, fps=meta.get("fps") or 5)
    elif rows is not None and tracking.exists():
        tracking.unlink()
    (folder / "match_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return meta

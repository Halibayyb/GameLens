#!/usr/bin/env python3
"""Replay a match into Azure Event Hubs (or print it, if no connection string).

    python stream/replay.py --speed 60                 # default: synthetic match, offline
    python stream/replay.py data/match_007 --speed 60  # an existing match folder
    python stream/replay.py --speed 1                  # real time
    python stream/replay.py --channel tracking --speed 1

Env: EVENTHUB_CONNECTION_STRING, EVENTHUB_NAME (default "match-events"),
EVENTHUB_TRACKING_NAME (default "match-tracking"), EVENTHUB_NAMESPACE,
WEBPUBSUB_CONNECTION_STRING or WEBPUBSUB_HOST, WEBPUBSUB_HUB (default "live"),
MATCH_SOURCE (default synthetic), MATCH_SEED, MATCH_DIR.
Tracking frames are also published to Web PubSub when that service is configured.
"""
import argparse
import json
import os
import time
from pathlib import Path


def prepare_folder(match_dir: str | None = None, source: str | None = None) -> Path:
    """Return a match folder. With no folder, build the selected feed (synthetic by default)."""
    if match_dir and source is None:
        return Path(match_dir)
    if source:
        os.environ["MATCH_SOURCE"] = source
    from feeds import ensure_materialized
    return ensure_materialized(source, Path(match_dir) if match_dir else None)


def load_events(folder: Path) -> list[dict]:
    return [json.loads(line) for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines() if line]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("match_dir", nargs="?", default=None,
                    help="existing match folder; omit to use MATCH_SOURCE (default synthetic)")
    ap.add_argument("--source", default=None, choices=["synthetic", "proto", "vision", "live"],
                    help="feed to materialise when no folder is given, or into match_dir")
    ap.add_argument("--speed", type=float, default=60.0, help="times faster than real time (1 = real time)")
    ap.add_argument("--channel", default="events", choices=["events", "tracking"],
                    help="events is the ticker; tracking is player and ball frames at up to 12 Hz")
    a = ap.parse_args()
    folder = prepare_folder(a.match_dir, a.source)
    if a.channel == "tracking":
        _replay_tracking(folder, a.speed)
        return
    events = load_events(folder)

    conn = os.getenv("EVENTHUB_CONNECTION_STRING")
    producer = None
    if conn:
        from azure.eventhub import EventData, EventHubProducerClient  # pip install azure-eventhub
        producer = EventHubProducerClient.from_connection_string(conn, eventhub_name=os.getenv("EVENTHUB_NAME", "match-events"))

    last = 0.0
    for e in events:
        time.sleep(max(0.0, (e["match_seconds"] - last) / a.speed))
        last = e["match_seconds"]
        if producer:
            batch = producer.create_batch()
            batch.add(EventData(json.dumps(e)))
            producer.send_batch(batch)
        else:
            print(f'{e["minute"]:>7} {e["type"]:<18} {e.get("player_name") or ""}')
    if producer:
        producer.close()


def _replay_tracking(folder: Path, speed: float) -> None:
    from feeds.tracking import iter_paced, iter_tracking_frames
    from stream.publish import open_tracking_publisher

    publisher = open_tracking_publisher(force=True)
    try:
        for frame, sleep in iter_paced(iter_tracking_frames(folder), speed=speed):
            time.sleep(sleep)
            if publisher:
                publisher.send(frame)
            else:
                ball = frame.get("ball") or {}
                print(f'{frame["clock"]:>7}  {len(frame["players"]):2} players  ball {ball.get("x", "-")},{ball.get("y", "-")}  risk {frame.get("counter_risk")}')
    finally:
        if publisher:
            publisher.close()


if __name__ == "__main__":
    main()

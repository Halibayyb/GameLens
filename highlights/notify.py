"""Push job progress to the UI.

Local: in-process callbacks that the Highlights WebSocket drains.
Azure: the same payload on Web PubSub hub ``highlights`` when configured.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from typing import Callable

Callback = Callable[[dict], None]

_lock = threading.Lock()
_subs: dict[str, list[Callback]] = {}
_pubsub = None
_pubsub_failed = False


def subscribe(job_id: str, callback: Callback) -> None:
    with _lock:
        _subs.setdefault(job_id, []).append(callback)


def unsubscribe(job_id: str, callback: Callback) -> None:
    with _lock:
        row = _subs.get(job_id) or []
        if callback in row:
            row.remove(callback)
        if not row:
            _subs.pop(job_id, None)


def emit(job_id: str, payload: dict) -> None:
    body = {"kind": "highlight", "job_id": job_id, **payload}
    with _lock:
        callbacks = list(_subs.get(job_id, []))
    for callback in callbacks:
        try:
            callback(body)
        except Exception:
            continue
    _send_pubsub(body)


def _send_pubsub(payload: dict) -> None:
    global _pubsub, _pubsub_failed
    if _pubsub_failed:
        return
    client = _client()
    if client is None:
        return
    try:
        client.send_to_all(json.dumps(payload))
    except Exception as exc:  # noqa: BLE001
        _pubsub_failed = True
        print(f"highlights Web PubSub stopped: {exc}", file=sys.stderr)


def _client():
    global _pubsub
    if _pubsub is not None:
        return _pubsub
    hub = os.getenv("WEBPUBSUB_HIGHLIGHTS_HUB", "highlights")
    connection = os.getenv("WEBPUBSUB_CONNECTION_STRING")
    host = os.getenv("WEBPUBSUB_HOST")
    try:
        if connection:
            from azure.messaging.webpubsubservice import WebPubSubServiceClient
            _pubsub = WebPubSubServiceClient.from_connection_string(connection, hub=hub)
            return _pubsub
        if host:
            from azure.identity import DefaultAzureCredential
            from azure.messaging.webpubsubservice import WebPubSubServiceClient
            endpoint = host if host.startswith("https://") else f"https://{host}"
            _pubsub = WebPubSubServiceClient(endpoint, hub, DefaultAzureCredential())
            return _pubsub
    except Exception as exc:  # noqa: BLE001
        print(f"highlights Web PubSub is off: {exc}", file=sys.stderr)
        return None
    return None

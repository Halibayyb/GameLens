"""Optional fan-out of live frames to Event Hubs and Web PubSub.

The browser path is the ``/api/live`` WebSocket and does not need either
service. ``stream/replay.py`` publishes when a connection string or a
managed-identity host is set. The API mirrors a tracking socket only when
``LIVE_PUBLISH=1``, so several open browsers do not each write the hub.

Missing client libraries are reported once. Nothing here runs on import, and
the synthetic demo does not set these variables.
"""
from __future__ import annotations

import json
import os
import sys


def _enabled(force: bool) -> bool:
    if force:
        return True
    return os.getenv("LIVE_PUBLISH", "0").strip().lower() in ("1", "true", "yes")


class TrackingPublisher:
    """Best-effort sender. The first failure disables the rest of the run."""

    def __init__(self, event_hub=None, web_pubsub=None):
        self._eh = event_hub
        self._wps = web_pubsub
        self._failed = False

    def send(self, payload: dict) -> None:
        if self._failed:
            return
        raw = json.dumps(payload)
        try:
            if self._eh is not None:
                from azure.eventhub import EventData
                batch = self._eh.create_batch()
                batch.add(EventData(raw))
                self._eh.send_batch(batch)
            if self._wps is not None:
                self._wps.send_to_all(raw)
        except Exception as exc:  # noqa: BLE001 - a down hub must not stop the pitch
            self._failed = True
            print(f"tracking publish stopped: {exc}", file=sys.stderr)

    def close(self) -> None:
        if self._eh is not None:
            self._eh.close()


def open_tracking_publisher(force: bool = False) -> TrackingPublisher | None:
    """Return a publisher, or None when publishing is off or not configured."""
    if not _enabled(force):
        return None
    event_hub = _event_hub()
    web_pubsub = _web_pubsub()
    if event_hub is None and web_pubsub is None:
        return None
    return TrackingPublisher(event_hub, web_pubsub)


def _event_hub():
    name = os.getenv("EVENTHUB_TRACKING_NAME", "match-tracking")
    connection = os.getenv("EVENTHUB_CONNECTION_STRING")
    namespace = os.getenv("EVENTHUB_NAMESPACE")
    try:
        if connection:
            from azure.eventhub import EventHubProducerClient
            return EventHubProducerClient.from_connection_string(connection, eventhub_name=name)
        if namespace:
            from azure.eventhub import EventHubProducerClient
            from azure.identity import DefaultAzureCredential
            return EventHubProducerClient(namespace, name, DefaultAzureCredential())
    except Exception as exc:  # noqa: BLE001
        print(f"Event Hubs tracking publisher is off: {exc}", file=sys.stderr)
    return None


def _web_pubsub():
    hub = os.getenv("WEBPUBSUB_HUB", "live")
    connection = os.getenv("WEBPUBSUB_CONNECTION_STRING")
    host = os.getenv("WEBPUBSUB_HOST")
    try:
        if connection:
            from azure.messaging.webpubsubservice import WebPubSubServiceClient
            return WebPubSubServiceClient.from_connection_string(connection, hub=hub)
        if host:
            from azure.identity import DefaultAzureCredential
            from azure.messaging.webpubsubservice import WebPubSubServiceClient
            endpoint = host if host.startswith("https://") else f"https://{host}"
            return WebPubSubServiceClient(endpoint, hub, DefaultAzureCredential())
    except Exception as exc:  # noqa: BLE001
        print(f"Web PubSub publisher is off: {exc}", file=sys.stderr)
    return None

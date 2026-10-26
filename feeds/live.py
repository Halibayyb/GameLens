"""Stub for a future licensed live feed.

No paid API is called. Wire a real connector here when a contract exists:
subscribe to the provider's push (or pull) stream, map each message with
``feeds.schema`` (the same records the synthetic generator
emit) and write them through ``feeds.store.write_match`` or yield them to
``stream/replay.py``.

It is off unless ``MATCH_SOURCE=live``. The default source is the synthetic
generator, which does not read this module's settings.

Environment
-----------
``LIVE_FEED_PROVIDER``   the licensed provider's name (shown in messages only)
``LIVE_FEED_API_KEY``    read from the environment. A hosted deploy should
                         inject this from Key Vault. The value is never
                         logged or included in an error message.
``LIVE_FEED_MATCH_ID``   the provider's match id, once a connector exists.
"""
import os
from pathlib import Path

from feeds.base import FeedNotImplemented

class LicensedLiveFeed:
    """Extension point. ``materialize`` always raises ``FeedNotImplemented``."""

    source = "live"

    def __init__(self, provider: str, api_key: str, match_id: str | None = None):
        self.provider = provider
        self.api_key = api_key
        self.match_id = match_id

    @classmethod
    def from_env(cls) -> "LicensedLiveFeed":
        provider = os.getenv("LIVE_FEED_PROVIDER", "").strip() or "licensed provider"
        return cls(
            provider=provider,
            api_key=os.getenv("LIVE_FEED_API_KEY", ""),
            match_id=os.getenv("LIVE_FEED_MATCH_ID") or None,
        )

    def materialize(self, folder: Path) -> dict:
        label = self.provider
        # Touch the key so a missing secret fails differently from a present one,
        # without ever echoing the secret.
        if not self.api_key:
            raise FeedNotImplemented(
                f"MATCH_SOURCE=live selects a licensed push feed ({label}). "
                "Set LIVE_FEED_API_KEY in the environment (Key Vault in a hosted deploy). "
                "No paid connector is implemented, and nothing is downloaded. "
                "Use MATCH_SOURCE=synthetic (the default)."
            )
        raise FeedNotImplemented(
            f"MATCH_SOURCE=live provider {label} is not implemented. "
            "Add the connector in feeds/live.py: read the push stream with the key already "
            "loaded from LIVE_FEED_API_KEY, map each message onto feeds.schema, and write a "
            "match folder plus tracking.csv in the synthetic generator's shape. "
            "The key was read and is not included in this error. Nothing is downloaded. "
            "Until then, use MATCH_SOURCE=synthetic (the default)."
        )

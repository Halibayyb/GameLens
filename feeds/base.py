"""Feed adapter interface.

A feed's only job is to materialise a match folder in the internal schema
(see ``feeds.schema``). Replay, the decision engine and the web API never
import a provider SDK.
"""
from pathlib import Path
from typing import Protocol


class FeedError(RuntimeError):
    """The selected feed could not produce a match."""


class FeedNotImplemented(FeedError):
    """The source is a documented extension point with no connector yet."""


class FeedAdapter(Protocol):
    """Something that can write ``events.jsonl`` + ``match_meta.json``."""

    source: str

    def materialize(self, folder: Path) -> dict:
        """Write the match folder and return its ``match_meta`` object."""
        ...

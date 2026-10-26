"""Local generators, kept as selectable sources.

``synthetic`` is the default simulated match. ``proto`` is the full match
built around the prototype's three hand-tuned goals (``data/match_proto``).
Both already write the internal schema; this module only plugs them into the
feed interface.
"""
import os
from pathlib import Path

from feeds.base import FeedAdapter


class ProtoFeed:
    source = "proto"

    def materialize(self, folder: Path) -> dict:
        from data_gen import proto_demo
        return proto_demo.build(folder)


class SyntheticFeed:
    """``data_gen/synthetic_match.py``. ``MATCH_SEED`` selects the match."""

    source = "synthetic"

    def __init__(self, seed: int | None = None, fps: int = 5, half_minutes: float | None = None):
        self.seed = int(os.getenv("MATCH_SEED", "7") if seed is None else seed)
        self.fps = fps
        self.half_minutes = float(os.getenv("MATCH_HALF_MINUTES", "45") if half_minutes is None else half_minutes)

    def materialize(self, folder: Path) -> dict:
        from data_gen.synthetic_match import Match
        return Match(self.seed, self.fps, self.half_minutes).run(folder)


# Both classes satisfy the adapter protocol; the annotation keeps that check local.
_: FeedAdapter = ProtoFeed()

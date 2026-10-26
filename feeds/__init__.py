"""Pluggable match feeds.

Every source materialises the same match folder the engine already reads
(``events.jsonl``, ``match_meta.json``, optional ``tracking.csv``). Importing
this package does not read a match and does not use the network.

``MATCH_SOURCE`` selects the feed (``FEED_SOURCE`` is used only when
``MATCH_SOURCE`` is unset):

    synthetic    ``data_gen/synthetic_match.py`` — the default, fully offline
    proto        optional full match around the prototype's three goals (``data_gen/proto_demo.py``)
    vision       optional; reads a clip only when VISION_VIDEO is set
    live         optional licensed push-feed stub (no download)

The event records are documented in ``feeds.schema``.
"""
import os
from pathlib import Path

from feeds.base import FeedAdapter, FeedError, FeedNotImplemented
from feeds.live import LicensedLiveFeed
from feeds.synthetic import ProtoFeed, SyntheticFeed

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ("synthetic", "proto", "vision", "live")
# Sources that build a match with no network and no weights. Vision is implemented
# but stays off the default path.
OFFLINE_SOURCES = ("synthetic", "proto")
DEFAULT_SOURCE = "synthetic"


def source_name() -> str:
    """The feed selected for this process. Default is the synthetic generator."""
    raw = os.getenv("MATCH_SOURCE") or os.getenv("FEED_SOURCE") or DEFAULT_SOURCE
    name = raw.strip().lower()
    if name not in SOURCES:
        raise FeedError(
            f"Unknown match source {raw!r}. Set MATCH_SOURCE to one of: {', '.join(SOURCES)}."
        )
    return name


def match_directory(source: str | None = None) -> Path:
    """Where the selected feed writes its match folder.

    ``MATCH_DIR`` overrides the default for every source.
    """
    override = os.getenv("MATCH_DIR")
    if override:
        return Path(override)
    source = source or source_name()
    if source == "proto":
        return ROOT / "data" / "match_proto"
    if source == "synthetic":
        seed = int(os.getenv("MATCH_SEED", "7"))
        return ROOT / "data" / f"match_{seed:03d}"
    if source == "vision":
        return ROOT / "data" / "vision"
    return ROOT / "data" / "live_feed"


def open_feed(source: str | None = None) -> FeedAdapter:
    source = source or source_name()
    if source == "proto":
        return ProtoFeed()
    if source == "synthetic":
        return SyntheticFeed()
    if source == "vision":
        from feeds.vision import VisionFeed
        return VisionFeed()
    if source == "live":
        return LicensedLiveFeed.from_env()
    raise FeedError(f"Unknown match source {source!r}.")


def ensure_materialized(source: str | None = None, folder: str | Path | None = None) -> Path:
    """Build ``folder`` if it is missing, and return it.

    An existing synthetic, prototype or vision folder is reused (tests point
    ``MATCH_DIR`` at a fixture). The licensed live feed is a stub:
    selecting it raises ``FeedNotImplemented`` and does not download.
    """
    source = source or source_name()
    dest = Path(folder) if folder is not None else match_directory(source)
    if source in ("synthetic", "proto", "vision") and (dest / "match_meta.json").exists():
        return dest
    if source in ("synthetic", "proto", "vision"):
        dest.parent.mkdir(parents=True, exist_ok=True)
    open_feed(source).materialize(dest)
    return dest

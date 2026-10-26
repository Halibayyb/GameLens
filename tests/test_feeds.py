"""The default feed is the offline synthetic generator. Vision is optional."""
import json
import socket
import urllib.request
from pathlib import Path

import pytest

from feeds import FeedError, FeedNotImplemented, ensure_materialized, open_feed, source_name
from feeds.schema import validate_event
from feeds.store import write_match
from feeds.synthetic import SyntheticFeed


def test_default_source_is_synthetic(monkeypatch):
    monkeypatch.delenv("MATCH_SOURCE", raising=False)
    monkeypatch.delenv("FEED_SOURCE", raising=False)
    assert source_name() == "synthetic"


def test_match_source_wins_over_feed_source(monkeypatch):
    monkeypatch.setenv("FEED_SOURCE", "proto")
    monkeypatch.setenv("MATCH_SOURCE", "synthetic")
    assert source_name() == "synthetic"
    monkeypatch.delenv("MATCH_SOURCE")
    assert source_name() == "proto"


def test_unknown_source_is_rejected(monkeypatch):
    monkeypatch.setenv("MATCH_SOURCE", "some-feed")
    with pytest.raises(FeedError):
        source_name()


@pytest.mark.parametrize("source", ["live"])
def test_optional_feeds_do_not_download(monkeypatch, tmp_path, source):
    def refuse(*args, **kwargs):
        raise AssertionError("default and stub feeds must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setenv("LIVE_FEED_API_KEY", "super-secret-key")
    monkeypatch.setenv("LIVE_FEED_PROVIDER", "Example Provider")
    with pytest.raises(FeedNotImplemented) as caught:
        open_feed(source).materialize(tmp_path / source)
    assert "super-secret-key" not in str(caught.value)
    assert not (tmp_path / source / "events.jsonl").exists()


def test_synthetic_feed_writes_events_and_tracking(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise AssertionError("synthetic feed must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    folder = tmp_path / "match"
    meta = SyntheticFeed(seed=3, fps=5, half_minutes=4).materialize(folder)
    assert meta["synthetic"] is True
    assert (folder / "events.jsonl").exists()
    assert (folder / "tracking.csv").exists()
    events = [__import__("json").loads(line) for line in (folder / "events.jsonl").read_text().splitlines() if line]
    assert events
    for event in events:
        validate_event(event)
    # tracking.csv is the pitch source: ball plus outfield players, same columns as the generator.
    header, *rows = (folder / "tracking.csv").read_text().splitlines()
    assert header.split(",")[:6] == ["frame", "period", "match_seconds", "clock", "object_id", "team"]
    ids = {row.split(",")[4] for row in rows}
    assert "ball" in ids
    assert len(ids) > 10


def test_tracking_rows_drive_the_pitch_replay(tmp_path):
    """A shot moment's replay window is the tracking file, which is what the pitch draws."""
    from engine.moments import Match

    squad = [
        {"player_id": "H01", "name": "Keeper", "number": 1, "role": "GK"},
        {"player_id": "H09", "name": "Striker", "number": 9, "role": "ST"},
        {"player_id": "A01", "name": "Away Keeper", "number": 1, "role": "GK"},
    ]
    meta = {
        "match_id": "SYN-TRACK", "synthetic": True, "source": "synthetic",
        "disclaimer": "Simulated.", "half_minutes": 45, "added_time_minutes": [0, 0],
        "fps": 5, "final_score": {"home": 1, "away": 0},
        "teams": {
            "home": {"name": "Home", "formation": "4-3-3", "squad": squad[:2]},
            "away": {"name": "Away", "formation": "4-3-3", "squad": squad[2:]},
        },
    }
    shot = _event(2, 100.0, "shot", "home", "Home", "H09", "Striker", 90.0, 34.0, "0-0",
                  outcome="goal", xg=0.4, shot_type="open_play")
    goal = _event(0, 100.0, "goal", "home", "Home", "H09", "Striker", 90.0, 34.0, "1-0")
    write_match(tmp_path, [shot, goal], meta, rows=[
        _row(t, "ball", "", 88 + t, 34) for t in (96.0, 98.0, 100.0, 101.0)
    ] + [
        _row(t, "H09", "home", 86 + t * 0.04, 34) for t in (96.0, 98.0, 100.0, 101.0)
    ] + [
        _row(t, "A01", "away", 100, 34) for t in (96.0, 98.0, 100.0, 101.0)
    ])
    match = Match(tmp_path)
    moment = next(m for m in match.moments if m.kind == "goal")
    assert "ball" in moment.track["objects"]
    assert "H09" in moment.track["objects"]
    assert moment.frame["H09"]["team"] == "home"
    assert len(moment.track["t"]) >= 2


def test_vision_feed_does_not_download_weights(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise AssertionError("vision feed must not use the network unless a clip is configured")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.delenv("VISION_VIDEO", raising=False)
    from feeds.vision import VisionFeed

    with pytest.raises(FeedError) as caught:
        VisionFeed().materialize(tmp_path / "vision")
    assert "VISION_VIDEO" in str(caught.value)
    assert not (tmp_path / "vision" / "events.jsonl").exists()


def test_ensure_reuses_a_synthetic_folder_without_network(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise AssertionError("reusing a match folder must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    folder = tmp_path / "ready"
    folder.mkdir()
    (folder / "match_meta.json").write_text('{"synthetic": true}')
    assert ensure_materialized("synthetic", folder) == folder


def _event(event_id, seconds, etype, team, team_name, pid, name, x, y, score, **extra):
    base = {
        "match_id": "SYN-TRACK", "event_id": event_id, "period": 1, "clock": "01:40", "minute": "2'",
        "match_seconds": seconds, "type": etype, "team": team, "team_name": team_name,
        "player_id": pid, "player_name": name, "x": x, "y": y, "score": score, "synthetic": True,
    }
    base.update(extra)
    return base


def _row(seconds, object_id, team, x, y):
    return {"period": 1, "match_seconds": seconds, "clock": "01:40", "object_id": object_id,
            "team": team, "x": x, "y": y, "ball_possession": "home", "ball_in_play": 1}

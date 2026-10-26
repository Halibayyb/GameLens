"""Convert vision tracks + events into the Broadcast / Analyst pitch schema.

The existing ``BroadcastPitch`` / ``AnalystView`` components consume
``moment`` + ``track`` + ``assist`` + ``shot``. Positions always come from
the tracks. Azure OpenAI may rewrite a title or explanation; it never
supplies coordinates.
"""
from __future__ import annotations

import os
from typing import Any

from engine.assist import build_assist, build_shot
from engine.decisions import analyse
from engine.explain import explain
from engine.moments import Match
from highlights.events import frames_to_rows
from feeds.store import write_match

DEFAULT_HOME = "Home"
DEFAULT_AWAY = "Away"


def build_pitch_views(
    frames: list[dict],
    events: list[dict],
    meta: dict,
    folder,
    *,
    reconstructed: bool,
    confidence: float,
    title_hints: dict | None = None,
) -> dict:
    """Write a match folder, then emit one payload per shot/goal the UI can render."""
    rows = frames_to_rows(frames)
    written = [dict(event) for event in events]
    write_match(folder, written, meta, rows=rows)
    match = Match(folder)
    views = []
    for moment in match.moments:
        if moment.kind not in ("goal", "chance"):
            continue
        summary = moment.summary()
        hint = (title_hints or {}).get(summary["id"]) or {}
        if hint.get("title"):
            summary["title"] = hint["title"]
            moment.title = hint["title"]
        if hint.get("team_name"):
            summary["team_name"] = hint["team_name"]
        summary["reconstructed"] = reconstructed
        summary["confidence"] = confidence
        summary["badge"] = (
            "Reconstructed from video · approximate motion" if reconstructed else "Schematic demo"
        )
        analysis = analyse(match, moment.id, "balanced")
        analysis["text"] = explain(analysis)
        analysis["track"] = moment.track or None
        analysis["assist"] = build_assist(match, moment)
        analysis["shot"] = build_shot(moment)
        analysis["moment"] = summary
        analysis["reconstructed"] = reconstructed
        analysis["confidence"] = confidence
        analysis["badge"] = summary["badge"]
        analysis["teams"] = {
            "home": meta["teams"]["home"]["name"],
            "away": meta["teams"]["away"]["name"],
        }
        views.append(analysis)
    return {
        "moments": [v["moment"] for v in views],
        "views": views,
        "reconstructed": reconstructed,
        "confidence": confidence,
        "badge": "Reconstructed from video · approximate motion" if reconstructed else "Schematic demo",
    }


def caption_moment(event: dict, teams: dict) -> dict:
    """Title (and optional team names). Positions are never taken from the model."""
    kind = event.get("type")
    outcome = event.get("outcome")
    if kind == "shot" and outcome == "goal":
        title = "Goal"
    elif kind == "shot":
        title = {"saved": "Shot saved", "blocked": "Shot blocked", "off_target": "Big chance missed"}.get(
            outcome, "Chance"
        )
    else:
        title = (kind or "Moment").replace("_", " ").title()
    home = teams.get("home") or DEFAULT_HOME
    away = teams.get("away") or DEFAULT_AWAY
    rewritten = _azure_caption(event, teams, title)
    if rewritten:
        title = rewritten.get("title") or title
    return {"title": title, "home": home, "away": away}


def _azure_caption(event: dict, teams: dict, fallback: str) -> dict | None:
    ep = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_API_KEY")
    dep = os.getenv("GAME_EXPLAIN_DEPLOYMENT") or os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not (ep and key and dep):
        return None
    payload = {
        "event_type": event.get("type"),
        "outcome": event.get("outcome"),
        "clock": event.get("clock"),
        "team": event.get("team"),
        "fallback_title": fallback,
        "teams": teams,
        "rule": "Never invent coordinates. Do not output x, y, or player positions.",
    }
    try:
        from openai import AzureOpenAI
        import json
        client = AzureOpenAI(
            azure_endpoint=ep, api_key=key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
        response = client.chat.completions.create(
            model=dep, response_format={"type": "json_object"}, max_tokens=200,
            messages=[
                {"role": "system", "content": (
                    "Write a short football moment title (2-6 words) as JSON {\"title\": ...}. "
                    "Never invent positions or coordinates."
                )},
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
        raw = json.loads(response.choices[0].message.content)
        out: dict[str, Any] = {}
        title = raw.get("title")
        if isinstance(title, str) and 1 < len(title) < 48 and not any(ch.isdigit() and "." in title for ch in title):
            out["title"] = title.strip()
        return out or None
    except Exception:
        return None



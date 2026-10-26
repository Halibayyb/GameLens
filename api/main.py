"""FastAPI backend.

Run locally:   uvicorn api.main:app --reload
In Azure:      the same container runs on Azure Container Apps (see infra/main.bicep).

The match comes from the feed selected by MATCH_SOURCE. The default is
``synthetic``: ``data_gen/synthetic_match.py``, including player and ball
tracking, with no download. See ``feeds``.
"""
import asyncio
import os
from functools import lru_cache
from pathlib import Path


from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from api.admin import router as admin_router
from api.highlights import router as highlights_router
from engine.assist import build_assist, build_shot
from engine.assist import verdict_template
from engine.commentary import commentary
from engine.explain import cached_verdict_sentence, explain, moment_text
from engine.momentum import pressure
from engine.moments import Match
from feeds import ensure_materialized, match_directory, source_name
from highlights.publish import get_published, list_published

ROOT = Path(__file__).resolve().parent.parent
# Resolved at import. Tests set MATCH_DIR before importing this module.
# Default source is the offline synthetic generator (MATCH_SEED picks the match).
SOURCE = source_name()
MATCH_DIR = match_directory(SOURCE)

app = FastAPI(title="GameLens API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(highlights_router)
app.include_router(admin_router)


def ensure_match() -> None:
    ensure_materialized(SOURCE, MATCH_DIR)


@lru_cache(maxsize=1)
def match() -> Match:
    ensure_match()
    return Match(MATCH_DIR)


@app.get("/api/health")
def health():
    source = source_name()
    return {"ok": True, "source": source, "synthetic": source in ("synthetic", "proto")}


@lru_cache(maxsize=1)
def _pressure() -> dict:
    """The pressure series, computed once for the served match."""
    return pressure(match().events, match().total_minutes)


@app.get("/api/match")
def get_match():
    m = match()
    meta = m.meta
    squad = {p["player_id"]: p for t in meta["teams"].values() for p in t["squad"]}
    synthetic = bool(meta.get("synthetic", True))
    return {"match_id": meta["match_id"], "synthetic": synthetic, "source": meta.get("source", SOURCE),
            "disclaimer": meta["disclaimer"],
            "teams": {k: v["name"] for k, v in meta["teams"].items()}, "final_score": meta["final_score"],
            "total_minutes": round(m.total_minutes, 2),
            "half_time_at": round(m._half2_offset / 60, 2),
            "score_segments": m.score_segments(),
            "pressure": _pressure(),
            "moments": [x.summary() for x in m.moments] + list_published(),
            "roles": {pid: p["role"] for pid, p in squad.items()},
            "numbers": {pid: p["number"] for pid, p in squad.items()},
            "names": {pid: p["name"] for pid, p in squad.items()}}


@app.get("/api/languages")
def languages():
    """Commentary languages (auto-translated, each with its own voice); "translated" is false when no Translator is set."""
    from engine.translate import LANGUAGES, configured
    return {"languages": [{"code": c, "name": n} for c, (n, _) in LANGUAGES.items()], "translated": configured()}


def _said_in(moment_id: str, lang: str | None) -> dict:
    """The commentary in ``lang``, its line and every caption (names untranslated); English, with "lang": "en", when
    translation is off or fails."""
    from engine.translate import language, translate
    said = dict(_commentary(moment_id))
    code = language(lang)
    if code == "en":
        return {**said, "lang": "en"}
    moment, assist = match().get(moment_id), _assist(moment_id)
    keep = [moment.summary().get("player"), *(v["name"] for v in match().meta["teams"].values())]
    if assist:
        keep += [assist["pass"].get("passer"), assist["pass"].get("receiver")]
    try:
        caps = [{**c, "text": translate(c["text"], code, keep)} for c in said["captions"]]
    except Exception:   # model and Translator unreachable or refused: the English captions rather than nothing
        return {**said, "lang": "en"}
    line = caps[0]["text"]
    if line == said["line"]:
        return {**said, "lang": "en"}
    return {**said, "line": line, "captions": caps, "lang": code, "line_en": said["line"]}


@app.get("/api/moments/{moment_id}")
def get_moment(moment_id: str, lang: str | None = None):
    published = get_published(moment_id)
    if published:
        return _published_moment(published)
    try:
        moment = match().get(moment_id)
    except KeyError:
        raise HTTPException(404, "No such moment")
    assist = _assist(moment_id)   # the pass behind a shot, scored by the formula; None when there is no assist
    summary = moment.summary()
    teams = {k: v["name"] for k, v in match().meta["teams"].items()}
    said = _said_in(moment_id, lang)
    text = moment_text(summary, assist, teams)
    text["commentary"] = said["line"]   # the commentator's line: the situation and the pass, in two or three short sentences
    return {"moment": summary, "available": assist is not None, "assist": assist,
            "text": text, "commentary": said,
            "track": moment.track or None,   # replay window
            "shot": build_shot(moment)}      # how the shot leaves the foot, for the ball in the replay


@lru_cache(maxsize=512)
def _commentary(moment_id: str) -> dict:
    moment = match().get(moment_id)
    goals_before = [m.summary() for m in match().moments if m.kind == "goal" and m.elapsed < moment.elapsed]
    teams = {k: v["name"] for k, v in match().meta["teams"].items()}
    return commentary(moment.summary(), _assist(moment_id), goals_before, _pressure(), teams)


@app.get("/api/moments/{moment_id}/speech")
def get_moment_speech(moment_id: str, lang: str | None = None, part: str = "line"):
    """One caption spoken (Azure AI Speech, MP3): ``part`` is line (default), smart or finish. Or the caption itself for
    the browser's voice when speech is not configured or the call fails. Only the server's own commentary is spoken."""
    from engine.speech import synthesize
    try:
        said = _said_in(moment_id, lang)
    except KeyError:
        raise HTTPException(404, "No such moment")
    text = next((c["text"] for c in said["captions"] if c["part"] == part), None)
    if text is None:
        raise HTTPException(404, f"This moment has no {part} caption")
    try:
        audio = synthesize(text, said["tags"], said["lang"])
    except Exception as exc:   # Azure unreachable or refused: the page still speaks, with the browser's voice
        return {"text": text, "lang": said["lang"], "speech": "browser", "note": f"Azure Speech failed: {type(exc).__name__}"}
    if audio:
        return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})
    return {"text": text, "lang": said["lang"], "speech": "browser", "note": "Azure Speech is not configured."}


def _published_moment(payload: dict) -> dict:
    """Serve a reconstructed (or schematic-demo) moment in the same shape as /api/moments."""
    result = dict(payload)
    result.setdefault("available", True)
    if "text" not in result:
        result["text"] = explain(result)
    return result


@app.get("/api/moments/{moment_id}/ai-sentence")
def get_ai_sentence(moment_id: str):
    """The verdict sentence worded by the language model, only when the user asks for it.
    ai is false (and the sentence is the template) when no model is configured or its reply fails the number guard."""
    try:
        out = _assist(moment_id)
    except KeyError:
        raise HTTPException(404, "No such moment")
    if not out:
        raise HTTPException(404, "No pass to word in this moment")
    sentence, source = cached_verdict_sentence(moment_id, out["payload"])
    return {"sentence": sentence, "source": source, "ai": source != "template"}


@lru_cache(maxsize=512)
def _assist(moment_id: str) -> dict | None:
    out = build_assist(match(), match().get(moment_id))
    if out:   # the language model is never called here: the page asks for it with the "What does AI say?" button
        out["sentence"], out["source"] = verdict_template(out["payload"]), "template"
    return out


@app.websocket("/api/live")
async def live(ws: WebSocket, speed: float = 30.0, channel: str = "events", hz: float = 10.0, start: float = 0.0):
    """Replay the selected match.

    ``channel=events`` (the default) is the event ticker the web app already
    collects. ``channel=tracking`` streams player and ball frames from
    ``tracking.csv``, throttled to at most 12 messages a second. ``speed`` is
    how many times faster than real time (``1`` plays the match in real time).
    ``start`` is a match-seconds offset so the live pitch can resume after a pause.

    The default match is the synthetic generator, so this socket needs no external data.
    When ``LIVE_PUBLISH=1`` and Event Hubs or Web PubSub is configured, each
    tracking frame is also published there.
    """
    await ws.accept()
    if channel == "tracking":
        await _live_tracking(ws, speed=speed, hz=hz, start=start)
        return
    if channel != "events":
        await ws.send_json({"kind": "error", "error": "channel must be events or tracking"})
        await ws.close()
        return
    events, last = match().events, 0.0
    try:
        for e in events:
            await asyncio.sleep(max(0.0, (e["match_seconds"] - last) / max(speed, 0.001)))
            last = e["match_seconds"]
            await ws.send_json(e)
    except Exception:
        return
    await ws.close()


async def _live_tracking(ws: WebSocket, speed: float, hz: float, start: float) -> None:
    from feeds.tracking import iter_paced, iter_tracking_frames
    from stream.publish import open_tracking_publisher

    folder = match().folder
    if not (folder / "tracking.csv").exists():
        await ws.send_json({"kind": "error", "error": "This match has no tracking.csv"})
        await ws.close()
        return
    publisher = open_tracking_publisher(force=False)
    frames = iter_tracking_frames(folder, start=start)
    try:
        for frame, sleep in iter_paced(frames, speed=speed, max_hz=hz):
            await asyncio.sleep(sleep)
            await ws.send_json(frame)
            if publisher is not None:
                await asyncio.to_thread(publisher.send, frame)
    except Exception:
        return
    if publisher is not None:
        publisher.close()
    await ws.close()


web_dist = ROOT / "web" / "dist"


@app.get("/admin/video")
@app.get("/admin/video/")
def admin_video_page():
    """SPA entry for the gated workshop. Token is checked by the page / API."""
    index = web_dist / "index.html"
    if not index.exists():
        raise HTTPException(404, "Web UI is not built. Run `npm run build` in web/.")
    return FileResponse(index)


if web_dist.exists():
    app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

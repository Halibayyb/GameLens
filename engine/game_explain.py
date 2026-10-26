"""Moment-by-moment game explanations.

Grounded only in ``engine.game_context.build_context``. Azure OpenAI / Foundry
rewrites those facts; templates always work offline. Cache is a JSON file
(and Cosmos when configured).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from engine.game_context import build_context, pick_key_moments

DEPTHS = ("fan", "analyst", "coach")
DEFAULT_MODEL = os.getenv("GAME_EXPLAIN_DEPLOYMENT") or os.getenv("AZURE_OPENAI_DEPLOYMENT") or "gpt-4.1-mini"

SYSTEM = (
    "You are an elite football analyst. You explain what is happening at one "
    "moment of a play and why, using ONLY the JSON facts you are given. "
    "Never invent quotes, injuries, transfer rumours, crowd noise, or any "
    "number that is not in the facts. If a field is null, do not guess it. "
    "Reply as JSON with keys: headline, what_happened, why, principles "
    "(array; only labels that appear in facts.principles), decision, "
    "alternatives, key_players (array of {name, role} using names in the facts), "
    "coaches_takeaway. Match the requested depth: fan = short plain language; "
    "analyst = tactics and the numbers; coach = rest defence, next action, "
    "teaching point. Write in the requested language. "
    "If facts.synthetic is true, do not claim this is a real broadcast."
)

ALLOWED_PRINCIPLES = {
    "line-breaking pass", "third-man run", "overload", "half-space",
    "cutback zone", "rest defence", "press",
}


def context_hash(ctx: dict, depth: str, language: str) -> str:
    payload = json.dumps({"c": ctx, "d": depth, "l": language}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def cache_path(job_id: str | None, key: str) -> Path:
    root = Path(os.getenv("HIGHLIGHTS_DIR") or Path(__file__).resolve().parent.parent / "data" / "highlights")
    folder = (root / job_id / "explain") if job_id else (root / "_explain")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{key}.json"


def load_cached(job_id: str | None, key: str) -> dict | None:
    path = cache_path(job_id, key)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return _cosmos_get(key)


def save_cached(job_id: str | None, key: str, body: dict) -> None:
    path = cache_path(job_id, key)
    path.write_text(json.dumps(body) + "\n", encoding="utf-8")
    _cosmos_put(key, body)


def template_explain(ctx: dict, depth: str = "analyst") -> dict:
    """Offline explanation. Every number is copied from ``ctx``."""
    event = ctx.get("event") or {}
    who = event.get("player_name") or (ctx.get("carrier") or {}).get("name") or "A player"
    team = event.get("team_name") or (ctx.get("teams") or {}).get(event.get("team") or "") or "the team"
    kind = ctx.get("kind") or event.get("type") or "moment"
    clock = ctx.get("clock") or "—"
    score = ctx.get("score") or "0-0"
    press = ctx.get("pressing") or {}
    control = ctx.get("pitch_control") or {}
    lanes = ctx.get("lanes") or []
    decision = ctx.get("decision")
    shot_xg = ctx.get("shot_xg")
    principles = list(ctx.get("principles") or [])
    chain = ctx.get("chain") or []
    length = event.get("length_m")
    receiver = event.get("receiver_name")
    title = {
        "build_up": "Build-up starts",
        "line_break": "Line-breaking pass",
        "assist": "The assist",
        "shot": "The shot",
        "goal": "The goal",
    }.get(kind, kind.replace("_", " ").title())

    what = f"{clock}. {title} by {who} ({team}). Score {score}."
    if event.get("type") == "pass" and receiver:
        what = f"{clock}. {who} finds {receiver}"
        if length is not None:
            what += f" ({length} m)"
        what += f". Score {score}."
    if event.get("type") == "shot":
        what = f"{clock}. {who} shoots"
        if shot_xg is not None:
            what += f" (xG {shot_xg})"
        if event.get("outcome"):
            what += f", {event['outcome']}"
        what += f". Score {score}."
    if event.get("type") == "goal":
        what = f"{clock}. Goal, {who} ({team}). Score {score}."

    why = f"{who} has the ball in a {ctx.get('game_state', 'drawing')} game."
    if press.get("opponents_within_10"):
        why += f" {press['opponents_within_10']} opponents are within 10 m (press intensity {press.get('intensity')})."
    if control:
        why += f" Pitch control {control.get('home')} home / {control.get('away')} away."
    if principles:
        why += " Principles supported by the numbers: " + ", ".join(principles) + "."
    if length and kind in ("line_break", "assist", "build_up"):
        why += f" The pass travels {length} m."

    alt = "No alternative pass was modelled at this frame."
    if lanes:
        best = lanes[0]
        alt = (f"Best modelled lane: {best['receiver']} "
               f"(success {best['success']}, xT {best['xt']}, "
               f"{best['bypassed']} defenders bypassed).")
    chosen = f"{who} played the {event.get('type') or 'action'} that is in the events."
    if decision and decision.get("options"):
        opts = {o["key"]: o for o in decision["options"]}
        picked = opts.get(decision.get("chosen") or "")
        top = opts.get(decision.get("best") or "")
        if picked:
            chosen = f"Chosen: {picked['label']} (reward {picked['reward']}, risk {picked['risk']})."
        if top and decision.get("gap") not in (None, 0):
            alt = f"Best by the engine: {top['label']} (gap {decision['gap']}). {alt}"

    players = []
    if who:
        players.append({"name": who, "role": "ball carrier" if kind != "goal" else "scorer"})
    if receiver and receiver != who:
        players.append({"name": receiver, "role": "receiver"})
    if lanes and lanes[0]["receiver"] not in {p["name"] for p in players}:
        players.append({"name": lanes[0]["receiver"], "role": "highest-value option"})

    takeaway = "Keep the numbers: reward versus risk, then the next pass."
    if kind == "assist":
        takeaway = f"The assist is the pass that set the shot. {alt}"
    if kind == "goal" and shot_xg is not None:
        takeaway = f"Finish from xG {shot_xg}. Check rest defence after the shot."
    if kind == "line_break":
        takeaway = "A line-breaking pass is only useful if the receiver can play forward on the next touch."
    if kind == "build_up":
        takeaway = "First pass of the sequence. Width and a free midfielder matter more than a long ball."

    fan = {
        "headline": title,
        "what_happened": what,
        "why": f"{who} is involved for {team}. The score is {score}.",
        "principles": principles[:1],
        "decision": chosen,
        "alternatives": alt if depth != "fan" else "The engine also lists safer and riskier options.",
        "key_players": players[:2],
        "coaches_takeaway": f"{title}. {who}.",
    }
    analyst = {
        "headline": title,
        "what_happened": what,
        "why": why,
        "principles": principles,
        "decision": chosen,
        "alternatives": alt,
        "key_players": players,
        "coaches_takeaway": takeaway,
    }
    coach = {
        "headline": title + " — coach view",
        "what_happened": what,
        "why": why + (f" Compactness home {((ctx.get('shapes') or {}).get('home') or {}).get('compactness_m')} m, "
                      f"away {((ctx.get('shapes') or {}).get('away') or {}).get('compactness_m')} m."),
        "principles": principles,
        "decision": chosen,
        "alternatives": alt,
        "key_players": players,
        "coaches_takeaway": takeaway,
    }
    body = {"fan": fan, "analyst": analyst, "coach": coach}[depth if depth in DEPTHS else "analyst"]
    body = {**body, "depth": depth if depth in DEPTHS else "analyst", "language": "en",
            "provider": "templates", "label": "AI-generated from event data",
            "attribution": ctx.get("attribution"),
            "chain_n": len(chain)}
    return body


def azure_explain(ctx: dict, depth: str, language: str) -> dict | None:
    ep = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_API_KEY")
    dep = os.getenv("GAME_EXPLAIN_DEPLOYMENT") or os.getenv("AZURE_OPENAI_DEPLOYMENT") or DEFAULT_MODEL
    if not (ep and key and dep):
        return None
    from openai import AzureOpenAI
    client = AzureOpenAI(azure_endpoint=ep, api_key=key,
                         api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"))
    user = json.dumps({"depth": depth, "language": language, "facts": ctx}, default=str)
    response = client.chat.completions.create(
        model=dep, response_format={"type": "json_object"}, max_tokens=500,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
    )
    raw = json.loads(response.choices[0].message.content)
    return _sanitize(raw, ctx, depth, language, "azure")


def _sanitize(raw: dict, ctx: dict, depth: str, language: str, provider: str) -> dict:
    base = template_explain(ctx, depth)
    names = _known_names(ctx)
    for key in ("headline", "what_happened", "why", "decision", "alternatives", "coaches_takeaway"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            base[key] = val.strip()
    if isinstance(raw.get("principles"), list):
        allowed = set(ctx.get("principles") or []) | ALLOWED_PRINCIPLES
        base["principles"] = [p for p in raw["principles"] if isinstance(p, str) and p in allowed]
    players = []
    if isinstance(raw.get("key_players"), list):
        for row in raw["key_players"]:
            if isinstance(row, dict) and row.get("name") in names:
                players.append({"name": row["name"], "role": str(row.get("role") or "player")[:40]})
    if players:
        base["key_players"] = players
    base["provider"] = provider
    base["language"] = language
    base["label"] = "AI-generated from event data"
    return base


def _known_names(ctx: dict) -> set[str]:
    names = set()
    ev = ctx.get("event") or {}
    for key in ("player_name", "receiver_name", "team_name"):
        if ev.get(key):
            names.add(ev[key])
    if ctx.get("carrier") and ctx["carrier"].get("name"):
        names.add(ctx["carrier"]["name"])
    for lane in ctx.get("lanes") or []:
        names.add(lane.get("receiver"))
    for side in (ctx.get("teams") or {}).values():
        if isinstance(side, str):
            names.add(side)
    return {n for n in names if n}


def explain_moment(ctx: dict, depth: str = "analyst", language: str = "en",
                   job_id: str | None = None, use_cache: bool = True) -> dict:
    depth = depth if depth in DEPTHS else "analyst"
    language = (language or "en").split("-")[0].lower()
    key = context_hash(ctx, depth, language)
    if use_cache:
        hit = load_cached(job_id, key)
        if hit:
            hit["cached"] = True
            return hit
    base = template_explain(ctx, depth)
    if language != "en":
        base["language"] = language
        base["note"] = "Template English; Azure OpenAI rewrites this when a key is set."
    for provider in (azure_explain,):
        try:
            got = provider(ctx, depth, language)
        except Exception as exc:  # noqa: BLE001 — demo must stay up
            base["provider_error"] = f"{provider.__name__}: {exc}"
            got = None
        if got:
            got["cached"] = False
            save_cached(job_id, key, got)
            return got
    base["cached"] = False
    save_cached(job_id, key, base)
    return base


def explain_all_depths(ctx: dict, job_id: str | None = None, language: str = "en") -> dict:
    return {depth: explain_moment(ctx, depth, language, job_id=job_id) for depth in DEPTHS}


def build_explanations(frames, events, teams, analyses=None, source=None, synthetic=True,
                       job_id=None, language="en") -> list[dict]:
    """Batch-generate the key-moment cards the UI draws on the timeline."""
    analyses = analyses or []
    picks = pick_key_moments(events, frames)
    out = []
    for pick in picks:
        analysis = _analysis_near(analyses, pick["match_seconds"])
        ctx = build_context(
            frames, events, teams, pick["match_seconds"],
            event=pick.get("event"), analysis=analysis, kind=pick["kind"],
            source=source, synthetic=synthetic,
        )
        by_depth = explain_all_depths(ctx, job_id=job_id, language=language)
        out.append({
            "id": pick["id"],
            "kind": pick["kind"],
            "title": pick["title"],
            "match_seconds": pick["match_seconds"],
            "clock": pick.get("clock"),
            "team": pick.get("team"),
            "team_name": pick.get("team_name"),
            "player_name": pick.get("player_name"),
            "context": ctx,
            "by_depth": by_depth,
            "provider": by_depth["analyst"].get("provider"),
            "label": "AI-generated from event data",
        })
    return out


def _analysis_near(analyses: list[dict], t: float) -> dict | None:
    best = None
    for row in analyses:
        elapsed = (row.get("moment") or {}).get("elapsed")
        if elapsed is None:
            continue
        if abs(elapsed * 60 - t) < 2.0:
            best = row
    return best


def narration_text(card: dict, depth: str = "analyst") -> str:
    body = (card.get("by_depth") or {}).get(depth) or card
    parts = [body.get("headline"), body.get("what_happened"), body.get("why"), body.get("coaches_takeaway")]
    return " ".join(p for p in parts if p)


def speech_configured() -> bool:
    from engine.speech import configured
    return configured()


def azure_speech(text: str) -> bytes | None:
    """Optional Azure AI Speech (engine.speech). Returns MP3 bytes, or None when unset."""
    from engine.speech import synthesize
    return synthesize(text)


def _cosmos_get(key: str) -> dict | None:
    client = _cosmos()
    if client is None:
        return None
    try:
        return client.read_item(key, partition_key=key)
    except Exception:
        return None


def _cosmos_put(key: str, body: dict) -> None:
    client = _cosmos()
    if client is None:
        return
    try:
        client.upsert_item({**body, "id": key})
    except Exception:
        return


def _cosmos():
    url = os.getenv("COSMOS_ENDPOINT")
    key = os.getenv("COSMOS_KEY")
    if not url:
        return None
    try:
        from azure.cosmos import CosmosClient
        if key:
            client = CosmosClient(url, credential=key)
        else:
            from azure.identity import DefaultAzureCredential
            client = CosmosClient(url, credential=DefaultAzureCredential())
        db = client.get_database_client(os.getenv("COSMOS_DATABASE", "gamelens"))
        return db.get_container_client(os.getenv("COSMOS_EXPLAIN_CONTAINER") or os.getenv("COSMOS_CONTAINER", "highlights"))
    except Exception:
        return None

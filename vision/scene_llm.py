"""Scene facts from a vision-language model: posture, on-ball events, shot types. Never positions.

Positions come from vision (detector + pitch homography). This module adds what vision cannot see and the
passing formulas (docs/how-it-works.md) need: whether a pass was lofted (PASS ignores lane defenders,
keeps the 0.10 aerial penalty), whether it was played into space (score the option at the pass end point),
and whether the finish was a header (the pHeader blend in XG AI). Keyframes are sent with the CV ids drawn on
them; the model may only refer to those ids. One prompt (prompts/scene_extract.md) and one JSON schema
(prompts/scene_extract.schema.json) serve every model, and the same code validates every answer.

The model is a deployment on Microsoft Foundry, called through the OpenAI v1 Chat Completions API [1]
(SCENE_LLM_ENDPOINT, e.g. https://<res>.openai.azure.com/openai/v1/; production uses gpt-5-mini). Frames go as
base64 data URLs; the answer is asked for as strict `json_schema`, degrading to `json_object` and then to the
prompt alone if a model does not support it, with the same validation every time.

Auth: Microsoft Entra ID through DefaultAzureCredential (scope `https://ai.azure.com/.default`), preferred where a
managed identity exists (Container Apps sets IDENTITY_ENDPOINT; the identity needs **Cognitive Services User** on
the Foundry resource [2]), or an API key (SCENE_LLM_API_KEY). `model` is the deployment name. A model without
image input cannot do this job: the result is skipped with the reason rather than guessed (SCENE_LLM_IMAGES=0,
or the endpoint rejects images).

[1] https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/endpoints
[2] https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/configure-entra-id

Env: SCENE_LLM_ENDPOINT, SCENE_LLM_MODEL, SCENE_LLM_API_KEY, SCENE_LLM_AUTH (auto|key|entra), SCENE_LLM_IMAGES,
SCENE_LLM_TIMEOUT_S, SCENE_LLM_REPLAY_FILE (a saved answer, for runs without a model).
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent / "prompts"
PROMPT = (HERE / "scene_extract.md").read_text(encoding="utf-8")
SCHEMA = json.loads((HERE / "scene_extract.schema.json").read_text(encoding="utf-8"))
PROMPT_VERSION = re.search(r"prompt_version:\s*(\S+)", PROMPT).group(1)
ENTRA_SCOPE = "https://ai.azure.com/.default"
LONG_EDGE = 1280
SHOT_SPLIT_S = 3.0      # a shot this long gets two keyframes
FILL_S = 0.8            # wide shots are topped up with a keyframe about this often
FOLLOW_S = 3.0          # a receiver's shot or header within this of the pass completes the hint
CORROBORATED_MIN = 0.3  # a pass this unsure is kept only if its receiver goes on to shoot (corroborated)
CORROBORATED_CONF = 0.65   # ... and then counts as sure enough to name the passer and the receiver
TEAM_RGB = {"H": (255, 70, 70), "A": (70, 150, 255), "R": (230, 230, 230)}
POSITION_TEXT = re.compile(r"\b\d+(\.\d+)?\s*(m|metres?|meters?|yards?|yds?|px|pixels?)\b|\b[xy]\s*[=:]\s*-?\d"
                           r"|coordinat|\bpitch zone", re.I)


class SceneLLMError(RuntimeError):
    pass


class ImagesUnsupported(SceneLLMError):
    pass


# ---------- configuration ----------

def config(env=None) -> tuple[dict | None, str | None]:
    """(settings, None) when a model is configured, else (None, why it is skipped)."""
    env = os.environ if env is None else env
    endpoint = (env.get("SCENE_LLM_ENDPOINT") or "").strip()
    model = (env.get("SCENE_LLM_MODEL") or "").strip()
    if str(env.get("SCENE_LLM_IMAGES", "1")).strip().lower() in ("0", "false", "no"):
        return None, "the configured model has no image input (SCENE_LLM_IMAGES=0)"
    key = (env.get("SCENE_LLM_API_KEY") or "").strip()
    cfg = {"api": "openai", "model": model, "endpoint": endpoint, "api_key": key,
           "timeout": float(env.get("SCENE_LLM_TIMEOUT_S") or 120)}
    if endpoint:
        if not model:
            return None, "SCENE_LLM_MODEL (the Foundry deployment name) is not set"
        auth = (env.get("SCENE_LLM_AUTH") or "auto").strip().lower()
        if auth == "auto":   # a managed identity beats a key; a key beats a developer login
            auth = "entra" if env.get("IDENTITY_ENDPOINT") or not key else "key"
        if auth == "key" and not key:
            return None, "SCENE_LLM_AUTH=key but SCENE_LLM_API_KEY is not set"
        cfg.update(auth=auth, provider="foundry")
        return cfg, None
    return None, "no vision model configured (set SCENE_LLM_ENDPOINT and SCENE_LLM_MODEL)"


def _entra_provider():
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    return get_bearer_token_provider(DefaultAzureCredential(), ENTRA_SCOPE)


def openai_base_url(endpoint: str) -> str:
    """Any Foundry/Azure OpenAI URL (resource root, target URI) -> the .../openai/v1/ base."""
    root = endpoint.split("/openai")[0].rstrip("/")
    return root + "/openai/v1/"


class _Replay:
    """Stands in for a model: returns a saved answer (SCENE_LLM_REPLAY_FILE), validated like a live one."""

    def __init__(self, path: Path):
        self.path = path

    def complete(self, system, frames, closing, repair=None):
        return self.path.read_text(encoding="utf-8"), {"model": f"replay:{self.path.name}", "structured": "replay"}


def make_adapter(cfg: dict, client=None):
    if isinstance(client, _Replay):
        return client
    return OpenAIAdapter(cfg, client)


# ---------- adapters: one call, raw JSON text back ----------

class OpenAIAdapter:
    """A Foundry deployment (gpt-5-mini) through the OpenAI v1 Chat Completions API."""
    MODES = ("json_schema", "json_object", "none")

    def __init__(self, cfg: dict, client=None):
        self.cfg, self.model = cfg, cfg["model"]
        self.client = client or self._client()
        self.mode = "json_schema"
        self.token_param = "max_completion_tokens"

    def _client(self):
        from openai import OpenAI
        key = _entra_provider() if self.cfg["auth"] == "entra" else self.cfg["api_key"]
        return OpenAI(base_url=openai_base_url(self.cfg["endpoint"]), api_key=key,
                      timeout=self.cfg["timeout"], max_retries=3)

    def complete(self, system: str, frames: list[tuple[str, bytes]], closing: str, repair=None) -> tuple[str, dict]:
        content = []
        for caption, jpeg in frames:
            content.append({"type": "text", "text": caption})
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.standard_b64encode(jpeg).decode(), "detail": "high"}})
        # json_object mode needs the schema in the prompt itself
        content.append({"type": "text", "text": closing + "\n\nJSON schema:\n" + json.dumps(SCHEMA)})
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        if repair:
            messages += [{"role": "assistant", "content": repair[0] or "(no answer)"},
                         {"role": "user", "content": repair[1]}]
        return self._send(messages)

    def _send(self, messages):
        import openai
        kwargs = {"model": self.model, "messages": messages, self.token_param: 16000}
        if self.mode == "json_schema":
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "scene_extract", "strict": True, "schema": SCHEMA}}
        elif self.mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        try:
            r = self.client.chat.completions.create(**kwargs)
        except openai.BadRequestError as exc:
            text = str(exc).lower()
            if "image" in text:
                raise ImagesUnsupported(f"{self.model} rejected image input: {exc}") from exc
            if self.token_param in text:
                self.token_param = "max_tokens"
                return self._send(messages)
            if self.mode != "none" and ("response_format" in text or "json_schema" in text or "schema" in text):
                self.mode = self.MODES[self.MODES.index(self.mode) + 1]
                return self._send(messages)
            raise
        choice = r.choices[0]
        if getattr(choice.message, "refusal", None):
            raise SceneLLMError(f"{self.model} declined the request: {choice.message.refusal}")
        if choice.finish_reason == "length":
            raise SceneLLMError("answer cut off at the token limit")
        usage = getattr(r, "usage", None)
        return choice.message.content or "", {"model": getattr(r, "model", self.model), "structured": self.mode,
                                              "input_tokens": getattr(usage, "prompt_tokens", None),
                                              "output_tokens": getattr(usage, "completion_tokens", None)}


# ---------- keyframes ----------

def _shot(frame: dict) -> tuple[str, str]:
    s = frame.get("shot") or {}
    sid = s.get("id", 0)
    return (sid if isinstance(sid, str) and sid.startswith("s") else f"s{sid}"), str(s.get("type") or "unknown")


def ball_moments(tracked: list[dict]) -> list[tuple[int, str]]:
    """Frames where the ball changes hands or reaches a goal line, with a caption for the model: (index, note).

    Uses the event detector's touches: a release (passer's last touch), a reception (receiver's first touch)
    and the ball reaching a goal line after the last touch. Notes are candidates for the model to confirm."""
    from vision.events import GOAL_LINE_M, L, _ball_series, touches
    runs = touches(tracked)
    out: list[tuple[int, str]] = []
    for a, b in zip(runs, runs[1:]):
        pa, pb = a["player"]["player_id"], b["player"]["player_id"]
        if pa != pb:
            out.append((a["last"], f"ball leaves {pa}"))
            out.append((b["first"], f"ball reaches {pb}"))
    if runs:
        pts = _ball_series(tracked)
        last = runs[-1]
        for i in range(last["last"] + 1, len(pts)):   # the last touch matters only when it is a shot
            if pts[i] and (pts[i][0] <= GOAL_LINE_M or pts[i][0] >= L - GOAL_LINE_M):
                out += [(last["last"], f"ball leaves {last['player']['player_id']}"), (i, "ball at a goal line")]
                break
    return sorted(set(out))


def select_keyframes(tracked: list[dict], max_images: int = 20) -> list[int]:
    """Frames around ball movements first (latest first: a highlight builds to its goal), then one per shot, then
    wide shots topped up about every FILL_S seconds (a lofted pass leaves no tracked handover to anchor on).

    Each release is bracketed by the frame before it, so the model sees the ball leave the passer."""
    if not tracked:
        return []
    moments = ball_moments(tracked)
    wanted: list[int] = []
    for i, note in reversed(moments):
        wanted += [i, i - 1] if note.startswith("ball leaves") else [i]
    shots: dict[str, list[int]] = {}
    for i, f in enumerate(tracked):
        shots.setdefault(_shot(f)[0], []).append(i)
    for idx in shots.values():   # one frame per shot, two for a long wide shot, so no shot is unseen
        span = tracked[idx[-1]]["match_seconds"] - tracked[idx[0]]["match_seconds"]
        wide = _shot(tracked[idx[0]])[1] in ("wide", "unknown")
        wanted += [idx[len(idx) // 3], idx[2 * len(idx) // 3]] if wide and span >= SHOT_SPLIT_S else [idx[len(idx) // 2]]
    for idx in shots.values():
        if _shot(tracked[idx[0]])[1] in ("wide", "unknown"):
            t0 = tracked[idx[0]]["match_seconds"]
            wanted += [i for i in idx if (tracked[i]["match_seconds"] - t0) % FILL_S < 0.21]
    picked: list[int] = []
    for i in wanted:
        if 0 <= i < len(tracked) and i not in picked:
            picked.append(i)
        if len(picked) >= max_images:
            break
    return sorted(picked)


def keyframe_boxes(k: int, tracked: list[dict], boxes=None, debug=None) -> list[dict]:
    """Image boxes with CV ids for frame k: from `boxes`, the players' own image boxes (vision frames carry them),
    else the debug detections matched by track id."""
    if boxes and k in boxes:
        return list(boxes[k])
    frame = tracked[k]
    own = [{"player_id": p["player_id"], "xyxy": p["xyxy"], "team": p.get("team")}
           for p in frame.get("players") or [] if p.get("xyxy")]
    if own:
        ball = (frame.get("ball") or {}).get("xyxy")
        return own + ([{"player_id": "ball", "xyxy": ball, "team": "ball"}] if ball else [])
    rows = (debug or {}).get("detections") or []
    row = next((r for r in rows if r.get("frame") == k), rows[k] if k < len(rows) else None)
    if not row:
        return []
    ids = {p["player_id"][1:]: p for p in tracked[k].get("players") or [] if p.get("player_id")}
    out = []
    for b in row.get("boxes") or []:
        if b.get("cls") == "ball":
            out.append({"player_id": "ball", "xyxy": b["xyxy"], "team": "ball"})
            continue
        p = ids.get(str(b.get("track_id")))
        if p:
            out.append({"player_id": p["player_id"], "xyxy": b["xyxy"], "team": p.get("team")})
    return out


def draw_ids(image, items: list[dict]) -> bytes:
    """RGB array or PIL image + boxes -> JPEG bytes (long edge LONG_EDGE) with id labels drawn."""
    from PIL import Image, ImageDraw, ImageFont
    img = image if isinstance(image, Image.Image) else Image.fromarray(image)
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)
    size = max(14, img.width // 90)
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:   # Pillow < 10.1
        font = ImageFont.load_default()
    for it in items:
        x1, y1, x2, y2 = [float(v) for v in it["xyxy"]]
        pid = it["player_id"]
        rgb = (255, 255, 255) if pid == "ball" else TEAM_RGB.get(pid[:1], (255, 255, 0))
        draw.rectangle([x1, y1, x2, y2], outline=rgb, width=max(2, size // 6))
        tw = draw.textlength(pid, font=font)
        ty = max(0, y1 - size - 4)
        draw.rectangle([x1, ty, x1 + tw + 6, ty + size + 4], fill=(0, 0, 0))
        draw.text((x1 + 3, ty + 1), pid, fill=rgb, font=font)
    scale = LONG_EDGE / max(img.size)
    if scale < 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return buf.getvalue()


# ---------- validation ----------

def _type_ok(value, t) -> bool:
    return {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str),
            "boolean": isinstance(value, bool), "null": value is None,
            "number": isinstance(value, (int, float)) and not isinstance(value, bool)}[t]


def schema_errors(value, schema=SCHEMA, path="$") -> list[str]:
    """The subset of JSON Schema the scene schema uses: type, enum, anyOf, properties, required, additionalProperties."""
    if "anyOf" in schema:
        return [] if any(not schema_errors(value, s, path) for s in schema["anyOf"]) else [f"{path}: no allowed form"]
    if "type" in schema and not _type_ok(value, schema["type"]):
        return [f"{path}: expected {schema['type']}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: {value!r} not in {schema['enum']}"]
    errs = []
    if schema.get("type") == "object":
        props = schema.get("properties", {})
        errs += [f"{path}: missing {k}" for k in schema.get("required", []) if k not in value]
        if schema.get("additionalProperties") is False:
            errs += [f"{path}: unexpected key {k!r}" for k in value if k not in props]
        for k, v in value.items():
            if k in props:
                errs += schema_errors(v, props[k], f"{path}.{k}")
    if schema.get("type") == "array":
        for i, v in enumerate(value):
            errs += schema_errors(v, schema["items"], f"{path}[{i}]")
    return errs


def validate(data, known_ids: set[str], t_max: float) -> tuple[dict, list[str]]:
    """Schema check (raises on failure), then drop what the model may not say: unknown ids, positions, odd times."""
    errs = schema_errors(data)
    if errs:
        raise ValueError("; ".join(errs[:12]))
    rejected = []

    def known(pid):
        if pid is None or pid in known_ids:
            return pid
        rejected.append(f"unknown id {pid!r} set to null")
        return None

    def no_position(text, where):
        if text and POSITION_TEXT.search(text):
            rejected.append(f"{where}: removed text that described a position")
            return ""
        return text

    events = []
    for e in data["events"]:
        if not -0.5 <= e["t"] <= t_max + 0.5:
            rejected.append(f"event at t={e['t']} outside the clip dropped")
            continue
        events.append({**e, "actor_id": known(e["actor_id"]), "receiver_id": known(e["receiver_id"]),
                       "confidence": min(1.0, max(0.0, float(e["confidence"]))),
                       "evidence": no_position(e["evidence"], f"event t={e['t']}")})
    posture = []
    for p in data["posture"]:
        if p["player_id"] not in known_ids:
            rejected.append(f"posture for unknown id {p['player_id']!r} dropped")
            continue
        posture.append(p)
    notes = [n for n in (no_position(n, "note") for n in data["notes"]) if n]
    return {"segments": data["segments"], "events": events, "posture": posture, "notes": notes}, rejected


def _parse(text: str):
    text = (text or "").strip()
    if text.startswith("```"):   # prompt-only mode may fence the JSON
        text = re.sub(r"^```(json)?|```$", "", text, flags=re.M).strip()
    return json.loads(text)


# ---------- the pipeline entry point ----------

def annotate_clip(frames_rgb: list, tracked: list[dict], fps: float, *, client=None, api: str | None = None,
                  max_images: int = 20, boxes: dict | None = None, debug: dict | None = None,
                  out_dir: str | Path | None = None, env=None) -> dict:
    """Keyframes with CV ids drawn -> the configured model -> validated scene facts. Never raises."""
    keys = select_keyframes(tracked, max_images) if frames_rgb else []
    notes: dict[int, list[str]] = {}
    for i, note in ball_moments(tracked) if keys else []:
        notes.setdefault(i, []).append(note)
    keyframes, images, known = [], [], set()
    for k in keys:
        if k >= len(frames_rgb):
            continue
        items = keyframe_boxes(k, tracked, boxes, debug)
        sid, stype = _shot(tracked[k])
        ids = sorted({it["player_id"] for it in items if it["player_id"] != "ball"})
        known.update(ids)
        jpeg = draw_ids(frames_rgb[k], items)
        kf = {"index": k, "t": round(float(tracked[k]["match_seconds"]), 2), "shot_id": sid, "shot_type": stype,
              "ids": ids, "ball": any(it["player_id"] == "ball" for it in items), "vision": notes.get(k, [])}
        if out_dir:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            (Path(out_dir) / f"kf_{k:04d}.jpg").write_bytes(jpeg)
            kf["file"] = f"kf_{k:04d}.jpg"
        keyframes.append(kf)
        seen = f" · vision: {'; '.join(kf['vision'])}" if kf["vision"] else ""
        images.append((f"Frame {k} · t={kf['t']:.2f} s · shot {sid} ({stype}) · ids drawn: "
                       f"{', '.join(ids) or 'none'}{', ball' if kf['ball'] else ''}{seen}", jpeg))
    out = {"prompt_version": PROMPT_VERSION, "keyframes": keyframes}
    replay = (env if env is not None else os.environ).get("SCENE_LLM_REPLAY_FILE")
    if replay and client is None:   # a saved model answer: local runs and evaluations without a model or key
        client = _Replay(Path(replay))
        api = "replay"
    if client is not None:
        cfg = {"api": api or "openai", "model": "scene-model", "endpoint": "", "api_key": "", "auth": "key",
               "provider": "test", "timeout": 60}
        cfg.update({k: v for k, v in (config(env)[0] or {}).items() if k == "model"})
    else:
        cfg, why = config(env)
        if not cfg:
            return {**out, "skipped": why}
    out.update(api=cfg["api"], model=cfg["model"])
    if not images:
        return {**out, "skipped": "no frames to annotate"}
    t_max = float(tracked[-1]["match_seconds"]) if tracked else 0.0
    closing = (f"Annotate these {len(images)} keyframes from a {t_max:.1f} s clip ({fps:g} fps tracking). "
               "Return only the JSON object.")
    try:
        adapter = make_adapter(cfg, client)
        text, meta = adapter.complete(PROMPT, images, closing)
        attempts = 1
        try:
            clean, rejected = validate(_parse(text), known, t_max)
        except (ValueError, json.JSONDecodeError) as exc:   # one repair round, then give up
            attempts = 2
            text, meta = adapter.complete(PROMPT, images, closing, repair=(
                text, f"Your answer failed validation: {exc}. Return the corrected JSON object only, "
                      "matching the schema, with no other text."))
            clean, rejected = validate(_parse(text), known, t_max)
    except ImagesUnsupported as exc:
        return {**out, "skipped": f"model has no image input: {exc}"}
    except (SceneLLMError, ValueError, json.JSONDecodeError) as exc:
        return {**out, "error": str(exc)}
    except Exception as exc:   # SDK/network errors after the SDK's own retries
        return {**out, "error": f"{type(exc).__name__}: {exc}"}
    for e in clean["events"]:   # the keyframe each fact was read from, for the review screen
        e["keyframe"] = min(keyframes, key=lambda kf: abs(kf["t"] - e["t"]))["index"]
    for p in clean["posture"]:
        p["keyframe"] = min(keyframes, key=lambda kf: abs(kf["t"] - p["t"]))["index"]
    return {**out, **clean, "model": meta.get("model") or cfg["model"], "rejected": rejected, "attempts": attempts,
            "usage": {k: meta.get(k) for k in ("input_tokens", "output_tokens")}, "structured": meta.get("structured")}


# ---------- hints for vision/events.py ----------

HINT_DOC = {
    "aerial": "event.lofted. PASS ignores lane defenders for a lofted pass and keeps the 0.10 aerial penalty.",
    "into_space": "event.into_space. The played option is scored at the pass end point (arrival), not at the "
                    "receiver's position when the pass is played.",
    "p_header": "1.0 when the receiver's next shot is a header, 0.0 when it is with the foot or the pass is on the "
                "ground; null when unknown (use the historical pHeader for lofted passes). "
                "shotXG = pHeader * xG(header) + (1 - pHeader) * xG(foot).",
}


def to_event_hints(result: dict, min_confidence: float = 0.5) -> dict:
    """Per-pass flags the formulas need, keyed by receiver id, from an annotate_clip result."""
    events = sorted(result.get("events") or [], key=lambda e: e["t"])
    passes = []
    for i, e in enumerate(events):
        if e["type"] != "pass" or e["confidence"] < CORROBORATED_MIN:
            continue
        rid = e.get("receiver_id")
        follow = next((f for f in events[i + 1:] if f["actor_id"] == rid and f["t"] - e["t"] <= FOLLOW_S
                       and f["type"] in ("shot", "header", "goal")), None)
        if follow and (follow["type"] == "header" or follow.get("body_part") == "head"):
            p_header = 1.0
        elif (follow and follow.get("body_part") == "foot") or e.get("lofted") is False:
            p_header = 0.0
        else:
            p_header = None
        passes.append({"t": e["t"], "actor_id": e.get("actor_id"), "receiver_id": rid, "aerial": e.get("lofted"),
                       "into_space": e.get("into_space"), "p_header": p_header, "confidence": e["confidence"],
                       "keyframe": e.get("keyframe")})
    for p in passes:   # an unseen reception: the passer's teammate who shoots next is the receiver
        if p["receiver_id"] is None and p.get("actor_id"):
            nxt = next((e for e in events if e["t"] > p["t"] and e["t"] - p["t"] <= 2 * FOLLOW_S
                        and e["type"] in ("shot", "header", "goal") and e.get("actor_id")
                        and e["actor_id"][:1] == p["actor_id"][:1] and e["actor_id"] != p["actor_id"]), None)
            if nxt:
                p.update(receiver_id=nxt["actor_id"], receiver_inferred=True)
                if p["p_header"] is None and (nxt["type"] == "header" or nxt.get("body_part") in ("head", "foot")):
                    p["p_header"] = 1.0 if nxt["type"] == "header" or nxt.get("body_part") == "head" else 0.0
    for p in passes:   # a receiver who goes on to shoot for the passer's team confirms the pass
        if p["receiver_id"] and any(e["type"] in ("shot", "header", "goal") and e.get("actor_id") == p["receiver_id"]
                                    and 0 < e["t"] - p["t"] <= 2 * FOLLOW_S for e in events):
            p["corroborated"] = True
            p["confidence"] = max(p["confidence"], CORROBORATED_CONF)
    passes = [p for p in passes if p["receiver_id"] and (p["confidence"] >= min_confidence or p.get("corroborated"))]
    goals = [e["t"] for e in events if e["type"] == "goal" and e["confidence"] >= min_confidence]
    shots = [{"t": e["t"], "actor_id": e["actor_id"], "type": e["type"], "confidence": e["confidence"]}
             for e in events if e["type"] in ("shot", "header", "goal") and e.get("actor_id")
             and e["confidence"] >= min_confidence]
    return {"prompt_version": result.get("prompt_version"), "model": result.get("model"), "passes": passes,
            "goals": goals, "shots": shots,
            "by_receiver": {p["receiver_id"]: p for p in passes}, "doc": HINT_DOC}


def hint_for_pass(hints: dict, receiver_id: str, t: float, tolerance_s: float = 1.5) -> dict | None:
    """The hint for a detected pass: same receiver, nearest time within tolerance_s."""
    cands = [p for p in (hints or {}).get("passes", []) if p["receiver_id"] == receiver_id
             and abs(p["t"] - t) <= tolerance_s]
    return min(cands, key=lambda p: abs(p["t"] - t)) if cands else None


def scene_for(frames_rgb: list, tracked: list[dict], fps: float, *, debug=None, out_dir=None) -> dict:
    """annotate_clip plus its pass hints, the shape stored on a highlight job: {"scene", "scene_hints"}."""
    scene = annotate_clip(frames_rgb, tracked, fps, debug=debug, out_dir=out_dir)
    ok = not (scene.get("skipped") or scene.get("error"))
    return {"scene": scene, "scene_hints": to_event_hints(scene) if ok else {}}

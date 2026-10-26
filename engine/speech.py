"""Commentary voice: Azure AI Speech neural text to speech, in an excited style, as MP3.

Configured on the API (all optional; without them the page reads the line with the browser's own voice):
    AZURE_SPEECH_REGION        e.g. northcentralus (the Speech or Foundry resource's region)
    AZURE_SPEECH_KEY           a resource key, or
    AZURE_SPEECH_RESOURCE_ID   the resource's ARM id: Entra ID with the managed identity (role: Cognitive Services
                               Speech User; the resource needs a custom subdomain)
    AZURE_SPEECH_VOICE         default en-US-GuyNeural (has the "excited" and "shouting" styles)
    AZURE_SPEECH_STYLE         default excited; "none" for no style
Audio is cached by its SSML, so a moment is synthesised once.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import urllib.request
from pathlib import Path

DEFAULT_VOICE = "en-US-GuyNeural"
DEFAULT_STYLE = "excited"
OUTPUT = "audio-24khz-48kbitrate-mono-mp3"
MAX_CHARS = 600
_TOKEN: dict = {}
_LOCK = threading.Lock()


def configured() -> bool:
    return bool(os.getenv("AZURE_SPEECH_REGION") and (os.getenv("AZURE_SPEECH_KEY") or os.getenv("AZURE_SPEECH_RESOURCE_ID")))


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def ssml(text: str, tags: list[str] | None = None, voice: str | None = None, style: str | None = None) -> str:
    """The commentary as SSML: the voice's excited style, stronger and a touch faster for a big goal."""
    tags = set(tags or [])
    voice = voice or os.getenv("AZURE_SPEECH_VOICE") or DEFAULT_VOICE
    style = (style or os.getenv("AZURE_SPEECH_STYLE") or DEFAULT_STYLE).strip()
    goal = bool(tags & {"opener", "equaliser", "go_ahead", "comeback", "extends", "pulls_back"})
    big = goal and ("late" in tags or bool(tags & {"go_ahead", "comeback"}))
    degree, rate = ("1.8", "+12%") if big else ("1.4", "+8%") if goal else ("1.0", "+0%")
    body = f"<prosody rate='{rate}'>{_escape(text[:MAX_CHARS])}</prosody>"
    if style and style.lower() != "none":
        body = f"<mstts:express-as style='{style}' styledegree='{degree}'>{body}</mstts:express-as>"
    lang = "-".join(voice.split("-")[:2]) or "en-US"
    return (f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
            f"xmlns:mstts='https://www.w3.org/2001/mstts' xml:lang='{lang}'><voice name='{voice}'>{body}</voice></speak>")


def _auth_header() -> dict:
    key = os.getenv("AZURE_SPEECH_KEY")
    if key:
        return {"Ocp-Apim-Subscription-Key": key}
    resource = os.environ["AZURE_SPEECH_RESOURCE_ID"]
    with _LOCK:   # a managed-identity token, reused until a few minutes before it expires
        if _TOKEN.get("expires", 0) - 300 < time.time():
            from azure.identity import DefaultAzureCredential
            tok = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default")
            _TOKEN.update(value=tok.token, expires=tok.expires_on)
        return {"Authorization": f"Bearer aad#{resource}#{_TOKEN['value']}"}


def _cache_dir() -> Path:
    from highlights.store import highlights_dir
    return highlights_dir() / "_speech"


EXCITED_VOICES = ("en-US-",)   # voice families with the "excited" style; others speak the line plainly


def voice_for(lang: str | None) -> str:
    """The neural voice for a commentary language (engine.translate.LANGUAGES); AZURE_SPEECH_VOICE for English."""
    from engine.translate import language, voice
    code = language(lang)
    return (os.getenv("AZURE_SPEECH_VOICE") or DEFAULT_VOICE) if code == "en" else voice(code)


def synthesize(text: str, tags: list[str] | None = None, lang: str | None = None) -> bytes | None:
    """MP3 bytes for the line in ``lang``'s voice, or None when speech is not configured. Raises on a failed call."""
    if not (configured() and text):
        return None
    name = voice_for(lang)
    style = None if name.startswith(EXCITED_VOICES) else "none"
    doc = ssml(text, tags, voice=name, style=style)
    path = _cache_dir() / f"{hashlib.sha256(doc.encode()).hexdigest()[:32]}.mp3"
    if path.exists():
        return path.read_bytes()
    region = os.environ["AZURE_SPEECH_REGION"]
    req = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1", data=doc.encode("utf-8"), method="POST",
        headers={**_auth_header(), "Content-Type": "application/ssml+xml", "X-Microsoft-OutputFormat": OUTPUT,
                 "User-Agent": "gamelens"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        audio = resp.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio)
    return audio

"""Auto-translated commentary, with player and team names kept as they are.

The line goes first to a language model on Microsoft Foundry (gpt-5-mini), asked to say it the way a TV
commentator in that language would. The answer is used only when every name and number of the English line
comes back unchanged (and a language with its own script is written in it), and when the model, proofreading its
answer against the English, finds nothing added or left out (one retry with the problem it named); otherwise, when the model fails,
or for Lao (TRANSLATOR_ONLY), Azure AI Translator translates it word for word.

Configured on the API (optional; without either the commentary stays in English):
    TRANSLATE_LLM_ENDPOINT      the Foundry OpenAI v1 endpoint, e.g. https://<name>.openai.azure.com/openai/v1/
    TRANSLATE_LLM_MODEL         its deployment name (gpt-5-mini)
    TRANSLATE_LLM_API_KEY       a key, or nothing: the managed identity signs in
    AZURE_TRANSLATOR_ENDPOINT   the resource's custom domain, e.g. https://<name>.cognitiveservices.azure.com
    AZURE_TRANSLATOR_KEY        a key (with AZURE_TRANSLATOR_REGION), or nothing: the managed identity signs in
                                (role for both: Cognitive Services User on the resource)
Spanish, French, Lao and Thai first, then the others, each with a native neural voice for engine.speech. Translations are cached by language and text.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request

# code -> (name in that language, neural voice). Voices without the "excited" style speak the line plainly.
LANGUAGES = {   # in the order the page lists them
    "en": ("English", "en-US-GuyNeural"),
    "es": ("Español", "es-ES-AlvaroNeural"),
    "fr": ("Français", "fr-FR-HenriNeural"),
    "lo": ("ລາວ", "lo-LA-ChanthavongNeural"),
    "th": ("ไทย", "th-TH-NiwatNeural"),
    "pt": ("Português", "pt-BR-AntonioNeural"),
    "de": ("Deutsch", "de-DE-ConradNeural"),
    "it": ("Italiano", "it-IT-DiegoNeural"),
    "ja": ("日本語", "ja-JP-KeitaNeural"),
    "ko": ("한국어", "ko-KR-InJoonNeural"),
    "zh-Hans": ("中文", "zh-CN-YunxiNeural"),
    "vi": ("Tiếng Việt", "vi-VN-NamMinhNeural"),
    "id": ("Bahasa Indonesia", "id-ID-ArdiNeural"),
    "ar": ("العربية", "ar-SA-HamedNeural"),
}
ENGLISH_NAMES = {"en": "English", "es": "Spanish (Spain)", "fr": "French", "lo": "Lao", "th": "Thai",
                 "pt": "Brazilian Portuguese", "de": "German", "it": "Italian", "ja": "Japanese", "ko": "Korean",
                 "zh-Hans": "Simplified Chinese", "vi": "Vietnamese", "id": "Indonesian", "ar": "Arabic"}
TRANSLATOR_ONLY = {"lo"}   # gpt-5-mini's Lao is poor (mixes in Thai, garbles words); the Translator's is better
SCRIPTS = {   # languages with their own script: the letters an answer must be written in (Lao is not Thai)
    "lo": ("Lao script", "\u0e80-\u0eff"), "th": ("Thai script", "\u0e00-\u0e7f"), "ar": ("Arabic script", "\u0600-\u06ff"),
    "ko": ("Hangul", "\uac00-\ud7af\u1100-\u11ff"), "ja": ("Japanese kana and kanji", "\u3040-\u30ff\u4e00-\u9fff"),
    "zh-Hans": ("simplified Chinese characters", "\u4e00-\u9fff")}
LLM_PROMPT = """You translate live football commentary for TV. Say the line in {language}{script} the way an excited \
{language}-speaking TV football commentator would say it on air: the football words, exclamations and rhythm that \
commentators in {language} really use, never a word-for-word rendering (an English exclamation becomes the one a \
{language} commentator would shout). Same meaning and energy, grammatical.
What the pass-analysis terms mean (translate the meaning, not the words):
- "the pass went past N defenders": it took N defenders out of the play (they did not stop it).
- "only one in three get through": of passes like this, only one in three reaches the teammate.
- "32% arrived, 16% score.": such a pass reaches the teammate 32% of the time, and 16% of the time ends in a goal.
- "Line-breaker": a pass that breaks through the opponent's defensive line. "The brave pass": a risky, bold pass.
  "Best option on the pitch": the best pass available. "Smart pass": a clever pass.
Keep these names exactly as written, never translated or transliterated: {names}.
Keep every number: digits and percentages stay digits ("32%"), a number word may become a word or a digit. Add nothing, leave nothing out: no detail the line does not have (no header,
no shot type). Never say or hint that a team has won or will win the match. Reply with the translated line only."""
CHECK_PROMPT = """You proofread {language} translations of live football commentary. Compare the translation \
with the English line. Reply "OK" if it says the same thing; idiomatic commentator wording and exclamations in \
{language} are wanted, not a problem, and numbers may be words or digits. Reply "PROBLEM: <what is wrong, in English>" \
if it adds a detail the English does not have (a header, a shot type, a result), leaves a fact out, changes who did \
what or a number, adds a remark or comment of its own (an extra exclamation of the same feeling is fine), or says \
or hints that a team has won or will win.
Meanings: "the pass went past N defenders" = it took them out of the play (a translation saying the defenders \
stopped, blocked or intercepted the ball reverses it: PROBLEM; eliminated, beat, left behind or got past those \
defenders says the same thing: OK); "one in three get through" = one in three such \
passes reach the teammate; "32% arrived, 16% score" = the odds that such a pass arrives and that it ends in a goal."""
FALLBACK_RETRY_S = 600   # a Translator answer that stood in for the model is kept this long, then the model is asked again
_CACHE: dict[tuple[str, str], str] = {}
_FALLBACK: dict[tuple[str, str], float] = {}
log = logging.getLogger(__name__)
_TOKEN: dict = {}
_LOCK = threading.Lock()


def llm_configured() -> bool:
    return bool(os.getenv("TRANSLATE_LLM_ENDPOINT") and os.getenv("TRANSLATE_LLM_MODEL"))


def configured() -> bool:
    return llm_configured() or bool(os.getenv("AZURE_TRANSLATOR_ENDPOINT"))


def language(code: str | None) -> str:
    """A supported language code for ``code`` (exact, then its base: "fr-CA" -> "fr", "zh" -> "zh-Hans"), else "en"."""
    if not code:
        return "en"
    if code in LANGUAGES:
        return code
    base = code.split("-")[0].lower()
    return next((c for c in LANGUAGES if c.split("-")[0].lower() == base), "en")


def voice(code: str) -> str:
    return LANGUAGES.get(language(code), LANGUAGES["en"])[1]


def _bearer() -> dict:
    with _LOCK:   # managed identity token, reused until a few minutes before it expires
        if _TOKEN.get("expires", 0) - 300 < time.time():
            from azure.identity import DefaultAzureCredential
            tok = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default")
            _TOKEN.update(value=tok.token, expires=tok.expires_on)
        return {"Authorization": f"Bearer {_TOKEN['value']}"}


def _headers() -> dict:
    key = os.getenv("AZURE_TRANSLATOR_KEY")
    if key:
        out = {"Ocp-Apim-Subscription-Key": key}
        if os.getenv("AZURE_TRANSLATOR_REGION"):
            out["Ocp-Apim-Subscription-Region"] = os.environ["AZURE_TRANSLATOR_REGION"]
        return out
    return _bearer()


def protect(text: str, keep: list[str]) -> str:
    """HTML for the Translator with every name in ``keep`` marked notranslate (longest names first)."""
    out = html.escape(text, quote=False)
    for name in sorted({k for k in keep if k}, key=len, reverse=True):
        safe = html.escape(name, quote=False)
        out = re.sub(rf"(?<![\w>]){re.escape(safe)}(?![\w<])", f'<span class="notranslate">{safe}</span>', out)
    return out


def unprotect(text: str) -> str:
    return html.unescape(re.sub(r"</?span[^>]*>", "", text))


def faithful(source: str, out: str, keep: list[str], to: str | None = None) -> bool:
    """Every name and number of the English line is in the translation, nothing more was written, and a
    language with its own script is written in it."""
    names = [k for k in keep if k and k in source]
    if to in SCRIPTS:
        rest = re.sub("|".join(map(re.escape, names)) or "$^", "", out)
        letters = [c for c in rest if c.isalpha()]
        if not letters or sum(bool(re.match(f"[{SCRIPTS[to][1]}]", c)) for c in letters) < 0.9 * len(letters):
            return False
    return (bool(out) and all(n in out for n in names) and len(out) < 3 * len(source) + 40
            and _numbers_kept(source, re.sub("|".join(map(re.escape, names)) or "$^", "", out)))


_WORD_NUMBERS = {w: str(i) for i, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                                                  "nine", "ten"])}


def _numbers_kept(source: str, out: str) -> bool:
    """Every digit number of the English is in the translation, and any digit in the translation is one the English
    has (as digits or as an English number word: "five" may come back as 5)."""
    src, got = re.findall(r"\d+(?:\.\d+)?", source), re.findall(r"\d+(?:\.\d+)?", out)
    words = {_WORD_NUMBERS[w] for w in re.findall(r"[a-z]+", source.lower()) if w in _WORD_NUMBERS}
    return all(got.count(n) >= src.count(n) for n in set(src)) and set(got) <= set(src) | words


def _chat(system: str, user: str) -> str:
    body = {"model": os.environ["TRANSLATE_LLM_MODEL"], "reasoning_effort": "minimal", "max_completion_tokens": 600,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    key = os.getenv("TRANSLATE_LLM_API_KEY")
    auth = {"api-key": key} if key else _bearer()
    url = os.environ["TRANSLATE_LLM_ENDPOINT"].rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={**auth, "Content-Type": "application/json"})
    for attempt in range(2):   # one retry when Foundry is busy (429) or hiccups (5xx)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return (json.loads(resp.read())["choices"][0]["message"].get("content") or "").strip()
        except urllib.error.HTTPError as e:
            if attempt or not (e.code == 429 or e.code >= 500):
                raise
            time.sleep(1.5)


def check(text: str, out: str, to: str) -> str | None:
    """The model proofreads a translation against the English: None when it says the same thing, else the problem."""
    answer = _chat(CHECK_PROMPT.format(language=ENGLISH_NAMES[to]), f"English: {text}\n{ENGLISH_NAMES[to]}: {out}")
    if answer.upper().startswith("OK"):
        return None
    return answer.split(":", 1)[-1].strip() or answer or "no answer"


def llm_translate(text: str, to: str, keep: list[str]) -> str:
    """The commentator's version from the Foundry model, proofread by the model against the English (one retry
    with the problem it found); raises when it fails, changes a name or number, or adds or drops something."""
    names = sorted({k for k in keep if k and k in text}, key=len, reverse=True)
    system = LLM_PROMPT.format(language=ENGLISH_NAMES[to], names=", ".join(names) or "none",
                               script=f" ({LANGUAGES[to][0]}, written in {SCRIPTS[to][0]})" if to in SCRIPTS else "")
    problem = None
    for _ in range(2):
        hint = f"\n\nA previous translation was rejected: {problem}. Avoid that." if problem else ""
        out = _chat(system + hint, text).strip('"\u201c\u201d')
        if not faithful(text, out, keep, to):
            problem = "it lost a name or number or is in the wrong script"
            continue
        problem = check(text, out, to)
        if problem is None:
            return out
    raise ValueError(f"the model's translation was rejected: {problem} ({out!r})")


def for_translation(text: str) -> str:
    """The English said plainly before it is translated: "five defenders cut out" (cut out of the play) also reads as
    "intercepted" in football English, so the translators get "the pass went past five defenders"."""
    return re.sub(r"\b(\w+) (defenders?) cut out\b", r"the pass went past \1 \2", text)


def translate(text: str, to: str, keep: list[str] | None = None) -> str:
    """``text`` in language ``to`` (names in ``keep`` untouched). English, empty text or no translation set up:
    unchanged. The language model first, then the Translator; raises when both fail so the caller can fall back
    to English."""
    to = language(to)
    if to == "en" or not text or not configured():
        return text
    text = for_translation(text)
    key = (to, text)
    if key in _CACHE and _FALLBACK.get(key, float("inf")) > time.time():
        return _CACHE[key]
    use_llm = llm_configured() and to not in TRANSLATOR_ONLY
    if use_llm:
        try:
            _CACHE[key] = llm_translate(text, to, keep or [])
            _FALLBACK.pop(key, None)
            return _CACHE[key]
        except Exception as e:
            log.warning("model translation to %s failed, using the Translator: %s", to, e)
            if not os.getenv("AZURE_TRANSLATOR_ENDPOINT"):
                raise
    endpoint = os.environ["AZURE_TRANSLATOR_ENDPOINT"].rstrip("/")
    url = f"{endpoint}/translator/text/v3.0/translate?api-version=3.0&from=en&to={to}&textType=html"
    body = json.dumps([{"Text": protect(text, keep or [])}]).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={**_headers(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        out = unprotect(json.loads(resp.read())[0]["translations"][0]["text"])
    _CACHE[key] = out
    if use_llm:
        _FALLBACK[key] = time.time() + FALLBACK_RETRY_S
    return out

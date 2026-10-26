"""Plain-language explanations and accessible outputs.

Provider order:
  1. Microsoft Foundry / Azure OpenAI  (AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT)
  2. Built-in templates                (always available, no network)

The LLM only rewrites numbers the engine already computed. It is told never to invent stats.
"""
import json
import os
import re
from functools import lru_cache

from .assist import easy_read, verdict_template

SYSTEM = ("You explain football decisions for fans, including blind and deaf fans. "
          "Use only the numbers given. Never invent players, stats or events. "
          "Reply as JSON with keys: why, audio_description, caption, easy_read. "
          "Keep each under 40 words. The match is synthetic and fictional.")


def _templates(analysis: dict) -> dict:
    m = analysis["moment"]
    who, team, minute, title = m.get("player") or "A player", m["team_name"], m["minute"], m["title"]
    out = {
        "audio_description": f"{minute}. {title} for {team}. {who}.",
        "caption": f"[{minute}] {title.upper()}: {who} ({team}). Score {m['score']}.",
        "easy_read": f"{title}. {who} plays for {team}. The score is {m['score']}.",
        "why": f"{title} by {who}.",
    }
    if analysis.get("available"):
        opts = {o["key"]: o for o in analysis["options"]}
        chosen, best = opts[analysis["chosen"]], opts[analysis["best"]]
        out["why"] = (f"{who} chose to {chosen['label'].lower()} (goal chance {chosen['reward']:.2f}). "
                      + ("That was the best option." if best is chosen or analysis["gap"] < 0.005 else
                         f"{best['label']} scored higher with this risk setting: "
                         f"reward {best['reward']:.2f}, risk {best['risk']:.2f}."))
    return out


def moment_text(moment: dict, assist: dict | None, teams: dict) -> dict:
    """The accessible outputs for a moment, from the pass analysis (templates only: no language model changes a number).

    moment: Moment.summary(); assist: build_assist() with its sentence, or None; teams: {"home": name, "away": name}.
    A goal with an assisting pass is described by the pass and its verdict; any other moment by what happened."""
    who, team, minute, title = moment.get("player") or "A player", moment["team_name"], moment["minute"], moment["title"]
    out = {"audio_description": f"{minute}. {title} for {team}. {who}.",
           "caption": f"[{minute}] {title.upper()}: {who} ({team}). Score {moment['score']}.",
           "easy_read": f"{title}. {who} plays for {team}. The score is {moment['score']}.",
           "why": f"{title} by {who}.", "provider": "templates"}
    if assist:
        passer = assist["pass"]["passer"]
        sentence = assist.get("sentence") or verdict_template(assist["payload"])
        out["why"] = sentence
        out["audio_description"] = f"{minute}. {title} for {team}. {assist['verdict']['label']}. {sentence}"
        out["caption"] = f"[{minute}] {title.upper()}: {who} ({team}), pass from {passer}. Score {moment['score']}."
        if moment["kind"] == "goal":
            home, away = (int(n) for n in (moment["score"].split("-") + ["0"])[:2])
            if moment.get("team") == "home":   # the score before the goal: the match is live, so the result is not given away
                home = max(0, home - 1)
            else:
                away = max(0, away - 1)
            out["easy_read"] = easy_read(assist["payload"], team, passer,
                                         f"{teams['home']} {home}, {teams['away']} {away}")
    return out


def _azure(analysis: dict) -> dict | None:
    ep, key, dep = (os.getenv("AZURE_OPENAI_ENDPOINT"), os.getenv("AZURE_OPENAI_API_KEY"),
                    os.getenv("AZURE_OPENAI_DEPLOYMENT"))
    if not (ep and key and dep):
        return None
    from openai import AzureOpenAI  # pip install openai
    client = AzureOpenAI(azure_endpoint=ep, api_key=key,
                         api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"))
    r = client.chat.completions.create(model=dep, response_format={"type": "json_object"}, max_tokens=300,
                                       messages=[{"role": "system", "content": SYSTEM},
                                                 {"role": "user", "content": json.dumps(analysis)}])
    return json.loads(r.choices[0].message.content)


def explain(analysis: dict) -> dict:
    base = _templates(analysis)
    for provider in (_azure,):
        try:
            got = provider(analysis)
        except Exception as exc:  # fall back to templates, never break the demo
            got = None
            base["provider_error"] = f"{provider.__name__}: {exc}"
        if got:
            base.update({k: v for k, v in got.items() if k in base and isinstance(v, str)})
            base["provider"] = provider.__name__.strip("_")
            return base
    base["provider"] = "templates"
    return base


VERDICT_SYSTEM = (
    "You write the one short line under a football pass verdict, for fans including blind and deaf fans. "
    "Write one or two short lines, at most 20 words in all, in the same style as the given template line: first how rarely a pass "
    "like this works (keep its fraction, such as 2/3 or 3/5), then what this pass did. Examples of the style: "
    "\"2/3 never make it. This one sliced through 5 defenders.\" / \"3/5 get picked off. Kyle went for it anyway, and it landed.\" / "
    "\"Only 2/5 get through. Still the best ball on the pitch.\" "
    "Positive, plain words, no jargon. Never criticise the passer or the pass. Never mention who wins the match or the score. "
    "Use only numbers that appear in the JSON facts or in the template line. Never invent players, numbers or stats. "
    "Do not repeat the verdict's name. Reply with the line only. The match is synthetic and fictional.")
VERDICT_TIMEOUT_S, VERDICT_MAX_WORDS = 4, 25
_WORD_NUMS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}


def _num_forms(v: float) -> set[float]:
    """A payload value and its percent forms (0.34 -> 34, 34.0)."""
    return {round(v, 4), round(v * 100, 4), round(v * 100, 1), float(round(v * 100))}


def _numbers_in(text: str) -> set[float]:
    """Every number in a text, as digits and as number words ("one" counts only before "in"/"of": "this one" is not a number)."""
    found = {round(float(n), 4) for n in re.findall(r"\d+(?:\.\d+)?", text)}
    for m in re.finditer(r"\b(" + "|".join(_WORD_NUMS) + r")\b(\s+(?:in|of)\b)?", text.lower()):
        if m.group(1) != "one" or m.group(2):
            found.add(float(_WORD_NUMS[m.group(1)]))
    return found


def valid_sentence(sentence: str, payload: dict) -> bool:
    """Reject empty or long replies and any number (digits or words) not in the facts or in the computed phrases."""
    if not sentence or len(sentence.split()) > VERDICT_MAX_WORDS:
        return False
    allowed = {round(payload["value_ratio_vs_next_best"], 4)}
    for k in ("played", "next_best", "safe"):
        f = payload[k]
        for key in ("arrives", "goal", "value"):
            allowed |= _num_forms(f[key])
        allowed.add(float(f["cut_out"]))
    allowed |= _numbers_in(payload.get("line", ""))
    return _numbers_in(sentence) <= allowed


def _verdict_azure(payload: dict) -> str | None:
    ep, key, dep = (os.getenv("AZURE_OPENAI_ENDPOINT"), os.getenv("AZURE_OPENAI_API_KEY"),
                    os.getenv("AZURE_OPENAI_DEPLOYMENT"))
    if not (ep and key and dep):
        return None
    from openai import AzureOpenAI
    client = AzureOpenAI(azure_endpoint=ep, api_key=key, timeout=VERDICT_TIMEOUT_S, max_retries=0,
                         api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"))
    r = client.chat.completions.create(model=dep, max_tokens=80,
                                       messages=[{"role": "system", "content": VERDICT_SYSTEM},
                                                 {"role": "user", "content": json.dumps(payload)}])
    return r.choices[0].message.content


def verdict_sentence(payload: dict) -> tuple[str, str]:
    """One positive sentence for the verdict and where it came from: azure-openai | template."""
    for source, provider in (("azure-openai", _verdict_azure),):
        try:
            got = (provider(payload) or "").strip().strip('"')
        except Exception:  # slow, down or misconfigured: try the next source
            continue
        if valid_sentence(got, payload):
            return got, source
    return verdict_template(payload), "template"


@lru_cache(maxsize=512)
def _verdict_cached(moment_id: str, payload: str) -> tuple[str, str]:
    return verdict_sentence(json.loads(payload))


def cached_verdict_sentence(moment_id: str, payload: dict) -> tuple[str, str]:
    """verdict_sentence, once per moment."""
    return _verdict_cached(moment_id, json.dumps(payload, sort_keys=True))

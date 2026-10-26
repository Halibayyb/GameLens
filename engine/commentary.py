"""Commentary: a short, passionate line for a moment, built from the analysis, never from imagination.

The situation is worked out from the data: what the goal does to the score (opener, equaliser, go-ahead, lead
extended, comeback), how late it is, which team had the momentum in the minutes before (engine.momentum),
and how the pass and the shot came about (engine.assist). The line is then written from templates in a
commentator's voice: two short sentences, only names and numbers the data holds. A late go-ahead goal asks
"could that be the winner?" rather than saying it is, so a replay never gives the result away.

When the pass analysis has its verdict and odds, the line is only that: "Line-breaker from Nick: only one in three get
through, five defenders cut out." (no reaction first, so it fits the analytics clip). The reaction line above is for a
goal without that analysis.

A goal's video is captioned in three parts (``captions``): the line over the analytics clip, then over the smart view the
odds ("32% arrived, 16% score.") and a closing exclamation picked by the verdict. All three come from here, so they are
translated and spoken by the same voice.

The wording is picked by the moment id, so the same moment always reads the same (captions, speech).
"""
from __future__ import annotations

import hashlib

from engine.assist import odds

LATE_MIN = 80            # a goal from here on is a late goal
MOMENTUM_WINDOW = 8      # minutes of pressure before the moment that set the mood
ON_TOP = 0.25            # pressure share (|value| on the -1..1 scale) that counts as one team on top
LONG_SHOT_XG = 0.07      # a goal from a chance this small came from almost nothing
FINISH = {"line": "Incredible vision!", "brave": "Sensational!", "best": "Beautiful pass!", "smart": "What a ball!"}
WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def _pick(options: list[str], key: str) -> str:
    return options[int(hashlib.sha1(key.encode()).hexdigest(), 16) % len(options)]


def _score(text: str) -> tuple[int, int]:
    try:
        h, a = (int(v) for v in str(text).split("-")[:2])
        return h, a
    except ValueError:
        return 0, 0


def momentum(pressure: dict | None, minute: float, team: str) -> str | None:
    """'with' (the scorer's team was on top), 'against' (the other team was), 'swing' (it changed hands
    in the window), or None (balanced or unknown), from the minutes just before ``minute``."""
    values = (pressure or {}).get("values") or []
    if not values or team not in ("home", "away"):
        return None
    sign = 1 if team == "home" else -1
    lo, hi = max(0, int(minute) - MOMENTUM_WINDOW), max(0, min(len(values), int(minute)))
    window = [sign * v for v in values[lo:hi]]
    if not window:
        return None
    mean = sum(window) / len(window)
    early, late = window[: len(window) // 2], window[len(window) // 2:]
    if early and late and min(early) < -ON_TOP and max(late) > ON_TOP and sum(late) / len(late) > 0:
        return "swing"
    if mean >= ON_TOP:
        return "with"
    if mean <= -ON_TOP:
        return "against"
    return None


def situation(moment: dict, goals_before: list[dict], pressure: dict | None) -> dict:
    """The facts a commentator reacts to, for one moment summary (engine.moments.Moment.summary)."""
    team = moment.get("team")
    minute = float(moment.get("elapsed") or 0)
    facts = {"kind": moment.get("kind"), "late": minute >= LATE_MIN, "momentum": momentum(pressure, minute, team)}
    if moment.get("kind") != "goal":
        return facts
    home, away = _score(moment.get("score"))
    before = (home - (team == "home"), away - (team == "away"))
    mine, theirs = (before if team == "home" else before[::-1])
    was_behind = any((g["team"] != team) and _lead(g, team) < 0 for g in goals_before)
    if mine + theirs == 0:
        swing = "opener"
    elif mine + 1 == theirs:
        swing = "equaliser"
    elif mine == theirs:
        swing = "comeback" if was_behind else "go_ahead"
    elif mine > theirs:
        swing = "extends"
    else:
        swing = "pulls_back"
    facts.update(swing=swing, score_before=list(before), score_after=[home, away])
    return facts


def _lead(goal: dict, team: str) -> int:
    """The given team's lead after an earlier goal (negative: behind)."""
    h, a = _score(goal.get("score"))
    return (h - a) if team == "home" else (a - h)


def line(moment: dict, assist: dict | None, facts: dict, teams: dict) -> str:
    """Two short sentences: the reaction (situation, momentum) and what happened (pass, finish)."""
    key = str(moment.get("id") or moment.get("minute"))
    team = moment.get("team_name") or teams.get(moment.get("team"), "")
    other = teams.get("away" if moment.get("team") == "home" else "home", "the opposition")
    who = moment.get("player") or "they"
    kind = facts.get("kind")
    if kind != "goal":
        return _not_a_goal(moment, key, who, team)

    react = {
        "opener": ["The deadlock is broken!", f"First blood to {team}!", f"{team} strike first!"],
        "equaliser": [f"{team} are level!", "All square again!", f"{team} hit straight back!"],
        "go_ahead": [f"{team} take the lead!", f"{team} in front!"],
        "comeback": [f"What a turnaround, {team} lead!", f"From behind to in front, {team} have turned it round!"],
        "extends": [f"{team} pull further clear!", f"{team} double down!"],
        "pulls_back": [f"{team} pull one back!", f"Game on, {team} are back in it!"],
    }[facts.get("swing", "opener")]
    first = _pick(react, key)
    if facts.get("late") and facts.get("swing") in ("go_ahead", "comeback"):
        first = _pick([f"{team} lead at the death, could that be the winner?!",
                       f"Late, late drama, {team} are in front!"], key)
    elif facts.get("late") and facts.get("swing") == "equaliser":
        first = _pick([f"Late drama, {team} are level!", f"{team} rescue it late on!"], key)
    mood = {"against": _pick([f"Against the run of play, with {other} on top!",
                              "And that is against the run of play!"], key + "m"),
            "with": _pick(["The pressure finally tells!", f"{team} had been knocking, now the door is open!"], key + "m"),
            "swing": _pick(["The tide had been turning, and here it is!", "The momentum swung, and they made it count!"], key + "m")}
    second = _how(assist, who, key)
    facts_line = _analytics(assist)   # the verdict label, the odds and the defenders cut out, said in words: it is the whole line
    if facts_line:
        return facts_line
    parts = [first, second]
    if facts.get("momentum") in mood and len(first) + len(second) < 110:
        parts.insert(1, mood[facts["momentum"]])
    xg = moment.get("xg")
    if xg is not None and xg < LONG_SHOT_XG and len(" ".join(parts)) < 120:
        parts.append("Out of almost nothing!")
    return " ".join(parts)


def _in_words(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def _analytics(assist: dict | None) -> str | None:
    """The easy explanation from the pass analysis, as it is said: "Line-breaker from Nick: only one in three get through,
    five defenders cut out." The same numbers as the verdict line (engine.assist.verdict_line)."""
    p = (assist or {}).get("pass") or {}
    played = ((assist or {}).get("payload") or {}).get("played") or {}
    label = ((assist or {}).get("verdict") or {}).get("label")
    arrives = played.get("arrives")
    if not (label and p.get("passer") and arrives is not None):
        return None
    n, d = (int(v) for v in odds(arrives).split("/"))
    chance = ("a long shot" if arrives < 0.05 else "it almost always gets there" if arrives >= 0.95
              else f"{'only ' if arrives < 0.5 else ''}{_in_words(n)} in {_in_words(d)} get through")
    cut = played.get("cut_out") or 0
    cuts = f"{_in_words(cut)} defender{'' if cut == 1 else 's'} cut out" if cut else ""   # cut out of the play: the pass went past them
    return f"{label} from {p['passer']}: {', '.join(x for x in (chance, cuts) if x)}."


def _pct(p: float) -> str:
    """The page's percentage format (web/src/broadcast/labels.js fmtPct): one decimal under 10 %, whole numbers above."""
    return f"{p * 100:.1f}%" if p < 0.1 else f"{int(p * 100 + 0.5)}%"


def captions(line_text: str, assist: dict | None) -> list[dict]:
    """[{"part", "text"}]: the line, then for an analysed goal the odds and the closing exclamation."""
    out = [{"part": "line", "text": line_text}]
    played = next((o for o in (assist or {}).get("options") or [] if o.get("played")), None)
    if played and played.get("arrives") is not None and played.get("goal") is not None:
        out.append({"part": "smart", "text": f"{_pct(played['arrives'])} arrived, {_pct(played['goal'])} score."})
        out.append({"part": "finish", "text": FINISH.get(((assist or {}).get("verdict") or {}).get("key"), FINISH["line"])})
    return out


def _how(assist: dict | None, scorer: str, key: str) -> str:
    if not assist:
        return _pick([f"{scorer} does it all and finishes.", f"{scorer} makes it happen."], key + "h")
    p = assist.get("pass") or {}
    passer = p.get("passer") or "the passer"
    verdict = (assist.get("verdict") or {}).get("key")
    cut = ((assist.get("payload") or {}).get("played") or {}).get("cut_out") or 0
    if p.get("aerial") and p.get("into_space"):
        return f"{passer} floats it over the top, {scorer} runs onto it and buries it."
    if p.get("into_space"):
        return f"{passer} threads it into space and {scorer} is away to finish."
    if verdict == "line" and cut >= 3:
        return f"{passer} slices {cut} defenders open, and {scorer} finishes."
    if p.get("aerial"):
        return f"{passer} lifts it in, and {scorer} is there."
    return {
        "best": f"{passer} picks the perfect pass, {scorer} does the rest.",
        "brave": f"A brave ball from {passer}, and {scorer} makes it count.",
        "line": f"{passer} cuts through the lines, and {scorer} finishes.",
        "smart": f"Clever from {passer}, and {scorer} puts it away.",
    }.get(verdict, f"{passer} finds {scorer}, and it is in.")


def _not_a_goal(moment: dict, key: str, who: str, team: str) -> str:
    title = moment.get("title") or ""
    if title == "Big chance missed":
        return _pick([f"So close! {who} really should have scored.", f"How has that stayed out? {who} will want that back."], key)
    if title == "Shot saved":
        return _pick([f"What a save! {who} is denied.", f"Brilliant stop, {who} cannot believe it."], key)
    if title == "Shot blocked":
        return f"Bodies on the line! {who}'s shot is blocked."
    if title == "Red card":
        return f"Down to ten! {who} is sent off for {team}."
    if title == "Yellow card":
        return f"{who} goes into the book."
    if title.startswith("VAR"):
        return f"VAR is checking it. {title.split(':', 1)[-1].strip().capitalize()}."
    if title == "Penalty awarded":
        return f"Penalty! A huge chance for {team}."
    return f"{title}{f', {who}' if moment.get('player') else ''}."


def commentary(moment: dict, assist: dict | None, goals_before: list[dict], pressure: dict | None, teams: dict) -> dict:
    """{"line", "captions", "tags", "facts", "provider"} for one moment summary; goals_before are earlier goal summaries."""
    facts = situation(moment, goals_before, pressure)
    tags = [t for t in (facts.get("swing"), "late" if facts.get("late") else None,
                        f"momentum_{facts['momentum']}" if facts.get("momentum") else None) if t]
    said = line(moment, assist, facts, teams)
    return {"line": said, "captions": captions(said, assist), "tags": tags, "facts": facts, "provider": "templates"}

"""Authored synthetic highlight: a pass, a shot and a goal. No video, no GPU.

Used when the user clicks “Use demo clip”. Positions are on the same 105×68
pitch as ``feeds.schema``. Teams match the default synthetic match so the rest
of the app and this panel feel like one demo.
"""
from __future__ import annotations

from feeds.schema import format_clock
from feeds.tracking import annotate_frame

TEAMS = {"home": "Home", "away": "Away"}
FPS = 5
DURATION = 12.0

# Fictional squad used only for this clip. Ids follow the synthetic convention.
SQUAD = {
    "home": [
        {"player_id": "H01", "name": "Jonah Vale", "number": 1, "role": "GK"},
        {"player_id": "H04", "name": "Mateo Krill", "number": 4, "role": "OUT"},
        {"player_id": "H07", "name": "Ellis Marrow", "number": 7, "role": "OUT"},
        {"player_id": "H08", "name": "Sami Okafor", "number": 8, "role": "OUT"},
        {"player_id": "H09", "name": "Rui Bennet", "number": 9, "role": "OUT"},
        {"player_id": "H10", "name": "Leah Vos", "number": 10, "role": "OUT"},
    ],
    "away": [
        {"player_id": "A01", "name": "Piotr Helm", "number": 1, "role": "GK"},
        {"player_id": "A03", "name": "Nico Brandt", "number": 3, "role": "OUT"},
        {"player_id": "A04", "name": "Omar Fiel", "number": 4, "role": "OUT"},
        {"player_id": "A05", "name": "Chris Wren", "number": 5, "role": "OUT"},
        {"player_id": "A08", "name": "Yara Holt", "number": 8, "role": "OUT"},
        {"player_id": "A11", "name": "Felix Drum", "number": 11, "role": "OUT"},
    ],
}


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _player(pid: str, team: str, x: float, y: float) -> dict:
    info = next(p for p in SQUAD[team] if p["player_id"] == pid)
    return {
        "player_id": pid,
        "team": team,
        "x": round(x, 2),
        "y": round(y, 2),
        "number": info["number"],
        "name": info["name"],
        "role": info["role"],
    }


def _path(t: float) -> tuple[list[dict], tuple[float, float], str]:
    """Positions at clip time ``t`` (seconds from 0). Home attacks +x."""
    # Build-up: Vos (H10) carries, then plays Bennet (H09), who shoots.
    if t < 3.0:
        u = t / 3.0
        ball = (_lerp(64, 72, u), 40.0)
        poss = "home"
        h10 = (_lerp(64, 72, u), 40.0)
        h09 = (86.0, 38.0)
    elif t < 4.2:
        u = (t - 3.0) / 1.2
        ball = (_lerp(72, 88, u), _lerp(40, 38, u))
        poss = "home"
        h10 = (72.0, 40.0)
        h09 = (88.0, 38.0)
    elif t < 7.0:
        u = (t - 4.2) / 2.8
        ball = (_lerp(88, 90, u), _lerp(38, 36, u))
        poss = "home"
        h10 = (70.0, 38.0)
        h09 = (_lerp(88, 90, u), _lerp(38, 36, u))
    elif t < 7.2:
        ball = (90.0, 36.0)
        poss = "home"
        h10 = (69.0, 37.0)
        h09 = (90.0, 36.0)
    else:
        u = min(1.0, (t - 7.2) / 0.2)
        ball = (_lerp(90, 103.4, u), _lerp(36, 34, u))
        poss = "home"
        h10 = (68.0, 36.0)
        h09 = (90.0, 36.0)

    # Press after the pass arrives so the ball is not "won" in flight.
    press = min(1.0, max(0.0, (t - 4.4) / 1.2))
    players = [
        _player("H01", "home", 8, 34),
        _player("H04", "home", 48, 28),
        _player("H07", "home", 62, 20),
        _player("H08", "home", 58, 50),
        _player("H10", "home", *h10),
        _player("H09", "home", *h09),
        _player("A01", "away", 100, 26),
        _player("A03", "away", _lerp(84, 88, press), 28),
        _player("A04", "away", _lerp(96, 91, press), 30),
        _player("A05", "away", _lerp(96, 89, press), 42),
        _player("A08", "away", 74, 50),
        _player("A11", "away", 80, 18),
    ]
    return players, ball, poss


def demo_frames() -> list[dict]:
    """Annotated tracking frames for the 12-second authored clip."""
    keepers = {"H01", "A01"}
    frames = []
    n = int(DURATION * FPS) + 1
    for i in range(n):
        t = round(i / FPS, 2)
        players, ball, poss = _path(t)
        frame = annotate_frame({
            "kind": "tracking",
            "match_id": "HIGHLIGHT-DEMO",
            "frame": i,
            "period": 1,
            "match_seconds": t,
            "clock": format_clock(1, t),
            "ball": {"x": round(ball[0], 2), "y": round(ball[1], 2)},
            "ball_in_play": True,
            "possession": poss,
            "players": players,
        }, keepers)
        frames.append(frame)
    return frames


def demo_meta() -> dict:
    return {
        "match_id": "HIGHLIGHT-DEMO",
        "synthetic": True,
        "source": "highlight-demo",
        "provider": "gamelens",
        "data_label": "Synthetic highlight demo",
        "disclaimer": (
            "Schematic demo clip. Positions are authored synthetic tracks, not a "
            "broadcast recording. GameLens does not ship copyrighted match footage; "
            "upload only clips you have the rights to use."
        ),
        "teams": {
            "home": {"name": TEAMS["home"], "formation": "demo", "squad": SQUAD["home"]},
            "away": {"name": TEAMS["away"], "formation": "demo", "squad": SQUAD["away"]},
        },
        "half_minutes": 45,
        "added_time_minutes": [0, 0],
        "final_score": {"home": 1, "away": 0},
        "fps": FPS,
    }

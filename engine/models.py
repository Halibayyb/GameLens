"""Simple, transparent football models.

They are deliberately small formulas so the hackathon demo is explainable.
Swap them for trained models (on Azure Machine Learning) later:
keep the same function signatures and the rest of the app keeps working.
"""
import math

L, W = 105.0, 68.0


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def to_frame(x: float, y: float, attack_right: bool) -> tuple[float, float]:
    """World coordinates -> team frame where the team always attacks towards x = 105."""
    return (x, y) if attack_right else (L - x, W - y)


def xg(x: float, y: float, attack_right: bool) -> float:
    """Expected goals of a shot from (x, y). Same curve the synthetic generator uses."""
    u, v = to_frame(x, y, attack_right)
    dist = math.hypot(L - u, 34 - v)
    angle = abs(math.atan2(37.66 - v, L - u) - math.atan2(30.34 - v, L - u))
    return sigmoid(-1.5 + 1.9 * angle - 0.07 * dist)


def _dist_to_segment(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    seg = dx * dx + dy * dy
    t = 0.0 if seg == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def pass_success(start, end, defenders) -> float:
    """Chance a pass from start to end arrives, given defender positions."""
    length = math.dist(start, end)
    p = 0.95 - 0.012 * max(0.0, length - 10)
    for d in defenders:
        gap = _dist_to_segment(d, start, end)
        if gap < 1.5:
            p -= 0.30
        elif gap < 3.5:
            p -= 0.10
    return max(0.05, min(0.97, p))


def counter_risk(lose_x: float, lose_y: float, attack_right: bool, defenders_behind: int) -> float:
    """Expected goals conceded if the ball is lost at (lose_x, lose_y).

    Losing it high up the pitch means more teammates are caught upfield,
    so the counter-attack is more dangerous.
    """
    u, _ = to_frame(lose_x, lose_y, attack_right)
    base = 0.01 + 0.07 * (u / L) ** 2
    cover = max(0, 4 - defenders_behind) * 0.015
    return round(base + cover, 3)

"""Elo math: E_A = 1/(1+10^((R_B-R_A)/400)),
R'_A = R_A + K(S_A - E_A), K = 32 (24 above 2200)."""

import math


def expected(r_a: int, r_b: int) -> float:
    return 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400))


def k_factor(rating: int) -> int:
    return 24 if rating > 2200 else 32


def _round_half_up(x: float) -> int:
    # Conventional round-half-up, not Python's round() (which rounds halves to
    # even — a slight, surprising asymmetry in a rating people watch tick).
    return math.floor(x + 0.5)


def apply(r_a: int, r_b: int, s_a: float) -> tuple[int, int]:
    """s_a: 1 win, 0.5 draw, 0 loss/forfeit for player A. Returns new ratings."""
    e_a = expected(r_a, r_b)
    s_b, e_b = 1.0 - s_a, 1.0 - e_a
    return (
        _round_half_up(r_a + k_factor(r_a) * (s_a - e_a)),
        _round_half_up(r_b + k_factor(r_b) * (s_b - e_b)),
    )


def update_one(rating: int, opp_rating: int, score: float, k: int) -> int:
    """One-sided Elo: the new rating after a single game (score: 1 win, 0.5 draw,
    0 loss) against a *fixed* opponent — only this side moves. Used for problem
    ratings, where the problem is a 'ghost player' and the duelists' ratings are
    the fixed opponents (their own Elo updates separately, from the duel result)."""
    return _round_half_up(rating + k * (score - expected(rating, opp_rating)))

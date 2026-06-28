"""Dynamic problem ratings (Codeforces-style): a problem's rating drifts based on
who solves it, so difficulty self-calibrates — essential for imported problems
whose true difficulty is unknown. Only the *problem's* rating moves here; player
Elo stays driven by the duel result (finalize_match), so no double counting.

The problem is a 'ghost player': for each duelist it "wins" if the player failed
(score 1.0) and "loses" if they solved it (0.0), via elo.update_one. K tapers as
the problem accumulates rated solves, so a fresh problem calibrates fast then
settles. Runs on the caller's transaction so it commits with the match outcome."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import elo

if TYPE_CHECKING:
    import asyncpg

# Outcomes that carry a clean solve/fail signal. A FORFEIT (disconnect/abandon)
# or VOID says nothing reliable about difficulty, so it never moves a rating.
RATED_OUTCOMES = {"WIN", "DRAW"}


def k_problem(rating_count: int) -> int:
    """Larger steps while a problem is young/uncalibrated, smaller once settled."""
    if rating_count < 10:
        return 48
    if rating_count < 40:
        return 32
    return 16


async def update_problem_rating(
    conn: asyncpg.Connection, match_id, outcome: str
) -> None:
    if outcome not in RATED_OUTCOMES:
        return
    m = await conn.fetchrow(
        """SELECT m.problem_id, m.player_a, m.player_b,
                  p.rating AS problem_rating, p.rating_count
           FROM matches m JOIN problems p ON p.id = m.problem_id
           WHERE m.id = $1""",
        match_id,
    )
    if m is None:
        return
    players = [m["player_a"], m["player_b"]]
    player_rating = {
        r["id"]: r["rating"]
        for r in await conn.fetch(
            "SELECT id, rating FROM users WHERE id = ANY($1::bigint[])", players
        )
    }
    # A player with no AC submission (incl. none at all) did not solve it.
    solved = {
        r["user_id"]: r["solved"]
        for r in await conn.fetch(
            """SELECT user_id, bool_or(verdict = 'AC') AS solved
               FROM submissions WHERE match_id = $1 GROUP BY user_id""",
            match_id,
        )
    }

    rating, count = m["problem_rating"], m["rating_count"]
    for uid in players:
        if uid not in player_rating:
            continue
        score = 0.0 if solved.get(uid) else 1.0  # problem "wins" when player fails
        rating = elo.update_one(rating, player_rating[uid], score, k_problem(count))
        count += 1
    await conn.execute(
        "UPDATE problems SET rating = $2, rating_count = $3 WHERE id = $1",
        m["problem_id"], rating, count,
    )

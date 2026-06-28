"""Unit tests for dynamic problem ratings (app/codeclash/rating/).

Pure: the one-sided Elo math + the K taper, and update_problem_rating driven by
an in-memory fake connection (no Postgres). Run directly:

    python3 tests/test_problem_rating.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from codeclash.rating import elo                       # noqa: E402
from codeclash.rating.problems import (                # noqa: E402
    k_problem, update_problem_rating,
)


def test_update_one_symmetric():
    # Equal ratings: win/loss move by ±K/2, draw moves nothing.
    assert elo.update_one(1500, 1500, 1.0, 32) == 1516
    assert elo.update_one(1500, 1500, 0.0, 32) == 1484
    assert elo.update_one(1500, 1500, 0.5, 32) == 1500


def test_update_one_directional():
    # A weak (1000) problem that BEATS a strong (1800) player (player failed,
    # score 1) is a big surprise -> large rise; being solved (score 0) is
    # expected -> tiny fall.
    surprise = elo.update_one(1000, 1800, 1.0, 32) - 1000
    expected_loss = 1000 - elo.update_one(1000, 1800, 0.0, 32)
    assert surprise > 25 and expected_loss < 5


def test_k_taper():
    assert k_problem(0) == 48 and k_problem(9) == 48
    assert k_problem(10) == 32 and k_problem(39) == 32
    assert k_problem(40) == 16 and k_problem(500) == 16


class FakeConn:
    """Minimal async stand-in for an asyncpg connection."""

    def __init__(self, match, users, solved):
        self._match, self._users, self._solved = match, users, solved
        self.updated = None  # args passed to the UPDATE, or None if never run

    async def fetchrow(self, q, *a):
        return self._match

    async def fetch(self, q, *a):
        if "FROM users" in q:
            return self._users
        if "submissions" in q:
            return self._solved
        return []

    async def execute(self, q, *a):
        self.updated = a  # (problem_id, rating, count)


def run(conn, outcome):
    asyncio.run(update_problem_rating(conn, "match-uuid", outcome))


def test_skips_unrated_outcomes():
    for outcome in ("FORFEIT", "VOID", "WHATEVER"):
        conn = FakeConn({"problem_id": 7, "player_a": 1, "player_b": 2,
                         "problem_rating": 1200, "rating_count": 0},
                        [{"id": 1, "rating": 1500}, {"id": 2, "rating": 1500}], [])
        run(conn, outcome)
        assert conn.updated is None, outcome  # never touched the problem


def test_failed_by_both_drifts_up():
    # WIN, problem 1200, both players 1500, neither solved (no AC rows at all).
    conn = FakeConn({"problem_id": 7, "player_a": 1, "player_b": 2,
                     "problem_rating": 1200, "rating_count": 0},
                    [{"id": 1, "rating": 1500}, {"id": 2, "rating": 1500}], [])
    run(conn, "WIN")
    pid, new_rating, new_count = conn.updated
    assert pid == 7 and new_count == 2 and new_rating > 1200


def test_solved_by_both_drifts_down():
    # WIN, problem 1500, both players 1200, both solved (AC).
    conn = FakeConn({"problem_id": 9, "player_a": 1, "player_b": 2,
                     "problem_rating": 1500, "rating_count": 5},
                    [{"id": 1, "rating": 1200}, {"id": 2, "rating": 1200}],
                    [{"user_id": 1, "solved": True}, {"user_id": 2, "solved": True}])
    run(conn, "WIN")
    pid, new_rating, new_count = conn.updated
    assert pid == 9 and new_count == 7 and new_rating < 1500


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name} ok")
    print("\nPROBLEM RATING TESTS PASSED")


if __name__ == "__main__":
    main()

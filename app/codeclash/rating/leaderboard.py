"""Redis leaderboard zset: updated after the rating transaction commits, so it
may be seconds stale — Postgres is the source of truth and the zset is rebuilt
from it on every startup."""

import asyncpg

LB_KEY = "leaderboard"


async def rebuild(pool: asyncpg.Pool, r) -> None:
    rows = await pool.fetch("SELECT id, rating FROM users")
    if rows:
        await r.zadd(LB_KEY, {str(row["id"]): row["rating"] for row in rows})


async def add_user(r, user_id: int, rating: int) -> None:
    await r.zadd(LB_KEY, {str(user_id): rating})


async def apply_results(r, results: dict) -> None:
    """results: finalize_match return value {user_id: {"old","new"} | None}."""
    changes = {uid: v["new"] for uid, v in results.items() if v}
    if changes:
        await r.zadd(LB_KEY, changes)


async def top(pool: asyncpg.Pool, r, limit: int) -> list[dict]:
    entries = await r.zrevrange(LB_KEY, 0, limit - 1, withscores=True)
    ids = [int(uid) for uid, _ in entries]
    names = {
        row["id"]: row["username"]
        for row in await pool.fetch(
            "SELECT id, username FROM users WHERE id = ANY($1::bigint[])", ids
        )
    }
    return [
        {"rank": i + 1, "username": names.get(int(uid), "?"), "rating": int(score)}
        for i, (uid, score) in enumerate(entries)
    ]

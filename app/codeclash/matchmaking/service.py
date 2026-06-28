"""Matchmaker: Redis zset keyed by rating; each player's window widens ±25 every
10s so wait time stays bounded with thin liquidity. Pairing also picks the
problem — one neither player has seen, rated near the pair's average, widening on
exhaustion. Runs as a single coroutine (SPOF: if the process dies, queued players
just rejoin)."""

import asyncio
import logging
import time

log = logging.getLogger(__name__)

QUEUE = "mm:queue"
WAIT = "mm:wait"
MATCHED = "mm:matched:{uid}"
PAIR_INTERVAL_S = 2
BASE_WINDOW = 50
WIDEN_PER_10S = 25


async def join_queue(r, pool, user_id: int) -> dict:
    # At most one ACTIVE duel per player. Without this, a player already in a duel
    # could re-queue (the API is reachable directly) and get a second concurrent
    # match — double timers, Elo applied twice. Route them back instead.
    active = await pool.fetchval(
        """SELECT id FROM matches WHERE state = 'ACTIVE'
           AND (player_a = $1 OR player_b = $1) LIMIT 1""",
        user_id,
    )
    if active is not None:
        return {"status": "matched", "match_id": str(active)}
    rating = await pool.fetchval("SELECT rating FROM users WHERE id = $1", user_id)
    await r.zadd(QUEUE, {str(user_id): rating})
    await r.hsetnx(WAIT, str(user_id), time.time())
    return {"status": "queued", "rating": rating}


async def leave_queue(r, user_id: int) -> None:
    await r.zrem(QUEUE, str(user_id))
    await r.hdel(WAIT, str(user_id))


async def queue_status(r, pool, user_id: int) -> dict:
    match_id = await r.get(MATCHED.format(uid=user_id))
    if match_id:
        return {"status": "matched", "match_id": match_id}
    active = await pool.fetchval(
        """SELECT id FROM matches WHERE state = 'ACTIVE'
           AND (player_a = $1 OR player_b = $1)
           ORDER BY created_at DESC LIMIT 1""",
        user_id,
    )
    if active:  # also covers reconnect-after-restart discovery
        return {"status": "matched", "match_id": str(active)}
    joined = await r.hget(WAIT, str(user_id))
    if joined is not None:
        return {"status": "queued", "waited_s": int(time.time() - float(joined))}
    return {"status": "idle"}


# Rating windows tried in order before falling back to any unseen problem
# (problem ratings are dynamic — see rating/problems.py).
PROBLEM_WINDOWS = (200, 400)


async def _pick_problem(pool, target: float, players: list[int]) -> int | None:
    unseen = """SELECT id FROM problems WHERE {where} AND id NOT IN
                (SELECT problem_id FROM seen_problems WHERE user_id = ANY($1::bigint[]))
                ORDER BY random() LIMIT 1"""
    for w in PROBLEM_WINDOWS:  # prefer a problem rated near the players
        pid = await pool.fetchval(
            unseen.format(where="rating BETWEEN $2 AND $3"),
            players, target - w, target + w,
        )
        if pid is not None:
            return pid
    pid = await pool.fetchval(unseen.format(where="TRUE"), players)  # any unseen
    if pid is None:  # pool exhausted for this pair — accept a repeat, loudly
        log.warning("no unseen problem for players %s; repeating one", players)
        pid = await pool.fetchval(
            "SELECT id FROM problems ORDER BY random() LIMIT 1"
        )
    return pid


async def _create_match(app, user_a: int, user_b: int) -> None:
    pool, r = app.state.pool, app.state.redis
    players = [user_a, user_b]
    users = {
        row["id"]: row
        for row in await pool.fetch(
            "SELECT id, username, rating FROM users WHERE id = ANY($1::bigint[])",
            players,
        )
    }
    avg = (users[user_a]["rating"] + users[user_b]["rating"]) / 2
    problem_id = await _pick_problem(pool, avg, players)
    if problem_id is None:
        log.error("no problems in database; cannot create match")
        return
    row = await pool.fetchrow(
        """INSERT INTO matches (player_a, player_b, problem_id, state, started_at)
           VALUES ($1, $2, $3, 'ACTIVE', now())
           RETURNING id, player_a, player_b, problem_id, started_at""",
        user_a,
        user_b,
        problem_id,
    )
    await pool.execute(
        """INSERT INTO seen_problems (user_id, problem_id)
           SELECT unnest($1::bigint[]), $2 ON CONFLICT DO NOTHING""",
        players,
        problem_id,
    )
    await r.zrem(QUEUE, str(user_a), str(user_b))
    await r.hdel(WAIT, str(user_a), str(user_b))
    for uid in players:
        await r.setex(MATCHED.format(uid=uid), 300, str(row["id"]))
    duel = await app.state.duels.create_duel(row)
    await app.state.duels.append_event(
        duel,
        "started",
        {
            "problem_id": problem_id,
            "players": {
                str(uid): {
                    "username": users[uid]["username"],
                    "rating": users[uid]["rating"],
                }
                for uid in players
            },
        },
    )
    log.info("matched %s vs %s on problem %s -> %s", user_a, user_b, problem_id, row["id"])


async def _pair_once(app) -> None:
    r = app.state.redis
    entries = await r.zrange(QUEUE, 0, -1, withscores=True)
    if len(entries) < 2:
        return
    waits = await r.hgetall(WAIT)
    now = time.time()

    def window(uid: str) -> float:
        waited = now - float(waits.get(uid, now))
        return BASE_WINDOW + WIDEN_PER_10S * (waited // 10)

    used: set[str] = set()
    # entries are rating-sorted, so adjacent candidates are the closest pairs
    for (uid_a, r_a), (uid_b, r_b) in zip(entries, entries[1:]):
        if uid_a in used or uid_b in used:
            continue
        if abs(r_a - r_b) <= window(uid_a) + window(uid_b):
            used.update((uid_a, uid_b))
            await _create_match(app, int(uid_a), int(uid_b))


async def matchmaker_loop(app) -> None:
    log.info("matchmaker started")
    while True:
        try:
            await _pair_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("matchmaker iteration failed")
        await asyncio.sleep(PAIR_INTERVAL_S)

"""Match finalization: the match row, both rating rows, and rating history update
together. The caller supplies the transaction (`conn`) so the duel's terminal
event commits with them — see DuelManager.finish. A VOID outcome never touches
ratings."""

from uuid import UUID

import asyncpg

from . import elo


async def finalize_match(
    conn: asyncpg.Connection, match_id: UUID, outcome: str, winner_id: int | None
) -> dict | None:
    """Finalize a match on the caller's open transaction. Returns
    {user_id: {"old": r, "new": r'}} or None if the match was already finalized
    (the state predicate makes concurrent finalizations a no-op — only the first
    one wins)."""
    m = await conn.fetchrow(
        """UPDATE matches
           SET state = 'FINISHED', outcome = $2, winner_id = $3,
               finished_at = now()
           WHERE id = $1 AND state = 'ACTIVE'
           RETURNING player_a, player_b""",
        match_id,
        outcome,
        winner_id,
    )
    if m is None:
        return None
    a, b = m["player_a"], m["player_b"]
    if outcome == "VOID":
        return {str(a): None, str(b): None}
    rows = await conn.fetch(
        "SELECT id, rating FROM users WHERE id = ANY($1::bigint[]) FOR UPDATE",
        [a, b],
    )
    old = {r["id"]: r["rating"] for r in rows}
    s_a = 1.0 if winner_id == a else (0.0 if winner_id == b else 0.5)
    new_a, new_b = elo.apply(old[a], old[b], s_a)
    new = {a: new_a, b: new_b}
    for uid in (a, b):
        await conn.execute(
            "UPDATE users SET rating = $2 WHERE id = $1", uid, new[uid]
        )
        await conn.execute(
            """INSERT INTO rating_history
               (user_id, match_id, rating_before, rating_after)
               VALUES ($1, $2, $3, $4)""",
            uid,
            match_id,
            old[uid],
            new[uid],
        )
    return {str(uid): {"old": old[uid], "new": new[uid]} for uid in (a, b)}

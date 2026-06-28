import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user, get_pool, get_redis, serialize
from . import leaderboard

router = APIRouter(prefix="/api", tags=["rating"])


@router.get("/leaderboard")
async def get_leaderboard(
    pool: asyncpg.Pool = Depends(get_pool),
    r=Depends(get_redis),
    limit: int = 50,
):
    return await leaderboard.top(pool, r, min(limit, 200))


@router.get("/users/{user_id}/ratings")
async def rating_history(
    user_id: int,
    user=Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
):
    # Self-only (see match_history): don't let sequential user ids be enumerated.
    if user_id != user["id"]:
        raise HTTPException(status_code=403, detail="can only view your own history")
    rows = await pool.fetch(
        """SELECT match_id, rating_before, rating_after, created_at
           FROM rating_history WHERE user_id = $1
           ORDER BY created_at DESC LIMIT 100""",
        user_id,
    )
    return [serialize(r, "match_id") for r in rows]

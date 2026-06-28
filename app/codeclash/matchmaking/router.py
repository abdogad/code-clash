import asyncpg
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends

from ..deps import get_current_user, get_pool, get_redis
from . import service

router = APIRouter(prefix="/api/queue", tags=["matchmaking"])


@router.post("/join")
async def join_queue(
    user=Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    r: aioredis.Redis = Depends(get_redis),
):
    return await service.join_queue(r, pool, user["id"])


@router.post("/leave")
async def leave_queue(
    user=Depends(get_current_user), r: aioredis.Redis = Depends(get_redis)
):
    await service.leave_queue(r, user["id"])
    return {"status": "left"}


@router.get("/status")
async def queue_status(
    user=Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    r: aioredis.Redis = Depends(get_redis),
):
    return await service.queue_status(r, pool, user["id"])

import asyncpg
import redis.asyncio as aioredis
from fastapi import HTTPException, Request


def serialize(record, *uuid_fields: str) -> dict:
    """asyncpg Record -> dict with the named UUID columns stringified for JSON.
    None passes through, so a nullable column (e.g. match_id) stays null."""
    out = dict(record)
    for field in uuid_fields:
        if out.get(field) is not None:
            out[field] = str(out[field])
    return out


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis


async def get_current_user(request: Request) -> asyncpg.Record:
    # local import avoids a cycle
    from .auth.security import ACCESS_COOKIE, decode_token

    token = request.cookies.get(ACCESS_COOKIE)
    user_id = decode_token(token) if token else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="missing or invalid session")
    row = await request.app.state.pool.fetchrow(
        "SELECT id, username, rating FROM users WHERE id = $1", user_id
    )
    if row is None:
        raise HTTPException(status_code=401, detail="user no longer exists")
    return row

import asyncio
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import streams
from .auth.router import router as auth_router
from .config import settings
from .match.duel import DuelManager
from .match.reaper import reaper_loop
from .match.router import router as match_router
from .match.progress import consume_progress
from .match.router import ws_router
from .match.verdicts import consume_verdicts
from .matchmaking.router import router as matchmaking_router
from .matchmaking.service import matchmaker_loop
from .migrate import migrate
from .problems.router import router as problems_router
from .rating import leaderboard
from .rating.router import router as rating_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(
        settings.database_url, min_size=2, max_size=10
    )
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    # Schema must be current before anything queries it (leaderboard, matchmaker).
    await migrate(app.state.pool)
    await streams.ensure_groups(app.state.redis)
    await leaderboard.rebuild(app.state.pool, app.state.redis)
    app.state.duels = DuelManager(app.state.pool, app.state.redis)
    # Must finish before verdicts flow: the consumer routes them through the
    # in-memory duel registry this rebuilds.
    await app.state.duels.load_active()
    tasks = [
        asyncio.create_task(consume_verdicts(app)),
        asyncio.create_task(consume_progress(app)),
        asyncio.create_task(matchmaker_loop(app)),
        asyncio.create_task(reaper_loop(app)),
    ]
    yield
    for t in tasks:
        t.cancel()
    await app.state.redis.aclose()
    await app.state.pool.close()


app = FastAPI(title="CodeClash", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(problems_router)
app.include_router(matchmaking_router)
app.include_router(match_router)
app.include_router(rating_router)
app.include_router(ws_router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# Mounted last: API routes and /ws take precedence, everything else is SPA.
app.mount("/", StaticFiles(directory="static", html=True), name="static")

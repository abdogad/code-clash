from typing import Literal
from uuid import UUID

import asyncpg
import redis.asyncio as aioredis
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

from .. import streams
from ..auth.security import ACCESS_COOKIE, decode_token
from ..deps import get_current_user, get_pool, get_redis, serialize
from ..ratelimit import check_rate

router = APIRouter(prefix="/api", tags=["match"])
ws_router = APIRouter()


class SubmissionIn(BaseModel):
    match_id: UUID | None = None  # None = practice submission
    problem_id: int | None = None  # ignored for duels (the match fixes it)
    code: str = Field(min_length=1, max_length=65536)
    language: Literal["python", "cpp"] = "python"  # invalid -> 422


@router.post("/submissions", status_code=202)
async def create_submission(
    body: SubmissionIn,
    request: Request,
    user=Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    r: aioredis.Redis = Depends(get_redis),
):
    user_id = user["id"]
    # Per-user submission cap. Duels already allow one in-flight submission (the
    # partial unique index), but practice submissions have match_id NULL, which
    # that index doesn't constrain — so without this, one user could flood the
    # judge queue. Generous enough for normal resubmits; only sheds floods.
    await check_rate(r, f"submit:{user_id}", limit=30, window_s=10)
    problem_id = body.problem_id
    if body.match_id is not None:
        m = await pool.fetchrow(
            "SELECT state, player_a, player_b, problem_id FROM matches WHERE id = $1",
            body.match_id,
        )
        if m is None:
            raise HTTPException(status_code=404, detail="match not found")
        if user_id not in (m["player_a"], m["player_b"]):
            raise HTTPException(status_code=403, detail="not a player in this match")
        if m["state"] != "ACTIVE":
            raise HTTPException(status_code=409, detail="match is not active")
        problem_id = m["problem_id"]
    if problem_id is None:
        raise HTTPException(status_code=422, detail="problem_id required")
    problem = await pool.fetchrow(
        "SELECT slug, time_limit_ms, memory_limit_kb FROM problems WHERE id = $1",
        problem_id,
    )
    if problem is None:
        raise HTTPException(status_code=404, detail="problem not found")
    try:
        row = await pool.fetchrow(
            """INSERT INTO submissions (match_id, user_id, problem_id, code, language)
               VALUES ($1, $2, $3, $4, $5) RETURNING id, submitted_at""",
            body.match_id,
            user_id,
            problem_id,
            body.code,
            body.language,
        )
    except asyncpg.UniqueViolationError:
        # One in-flight submission per player, enforced by the partial unique index.
        raise HTTPException(
            status_code=409, detail="previous submission still being judged"
        )
    await r.xadd(
        streams.SUBMISSIONS,
        {
            "submission_id": str(row["id"]),
            "problem_slug": problem["slug"],
            "time_limit_ms": str(problem["time_limit_ms"]),
            "memory_limit_kb": str(problem["memory_limit_kb"]),
            "code": body.code,
            "language": body.language,
            "submitted_at": row["submitted_at"].isoformat(),
        },
        maxlen=streams.SUBMISSIONS_MAXLEN,
        approximate=True,
    )
    if body.match_id is not None:
        duels = request.app.state.duels
        duel = duels.get(body.match_id)
        if duel is not None:
            attempt = await pool.fetchval(
                "SELECT count(*) FROM submissions WHERE match_id = $1 AND user_id = $2",
                body.match_id,
                user_id,
            )
            await duels.append_event(
                duel, "submission", {"user_id": user_id, "attempt": attempt}
            )
    return {"id": str(row["id"]), "verdict": "PENDING"}


@router.get("/submissions/{submission_id}")
async def get_submission(
    submission_id: UUID,
    user=Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
):
    row = await pool.fetchrow(
        """SELECT id, match_id, user_id, problem_id, verdict, tests_passed,
                  tests_total, submitted_at, judged_at
           FROM submissions WHERE id = $1""",
        submission_id,
    )
    # Owner-only; 404 (not 403) so we don't confirm someone else's submission exists.
    if row is None or row["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="submission not found")
    return serialize(row, "id", "match_id")


@router.get("/matches/{match_id}")
async def get_match(
    match_id: UUID,
    user=Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
):
    row = await pool.fetchrow(
        """SELECT m.id, m.state, m.outcome, m.winner_id, m.started_at,
                  m.finished_at, m.problem_id,
                  p.title, p.statement_md, p.time_limit_ms, p.band,
                  ua.id AS a_id, ua.username AS a_name, ua.rating AS a_rating,
                  ub.id AS b_id, ub.username AS b_name, ub.rating AS b_rating
           FROM matches m
           JOIN problems p ON p.id = m.problem_id
           JOIN users ua ON ua.id = m.player_a
           JOIN users ub ON ub.id = m.player_b
           WHERE m.id = $1""",
        match_id,
    )
    # Participants only; 404 (not 403) so a non-participant can't confirm the
    # match exists or read its statement/ratings.
    if row is None or user["id"] not in (row["a_id"], row["b_id"]):
        raise HTTPException(status_code=404, detail="match not found")
    return {
        "id": str(row["id"]),
        "state": row["state"],
        "outcome": row["outcome"],
        "winner_id": row["winner_id"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "problem": {
            "id": row["problem_id"],
            "title": row["title"],
            "statement_md": row["statement_md"],
            "time_limit_ms": row["time_limit_ms"],
            "band": row["band"],
        },
        "players": {
            str(row["a_id"]): {"username": row["a_name"], "rating": row["a_rating"]},
            str(row["b_id"]): {"username": row["b_name"], "rating": row["b_rating"]},
        },
    }


@router.post("/matches/{match_id}/forfeit")
async def forfeit_match(
    match_id: UUID,
    request: Request,
    user=Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Concede immediately: the opponent wins now (Elo applied), instead of
    relying on the disconnect → grace-timer forfeit. The `finished` event is
    broadcast over the duel socket, which drives the loser's result screen."""
    user_id = user["id"]
    m = await pool.fetchrow(
        "SELECT player_a, player_b FROM matches WHERE id = $1", match_id
    )
    if m is None:
        raise HTTPException(status_code=404, detail="match not found")
    if user_id not in (m["player_a"], m["player_b"]):
        raise HTTPException(status_code=403, detail="not a player in this match")
    duels = request.app.state.duels
    duel = duels.get(match_id)
    if duel is None or duel.finished:
        raise HTTPException(status_code=409, detail="match is not active")
    await duels.finish(
        duel, "FORFEIT", duel.opponent(user_id),
        reason=f"player {user_id} forfeited",
    )
    return {"ok": True}


@router.get("/users/{user_id}/matches")
async def match_history(
    user_id: int,
    user=Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
):
    # Self-only: user_id is a sequential bigint, so a cross-user read would let
    # anyone enumerate every player's history.
    if user_id != user["id"]:
        raise HTTPException(status_code=403, detail="can only view your own history")
    # Join opponent_name + rating_delta so the lobby renders "vs <name> +24"
    # without an extra request per row.
    rows = await pool.fetch(
        """SELECT m.id, m.state, m.outcome, m.winner_id, m.finished_at,
                  p.title,
                  CASE WHEN m.player_a = $1 THEN m.player_b ELSE m.player_a END
                      AS opponent_id,
                  opp.username AS opponent_name,
                  rh.rating_after - rh.rating_before AS rating_delta
           FROM matches m
           JOIN problems p ON p.id = m.problem_id
           JOIN users opp
             ON opp.id = CASE WHEN m.player_a = $1 THEN m.player_b
                              ELSE m.player_a END
           LEFT JOIN rating_history rh
             ON rh.match_id = m.id AND rh.user_id = $1
           WHERE m.player_a = $1 OR m.player_b = $1
           ORDER BY m.created_at DESC LIMIT 50""",
        user_id,
    )
    return [serialize(r, "id") for r in rows]


@ws_router.websocket("/ws/duel/{match_id}")
async def duel_socket(websocket: WebSocket, match_id: UUID, last_seq: int = 0):
    # The same-origin handshake carries the httpOnly access cookie automatically,
    # so the JWT isn't in the URL (where it would leak into logs/history). On 4401
    # the client refreshes and reconnects.
    token = websocket.cookies.get(ACCESS_COOKIE)
    user_id = decode_token(token) if token else None
    if user_id is None:
        await websocket.close(code=4401)
        return
    manager = websocket.app.state.duels
    live = await manager.connect(match_id, user_id, websocket, last_seq)
    if not live:
        return
    try:
        while True:
            await websocket.receive_text()  # client keepalives; ignored
    except WebSocketDisconnect:
        await manager.disconnect(match_id, user_id)

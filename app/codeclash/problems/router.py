import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_pool

router = APIRouter(prefix="/api/problems", tags=["problems"])


@router.get("")
async def list_problems(pool: asyncpg.Pool = Depends(get_pool)):
    rows = await pool.fetch(
        "SELECT id, slug, title, band, rating FROM problems ORDER BY rating, id"
    )
    return [dict(r) for r in rows]


@router.get("/{problem_id}")
async def get_problem(problem_id: int, pool: asyncpg.Pool = Depends(get_pool)):
    row = await pool.fetchrow(
        """SELECT id, slug, title, statement_md, band, time_limit_ms,
                  memory_limit_kb, rating, rating_count
           FROM problems WHERE id = $1""",
        problem_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="problem not found")
    return dict(row)

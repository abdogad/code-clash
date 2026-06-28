"""Forward-only schema migrations + first-boot seed, applied by the app at startup
(main.py lifespan).

Why here, not db/init: docker-entrypoint-initdb.d runs only on a fresh volume, so
it never reaches an already-provisioned DB — schema changes silently broke prod.
This applies any unapplied migration on every boot, fresh or existing, so a deploy
can't leave the schema behind the code.

Contract:
- db/migrations/NNNN_name.sql — forward-only, applied in filename order, each in
  its own transaction, recorded in schema_migrations (runs once). Keep them
  idempotent (IF NOT EXISTS): the first rollout may hit a DB that already has some
  of the changes (e.g. a hand-patched prod).
- db/seed/*.sql — emptiness-guarded bootstrap data, so it never re-adds rows
  removed on purpose later.

An advisory lock serializes concurrent app starts — cheap insurance.
"""

import logging
import os
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)

# Container layout: code at /srv/codeclash, SQL at /srv/db; DB_DIR overrides for
# host runs where the tree isn't flattened that way.
DB_DIR = Path(os.environ.get("DB_DIR") or Path(__file__).resolve().parents[1] / "db")

# Arbitrary fixed key so every app instance contends for the same lock.
_LOCK_KEY = 0xC0DEC1A5


async def migrate(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
        try:
            await _apply_migrations(conn)
            await _apply_seeds(conn)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)


async def _apply_migrations(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version    TEXT PRIMARY KEY,
               applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
           )"""
    )
    applied = {
        r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")
    }
    for path in sorted((DB_DIR / "migrations").glob("*.sql")):
        version = path.stem
        if version in applied:
            continue
        async with conn.transaction():
            await conn.execute(path.read_text())
            await conn.execute(
                "INSERT INTO schema_migrations (version) VALUES ($1)", version
            )
        log.info("applied migration %s", version)


async def _apply_seeds(conn: asyncpg.Connection) -> None:
    from .config import settings

    seed = DB_DIR / "seed"
    # Problem catalog: seed only when empty, so it never fights later edits via
    # the import tooling.
    if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM problems)"):
        await conn.execute((seed / "problems.sql").read_text())
        log.info("seeded problem catalog")
    # Dev logins (alice/bob, known password) — never on a production database.
    if settings.app_env != "production" and not await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM users)"
    ):
        await conn.execute((seed / "dev_users.sql").read_text())
        log.info("seeded dev users")

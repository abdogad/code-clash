"""Verdict consumer: reads the verdicts stream and applies results to Postgres.

Idempotent: the UPDATE only touches rows still PENDING, so a redelivered verdict
is a no-op. At-least-once delivery + idempotent write = effectively-once.
"""

import asyncio
import logging
from uuid import UUID

from .. import streams

log = logging.getLogger(__name__)

CONSUMER = "app-main"


async def consume_verdicts(app) -> None:
    pool, r = app.state.pool, app.state.redis
    while True:
        try:
            resp = await r.xreadgroup(
                streams.APP_GROUP,
                CONSUMER,
                {streams.VERDICTS: ">"},
                count=10,
                block=5000,
            )
            for _stream, messages in resp or []:
                for msg_id, f in messages:
                    await apply_verdict(app, pool, f)
                    await r.xack(streams.VERDICTS, streams.APP_GROUP, msg_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("verdict consumer error; retrying in 1s")
            await asyncio.sleep(1)


async def apply_verdict(app, pool, f: dict) -> None:
    row = await pool.fetchrow(
        """UPDATE submissions
           SET verdict = $2::verdict, tests_passed = $3, tests_total = $4,
               judged_at = now()
           WHERE id = $1 AND verdict = 'PENDING'
           RETURNING match_id, user_id, verdict, tests_passed, tests_total""",
        UUID(f["submission_id"]),
        f["verdict"],
        int(f["tests_passed"]),
        int(f["tests_total"]),
    )
    if row is None:
        log.info("duplicate verdict for %s ignored", f["submission_id"])
        return
    if row["match_id"] is not None:
        # Duel flow: append the verdict event and re-run the fair-win check.
        await app.state.duels.on_verdict(dict(row))

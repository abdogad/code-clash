"""Re-enqueues submissions stranded in PENDING.

A submission is written to Postgres and *then* XADDed (two systems, no 2PC), so a
crash between them — or a dropped/trimmed message — leaves a PENDING row no worker
will pick up (XAUTOCLAIM only reclaims messages that reached the stream). Since the
fair-win rule holds finalization while an opponent has an *earlier* PENDING
submission, one stranded PENDING can wedge a whole duel until its time cap.

Any submission still PENDING past STUCK_AFTER_S is re-XADDed. Safe because judging
and the verdict write are idempotent, so a genuinely in-flight one just gets judged
again (wasted CPU), never double-counted. STUCK_AFTER_S sits well above the
worst-case honest judge time (worker reclaim 120s + run), so a slow-but-live
submission is never reaped early.
"""

import asyncio
import logging

from .. import streams

log = logging.getLogger(__name__)

STUCK_AFTER_S = 300
SWEEP_INTERVAL_S = 60


async def _reap_once(app) -> None:
    pool, r = app.state.pool, app.state.redis
    rows = await pool.fetch(
        """SELECT s.id, s.code, s.language, s.submitted_at,
                  p.slug, p.time_limit_ms, p.memory_limit_kb
           FROM submissions s
           JOIN problems p ON p.id = s.problem_id
           WHERE s.verdict = 'PENDING'
             AND s.submitted_at < now() - make_interval(secs => $1)""",
        STUCK_AFTER_S,
    )
    for row in rows:
        await r.xadd(
            streams.SUBMISSIONS,
            {
                "submission_id": str(row["id"]),
                "problem_slug": row["slug"],
                "time_limit_ms": str(row["time_limit_ms"]),
                "memory_limit_kb": str(row["memory_limit_kb"]),
                "code": row["code"],
                "language": row["language"],
                "submitted_at": row["submitted_at"].isoformat(),
            },
            maxlen=streams.SUBMISSIONS_MAXLEN,
            approximate=True,
        )
        log.warning("re-enqueued stranded PENDING submission %s", row["id"])


async def reaper_loop(app) -> None:
    log.info("submission reaper started")
    while True:
        try:
            await _reap_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("reaper iteration failed")
        await asyncio.sleep(SWEEP_INTERVAL_S)

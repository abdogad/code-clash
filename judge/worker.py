"""CodeClash judge worker.

Consumes the submissions stream (XREADGROUP), judges, publishes to the verdicts
stream, then acks. Crash safety: an un-acked message is reclaimed by another worker
via XAUTOCLAIM, and the app's verdict write is idempotent, so reclaim-after-publish
is harmless. By design this process talks only to Redis — no Postgres, no internet
(internal-only docker network).
"""

import asyncio
import logging
import os
import socket

import redis.asyncio as aioredis

from judging import judge_submission

# Mirrors app/codeclash/streams.py — keep in sync.
SUBMISSIONS = "stream:submissions"
VERDICTS = "stream:verdicts"
PROGRESS = "stream:judge_progress"  # cosmetic per-test progress (no group)
JUDGE_GROUP = "judges"
VERDICTS_MAXLEN = 10_000  # mirror of streams.VERDICTS_MAXLEN

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
CONSUMER = os.environ.get("JUDGE_NAME", socket.gethostname())
TESTCASES_DIR = os.environ.get("TESTCASES_DIR", "/testcases")
# A submission idle this long is assumed orphaned and reclaimed. Must exceed the
# worst-case honest judge time, or a slow-but-correct one gets judged twice (still
# correct via the idempotent write, just wasted CPU): Python runs at time_mult=3.0,
# so ~10 tests at a 2s limit approach 60s; 120s leaves headroom.
RECLAIM_IDLE_MS = 120_000

log = logging.getLogger("judge")


async def judge(fields: dict, on_progress=None) -> dict:
    """Judge one submission and return the verdict message."""
    try:
        verdict = await judge_submission(fields, on_progress)
    except Exception:
        # A judging-infrastructure bug must not wedge the duel: emit RE so
        # the player can resubmit, and log loudly for us.
        # TODO: track delivery count and route true poison messages aside.
        log.exception("internal judging error for %s", fields.get("submission_id"))
        verdict = {
            "submission_id": fields["submission_id"],
            "verdict": "RE",
            "tests_passed": "0",
            "tests_total": "0",
            "detail": "internal judging error",
        }
    verdict["judged_by"] = CONSUMER
    return verdict


async def handle(r: aioredis.Redis, msg_id: str, fields: dict) -> None:
    async def on_progress(passed: int, total: int) -> None:
        # Fire-and-forget progress for the live bars: a dropped tick is harmless
        # (the verdict is authoritative), so a publish failure never breaks judging.
        try:
            await r.xadd(
                PROGRESS,
                {
                    "submission_id": fields["submission_id"],
                    "tests_passed": str(passed),
                    "tests_total": str(total),
                },
                maxlen=2000,
                approximate=True,
            )
        except Exception:
            log.debug("progress publish failed", exc_info=True)

    # No fields = the message was trimmed out from under its PEL entry (stream hit
    # MAXLEN while un-acked). Nothing to judge; ack so it stops being reclaimed.
    if not fields:
        await r.xack(SUBMISSIONS, JUDGE_GROUP, msg_id)
        log.warning("acked trimmed/empty submission message %s", msg_id)
        return
    verdict = await judge(fields, on_progress)
    await r.xadd(VERDICTS, verdict, maxlen=VERDICTS_MAXLEN, approximate=True)
    await r.xack(SUBMISSIONS, JUDGE_GROUP, msg_id)
    # The per-submission "judged" line (with the decisive sandbox JSON) is emitted
    # by judge_submission itself; the internal-error path is logged in judge().


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Per-run CPU/memory/instruction accounting is set up by the tallyrun binary
    # itself, once per run (sandbox.py) — no worker-startup cgroup wiring needed.
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await r.xgroup_create(SUBMISSIONS, JUDGE_GROUP, id="0", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    log.info("worker %s ready", CONSUMER)
    while True:
        try:
            # First reclaim work orphaned by a crashed worker. count=1 spreads a
            # backlog across workers instead of one grabbing a batch to judge serially.
            reclaimed = await r.xautoclaim(
                SUBMISSIONS, JUDGE_GROUP, CONSUMER,
                min_idle_time=RECLAIM_IDLE_MS, count=1,
            )
            messages = list(reclaimed[1]) if reclaimed else []
            # ...then block for new submissions.
            if not messages:
                resp = await r.xreadgroup(
                    JUDGE_GROUP, CONSUMER, {SUBMISSIONS: ">"}, count=1, block=5000
                )
                for _stream, msgs in resp or []:
                    messages.extend(msgs)
            for msg_id, fields in messages:
                await handle(r, msg_id, fields)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker loop error; retrying in 1s")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())

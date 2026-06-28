"""Redis Streams names shared by the app and the judge workers.

The judge image keeps its own copy (judge/worker.py) — rename in both.
"""

SUBMISSIONS = "stream:submissions"
VERDICTS = "stream:verdicts"
# Cosmetic per-test progress: read with XREAD (no consumer group), ephemeral.
PROGRESS = "stream:judge_progress"
JUDGE_GROUP = "judges"
APP_GROUP = "app"

# Safety cap on the durable streams (approximate trim). Redis runs maxmemory +
# noeviction, so an uncapped stream growing during a judging outage would make
# Redis reject all writes and wedge the app. Sized well above any healthy backlog,
# so it only bounds the disaster case. Judge keeps its own copy of the verdict cap
# (judge/worker.py) — keep in sync.
SUBMISSIONS_MAXLEN = 10_000
VERDICTS_MAXLEN = 10_000


async def ensure_groups(r) -> None:
    for stream, group in ((SUBMISSIONS, JUDGE_GROUP), (VERDICTS, APP_GROUP)):
        try:
            await r.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise

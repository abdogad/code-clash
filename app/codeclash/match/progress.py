"""Judge progress consumer: relays cosmetic per-test progress to duel sockets.

Progress is ephemeral — XREAD from '$' (new messages only), no consumer group, no
ack. A dropped message just means a live bar skips a tick; the verdict stays
authoritative.
"""

import asyncio
import logging

from .. import streams

log = logging.getLogger(__name__)


async def consume_progress(app) -> None:
    r = app.state.redis
    last_id = "$"
    while True:
        try:
            resp = await r.xread({streams.PROGRESS: last_id}, count=50, block=5000)
            for _stream, messages in resp or []:
                for msg_id, fields in messages:
                    last_id = msg_id
                    await app.state.duels.on_progress(fields)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("progress consumer error; retrying in 1s")
            await asyncio.sleep(1)

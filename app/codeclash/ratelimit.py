"""Fixed-window rate limiter (Redis INCR + per-window TTL).

Coarse — a burst across a window boundary can briefly see up to 2x the cap — but
O(1) and enough for shedding abuse, not fairness. Keyed by app identity, not
client IP (every request shares nginx's IP until X-Forwarded-For is trusted).
"""

import time

from fastapi import HTTPException


async def check_rate(r, key: str, limit: int, window_s: int) -> None:
    """Raise 429 if `key` exceeds `limit` events in the current window."""
    bucket = int(time.time()) // window_s
    rkey = f"rl:{key}:{bucket}"
    # EXPIRE NX in the same pipeline so a crash can't leave the key without a TTL.
    pipe = r.pipeline(transaction=False)
    pipe.incr(rkey)
    pipe.expire(rkey, window_s, nx=True)
    n, _ = await pipe.execute()
    if n > limit:
        raise HTTPException(status_code=429, detail="too many requests; slow down")

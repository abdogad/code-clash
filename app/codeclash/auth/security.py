import asyncio
import time
import uuid

import bcrypt
import jwt
from starlette.concurrency import run_in_threadpool

from ..config import settings

# Short-lived access token + long-lived refresh token (sent only to
# /api/auth/refresh): a leaked access token is good for minutes, not the week-long
# session.
ACCESS_TTL_S = 15 * 60
REFRESH_TTL_S = 7 * 24 * 3600
ALGORITHM = "HS256"

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:  # malformed hash (e.g. legacy placeholder rows)
        return False


# bcrypt (~200ms CPU) runs in the shared threadpool so it never blocks the loop,
# but that pool is finite (~40 threads): an auth storm could fill it and stall
# every other run_in_threadpool caller. This semaphore caps concurrent hashes so
# the pool stays available under a flood.
_BCRYPT_CONCURRENCY = 8
_bcrypt_sem = asyncio.Semaphore(_BCRYPT_CONCURRENCY)


async def hash_password_async(password: str) -> str:
    async with _bcrypt_sem:
        return await run_in_threadpool(hash_password, password)


async def verify_password_async(password: str, password_hash: str) -> bool:
    async with _bcrypt_sem:
        return await run_in_threadpool(verify_password, password, password_hash)


def create_access_token(user_id: int) -> str:
    now = int(time.time())
    payload = {"sub": str(user_id), "typ": "access", "iat": now, "exp": now + ACCESS_TTL_S}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "typ": "refresh",
        "jti": uuid.uuid4().hex,  # unique id so a single token can be revoked
        "iat": now,
        "exp": now + REFRESH_TTL_S,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str, expected_typ: str = "access") -> int | None:
    """Verify a token and return its user_id, or None if invalid/expired/wrong
    type. `expected_typ` stops a refresh token from being replayed as an access
    token (and vice-versa)."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        if payload.get("typ") != expected_typ:
            return None
        return int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def decode_refresh(token: str) -> dict | None:
    """Full refresh-token payload (we need jti + exp for rotation/revocation),
    or None if invalid/expired/not a refresh token."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        return payload if payload.get("typ") == "refresh" else None
    except jwt.InvalidTokenError:
        return None


# --- refresh-token revocation -------------------------------------------------
# Stateless JWTs can't be un-issued, so keep a Redis denylist of revoked jtis
# (logout + the old token on every rotation). Entries expire with the token, so
# the set never grows unbounded.

def _revoked_key(jti: str) -> str:
    return f"revoked_refresh:{jti}"


async def revoke_refresh(r, payload: dict) -> None:
    ttl = max(1, int(payload["exp"]) - int(time.time()))
    await r.set(_revoked_key(payload["jti"]), "1", ex=ttl)


async def is_refresh_revoked(r, jti: str) -> bool:
    return bool(await r.exists(_revoked_key(jti)))

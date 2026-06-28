import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..config import settings
from ..deps import get_current_user, get_pool, get_redis
from ..ratelimit import check_rate
from ..rating.leaderboard import add_user
from . import security

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_auth_cookies(response: Response, user_id: int) -> None:
    """Issue access+refresh as httpOnly cookies: httpOnly keeps them out of JS (XSS
    can't read them), SameSite=strict is our CSRF defense (same-origin SPA), Secure
    (prod only) keeps them off the wire in cleartext. The refresh cookie is
    path-scoped to /api/auth so it isn't sent on every API call."""
    secure = settings.app_env == "production"
    response.set_cookie(
        security.ACCESS_COOKIE,
        security.create_access_token(user_id),
        max_age=security.ACCESS_TTL_S,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        security.REFRESH_COOKIE,
        security.create_refresh_token(user_id),
        max_age=security.REFRESH_TTL_S,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/api/auth",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(security.ACCESS_COOKIE, path="/")
    response.delete_cookie(security.REFRESH_COOKIE, path="/api/auth")


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/register", status_code=201)
async def register(
    body: RegisterIn,
    response: Response,
    pool: asyncpg.Pool = Depends(get_pool),
    r=Depends(get_redis),
):
    # bcrypt (~200ms CPU) runs off the event loop and concurrency-capped so a
    # registration storm can't starve the shared threadpool (see
    # security.hash_password_async).
    pw_hash = await security.hash_password_async(body.password)
    try:
        row = await pool.fetchrow(
            """INSERT INTO users (username, email, password_hash)
               VALUES ($1, $2, $3) RETURNING id, rating""",
            body.username,
            body.email,
            pw_hash,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="username or email already taken")
    await add_user(r, row["id"], row["rating"])
    _set_auth_cookies(response, row["id"])
    return {"user": {"id": row["id"], "username": body.username, "rating": row["rating"]}}


@router.post("/login")
async def login(
    body: LoginIn,
    response: Response,
    pool: asyncpg.Pool = Depends(get_pool),
    r=Depends(get_redis),
):
    # Throttle password guesses per account (keyed by username, not IP), capping
    # brute force at 10/min per target.
    await check_rate(r, f"login:{body.username.lower()}", limit=10, window_s=60)
    row = await pool.fetchrow(
        "SELECT id, username, rating, password_hash FROM users WHERE username = $1",
        body.username,
    )
    ok = row is not None and await security.verify_password_async(
        body.password, row["password_hash"]
    )
    if not ok:
        raise HTTPException(status_code=401, detail="invalid credentials")
    _set_auth_cookies(response, row["id"])
    return {"user": {"id": row["id"], "username": row["username"], "rating": row["rating"]}}


@router.post("/refresh")
async def refresh(request: Request, response: Response, r=Depends(get_redis)):
    """Mint a new access token from the refresh cookie, rotating the refresh token
    too: the presented one is revoked and replaced, so a stolen token works at most
    once — replaying the revoked one is rejected and logs the client out, surfacing
    theft."""
    token = request.cookies.get(security.REFRESH_COOKIE)
    payload = security.decode_refresh(token) if token else None
    if payload is None or await security.is_refresh_revoked(r, payload["jti"]):
        # HTTPException would discard cookies set on `response`; return an explicit
        # 401 that carries the Set-Cookie deletions.
        err = JSONResponse({"detail": "invalid or expired refresh token"}, status_code=401)
        _clear_auth_cookies(err)
        return err
    await security.revoke_refresh(r, payload)
    _set_auth_cookies(response, int(payload["sub"]))
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response, r=Depends(get_redis)):
    """Invalidate the session: revoke the refresh token server-side and clear both
    cookies. (The access token stays valid until it expires, ≤15 min.)"""
    token = request.cookies.get(security.REFRESH_COOKIE)
    payload = security.decode_refresh(token) if token else None
    if payload is not None:
        await security.revoke_refresh(r, payload)
    _clear_auth_cookies(response)
    return {"ok": True}


@router.get("/me")
async def me(user=Depends(get_current_user)):
    return dict(user)

"""Canonical Better Auth session resolution for FastAPI.

FastAPI never parses Better Auth cookies. It asks the colocated Better Auth
service for the session and exposes a small immutable user object to route
dependencies. Missing or unavailable auth fails closed on protected routes
without breaking intentionally public APIs.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)
_SESSION_COOKIE_FRAGMENT = "better-auth.session_token="


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: str
    name: str
    email: str
    email_verified: bool
    image: str | None = None


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _user_from_payload(payload: object) -> CurrentUser | None:
    if not isinstance(payload, dict):
        return None
    user = payload.get("user")
    session = payload.get("session")
    if not isinstance(user, dict) or not isinstance(session, dict):
        return None

    expires_at = _parse_datetime(session.get("expiresAt"))
    if expires_at is None or expires_at <= datetime.now(UTC):
        return None

    user_id = user.get("id")
    email = user.get("email")
    if not isinstance(user_id, str) or not user_id or not isinstance(email, str) or not email:
        return None

    return CurrentUser(
        id=user_id,
        name=str(user.get("name") or ""),
        email=email,
        email_verified=bool(user.get("emailVerified")),
        image=user.get("image") if isinstance(user.get("image"), str) else None,
    )


async def _fetch_session(request: Request) -> object:
    internal_url = os.getenv("BETTER_AUTH_INTERNAL_URL", "http://127.0.0.1:3000").rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    headers = {
        "cookie": request.headers.get("cookie", ""),
        "host": host,
        "x-forwarded-host": host,
        "x-forwarded-proto": forwarded_proto,
        "user-agent": request.headers.get("user-agent", ""),
    }
    async with httpx.AsyncClient(timeout=2.0) as client:
        response = await client.get(f"{internal_url}/api/auth/get-session", headers=headers)
    if response.status_code in {401, 403}:
        return None
    response.raise_for_status()
    return response.json()


async def better_auth_session_middleware(request: Request, call_next):
    request.state.current_user = None
    if _SESSION_COOKIE_FRAGMENT in request.headers.get("cookie", ""):
        try:
            request.state.current_user = _user_from_payload(await _fetch_session(request))
        except Exception as exc:
            logger.warning("Better Auth session resolution failed: %s", type(exc).__name__)
    return await call_next(request)


def get_current_user(request: Request) -> CurrentUser | None:
    return getattr(getattr(request, "state", None), "current_user", None)


def require_current_user(request: Request) -> CurrentUser:
    user = get_current_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Session"},
        )
    return user

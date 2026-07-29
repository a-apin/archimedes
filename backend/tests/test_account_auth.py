from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from archimedes.api import account_auth
from archimedes.api.account_auth import CurrentUser, require_current_user
from fastapi import Depends, FastAPI


def _session_payload(*, expired: bool = False) -> dict:
    expires_at = datetime.now(UTC) + (-timedelta(seconds=1) if expired else timedelta(hours=1))
    return {
        "user": {
            "id": "user-1",
            "name": "Daniel",
            "email": "daniel@example.com",
            "emailVerified": True,
            "image": None,
        },
        "session": {"id": "session-1", "expiresAt": expires_at.isoformat()},
    }


def _app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(account_auth.better_auth_session_middleware)

    @app.get("/public")
    async def public():
        return {"ok": True}

    @app.get("/protected")
    async def protected(user: CurrentUser = Depends(require_current_user)):
        return {"id": user.id, "email": user.email}

    return app


@pytest.mark.asyncio
async def test_protected_route_requires_better_auth_session(monkeypatch):
    fetch = AsyncMock()
    monkeypatch.setattr(account_auth, "_fetch_session", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.get("/protected")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_session_exposes_canonical_user(monkeypatch):
    monkeypatch.setattr(account_auth, "_fetch_session", AsyncMock(return_value=_session_payload()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.get(
            "/protected",
            headers={"cookie": "better-auth.session_token=opaque", "host": "archimedes-arc.com"},
        )

    assert response.status_code == 200
    assert response.json() == {"id": "user-1", "email": "daniel@example.com"}


@pytest.mark.asyncio
async def test_expired_or_invalid_session_is_rejected(monkeypatch):
    fetch = AsyncMock(side_effect=[_session_payload(expired=True), None])
    monkeypatch.setattr(account_auth, "_fetch_session", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://test") as client:
        expired = await client.get("/protected", headers={"cookie": "better-auth.session_token=expired"})
        invalid = await client.get("/protected", headers={"cookie": "better-auth.session_token=invalid"})

    assert expired.status_code == 401
    assert invalid.status_code == 401


@pytest.mark.asyncio
async def test_auth_outage_fails_closed_only_for_protected_routes(monkeypatch):
    monkeypatch.setattr(account_auth, "_fetch_session", AsyncMock(side_effect=RuntimeError("auth unavailable")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://test") as client:
        public = await client.get("/public", headers={"cookie": "better-auth.session_token=opaque"})
        protected = await client.get("/protected", headers={"cookie": "better-auth.session_token=opaque"})

    assert public.status_code == 200
    assert protected.status_code == 401

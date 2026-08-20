"""``GET /api/account/usage`` — the CLI ``meter`` command backend (#1305).

Covers:

  1. the route 401s without a Better Auth session (mirrors
     ``test_paper_routes_auth.py``'s auth-swap contract);
  2. usage reads the SAME Redis buckets ``enforce_generation_quota`` writes —
     ``GenerationQuota.peek`` reads the identical
     ``archimedes:genquota:{scope}:{day}:{identity}`` key format, proven here
     by driving a real (mocked-at-the-Redis-boundary) ``GenerationQuota``
     instance through both a peek and a real increment and checking they see
     the same counter, not two independently-behaving code paths;
  3. the price quote embedded in the response is the literal
     ``generation_payment.quote()`` dict — never a re-derived number;
  4. a Redis outage renders as an honest ``used: null`` +
     ``error: "quota_backend_unavailable"``, never a fabricated ``0`` (the
     repo's fail-soft rule) — the ADVERSARIAL case: an outage that a naive
     implementation would silently report as "0 used, plenty of room left".

Hermetic: mocks the Better Auth session fetch and the Redis client at
``GenerationQuota._get_redis`` (the same boundary
``test_generation_quota.py`` mocks) — no live Redis, no live DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from archimedes.api import account_auth, account_usage_routes
from archimedes.services.generation_quota import GenerationQuota
from fastapi import FastAPI


@pytest.fixture()
def app():
    application = FastAPI()
    application.middleware("http")(account_auth.better_auth_session_middleware)
    application.include_router(account_usage_routes.account_usage_router)
    return application


def _session_for(user_id: str):
    async def fetch(_request):
        return {
            "user": {"id": user_id, "name": user_id, "email": f"{user_id}@example.com", "emailVerified": True},
            "session": {"id": f"s-{user_id}", "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        }

    return fetch


def _sign_in(monkeypatch, user_id: str = "user-1"):
    monkeypatch.setattr(account_auth, "_fetch_session", _session_for(user_id))


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"cookie": "better-auth.session_token=opaque", "host": "archimedes-arc.com"},
    )


def _mock_redis(get_returns: dict[str, str | None] | None = None):
    """A MagicMock redis client whose GET reads from a plain dict, so a peek
    and a subsequent real check_and_increment (via .incr) can be composed in
    the same test to prove they touch the same key space."""
    store: dict[str, str] = dict(get_returns or {})
    r = MagicMock()

    async def _get(key):
        return store.get(key)

    async def _incr(key):
        store[key] = str(int(store.get(key, "0")) + 1)
        return int(store[key])

    r.get = AsyncMock(side_effect=_get)
    r.incr = AsyncMock(side_effect=_incr)
    r.expire = AsyncMock(return_value=True)
    return r, store


# ── 1. Auth gate ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_usage_requires_a_better_auth_session(app, monkeypatch):
    monkeypatch.setattr(account_auth, "_fetch_session", AsyncMock(return_value=None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/account/usage")
    assert resp.status_code == 401


# ── 2. Reads the SAME buckets enforcement writes ────────────────────────


@pytest.mark.asyncio
async def test_usage_reads_the_same_bucket_enforcement_increments(app, monkeypatch):
    _sign_in(monkeypatch, "user-abc")
    redis_mock, _store = _mock_redis()
    monkeypatch.setattr(GenerationQuota, "_get_redis", AsyncMock(return_value=redis_mock))
    monkeypatch.setattr(account_usage_routes, "client_ip", lambda _r: "203.0.113.9")
    monkeypatch.setattr(account_usage_routes, "user_daily_cap", lambda: 10)
    monkeypatch.setattr(account_usage_routes, "ip_daily_cap", lambda: 20)

    # Simulate 3 prior generations via the REAL enforcement key format.
    quota = GenerationQuota()
    for _ in range(3):
        await quota.check_and_increment("user", "user-abc", 10)

    async with _client(app) as client:
        resp = await client.get("/api/account/usage")
    assert resp.status_code == 200
    body = resp.json()

    assert body["user_id"] == "user-abc"
    assert body["user"]["used"] == 3
    assert body["user"]["cap"] == 10
    assert body["user"]["remaining"] == 7
    assert body["user"]["unlimited"] is False
    assert body["user"]["error"] is None
    assert body["ip"]["used"] == 0
    assert body["ip"]["cap"] == 20
    assert body["ip"]["remaining"] == 20
    # UTC day bucket, sanity-shaped.
    assert body["date"] == datetime.now(UTC).strftime("%Y-%m-%d")


@pytest.mark.asyncio
async def test_usage_unlimited_cap_reports_no_remaining_ceiling(app, monkeypatch):
    _sign_in(monkeypatch, "user-unlimited")
    redis_mock, _store = _mock_redis()
    monkeypatch.setattr(GenerationQuota, "_get_redis", AsyncMock(return_value=redis_mock))
    monkeypatch.setattr(account_usage_routes, "client_ip", lambda _r: "203.0.113.9")
    monkeypatch.setattr(account_usage_routes, "user_daily_cap", lambda: 0)  # <=0 disables the layer
    monkeypatch.setattr(account_usage_routes, "ip_daily_cap", lambda: 20)

    async with _client(app) as client:
        resp = await client.get("/api/account/usage")
    body = resp.json()
    assert body["user"]["unlimited"] is True
    assert body["user"]["remaining"] is None


# ── 3. quote() is the single source of the price ────────────────────────


@pytest.mark.asyncio
async def test_usage_embeds_the_literal_generation_payment_quote(app, monkeypatch):
    _sign_in(monkeypatch)
    redis_mock, _store = _mock_redis()
    monkeypatch.setattr(GenerationQuota, "_get_redis", AsyncMock(return_value=redis_mock))
    monkeypatch.setattr(account_usage_routes, "client_ip", lambda _r: "203.0.113.9")

    sentinel_quote = {"price": "$0.42", "asset": "USDC", "pricing_model": "flat_v1", "dry_run": True}
    monkeypatch.setattr(account_usage_routes.generation_payment, "quote", lambda: dict(sentinel_quote))

    async with _client(app) as client:
        resp = await client.get("/api/account/usage")
    assert resp.json()["quote"] == sentinel_quote


# ── 4. Adversarial: Redis outage must render as an honest absence ──────


@pytest.mark.asyncio
async def test_redis_outage_renders_null_used_not_a_fabricated_zero(app, monkeypatch):
    """The guard-review adversarial demonstration: a naive read (``GET`` that
    swallows the exception and returns 0) would report "0 used, plenty of
    room" during an outage — indistinguishable from a genuinely idle account.
    This must instead surface ``used: null`` + an explicit error marker."""
    _sign_in(monkeypatch)

    r = MagicMock()
    r.get = AsyncMock(side_effect=ConnectionError("redis unreachable"))
    monkeypatch.setattr(GenerationQuota, "_get_redis", AsyncMock(return_value=r))
    monkeypatch.setattr(account_usage_routes, "client_ip", lambda _r: "203.0.113.9")

    async with _client(app) as client:
        resp = await client.get("/api/account/usage")
    assert resp.status_code == 200
    body = resp.json()

    assert body["user"]["used"] is None
    assert body["user"]["error"] == "quota_backend_unavailable"
    assert body["ip"]["used"] is None
    assert body["ip"]["error"] == "quota_backend_unavailable"
    # The would-be-naive failure mode this guards against:
    assert body["user"]["used"] != 0

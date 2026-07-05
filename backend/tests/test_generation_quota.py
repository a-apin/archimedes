"""Tests for the wallet-less generation quota (anti-abuse per-IP daily cap).

Hermetic — mocks at the Redis boundary; tests the cap logic, the real-IP
resolver, the 429 steering response, the authenticated bypass, and fail-closed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from archimedes.services.generation_quota import (
    GenerationQuota,
    client_ip,
    daily_cap,
    enforce_generation_quota,
)
from fastapi import HTTPException


def _mock_redis(incr_return: int):
    r = MagicMock()
    r.incr = AsyncMock(return_value=incr_return)
    r.expire = AsyncMock(return_value=True)
    return r


def _req(headers: dict | None = None, client_host: str | None = None):
    client = SimpleNamespace(host=client_host) if client_host else None
    return SimpleNamespace(headers=headers or {}, client=client)


# ─── GenerationQuota.check_and_increment ─────────────────────────────────


async def test_under_cap_allowed():
    q = GenerationQuota()
    q._get_redis = AsyncMock(return_value=_mock_redis(3))
    allowed, used = await q.check_and_increment("1.2.3.4", 5)
    assert allowed is True
    assert used == 3


async def test_at_cap_allowed():
    q = GenerationQuota()
    q._get_redis = AsyncMock(return_value=_mock_redis(5))
    allowed, used = await q.check_and_increment("1.2.3.4", 5)
    assert allowed is True  # the 5th is still allowed
    assert used == 5


async def test_over_cap_not_allowed():
    q = GenerationQuota()
    q._get_redis = AsyncMock(return_value=_mock_redis(6))
    allowed, used = await q.check_and_increment("1.2.3.4", 5)
    assert allowed is False
    assert used == 6


async def test_ttl_set_with_nx_every_hit():
    # EXPIRE ... NX runs on every hit: it self-heals a missing TTL but (being NX)
    # only applies when the key has none, so it never slides the window.
    r = _mock_redis(2)
    q = GenerationQuota()
    q._get_redis = AsyncMock(return_value=r)
    await q.check_and_increment("1.2.3.4", 5)
    r.expire.assert_awaited_once()
    assert r.expire.await_args.kwargs.get("nx") is True


async def test_expire_failure_does_not_discard_count():
    # A failed TTL set must NOT throw away the successful INCR — the cap decision
    # is still made from the real count. (Copilot #792)
    r = _mock_redis(6)
    r.expire = AsyncMock(side_effect=ConnectionError("expire blip"))
    q = GenerationQuota()
    q._get_redis = AsyncMock(return_value=r)
    allowed, used = await q.check_and_increment("1.2.3.4", 5)
    assert used == 6  # the count survived the EXPIRE failure
    assert allowed is False  # 6 > 5 still enforced


async def test_check_fails_closed_on_redis_error():
    # A Redis outage must NOT silently disable the wallet-less spend cap (#929).
    q = GenerationQuota()
    q._get_redis = AsyncMock(side_effect=ConnectionError("redis down"))
    allowed, used = await q.check_and_increment("1.2.3.4", 5)
    assert allowed is False  # fail CLOSED — cap held, not bypassed
    assert used == 0


# ─── client_ip resolver ──────────────────────────────────────────────────


def test_client_ip_prefers_x_real_ip():
    req = _req({"x-real-ip": "9.9.9.9", "x-forwarded-for": "1.1.1.1"})
    assert client_ip(req) == "9.9.9.9"


def test_client_ip_ignores_spoofable_xff():
    # X-Forwarded-For is client-forgeable and must NOT be used; with no X-Real-IP,
    # fall back to the socket peer, never the XFF value. (Copilot #792)
    req = _req({"x-forwarded-for": "1.1.1.1, 2.2.2.2"}, client_host="3.3.3.3")
    assert client_ip(req) == "3.3.3.3"
    # And with no socket either, it's "unknown" — never the spoofable XFF.
    assert client_ip(_req({"x-forwarded-for": "1.1.1.1"})) == "unknown"


def test_client_ip_rejects_malformed_x_real_ip():
    # A non-IP header value must not become a Redis key — fall back. (Copilot #792)
    req = _req({"x-real-ip": "not-an-ip; DROP"}, client_host="3.3.3.3")
    assert client_ip(req) == "3.3.3.3"


def test_client_ip_socket_fallback():
    assert client_ip(_req({}, client_host="3.3.3.3")) == "3.3.3.3"


def test_client_ip_unknown_when_nothing_available():
    assert client_ip(_req({})) == "unknown"


def test_client_ip_accepts_ipv6_real_ip():
    assert client_ip(_req({"x-real-ip": "2001:db8::1"})) == "2001:db8::1"


# ─── enforce_generation_quota ────────────────────────────────────────────


async def test_enforce_skips_authenticated_wallet(monkeypatch):
    called = {"n": 0}

    class Boom:
        async def check_and_increment(self, *a):
            called["n"] += 1
            return (False, 99)

        async def close(self):
            pass

    monkeypatch.setattr("archimedes.services.generation_quota.GenerationQuota", Boom)
    # A wallet-bearing caller must bypass the cap entirely — never touch Redis.
    await enforce_generation_quota(_req({"x-real-ip": "1.1.1.1"}), wallet="0xabc")
    assert called["n"] == 0


async def test_enforce_disabled_when_cap_zero(monkeypatch):
    monkeypatch.setenv("WALLET_LESS_GENERATION_DAILY_CAP", "0")
    called = {"n": 0}

    class Boom:
        async def check_and_increment(self, *a):
            called["n"] += 1
            return (False, 99)

        async def close(self):
            pass

    monkeypatch.setattr("archimedes.services.generation_quota.GenerationQuota", Boom)
    await enforce_generation_quota(_req({"x-real-ip": "1.1.1.1"}), wallet=None)
    assert called["n"] == 0


async def test_enforce_allows_under_cap(monkeypatch):
    monkeypatch.setenv("WALLET_LESS_GENERATION_DAILY_CAP", "5")

    class Under:
        async def check_and_increment(self, ip, cap):
            return (True, 2)

        async def close(self):
            pass

    monkeypatch.setattr("archimedes.services.generation_quota.GenerationQuota", Under)
    # No raise.
    await enforce_generation_quota(_req({"x-real-ip": "1.1.1.1"}), wallet=None)


async def test_enforce_raises_429_over_cap(monkeypatch):
    monkeypatch.setenv("WALLET_LESS_GENERATION_DAILY_CAP", "5")

    class Over:
        async def check_and_increment(self, ip, cap):
            return (False, 6)

        async def close(self):
            pass

    monkeypatch.setattr("archimedes.services.generation_quota.GenerationQuota", Over)
    with pytest.raises(HTTPException) as ei:
        await enforce_generation_quota(_req({"x-real-ip": "1.1.1.1"}), wallet=None)
    assert ei.value.status_code == 429
    assert ei.value.detail["reason"] == "wallet_less_generation_cap"
    assert ei.value.detail["cap"] == 5
    assert "Connect a wallet" in ei.value.detail["message"]


async def test_enforce_rejects_wallet_less_when_redis_down(monkeypatch):
    # End-to-end fail-closed: a real GenerationQuota whose Redis is unreachable
    # rejects the wallet-less caller with a 429 (the cap is not bypassed, #929).
    monkeypatch.setenv("WALLET_LESS_GENERATION_DAILY_CAP", "5")
    monkeypatch.setattr(
        "archimedes.services.generation_quota.GenerationQuota._get_redis",
        AsyncMock(side_effect=ConnectionError("redis down")),
    )
    with pytest.raises(HTTPException) as ei:
        await enforce_generation_quota(_req({"x-real-ip": "1.1.1.1"}), wallet=None)
    assert ei.value.status_code == 429


async def test_enforce_still_allows_authenticated_wallet_when_redis_down(monkeypatch):
    # The fail-closed cap must NOT affect a SIWE-authenticated caller — they
    # bypass the cap before any Redis call, so a cache outage can't block them.
    monkeypatch.setenv("WALLET_LESS_GENERATION_DAILY_CAP", "5")
    monkeypatch.setattr(
        "archimedes.services.generation_quota.GenerationQuota._get_redis",
        AsyncMock(side_effect=ConnectionError("redis down")),
    )
    # No exception — wallet-connected user sails through.
    await enforce_generation_quota(_req({"x-real-ip": "1.1.1.1"}), wallet="0x" + "a" * 40)


# ─── daily_cap config ────────────────────────────────────────────────────


def test_daily_cap_default(monkeypatch):
    monkeypatch.delenv("WALLET_LESS_GENERATION_DAILY_CAP", raising=False)
    assert daily_cap() == 5


def test_daily_cap_env_override(monkeypatch):
    monkeypatch.setenv("WALLET_LESS_GENERATION_DAILY_CAP", "3")
    assert daily_cap() == 3


def test_daily_cap_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("WALLET_LESS_GENERATION_DAILY_CAP", "not-an-int")
    assert daily_cap() == 5

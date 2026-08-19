"""Tests for the rebuilt generation caps (#1194 revision a).

Two stacked daily buckets — per-account (``user``) and per-IP (``ip``) — both
of which must pass. Hermetic: mocks at the Redis boundary. Covers the cap
arithmetic, the layer ORDERING (a user over their own cap must not drain the
shared IP bucket), the honest outage semantics (Redis down → 503, never a
false "limit reached" 429), the real-IP resolver, and per-layer disable.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from archimedes.services.generation_quota import (
    GenerationQuota,
    client_ip,
    enforce_generation_quota,
    ip_daily_cap,
    user_daily_cap,
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


def _quota_with(redis_mock) -> GenerationQuota:
    q = GenerationQuota()
    q._get_redis = AsyncMock(return_value=redis_mock)
    q.close = AsyncMock()  # nothing real to close
    return q


# ─── GenerationQuota.check_and_increment ─────────────────────────────────


async def test_under_cap_allowed():
    q = _quota_with(_mock_redis(3))
    allowed, used = await q.check_and_increment("user", "u1", 5)
    assert allowed is True
    assert used == 3


async def test_at_cap_allowed():
    q = _quota_with(_mock_redis(5))
    allowed, used = await q.check_and_increment("user", "u1", 5)
    assert allowed is True  # the 5th is still allowed
    assert used == 5


async def test_over_cap_not_allowed():
    q = _quota_with(_mock_redis(6))
    allowed, used = await q.check_and_increment("user", "u1", 5)
    assert allowed is False
    assert used == 6


async def test_key_carries_scope_so_layers_cannot_collide():
    """A user id that happens to look like an IP must land in a different
    bucket than the same string in the ip layer — the scope is part of the key."""
    r = _mock_redis(1)
    q = _quota_with(r)
    await q.check_and_increment("user", "1.2.3.4", 5)
    user_key = r.incr.await_args.args[0]
    await q.check_and_increment("ip", "1.2.3.4", 5)
    ip_key = r.incr.await_args.args[0]
    assert user_key != ip_key
    assert ":user:" in user_key and ":ip:" in ip_key


async def test_ttl_set_with_nx_every_hit():
    r = _mock_redis(2)
    q = _quota_with(r)
    await q.check_and_increment("ip", "1.2.3.4", 5)
    r.expire.assert_awaited_once()
    assert r.expire.await_args.kwargs.get("nx") is True


async def test_expire_failure_does_not_discard_count():
    r = _mock_redis(6)
    r.expire = AsyncMock(side_effect=ConnectionError("expire blip"))
    q = _quota_with(r)
    allowed, used = await q.check_and_increment("ip", "1.2.3.4", 5)
    assert used == 6  # the count survived the EXPIRE failure
    assert allowed is False


async def test_redis_error_fails_closed_with_error_sentinel():
    r = MagicMock()
    r.incr = AsyncMock(side_effect=ConnectionError("redis down"))
    q = _quota_with(r)
    allowed, used = await q.check_and_increment("user", "u1", 5)
    assert allowed is False  # fail-closed: spend cap held
    assert used == 0  # the sentinel enforce_generation_quota maps to 503


# ─── enforce_generation_quota: stacking and ordering ─────────────────────


def _patched_enforce(monkeypatch, redis_mock):
    """Route the module-level GenerationQuota construction through our mock.

    String-target setattr, deliberately: the module is already imported via
    ``from ... import`` at the top, and importing it a second way just to
    hand monkeypatch an object tripped the both-import-styles lint.
    """
    q = _quota_with(redis_mock)
    monkeypatch.setattr("archimedes.services.generation_quota.GenerationQuota", lambda *a, **k: q)
    return q


async def test_both_layers_under_cap_passes_and_counts_both(monkeypatch):
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_USER", "10")
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_IP", "20")
    r = _mock_redis(1)
    _patched_enforce(monkeypatch, r)
    await enforce_generation_quota(_req(headers={"x-real-ip": "9.9.9.9"}), "user-abc")
    keys = [c.args[0] for c in r.incr.await_args_list]
    assert len(keys) == 2
    assert any(":user:" in k and k.endswith("user-abc") for k in keys)
    assert any(":ip:" in k and k.endswith("9.9.9.9") for k in keys)


async def test_user_cap_hit_raises_429_and_never_touches_ip_bucket(monkeypatch):
    """THE ordering guard. A caller hammering past their own cap must not
    drain the shared per-IP bucket for everyone behind the same NAT — so when
    the user layer rejects, the ip layer's INCR must never run."""
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_USER", "5")
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_IP", "20")
    r = _mock_redis(6)  # user bucket returns over-cap
    _patched_enforce(monkeypatch, r)
    with pytest.raises(HTTPException) as ei:
        await enforce_generation_quota(_req(headers={"x-real-ip": "9.9.9.9"}), "user-abc")
    assert ei.value.status_code == 429
    assert ei.value.detail["scope"] == "user"
    assert ei.value.detail["reason"] == "generation_daily_cap"
    keys = [c.args[0] for c in r.incr.await_args_list]
    assert len(keys) == 1 and ":user:" in keys[0]  # ip bucket untouched


async def test_ip_cap_hit_raises_429_with_ip_scope(monkeypatch):
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_USER", "10")
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_IP", "5")
    r = MagicMock()
    r.incr = AsyncMock(side_effect=[1, 6])  # user fine, ip over
    r.expire = AsyncMock(return_value=True)
    _patched_enforce(monkeypatch, r)
    with pytest.raises(HTTPException) as ei:
        await enforce_generation_quota(_req(headers={"x-real-ip": "9.9.9.9"}), "user-abc")
    assert ei.value.status_code == 429
    assert ei.value.detail["scope"] == "ip"


async def test_redis_outage_raises_honest_503_not_false_429(monkeypatch):
    """Fail-closed must not masquerade as fail-limit: during an outage the
    user has NOT reached any limit, and telling them they have is a confident
    wrong diagnosis. The error sentinel (used == 0) maps to 503."""
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_USER", "10")
    r = MagicMock()
    r.incr = AsyncMock(side_effect=ConnectionError("redis down"))
    _patched_enforce(monkeypatch, r)
    with pytest.raises(HTTPException) as ei:
        await enforce_generation_quota(_req(), "user-abc")
    assert ei.value.status_code == 503
    assert ei.value.detail["reason"] == "generation_quota_unavailable"


async def test_user_layer_disabled_still_enforces_ip_layer(monkeypatch):
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_USER", "0")
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_IP", "5")
    r = _mock_redis(6)
    _patched_enforce(monkeypatch, r)
    with pytest.raises(HTTPException) as ei:
        await enforce_generation_quota(_req(headers={"x-real-ip": "9.9.9.9"}), "user-abc")
    assert ei.value.detail["scope"] == "ip"
    keys = [c.args[0] for c in r.incr.await_args_list]
    assert len(keys) == 1 and ":ip:" in keys[0]  # user layer never ran


async def test_both_layers_disabled_never_touches_redis(monkeypatch):
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_USER", "0")
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_IP", "-1")
    r = _mock_redis(1)
    q = _patched_enforce(monkeypatch, r)
    await enforce_generation_quota(_req(), "user-abc")
    r.incr.assert_not_awaited()
    q._get_redis.assert_not_awaited()


# ─── cap env parsing ─────────────────────────────────────────────────────


def test_invalid_cap_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_USER", "banana")
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_IP", "1e3")
    assert user_daily_cap() == 10
    assert ip_daily_cap() == 20


# ─── client_ip resolution (anti-spoofing) ────────────────────────────────


def test_client_ip_prefers_x_real_ip():
    assert client_ip(_req(headers={"x-real-ip": "203.0.113.7"}, client_host="10.0.0.1")) == "203.0.113.7"


def test_client_ip_rejects_malformed_header_falls_back_to_peer():
    """A malformed header must not become a Redis key — an attacker sending
    garbage per request would otherwise mint a fresh bucket every time."""
    assert client_ip(_req(headers={"x-real-ip": "evil; DROP"}, client_host="10.0.0.1")) == "10.0.0.1"


def test_client_ip_unknown_when_nothing_trustworthy():
    assert client_ip(_req(headers={"x-real-ip": "not-an-ip"})) == "unknown"

"""Tests for the conversion-funnel instrument (issue #787).

Hermetic — mocks at the Redis boundary (the project standard; no live Redis).
Covers: FunnelStore record/read + fail-safe, the ratio math in
``metrics_routes._build_funnel``, the ``record_funnel`` emit helper, the beacon
stage allowlist, and the visitor-id middleware.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from archimedes.api import metrics_routes
from archimedes.api.funnel_middleware import ensure_visitor_id_middleware, record_funnel
from archimedes.api.metrics_routes import _build_funnel
from archimedes.models.telemetry import FunnelEventRequest
from archimedes.services.funnel_store import STAGES, FunnelStore


def _mock_redis_with_pipeline(execute_return):
    """A mock redis whose .pipeline() queues sync commands and awaits execute()."""
    pipe = MagicMock()
    pipe.pfadd = MagicMock(return_value=pipe)
    pipe.pfcount = MagicMock(return_value=pipe)
    pipe.expire = MagicMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=execute_return)
    redis = MagicMock()
    redis.pipeline = MagicMock(return_value=pipe)
    return redis, pipe


# ─── FunnelStore.record ──────────────────────────────────────────────────


async def test_record_pfadds_total_and_day_with_ttl():
    store = FunnelStore()
    redis, pipe = _mock_redis_with_pipeline([1, 1, True])
    store._get_redis = AsyncMock(return_value=redis)

    await store.record("landed", "vid-abc")

    assert pipe.pfadd.call_count == 2  # total + day
    assert pipe.expire.call_count == 1  # TTL on the day bucket only
    total_key = pipe.pfadd.call_args_list[0].args[0]
    day_key = pipe.pfadd.call_args_list[1].args[0]
    assert total_key == "archimedes:funnel:total:landed"
    assert day_key.startswith("archimedes:funnel:day:")
    assert day_key.endswith(":landed")
    pipe.execute.assert_awaited_once()


async def test_record_noops_on_unknown_stage():
    store = FunnelStore()
    redis, _ = _mock_redis_with_pipeline([])
    store._get_redis = AsyncMock(return_value=redis)

    await store.record("not-a-real-stage", "vid")

    redis.pipeline.assert_not_called()


async def test_record_noops_on_empty_visitor():
    store = FunnelStore()
    redis, _ = _mock_redis_with_pipeline([])
    store._get_redis = AsyncMock(return_value=redis)

    await store.record("landed", "")

    redis.pipeline.assert_not_called()


async def test_record_failsafe_on_redis_error():
    store = FunnelStore()
    store._get_redis = AsyncMock(side_effect=ConnectionError("redis down"))

    # Must not raise — telemetry can never break the request it measures.
    await store.record("landed", "vid")


# ─── FunnelStore reads ───────────────────────────────────────────────────


async def test_get_totals_reads_pfcount_in_stage_order():
    store = FunnelStore()
    redis, pipe = _mock_redis_with_pipeline([10, 4, 2, 1])
    store._get_redis = AsyncMock(return_value=redis)

    counts = await store.get_totals()

    assert counts == {
        "landed": 10,
        "wallet_connected": 4,
        "generation_started": 2,
        "vault_deployed": 1,
    }
    assert pipe.pfcount.call_count == len(STAGES)


async def test_get_day_uses_day_keyspace():
    store = FunnelStore()
    redis, pipe = _mock_redis_with_pipeline([3, 1, 0, 0])
    store._get_redis = AsyncMock(return_value=redis)

    counts = await store.get_day("2026-06-28")

    assert counts["landed"] == 3
    first_key = pipe.pfcount.call_args_list[0].args[0]
    assert first_key == "archimedes:funnel:day:2026-06-28:landed"


async def test_get_totals_failsafe_returns_zeros():
    store = FunnelStore()
    store._get_redis = AsyncMock(side_effect=ConnectionError("down"))

    counts = await store.get_totals()

    assert counts == dict.fromkeys(STAGES, 0)


# ─── Ratio math (_build_funnel) ──────────────────────────────────────────


def test_build_funnel_ratios():
    # Post-#851 journey: wallet_connected precedes generation_started.
    counts = {"landed": 100, "wallet_connected": 25, "generation_started": 5, "vault_deployed": 2}
    resp = _build_funnel(counts, "all-time")
    by = {s.stage: s for s in resp.stages}

    assert resp.window == "all-time"
    assert by["landed"].pct_of_landed == 1.0
    assert by["landed"].step_conversion == 1.0
    assert by["wallet_connected"].pct_of_landed == 0.25
    assert by["wallet_connected"].step_conversion == 0.25  # 25 / 100
    assert by["generation_started"].pct_of_landed == 0.05
    assert by["generation_started"].step_conversion == 0.2  # 5 / 25
    assert by["vault_deployed"].pct_of_landed == 0.02
    assert by["vault_deployed"].step_conversion == 0.4  # 2 / 5


def test_build_funnel_zero_landed_no_divzero():
    counts = dict.fromkeys(STAGES, 0)
    resp = _build_funnel(counts, "all-time")

    for s in resp.stages:
        assert s.pct_of_landed == 0.0
    # The top of funnel is defined as 1.0 step-conversion (nothing precedes it).
    assert resp.stages[0].step_conversion == 1.0
    # A zero previous stage yields 0.0, never a ZeroDivisionError.
    assert resp.stages[1].step_conversion == 0.0


# ─── record_funnel emit helper ───────────────────────────────────────────


async def test_record_funnel_uses_request_visitor_id(monkeypatch):
    recorded = {}

    class FakeStore:
        async def record(self, stage, vid):
            recorded["call"] = (stage, vid)

        async def close(self):
            pass

    monkeypatch.setattr("archimedes.services.funnel_store.FunnelStore", FakeStore)
    req = SimpleNamespace(state=SimpleNamespace(visitor_id="vid-xyz"))

    await record_funnel(req, "generation_started")

    assert recorded["call"] == ("generation_started", "vid-xyz")


async def test_record_funnel_noop_without_visitor_id(monkeypatch):
    called = {"n": 0}

    class FakeStore:
        async def record(self, *a):
            called["n"] += 1

        async def close(self):
            pass

    monkeypatch.setattr("archimedes.services.funnel_store.FunnelStore", FakeStore)
    req = SimpleNamespace(state=SimpleNamespace())  # no visitor_id attribute

    await record_funnel(req, "landed")

    assert called["n"] == 0


# ─── Beacon stage allowlist ──────────────────────────────────────────────


async def test_funnel_event_rejects_non_landed_stage(monkeypatch):
    calls = []

    async def fake_record(request, stage):
        calls.append(stage)

    monkeypatch.setattr(metrics_routes, "record_funnel", fake_record)
    req = SimpleNamespace(state=SimpleNamespace(visitor_id="v"))

    out = await metrics_routes.record_funnel_event(FunnelEventRequest(stage="vault_deployed"), req)

    assert out == {"recorded": False}
    assert calls == []  # a client can NEVER record a server-authoritative stage


async def test_funnel_event_accepts_landed(monkeypatch):
    calls = []

    async def fake_record(request, stage):
        calls.append(stage)

    monkeypatch.setattr(metrics_routes, "record_funnel", fake_record)
    req = SimpleNamespace(state=SimpleNamespace(visitor_id="v"))

    out = await metrics_routes.record_funnel_event(FunnelEventRequest(stage="landed"), req)

    assert out == {"recorded": True}
    assert calls == ["landed"]


# ─── Visitor-id middleware ───────────────────────────────────────────────


async def test_visitor_id_middleware_sets_state_and_cookie():
    req = SimpleNamespace(cookies={}, state=SimpleNamespace())
    set_cookies = {}

    class FakeResp:
        def set_cookie(self, **kw):
            set_cookies.update(kw)

    async def call_next(r):
        # The id must be on request.state BEFORE the route runs.
        assert getattr(r.state, "visitor_id", None)
        return FakeResp()

    await ensure_visitor_id_middleware(req, call_next)

    assert set_cookies["key"] == "archimedes_vid"
    assert set_cookies["httponly"] is True
    assert len(set_cookies["value"]) >= 16


async def test_visitor_id_middleware_reuses_existing_cookie():
    req = SimpleNamespace(cookies={"archimedes_vid": "existing-vid"}, state=SimpleNamespace())
    set_cookies = {}

    class FakeResp:
        def set_cookie(self, **kw):
            set_cookies.update(kw)

    async def call_next(r):
        assert r.state.visitor_id == "existing-vid"
        return FakeResp()

    await ensure_visitor_id_middleware(req, call_next)

    assert set_cookies == {}  # no new cookie when one already exists

"""Route-level contract: POST /api/strategies/generate shares the SAME daily
generation quota as POST /api/generate/start (WP-6 / cluster-4's "unmetered
budget hole" — docs/sprint/cluster-4-strategies-route.md § 4).

Before this wiring, ``/api/strategies/generate`` (the fusion path, ``_run_fusion_job``)
was rate-limited only by slowapi's 20/minute burst limit — it never called
``services/generation_quota.enforce_generation_quota``, so an account could get an
effectively unlimited number of LLM-spending generations per day by calling this
endpoint instead of the metered ``/api/generate/start`` (5/minute + the 10/day/account
+ 20/day/IP caps). See ``backend/archimedes/api/strategies_routes.py`` (pre-fix,
``generate_strategy`` had no quota call at all) vs.
``backend/archimedes/api/generate_routes.py::start_generation`` (already called
``enforce_generation_quota`` before this change).

Hermetic — same harness style as test_generate_payment_gate.py: TestClient(app),
signed legacy-SIWE cookies (mapped to a canonical test user id by conftest's
``_legacy_siwe_test_adapter``), every boundary mocked (Redis client behind
``GenerationQuota``, the job store, the fire-and-forget background task, the
optional Redis-backed market-regime read). No live Redis, DB, or LLM.

Covers:
  * quota enforced on /api/strategies/generate — 429 with the identical
    reason/scope/cap/message shape /api/generate/start produces (same function,
    same HTTPException factory);
  * the two endpoints' daily counters are the SAME Redis bucket — exhausting the
    cap via one endpoint blocks the very next call on the OTHER endpoint;
  * quota disabled (caps <= 0) preserves prior behavior: Redis is never even
    touched, and the request reaches the fusion enqueue exactly as before this
    change.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from tests.auth_helpers import auth_cookies

_GENERATE_START_BODY = {"brief": {"intent": "low-vol treasury alternative", "risk_appetite": "moderate"}}


def _client() -> TestClient:
    from archimedes.main import app

    return TestClient(app)


def _close_background_coroutine(coro):
    """Stand-in for asyncio.create_task: closes the coroutine instead of
    scheduling it, so the fire-and-forget background job never actually runs
    inside the test process (mirrors test_generate_payment_gate.py)."""
    coro.close()
    return MagicMock()


def _over_cap_redis() -> MagicMock:
    """A Redis stub whose INCR always returns a value far past any cap used
    here — simulates "this identity already exhausted today's allowance"."""
    r = MagicMock()
    r.incr = AsyncMock(return_value=999)
    r.expire = AsyncMock(return_value=True)
    return r


class _FakeCountingRedis:
    """A minimal in-memory INCR/EXPIRE fake that actually persists counts per
    key across calls — unlike a MagicMock returning a fixed value, this is
    what makes the shared-counter test a real proof: if the two routes hit
    different Redis keys, this fake would show two independent counts of 1
    each instead of one shared count of 2."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    async def expire(self, key: str, ttl: int, nx: bool = False) -> bool:
        return True


def _mock_job_store(job_id: str = "job-strat-1") -> MagicMock:
    store = MagicMock()
    store.enqueue = AsyncMock(return_value=job_id)
    store.close = AsyncMock()
    return store


def _mock_generate_store(job_id: str = "job-gen-1") -> MagicMock:
    store = MagicMock()
    store.enqueue = AsyncMock(return_value=job_id)
    return store


def _mock_agent_state_store() -> MagicMock:
    """Boundary mock for the optional market-regime read in generate_strategy —
    avoids any real Redis connection attempt for the ``AgentStateStore()`` path,
    which is independent of the quota's own Redis client."""
    state = MagicMock()
    state.load_regime = AsyncMock(return_value=None)
    state.load_ensemble_consensus = AsyncMock(return_value=None)
    state.close = AsyncMock()
    return state


def _strategies_generate_harness(job_store: MagicMock, state_store: MagicMock):
    """Patches needed to let POST /api/strategies/generate reach its enqueue
    step: fusion enabled, a real-enough corpus (len >= 2), the job store, the
    fire-and-forget task, and the market-context Redis read."""
    return (
        patch("archimedes.agents.strategy_fusion.fusion_enabled", return_value=True),
        patch("archimedes.agents.strategy_fusion.load_corpus", return_value=[object(), object()]),
        patch("archimedes.services.job_queue.JobStore", return_value=job_store),
        patch("archimedes.api.strategies_routes.asyncio.create_task", side_effect=_close_background_coroutine),
        patch("archimedes.services.redis_state.AgentStateStore", return_value=state_store),
    )


# ── 429 shape: identical to the primary path ──────────────────────────────


def test_quota_enforced_on_strategies_generate_returns_429_before_fusion_work(monkeypatch) -> None:
    """A caller already over their daily cap gets the SAME 429 shape
    /api/generate/start would give them, and fusion work never starts —
    the quota check runs before ANY other work, exactly like the primary
    path's "cheapest anti-abuse check first" ordering."""
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_USER", "1")
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_IP", "0")

    fusion_spy = MagicMock(side_effect=AssertionError("fusion_enabled must not run after a quota block"))
    with (
        patch(
            "archimedes.services.generation_quota.GenerationQuota._get_redis", AsyncMock(return_value=_over_cap_redis())
        ),
        patch("archimedes.agents.strategy_fusion.fusion_enabled", fusion_spy),
    ):
        resp = _client().post("/api/strategies/generate", cookies=auth_cookies())

    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["reason"] == "generation_daily_cap"
    assert detail["scope"] == "user"
    assert detail["cap"] == 1
    fusion_spy.assert_not_called()


def test_429_body_is_byte_identical_between_the_two_endpoints(monkeypatch) -> None:
    """Same over-cap identity hitting both endpoints gets the exact same
    ``detail`` payload — proof the two routes call the identical
    ``enforce_generation_quota`` / ``_quota_429`` machinery, not parallel
    reimplementations that could drift."""
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_USER", "1")
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_IP", "0")

    with patch(
        "archimedes.services.generation_quota.GenerationQuota._get_redis", AsyncMock(return_value=_over_cap_redis())
    ):
        cookies = auth_cookies()
        resp_primary = _client().post("/api/generate/start", json=_GENERATE_START_BODY, cookies=cookies)
        resp_secondary = _client().post("/api/strategies/generate", cookies=cookies)

    assert resp_primary.status_code == 429
    assert resp_secondary.status_code == 429
    assert resp_primary.json()["detail"] == resp_secondary.json()["detail"]


# ── shared counters: exhausting one endpoint blocks the other ─────────────


def test_quota_counters_are_shared_across_both_endpoints(monkeypatch) -> None:
    """A user must not get 2x the daily quota by alternating endpoints.

    cap=1 for the user bucket (IP layer disabled to isolate it). The FIRST
    call, on the PRIMARY endpoint, is allowed (count 1 <= cap 1) and reaches
    enqueue. The SECOND call, on the DIFFERENT (strategies) endpoint, must be
    refused — which is only possible if both routes incremented the SAME
    Redis key. The in-memory ``_FakeCountingRedis`` really persists per-key
    counts (unlike a MagicMock returning a constant), so this is a genuine
    proof of shared state, not a coincidence of two independent mocks.
    """
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_USER", "1")
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_IP", "0")

    fake_redis = _FakeCountingRedis()
    generate_store = _mock_generate_store()
    strategies_store = _mock_job_store()
    state_store = _mock_agent_state_store()

    p_strat = _strategies_generate_harness(strategies_store, state_store)

    with (
        patch("archimedes.services.generation_quota.GenerationQuota._get_redis", AsyncMock(return_value=fake_redis)),
        patch("archimedes.api.generate_routes.get_job_store", return_value=generate_store),
        patch("archimedes.api.generate_routes.asyncio.create_task", side_effect=_close_background_coroutine),
        p_strat[0],
        p_strat[1],
        p_strat[2],
        p_strat[3],
        p_strat[4],
    ):
        cookies = auth_cookies()

        resp1 = _client().post("/api/generate/start", json=_GENERATE_START_BODY, cookies=cookies)
        assert resp1.status_code == 202, resp1.text
        generate_store.enqueue.assert_awaited_once()

        resp2 = _client().post("/api/strategies/generate", cookies=cookies)

    assert resp2.status_code == 429, resp2.text
    detail = resp2.json()["detail"]
    assert detail["reason"] == "generation_daily_cap"
    assert detail["scope"] == "user"
    strategies_store.enqueue.assert_not_called()

    # Exactly one Redis key was ever incremented — the user bucket, hit twice.
    assert len(fake_redis._counts) == 1
    ((_key, count),) = fake_redis._counts.items()
    assert count == 2


def test_quota_counters_shared_in_the_other_call_order_too(monkeypatch) -> None:
    """Symmetry check: exhausting the cap via /api/strategies/generate FIRST
    then blocks /api/generate/start — the sharing isn't an artifact of which
    endpoint happens to run first."""
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_USER", "1")
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_IP", "0")

    fake_redis = _FakeCountingRedis()
    generate_store = _mock_generate_store()
    strategies_store = _mock_job_store()
    state_store = _mock_agent_state_store()

    p_strat = _strategies_generate_harness(strategies_store, state_store)

    with (
        patch("archimedes.services.generation_quota.GenerationQuota._get_redis", AsyncMock(return_value=fake_redis)),
        patch("archimedes.api.generate_routes.get_job_store", return_value=generate_store),
        patch("archimedes.api.generate_routes.asyncio.create_task", side_effect=_close_background_coroutine),
        p_strat[0],
        p_strat[1],
        p_strat[2],
        p_strat[3],
        p_strat[4],
    ):
        cookies = auth_cookies()

        resp1 = _client().post("/api/strategies/generate", cookies=cookies)
        assert resp1.status_code == 202, resp1.text
        strategies_store.enqueue.assert_awaited_once()

        resp2 = _client().post("/api/generate/start", json=_GENERATE_START_BODY, cookies=cookies)

    assert resp2.status_code == 429, resp2.text
    generate_store.enqueue.assert_not_called()
    assert len(fake_redis._counts) == 1
    ((_key, count),) = fake_redis._counts.items()
    assert count == 2


# ── quota disabled: prior behavior preserved ───────────────────────────────


def test_quota_disabled_preserves_prior_strategies_generate_behavior(monkeypatch) -> None:
    """Both caps <= 0 → the quota layer returns immediately without ever
    constructing a Redis client, and /api/strategies/generate reaches enqueue
    exactly as it did before this change (202 + job_id)."""
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_USER", "0")
    monkeypatch.setenv("GENERATION_DAILY_CAP_PER_IP", "0")

    redis_spy = AsyncMock(side_effect=AssertionError("Redis must not be touched when both caps are disabled"))
    job_store = _mock_job_store(job_id="job-disabled-1")
    state_store = _mock_agent_state_store()
    p_strat = _strategies_generate_harness(job_store, state_store)

    with (
        patch("archimedes.services.generation_quota.GenerationQuota._get_redis", redis_spy),
        p_strat[0],
        p_strat[1],
        p_strat[2],
        p_strat[3],
        p_strat[4],
    ):
        resp = _client().post("/api/strategies/generate", cookies=auth_cookies())

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "queued", "job_id": "job-disabled-1"}
    job_store.enqueue.assert_awaited_once()
    redis_spy.assert_not_called()

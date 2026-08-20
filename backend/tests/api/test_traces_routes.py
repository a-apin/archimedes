"""Redis-degradation coverage for /api/traces/* (#1107 review follow-up).

The audit PR added graceful-degradation branches to the trace routes; none had
tests. These pin the two load-bearing behaviors:

- READ path (list_traces): Redis down → 200 with an empty listing, never a 500
  (the PR's intent — the on-chain anchors still exist, the page still renders).
- WRITE path (publish_trace): a failed off-chain persist must NOT return a
  200/is_anchored=True success — the anchor would be unverifiable against an
  off-chain body that was never stored. It must fail loudly (503) so the
  internal-agent caller retries.

Hermetic: AgentStateStore and the chain trace_publisher are mocked at the
boundary; the internal-agent key guard is dependency-overridden.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _publish_body() -> dict:
    return {
        "vault_address": "0xVault",
        "decision_type": "rebalance",
        "trigger": "drift",
        "market_context": {},
        "portfolio_before": {},
        "portfolio_after": {},
        "reasoning": "test reasoning",
        "confidence": 0.9,
        "trades_executed": [],
        "strategies_referenced": [],
    }


async def test_list_traces_degrades_to_empty_when_redis_down():
    from archimedes.main import app
    from archimedes.services.redis_state import AgentStateStore

    with (
        patch.object(AgentStateStore, "list_traces", AsyncMock(side_effect=ConnectionError("redis down"))),
        patch.object(AgentStateStore, "close", AsyncMock()),
        patch("archimedes.chain.trace_publisher.trace_publisher") as mock_pub,
    ):
        # The route's on-chain fallback calls get_total_trace_count() +
        # get_trace_by_id() — mock THOSE (review follow-up: an earlier version
        # mocked a method the route never calls, so the fallback's swallowed
        # exception made the test pass vacuously).
        mock_pub.get_total_trace_count = AsyncMock(return_value=0)
        mock_pub.get_trace_by_id = AsyncMock(return_value=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/traces/")

    assert resp.status_code == 200
    body = resp.json()
    assert body["traces"] == []


async def test_list_traces_onchain_fallback_reports_real_total_and_unknown_type():
    """Redis down, on-chain fallback with real traces (#1356).

    Pins two invented-fact fixes on the same fallback path:
      - `total` must be the registry size read from `get_total_trace_count()`
        (5 here), not `len(traces)` (the current page, capped at `limit`) —
        the old code discarded the real count it had just read. `limit=2` is
        deliberately smaller than the registry so the two values provably
        differ: the pre-fix code returns `total=2` here, not `total=5`.
      - `decision_type` must be "unknown" for every on-chain-only trace, never
        the invented literal "rebalance" — the anchor doesn't record which
        decision type produced it.
    """
    from archimedes.main import app
    from archimedes.services.redis_state import AgentStateStore

    def _detail(trace_id: int) -> dict:
        return {"vault": "0xVault", "timestamp": 1_700_000_000, "trace_hash": f"0xhash{trace_id}"}

    with (
        patch.object(AgentStateStore, "list_traces", AsyncMock(side_effect=ConnectionError("redis down"))),
        patch.object(AgentStateStore, "close", AsyncMock()),
        patch("archimedes.chain.trace_publisher.trace_publisher") as mock_pub,
    ):
        mock_pub.get_total_trace_count = AsyncMock(return_value=5)
        mock_pub.get_trace_by_id = AsyncMock(side_effect=lambda tid: _detail(tid))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/traces/", params={"limit": 2, "offset": 0})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["traces"]) == 2  # the page is still capped at `limit`
    assert body["total"] == 5  # but `total` reports the real registry size, not the page size
    assert all(t["decision_type"] == "unknown" for t in body["traces"])


async def test_publish_trace_returns_503_when_offchain_persist_fails():
    """WRITE path: never a silent 200 for a trace whose body was not stored."""
    from archimedes.api.auth_guard import require_internal_agent_key
    from archimedes.main import app
    from archimedes.services.redis_state import AgentStateStore

    prev = app.dependency_overrides.get(require_internal_agent_key)
    app.dependency_overrides[require_internal_agent_key] = lambda: None
    try:
        with (
            patch.object(AgentStateStore, "save_trace", AsyncMock(side_effect=ConnectionError("redis down"))),
            patch.object(AgentStateStore, "close", AsyncMock()),
            patch("archimedes.chain.trace_publisher.trace_publisher") as mock_pub,
        ):
            # On-chain half succeeds — the failure mode under test is precisely
            # "anchored on-chain, but the off-chain body never persisted".
            mock_pub.publish = AsyncMock(return_value="0xAnchorTx")
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post("/api/traces/publish", json=_publish_body())
    finally:
        if prev is None:
            app.dependency_overrides.pop(require_internal_agent_key, None)
        else:
            app.dependency_overrides[require_internal_agent_key] = prev

    assert resp.status_code == 503
    assert "off-chain persistence failed" in resp.json()["detail"]

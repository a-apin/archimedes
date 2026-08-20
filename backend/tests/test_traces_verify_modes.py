"""GET /api/traces/{id}/verify tri-state coverage (#1359).

Two claim-integrity defects fixed here:

1. The endpoint compared two already-stored strings and never distinguished
   "compared and matched" from "nothing was compared" — both painted the same
   green `is_verified=True`. `TraceVerifyResponse.verification_mode` now
   names which of the three actually happened: `hash_matched` (off-chain
   `trace_hash` re-fetched from the on-chain receipt and compared byte-for-
   byte), `anchored_only` (reachable store, no off-chain record — zero
   hashes compared), or `failed` (mismatch / missing receipt / never
   anchored).

2. A Redis outage fell through into the SAME "no off-chain data" branch as a
   reachable-but-empty store, so an outage silently upgraded every trace to
   `is_verified=True` with nothing compared. `verify_trace` now raises 503 on
   a store exception — mirroring `get_trace_canonical` (traces_routes.py
   :387-394) — instead of falling through. `anchored_only` is now reachable
   ONLY when the store answered (with nothing found), never when it failed.

Hermetic: `AgentStateStore` and the chain `trace_publisher` are both mocked
at the boundary — same pattern as
`backend/tests/api/test_traces_routes.py` (list/publish coverage for the
same router) and `backend/tests/test_api_routes.py::test_agent_status_redis_down_defaults`
(Redis-down via `patch.object(..., AsyncMock(side_effect=ConnectionError))`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _off_chain_trace(**overrides) -> dict:
    base = {
        "id": "trace-1",
        "vault_address": "0xVault",
        "trace_hash": "0xaabbccdd",
        "arc_tx_hash": "0xTxHash",
        "commit_block_number": 100,
        "trade_block_number": 101,
        "reveal_block_number": 102,
        "temporal_binding_valid": True,
    }
    base.update(overrides)
    return base


async def test_hash_match_reports_hash_matched_and_is_verified_true():
    from archimedes.main import app
    from archimedes.services.redis_state import AgentStateStore

    with (
        patch.object(AgentStateStore, "get_trace", AsyncMock(return_value=_off_chain_trace())),
        patch.object(AgentStateStore, "close", AsyncMock()),
        patch("archimedes.chain.trace_publisher.trace_publisher") as mock_pub,
    ):
        mock_pub.get_trace_by_tx_hash = AsyncMock(
            return_value={
                "agent": "0xAgent",
                "vault": "0xVault",
                "trace_hash": "0xaabbccdd",  # same bytes, off-chain and on-chain both compared
                "timestamp": 12345,
            }
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/traces/1/verify")

    assert resp.status_code == 200
    body = resp.json()
    assert body["verification_mode"] == "hash_matched"
    assert body["is_verified"] is True
    assert body["details"] == "Hash verified on-chain ✓"


async def test_hash_mismatch_reports_failed_and_is_verified_false():
    from archimedes.main import app
    from archimedes.services.redis_state import AgentStateStore

    with (
        patch.object(AgentStateStore, "get_trace", AsyncMock(return_value=_off_chain_trace())),
        patch.object(AgentStateStore, "close", AsyncMock()),
        patch("archimedes.chain.trace_publisher.trace_publisher") as mock_pub,
    ):
        mock_pub.get_trace_by_tx_hash = AsyncMock(
            return_value={
                "agent": "0xAgent",
                "vault": "0xVault",
                "trace_hash": "0xdeadbeef",  # deliberately different from the stored 0xaabbccdd
                "timestamp": 12345,
            }
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/traces/1/verify")

    assert resp.status_code == 200
    body = resp.json()
    assert body["verification_mode"] == "failed"
    assert body["is_verified"] is False
    assert "mismatch" in body["details"].lower()


async def test_redis_down_returns_503_never_a_fabricated_pass():
    """A store outage must be a loud 503, not the anchored_only fallthrough.

    Before the fix, an AgentStateStore.get_trace exception was swallowed and
    fell into the SAME branch as a reachable-but-empty store, returning
    is_verified=True with zero hashes compared. That is the exact defect
    this test pins: neither `"is_verified": true` nor `anchored_only` may
    appear anywhere in the response body when the store is down.

    `trace_publisher` is mocked here too (same shape as
    test_reachable_empty_store_with_onchain_trace_reports_anchored_only)
    even though the fixed code never reaches it — get_trace raises before
    verify_trace falls into the `if not off_chain:` branch that calls
    trace_publisher.get_trace_by_id. Without this mock, reverting the fix
    (dropping the except-and-503 handling so the Redis exception falls
    through to that branch) hits an unrelated hermetic-env ValueError
    (`Contract address not configured for ReasoningTraceRegistry` from
    trace_publisher.loader.trace_registry, which sits outside that method's
    own try/except) before any assertion below runs — masking the mutation
    instead of rejecting it. With the mock in place, the mutated code
    reaches a real 200 response with verification_mode=anchored_only and
    is_verified=True, and these assertions correctly reject it.
    """
    from archimedes.main import app
    from archimedes.services.redis_state import AgentStateStore

    with (
        patch.object(AgentStateStore, "get_trace", AsyncMock(side_effect=ConnectionError("redis down"))),
        patch.object(AgentStateStore, "close", AsyncMock()),
        patch("archimedes.chain.trace_publisher.trace_publisher") as mock_pub,
    ):
        mock_pub.get_trace_by_id = AsyncMock(
            return_value={
                "agent": "0xAgent",
                "vault": "0xVault",
                "trace_hash": "0xaabbccdd",
                "timestamp": 12345,
                "metadata": None,
            }
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/traces/1/verify")

    assert resp.status_code == 503
    raw = resp.text
    assert '"is_verified": true' not in raw
    assert '"is_verified":true' not in raw
    assert "anchored_only" not in raw


async def test_reachable_empty_store_with_onchain_trace_reports_anchored_only():
    """Store reachable, no key for this id, but the trace exists on-chain.

    This is the ONLY legitimate path to anchored_only: the store answered
    (found nothing), it did not fail.
    """
    from archimedes.main import app
    from archimedes.services.redis_state import AgentStateStore

    with (
        patch.object(AgentStateStore, "get_trace", AsyncMock(return_value=None)),
        patch.object(AgentStateStore, "close", AsyncMock()),
        patch("archimedes.chain.trace_publisher.trace_publisher") as mock_pub,
    ):
        mock_pub.get_trace_by_id = AsyncMock(
            return_value={
                "agent": "0xAgent",
                "vault": "0xVault",
                "trace_hash": "0xaabbccdd",
                "timestamp": 12345,
                "metadata": None,
            }
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/traces/42/verify")

    assert resp.status_code == 200
    body = resp.json()
    assert body["verification_mode"] == "anchored_only"
    assert body["is_verified"] is True
    assert body["trace_id"] == 42


async def test_reachable_empty_store_with_no_onchain_trace_is_404():
    """Store reachable + empty, AND nothing on-chain either -> 404, not anchored_only."""
    from archimedes.main import app
    from archimedes.services.redis_state import AgentStateStore

    with (
        patch.object(AgentStateStore, "get_trace", AsyncMock(return_value=None)),
        patch.object(AgentStateStore, "close", AsyncMock()),
        patch("archimedes.chain.trace_publisher.trace_publisher") as mock_pub,
    ):
        mock_pub.get_trace_by_id = AsyncMock(return_value=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/traces/999/verify")

    assert resp.status_code == 404

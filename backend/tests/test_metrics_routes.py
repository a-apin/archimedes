"""HTTP-layer tests for /api/metrics + the identity-ledger metrics views (issue #428, #1028).

Mocks the Redis boundary (``TelemetryStore.get_counts_or_none``) so the test is
hermetic — no live Redis. Asserts the response shape, the derived total, and
(issue #1028) the D8 Postgres-snapshot reconciliation + the new
identity-ledger-backed endpoints.

The ``request_count_snapshots`` singleton row is shared process-wide (same
"one bound engine per pytest run" caveat as every other DB-backed test in this
suite — see ``test_api_routes.py``'s ``_use_tmp_db`` docstring), so every test
that cares about its exact value resets it first via ``_reset_request_snapshot``
rather than assuming a fresh row.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


def _reset_request_snapshot() -> None:
    """Delete the singleton request_count_snapshots row so a test starts from zero.

    Direct-DB reset (not a DATABASE_URL monkeypatch) because the engine this
    process uses was bound at first import — see the module docstring.
    """
    from archimedes.db import get_session
    from archimedes.models.request_snapshot import SNAPSHOT_ROW_ID, RequestCountSnapshot

    session = get_session()
    try:
        session.query(RequestCountSnapshot).filter(RequestCountSnapshot.id == SNAPSHOT_ROW_ID).delete()
        session.commit()
    finally:
        session.close()


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_counts_and_total():
    from archimedes.main import app

    _reset_request_snapshot()
    with patch(
        "archimedes.services.telemetry_store.TelemetryStore.get_counts_or_none",
        new=AsyncMock(return_value=(7, 3)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/metrics")

    assert resp.status_code == 200
    data = resp.json()
    assert data["human_count"] == 7
    assert data["agent_count"] == 3
    assert data["total_requests"] == 10
    # D8: a fresh snapshot (no prior resets) reports epoch metadata, not None.
    assert data["epoch_started_at"] is not None
    assert data["epoch_resets"] == 0
    assert isinstance(data["timestamp"], str) and data["timestamp"]


@pytest.mark.asyncio
async def test_metrics_endpoint_shape_keys_present():
    from archimedes.main import app

    _reset_request_snapshot()
    with patch(
        "archimedes.services.telemetry_store.TelemetryStore.get_counts_or_none",
        new=AsyncMock(return_value=(0, 0)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/metrics")

    assert resp.status_code == 200
    data = resp.json()
    for key in ("human_count", "agent_count", "total_requests", "real_users", "timestamp"):
        assert key in data, f"missing {key} in {list(data.keys())}"
    # Zero state is well-formed, not an error.
    assert data["total_requests"] == 0


@pytest.mark.asyncio
async def test_metrics_endpoint_falls_back_to_snapshot_when_redis_down():
    """When Redis is unreachable, the durable Postgres snapshot is served, not a false zero (D8/AC6).

    On a freshly-reset snapshot (no prior successful read), that floor is
    itself zero — so this also exercises the "genuinely nothing recorded yet"
    path and confirms it degrades to 0, not an error.
    """
    from archimedes.main import app
    from archimedes.services.telemetry_store import TelemetryStore

    _reset_request_snapshot()
    # Patch the underlying client accessor to fail; get_counts_or_none swallows -> None.
    with patch.object(
        TelemetryStore,
        "_get_redis",
        new=AsyncMock(side_effect=ConnectionError("redis down")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/metrics")

    assert resp.status_code == 200
    data = resp.json()
    assert data["human_count"] == 0
    assert data["agent_count"] == 0
    assert data["total_requests"] == 0


@pytest.mark.asyncio
async def test_metrics_survives_a_redis_reset_without_regressing():
    """D8/AC6: a Redis restart (counters drop back to a small number) must not zero the lifetime total.

    First read establishes a baseline (100 human / 20 agent). Second read
    simulates Redis having been restarted (2 human / 1 agent — lower than
    before) — the reconciled total must be the OLD values plus the NEW ones,
    never just the new (post-reset) reading alone.
    """
    from archimedes.main import app

    _reset_request_snapshot()
    with patch(
        "archimedes.services.telemetry_store.TelemetryStore.get_counts_or_none",
        new=AsyncMock(return_value=(100, 20)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.get("/api/metrics")
    assert first.json()["human_count"] == 100
    assert first.json()["agent_count"] == 20
    assert first.json()["epoch_resets"] == 0

    with patch(
        "archimedes.services.telemetry_store.TelemetryStore.get_counts_or_none",
        new=AsyncMock(return_value=(2, 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            second = await client.get("/api/metrics")

    data = second.json()
    # Never regress: 100 + 2, not just 2.
    assert data["human_count"] == 102
    assert data["agent_count"] == 21
    assert data["epoch_resets"] == 1


@pytest.mark.asyncio
async def test_get_wallets_endpoint_shape_and_boundary():
    """GET /api/metrics/wallets — issue #1028 AC1 enumeration endpoint.

    Mocks the identity-ledger boundary (consistent with how this file already
    mocks the Redis/user-count boundaries) so the assertion is deterministic
    regardless of what other tests have written into the shared DB.
    """
    from archimedes.main import app

    fake_wallets = [
        {
            "wallet_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "actor_class": "human",
            "first_seen_at": "2026-07-01T00:00:00+00:00",
            "last_auth_at": "2026-07-02T00:00:00+00:00",
        }
    ]
    with (
        patch("archimedes.api.metrics_routes.count_human_wallets", return_value=1),
        patch("archimedes.api.metrics_routes.list_human_wallets", return_value=fake_wallets),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/metrics/wallets")

    assert resp.status_code == 200
    data = resp.json()
    assert data["real_users"] == 1
    assert len(data["wallets"]) == 1
    assert data["wallets"][0]["wallet_address"] == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


@pytest.mark.asyncio
async def test_get_wallet_connections_endpoint():
    """GET /api/metrics/wallets/connections — issue #1028 AC2: which wallets connected, and when."""
    from archimedes.main import app

    fake_connections = [
        {"wallet": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "connected_at": "2026-07-01T00:00:00+00:00"},
    ]
    with patch("archimedes.api.metrics_routes.list_wallet_connections", return_value=fake_connections):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/metrics/wallets/connections")

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["connections"][0]["wallet"] == "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.mark.asyncio
async def test_get_funnel_defaults_to_visitor_source():
    """GET /api/metrics/funnel with no params stays the pre-#1028 Redis/HLL funnel (no behavior change)."""
    from archimedes.main import app

    with patch(
        "archimedes.api.metrics_routes.FunnelStore.get_totals",
        new=AsyncMock(
            return_value={
                "landed": 10,
                "wallet_connected": 4,
                "generation_started": 2,
                "vault_deployed": 1,
            }
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/metrics/funnel")

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "visitor"
    assert [s["stage"] for s in data["stages"]] == [
        "landed",
        "wallet_connected",
        "generation_started",
        "vault_deployed",
    ]


@pytest.mark.asyncio
async def test_get_funnel_identity_source_recomputes_from_ledger():
    """GET /api/metrics/funnel?source=identity — issue #1028 AC3.

    No Redis/HLL involved; stages are relabeled onto the pre-existing
    visitor-funnel vocabulary (wallet_connected/generation_started/
    vault_deployed) so the response shape — and the frontend's existing
    label map — stays reusable.
    """
    from archimedes.main import app

    with patch(
        "archimedes.api.metrics_routes.get_identity_funnel",
        return_value={"auth_verified": 6, "generation_started": 3, "vault_created": 1},
    ) as mocked:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/metrics/funnel", params={"source": "identity"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "identity"
    stage_names = [s["stage"] for s in data["stages"]]
    assert stage_names == ["wallet_connected", "generation_started", "vault_deployed"]
    counts = {s["stage"]: s["distinct_visitors"] for s in data["stages"]}
    assert counts == {"wallet_connected": 6, "generation_started": 3, "vault_deployed": 1}
    # human_only defaults True (AC3's dogfood/agent exclusion).
    mocked.assert_called_once_with(exclude_dogfood=True)


@pytest.mark.asyncio
async def test_get_funnel_identity_source_human_only_false_disables_exclusion():
    from archimedes.main import app

    with patch(
        "archimedes.api.metrics_routes.get_identity_funnel",
        return_value=dict.fromkeys(("auth_verified", "generation_started", "vault_created"), 0),
    ) as mocked:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/metrics/funnel", params={"source": "identity", "human_only": "false"})

    assert resp.status_code == 200
    mocked.assert_called_once_with(exclude_dogfood=False)

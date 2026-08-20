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
async def test_get_wallets_unauthenticated_is_401_never_a_roster():
    """#1366 guard demo: the wallet roster must NOT be publicly enumerable.

    This endpoint served the complete per-wallet user roster (addresses +
    first/last-seen) to anonymous callers in production. The guard is shown
    rejecting the exact request that leaked: unauthenticated GET → 401, and the
    body must not contain a wallets list.
    """
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/metrics/wallets")

    assert resp.status_code == 401
    assert "wallets" not in resp.json()


@pytest.mark.asyncio
async def test_get_wallet_connections_unauthenticated_is_401():
    """#1366 guard demo: the per-wallet connection ledger is identity data."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/metrics/wallets/connections")

    assert resp.status_code == 401
    assert "connections" not in resp.json()


@pytest.mark.asyncio
async def test_get_wallets_verified_non_admin_is_403(monkeypatch):
    """A verified linked wallet that is not a platform admin gets 403 — any
    authenticated user being able to enumerate every other user would still be
    the leak, just behind a signup."""
    from archimedes.api.wallet_routes import require_linked_wallet
    from archimedes.main import app

    monkeypatch.setenv("PLATFORM_ADMIN_WALLETS", "0xadmin000000000000000000000000000000000001")
    app.dependency_overrides[require_linked_wallet] = lambda: "0xnotadmin0000000000000000000000000000000002"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/metrics/wallets")
    finally:
        app.dependency_overrides.pop(require_linked_wallet, None)

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_wallets_admin_shape_and_boundary(monkeypatch):
    """The admin path keeps issue #1028 AC1's response shape unchanged.

    Mocks the identity-ledger boundary (consistent with how this file already
    mocks the Redis/user-count boundaries) so the assertion is deterministic
    regardless of what other tests have written into the shared DB.
    """
    from archimedes.api.wallet_routes import require_linked_wallet
    from archimedes.main import app

    admin = "0xadmin000000000000000000000000000000000001"
    monkeypatch.setenv("PLATFORM_ADMIN_WALLETS", admin)
    app.dependency_overrides[require_linked_wallet] = lambda: admin

    fake_wallets = [
        {
            "wallet_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "actor_class": "human",
            "first_seen_at": "2026-07-01T00:00:00+00:00",
            "last_auth_at": "2026-07-02T00:00:00+00:00",
        }
    ]
    try:
        with (
            patch("archimedes.api.metrics_routes.count_human_wallets", return_value=1),
            patch("archimedes.api.metrics_routes.list_human_wallets", return_value=fake_wallets),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/metrics/wallets")
    finally:
        app.dependency_overrides.pop(require_linked_wallet, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["real_users"] == 1
    assert len(data["wallets"]) == 1
    assert data["wallets"][0]["wallet_address"] == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


@pytest.mark.asyncio
async def test_get_wallet_connections_admin_shape(monkeypatch):
    """Admin path keeps issue #1028 AC2's response shape unchanged."""
    from archimedes.api.wallet_routes import require_linked_wallet
    from archimedes.main import app

    admin = "0xadmin000000000000000000000000000000000001"
    monkeypatch.setenv("PLATFORM_ADMIN_WALLETS", admin)
    app.dependency_overrides[require_linked_wallet] = lambda: admin

    fake_connections = [
        {"wallet": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "connected_at": "2026-07-01T00:00:00+00:00"},
    ]
    try:
        with patch("archimedes.api.metrics_routes.list_wallet_connections", return_value=fake_connections):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/metrics/wallets/connections")
    finally:
        app.dependency_overrides.pop(require_linked_wallet, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["connections"][0]["wallet"] == "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.mark.asyncio
async def test_get_funnel_defaults_to_visitor_source():
    """GET /api/metrics/funnel with no params stays the pre-#1028 Redis/HLL funnel (no behavior change)."""
    from archimedes.main import app

    with (
        patch(
            "archimedes.api.metrics_routes.FunnelStore.get_totals",
            new=AsyncMock(
                return_value={
                    "landed": 10,
                    "wallet_connected": 4,
                    "generation_started": 2,
                    "vault_deployed": 1,
                }
            ),
        ),
        patch(
            "archimedes.api.metrics_routes.FunnelStore.get_totals_by_agent_type",
            new=AsyncMock(
                return_value={
                    "landed": {"internal": 1, "external": 2, "human": 7},
                    "wallet_connected": {"internal": 0, "external": 0, "human": 4},
                    "generation_started": {"internal": 0, "external": 0, "human": 2},
                    "vault_deployed": {"internal": 0, "external": 0, "human": 1},
                }
            ),
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
    # #788: the per-stage agent_type breakdown rides alongside the unchanged aggregate.
    by_stage = {s["stage"]: s["by_agent_type"] for s in data["stages"]}
    assert by_stage["landed"] == {"internal": 1, "external": 2, "human": 7}
    landed = next(s for s in data["stages"] if s["stage"] == "landed")
    assert landed["distinct_visitors"] == 10  # unchanged by the additive breakdown


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

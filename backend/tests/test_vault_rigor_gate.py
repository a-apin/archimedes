"""Hermetic tests for the server-side rigor deploy gate (#818).

The guarantee "only rigor-passing strategies are deployed" must hold server-side,
where a direct (non-UI) API call cannot route around it. These pin: the resolver
reads the authoritative verdict across curated + generated sources, fails closed on
a DB error, and `create_vault` returns 422 (without spending gas) for a strategy
that failed the gate or was never validated.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch

import pytest
from archimedes.api.vaults_routes import _assert_strategies_pass_rigor, _strategy_rigor_status
from fastapi import HTTPException

V = "archimedes.api.vaults_routes"


@contextlib.contextmanager
def _override_verified_wallet(app, wallet: str = "0x000000000000000000000000000000000000dEaD"):
    """Override the SIWE wallet dependency, then RESTORE the prior override on exit.

    Restoring (rather than a global ``app.dependency_overrides.clear()``) keeps this
    test from wiping overrides another test/fixture set on the shared ``app`` instance.
    """
    from archimedes.api.auth_siwe import require_verified_wallet

    prev = app.dependency_overrides.get(require_verified_wallet)
    app.dependency_overrides[require_verified_wallet] = lambda: wallet
    try:
        yield
    finally:
        if prev is None:
            app.dependency_overrides.pop(require_verified_wallet, None)
        else:
            app.dependency_overrides[require_verified_wallet] = prev


class _Strat:
    def __init__(self, passes):
        self.passes_rigor_gate = passes


# ── _strategy_rigor_status ────────────────────────────────────────────────


def test_status_curated_passing():
    # strategy_provider is now a lazily-cached accessor (strategy_provider());
    # patch the name wholesale and configure the mocked callable's return_value.
    with patch(f"{V}.strategy_provider") as mock_provider:
        mock_provider.return_value.get_strategy.return_value = _Strat(True)
        assert _strategy_rigor_status("s1") == (True, True)


def test_status_curated_failing():
    with patch(f"{V}.strategy_provider") as mock_provider:
        mock_provider.return_value.get_strategy.return_value = _Strat(False)
        assert _strategy_rigor_status("s1") == (True, False)


def test_status_fails_closed_on_db_error():
    # Not curated → falls to the DB; if the session raises, we must report not-found
    # (False, False) so the caller blocks the deploy rather than waving it through.
    with (
        patch(f"{V}.strategy_provider") as mock_provider,
        patch("archimedes.db.get_session", side_effect=RuntimeError("db down")),
    ):
        mock_provider.return_value.get_strategy.return_value = None
        assert _strategy_rigor_status("missing") == (False, False)


# ── _assert_strategies_pass_rigor ─────────────────────────────────────────


def test_assert_raises_422_on_failing():
    with patch(f"{V}._strategy_rigor_status", return_value=(True, False)), pytest.raises(HTTPException) as exc:
        _assert_strategies_pass_rigor(["s1"])
    assert exc.value.status_code == 422
    assert "rigor gate" in exc.value.detail


def test_assert_raises_422_on_not_found():
    with patch(f"{V}._strategy_rigor_status", return_value=(False, False)), pytest.raises(HTTPException) as exc:
        _assert_strategies_pass_rigor(["ghost"])
    assert exc.value.status_code == 422
    assert "not found" in exc.value.detail


def test_assert_passes_on_passing():
    with patch(f"{V}._strategy_rigor_status", return_value=(True, True)):
        _assert_strategies_pass_rigor(["s1", "s2"])  # no raise


def test_assert_empty_list_is_noop():
    _assert_strategies_pass_rigor([])  # nothing to validate → no raise


def test_assert_blocks_if_any_one_fails():
    # All must pass; one failing id blocks the whole deploy.
    def _status(sid):
        return (True, sid != "bad")

    with patch(f"{V}._strategy_rigor_status", side_effect=_status), pytest.raises(HTTPException) as exc:
        _assert_strategies_pass_rigor(["good", "bad", "good2"])
    assert exc.value.status_code == 422


# ── HTTP: the gate fires BEFORE the on-chain deploy ───────────────────────


async def test_create_vault_rejects_failing_strategy_before_deploy():
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    with (
        _override_verified_wallet(app),
        patch(f"{V}._strategy_rigor_status", return_value=(True, False)),
        patch(f"{V}.chain_executor.create_vault") as mock_create,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/vaults/create",
                json={"name": "V", "symbol": "V", "strategy_ids": ["failing"]},
            )

    assert resp.status_code == 422
    assert "rigor gate" in resp.json()["detail"]
    mock_create.assert_not_called()  # never spent gas — gate fired first


async def test_create_vault_proceeds_when_rigor_passes():
    # Complementary happy path: a passing strategy must NOT be blocked by the new
    # precondition — the deploy proceeds and chain_executor.create_vault is called.
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    with (
        _override_verified_wallet(app),
        patch(f"{V}._strategy_rigor_status", return_value=(True, True)),
        patch(f"{V}.chain_executor.create_vault", new=AsyncMock(return_value="0xVaultDeployedAddress")) as mock_create,
        # record_funnel touches Redis; stub it so the test stays hermetic.
        patch("archimedes.api.funnel_middleware.record_funnel", new=AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/vaults/create",
                json={"name": "V", "symbol": "V", "strategy_ids": ["passing"]},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["vault_address"] == "0xVaultDeployedAddress"
    assert body["strategy_ids"] == ["passing"]
    mock_create.assert_awaited_once()  # gate let it through → gas spent


async def test_create_vault_writes_the_identity_ledger_vault_created_event():
    """Issue #1028 (D2): the SIWE-verified deployer's ``vault_created`` event
    lands in the ledger — the bottom-of-funnel identity write, unlike
    Generate's flag-gated one, since ``require_verified_wallet`` always
    identifies the caller here."""
    from archimedes.db import get_session
    from archimedes.main import app
    from archimedes.models.identity import IdentityEvent, WalletIdentity
    from httpx import ASGITransport, AsyncClient

    wallet = "0x" + "de" * 20  # matches _override_verified_wallet's lowercased form

    with (
        _override_verified_wallet(app, wallet=wallet),
        patch(f"{V}._strategy_rigor_status", return_value=(True, True)),
        patch(f"{V}.chain_executor.create_vault", new=AsyncMock(return_value="0xLedgerVaultAddress")),
        patch("archimedes.api.funnel_middleware.record_funnel", new=AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/vaults/create",
                json={"name": "V", "symbol": "V", "strategy_ids": ["passing"]},
            )
    assert resp.status_code == 200

    session = get_session()
    try:
        events = session.query(IdentityEvent).filter(IdentityEvent.wallet == wallet).all()
        shapes = [(e.event_type, e.actor_class, e.meta) for e in events]
    finally:
        session.query(IdentityEvent).filter(IdentityEvent.wallet == wallet).delete(synchronize_session=False)
        session.query(WalletIdentity).filter(WalletIdentity.wallet_address == wallet).delete(synchronize_session=False)
        session.commit()
        session.close()

    matching = [s for s in shapes if s[0] == "vault_created"]
    assert len(matching) == 1
    _, actor_class, meta = matching[0]
    assert actor_class == "human"
    assert meta["vault_address"] == "0xLedgerVaultAddress"
    assert meta["strategy_ids"] == ["passing"]

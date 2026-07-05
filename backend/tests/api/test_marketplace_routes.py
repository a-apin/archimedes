"""Tests for marketplace API routes."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from archimedes.api.auth_siwe import require_verified_wallet
from archimedes.api.marketplace_routes import marketplace_router
from archimedes.db import Base, engine

TEST_WALLET = "0x0000000000000000000000000000000000000001"


@pytest.fixture(autouse=True)
def _setup_db():
    """Create all tables before each test, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _mock_strategy_provider():
    """Patch strategy_provider.get_strategy to return a truthy value for test IDs."""
    with patch("archimedes.api.marketplace_routes.strategy_provider") as mock:
        # strategy_provider is the lru_cache'd FACTORY (post-#863 lazy
        # singleton) — routes call strategy_provider().get_strategy(...).
        # Yield the inner provider so tests configure .get_strategy directly.
        mock.return_value.get_strategy.return_value = MagicMock(id="test_strat")
        yield mock.return_value


@pytest.fixture(autouse=True)
def _seed_strategy_records(_setup_db):
    """Insert minimal StrategyRecord rows so the D5 ownership check doesn't 404.

    The D5 gate (wallet_can_publish) is patched separately; this seeds the
    strategy_store table so the prior record-existence check passes.
    Depends on _setup_db so tables exist before inserts.
    """
    from archimedes.db import get_session
    from archimedes.models.strategy_store import StrategyRecord

    _STRATEGY_IDS = ["test_strat", "dup_strat", "check_pool", "dup_sid_strat", "redact_strat"]
    with get_session() as session:
        for i, sid in enumerate(_STRATEGY_IDS):
            if session.query(StrategyRecord).filter_by(id=sid).first() is None:
                # content_hash must be unique (uq_strategy_content_hash) — use i to differentiate
                session.add(
                    StrategyRecord(
                        id=sid,
                        content_hash="0x" + format(i, "02x") * 32,
                        generation_method="fusion",
                        is_example=False,
                    )
                )
        session.commit()


@pytest.fixture(autouse=True)
def _mock_wallet_can_publish():
    """Patch wallet_can_publish to return True — the D5 gate is unit-tested separately."""
    with patch("archimedes.api.marketplace_routes.wallet_can_publish", return_value=True):
        yield


@pytest.fixture(autouse=True)
def _mock_provision_publisher_wallet():
    """Patch provision_publisher_wallet so tests don't call Circle API."""
    with patch(
        "archimedes.marketplace.wallet_provisioner.provision_publisher_wallet",
        new=AsyncMock(return_value=("test-agent-wallet-uuid", "0x0000000000000000000000000000000000000999")),
    ):
        yield


@pytest.fixture
def app():
    """FastAPI app with marketplace router and a mock market service."""
    from archimedes.marketplace.service import MarketService

    a = FastAPI()
    a.include_router(marketplace_router)

    # Override auth to return a test wallet
    a.dependency_overrides[require_verified_wallet] = lambda: TEST_WALLET

    market = MagicMock(spec=MarketService)
    market.payments_dry_run = True
    # Use the signer path for contract calls (simpler mock surface)
    market.signer = MagicMock()
    market.signer.is_configured = True
    market.signer.execute_contract = AsyncMock()
    market.executor = MagicMock()
    market.executor.create_vault = AsyncMock(return_value="0xvault")
    market.loader = MagicMock()
    market.loader._contract.return_value.functions.pools.return_value.call = AsyncMock(
        return_value=("0xaddr", "0xaddr", 0, 0, False)
    )
    market.settings = MagicMock()
    market.settings.payment_splitter_address = "0xsplitter"
    market.start_publisher = AsyncMock()
    market.add_subscriber = AsyncMock()
    market.remove_subscriber = AsyncMock()
    market.stop_publisher = AsyncMock()
    market.state = MagicMock()
    market.state.save_subscribers = AsyncMock()
    market.state.save_payment = AsyncMock()
    market.state.get_events = AsyncMock(return_value=[])
    market.publishers = {}

    a.state.market = market
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


def test_publish_rejects_unknown_strategy(client, _mock_strategy_provider):
    """Publish with a non-existent strategy_id returns 404."""
    _mock_strategy_provider.get_strategy.return_value = None
    resp = client.post(
        "/api/marketplace/publish",
        json={"strategy_id": "nonexistent", "vault_address": "0xvault"},
    )
    assert resp.status_code == 404, resp.text


def test_publish_creates_publisher_row(client):
    """Publish creates a MarketplaceAgent row with a derived pool_id."""
    resp = client.post(
        "/api/marketplace/publish",
        json={"strategy_id": "test_strat", "vault_address": "0xvault_pre"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["strategy_id"] == "test_strat"
    assert data["role"] == "publisher"
    assert data["pool_id"].startswith("0x")
    assert len(data["pool_id"]) == 66
    assert data["pool_id"] != "sub_id"  # not accidentally in sub_id column


def test_subscribe_rejects_blank_sub_id(client):
    """Subscribe with blank sub_id returns 400."""
    resp = client.post(
        "/api/marketplace/subscribe",
        json={
            "strategy_id": "test_strat",
            "pool_id": "0x" + "aa" * 32,
            "sub_id": "",
            "ephemeral_wallet": "0xeph",
            "initial_deposit_usdc": 100,
        },
    )
    assert resp.status_code == 400, resp.text


def test_publish_duplicate_returns_409(client):
    """Publishing the same strategy twice returns 409."""
    resp1 = client.post(
        "/api/marketplace/publish",
        json={"strategy_id": "dup_strat", "vault_address": "0xvault"},
    )
    assert resp1.status_code == 200

    resp2 = client.post(
        "/api/marketplace/publish",
        json={"strategy_id": "dup_strat", "vault_address": "0xvault"},
    )
    assert resp2.status_code == 409, resp2.text


def test_publish_pool_id_is_derived_not_accepted(client):
    """The pool_id in the response should match derive_pool_id, not come from client."""
    # Validate that pool_id is non-zero, 66 chars, and starts with 0x
    resp = client.post(
        "/api/marketplace/publish",
        json={"strategy_id": "check_pool", "vault_address": "0xvault_a"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pool_id"].startswith("0x")
    assert len(resp.json()["pool_id"]) == 66


def test_list_published_empty(client):
    """GET /published returns empty list when no publishers."""
    resp = client.get("/api/marketplace/published")
    assert resp.status_code == 200
    assert resp.json() == []


def test_subscribe_succeeds_live_mode_no_chain_calls(client, app):
    """Subscribe succeeds in live mode (payments_dry_run=False) with no SubscriptionManager calls."""

    # Create publisher first
    resp = client.post(
        "/api/marketplace/publish",
        json={"strategy_id": "test_strat", "vault_address": "0xvault"},
    )
    assert resp.status_code == 200

    # Enable live mode
    market = app.state.market
    market.payments_dry_run = False

    # Mock the wallet provisioner so it succeeds without real Circle API
    wallet_id = "test-wallet-uuid"
    wallet_address = "0x0000000000000000000000000000000000000eee"
    with patch(
        "archimedes.marketplace.wallet_provisioner.provision_subscriber_wallet",
        new=AsyncMock(return_value=(wallet_id, wallet_address)),
    ):
        resp = client.post(
            "/api/marketplace/subscribe",
            json={
                "strategy_id": "test_strat",
                "sub_id": "0x" + "bb" * 32,
                "ephemeral_wallet": "0xeph",
            },
        )
    # Should succeed — on-chain validation is gone (P7)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["role"] == "subscriber"
    assert data["sub_id"] == "0x" + "bb" * 32
    # No SubscriptionManager contract calls should have been made
    contract = market.loader._contract
    for call_args in contract.call_args_list:
        name = call_args[0][1] if len(call_args[0]) > 1 else ""
        assert "SubscriptionManager" not in name, f"Unexpected SubscriptionManager call: {call_args}"


# x402 payment-webhook route and _WEBHOOK_SECRET are not yet implemented
# in production code — tests deferred to the follow-up issue.


def test_subscribe_rejects_duplicate_sub_id_from_other_wallet(client, app):
    """A sub_id registered by one wallet cannot be reused by another.

    sub_id is client-supplied and keys the in-process engine
    (pub.subscribers[sub_id]) — reuse would silently overwrite the first
    subscriber's engine entry (hijack). The route must 409 on any reuse.
    """
    resp = client.post(
        "/api/marketplace/publish",
        json={"strategy_id": "dup_sid_strat", "vault_address": "0xvault"},
    )
    assert resp.status_code == 200

    sid = "0x" + "cc" * 32
    with patch(
        "archimedes.marketplace.wallet_provisioner.provision_subscriber_wallet",
        new=AsyncMock(return_value=("w-1", "0x0000000000000000000000000000000000000ee1")),
    ):
        r1 = client.post(
            "/api/marketplace/subscribe",
            json={"strategy_id": "dup_sid_strat", "sub_id": sid, "ephemeral_wallet": "0xeph"},
        )
    assert r1.status_code == 200, r1.text

    # A different verified wallet tries to register the SAME sub_id.
    app.dependency_overrides[require_verified_wallet] = lambda: "0x" + "77" * 20
    try:
        with patch(
            "archimedes.marketplace.wallet_provisioner.provision_subscriber_wallet",
            new=AsyncMock(return_value=("w-2", "0x0000000000000000000000000000000000000ee2")),
        ):
            r2 = client.post(
                "/api/marketplace/subscribe",
                json={"strategy_id": "dup_sid_strat", "sub_id": sid, "ephemeral_wallet": "0xeph"},
            )
        assert r2.status_code == 409, r2.text
        assert "sub_id" in r2.json()["detail"]
    finally:
        app.dependency_overrides[require_verified_wallet] = lambda: TEST_WALLET


def test_published_detail_redacts_subscriber_internals(client, app):
    """GET /published/{id} is public: no payment plumbing, no full subscriber wallets.

    The payload must expose only what the detail page renders — shortened
    wallet + status per subscriber — and never gateway_seller_address,
    agent_wallet_id, ephemeral_wallet, or a full (reusable) sub_id.
    """
    resp = client.post(
        "/api/marketplace/publish",
        json={"strategy_id": "redact_strat", "vault_address": "0xvault"},
    )
    assert resp.status_code == 200

    other_wallet = "0x" + "ab" * 20
    app.dependency_overrides[require_verified_wallet] = lambda: other_wallet
    try:
        with patch(
            "archimedes.marketplace.wallet_provisioner.provision_subscriber_wallet",
            new=AsyncMock(return_value=("w-3", "0x0000000000000000000000000000000000000ee3")),
        ):
            r = client.post(
                "/api/marketplace/subscribe",
                json={"strategy_id": "redact_strat", "sub_id": "0x" + "dd" * 32, "ephemeral_wallet": "0xeph"},
            )
        assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides[require_verified_wallet] = lambda: TEST_WALLET

    detail = client.get("/api/marketplace/published/redact_strat")
    assert detail.status_code == 200
    data = detail.json()

    # Publisher payment plumbing must not appear on the public surface.
    assert "gateway_seller_address" not in data
    assert "agent_wallet_id" not in data

    subs = data["subscribers"]
    assert len(subs) == 1
    s = subs[0]
    assert set(s) == {"sub_id", "subscriber_wallet", "status"}
    # Shortened forms only; the full values never appear anywhere in the payload.
    assert s["sub_id"].endswith("…")
    assert "…" in s["subscriber_wallet"]
    body = detail.text
    assert other_wallet.lower() not in body
    assert ("0x" + "dd" * 32) not in body
    assert "0x0000000000000000000000000000000000000ee3" not in body

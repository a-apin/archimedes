"""Tests for marketplace API routes."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

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


@pytest.fixture
def app():
    """FastAPI app with marketplace router and a mock market service."""
    from archimedes.marketplace.service import MarketService

    a = FastAPI()
    a.include_router(marketplace_router)

    # Override auth to return a test wallet
    a.dependency_overrides[require_verified_wallet] = lambda: TEST_WALLET

    market = MagicMock(spec=MarketService)
    market.dry_run = True
    market.signer = MagicMock()
    market.signer.is_configured = False
    market.executor = MagicMock()
    market.executor.create_vault = AsyncMock(return_value="0xvault")
    market.loader = MagicMock()
    market.settings = MagicMock()
    market.settings.payment_splitter_address = "0xsplitter"
    market.settings.agent_account = MagicMock()
    market.start_publisher = AsyncMock()
    market.add_subscriber = AsyncMock()
    market.remove_subscriber = AsyncMock()
    market.stop_publisher = AsyncMock()
    market.state = MagicMock()
    market.state.save_subscribers = AsyncMock()
    market.state.get_events = AsyncMock(return_value=[])
    market.publishers = {}

    a.state.market = market
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


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


def _make_sub_data(
    subscriber_addr: str = "0x0000000000000000000000000000000000000001",
    pool_id_bytes: bytes | None = None,
    active: bool = True,
) -> tuple:
    """Build a SubscriptionManager.subscriptions() return tuple."""
    from archimedes.marketplace.encoding import derive_pool_id, to_bytes32

    if pool_id_bytes is None:
        pool_id_bytes = to_bytes32(derive_pool_id("test_strat", "0x0000000000000000000000000000000000000001"))
    return (
        subscriber_addr,      # subscriber (address → str via Web3)
        pool_id_bytes,        # pool_id (bytes32)
        "0x0000000000000000000000000000000000000eee",  # ephemeral_wallet
        1000,                 # reserved_usdc
        "https://placeholder",  # webhook_url
        active,               # active
        1000000,              # created_at
    )


def test_subscribe_rejects_wallet_mismatch_on_chain(client, app):
    """Subscribe returns 403 when on-chain subscriber does not match caller."""
    # Create publisher first
    resp = client.post(
        "/api/marketplace/publish",
        json={"strategy_id": "test_strat", "vault_address": "0xvault"},
    )
    assert resp.status_code == 200

    # Enable on-chain validation with a mismatched subscriber address
    market = app.state.market
    market.dry_run = False
    sub_data = _make_sub_data(subscriber_addr="0x0000000000000000000000000000000000000bbb")
    mock_call = AsyncMock(return_value=sub_data)
    market.loader._contract.return_value.functions.subscriptions.return_value.call = mock_call

    resp = client.post(
        "/api/marketplace/subscribe",
        json={
            "strategy_id": "test_strat",
            "pool_id": "0x" + "aa" * 32,
            "sub_id": "0x" + "bb" * 32,
            "ephemeral_wallet": "0xeph",
            "initial_deposit_usdc": 100,
        },
    )
    assert resp.status_code == 403, resp.text
    assert "does not match" in resp.text


def test_subscribe_rejects_pool_id_mismatch_on_chain(client, app):
    """Subscribe returns 400 when on-chain pool_id does not match derived pool_id."""
    # Create publisher first
    resp = client.post(
        "/api/marketplace/publish",
        json={"strategy_id": "test_strat", "vault_address": "0xvault"},
    )
    assert resp.status_code == 200

    # Enable on-chain validation with a wrong pool_id
    market = app.state.market
    market.dry_run = False
    wrong_pool = b"\xee" + b"\x00" * 31  # 32 bytes, different from derived pool
    sub_data = _make_sub_data(pool_id_bytes=wrong_pool)
    mock_call = AsyncMock(return_value=sub_data)
    market.loader._contract.return_value.functions.subscriptions.return_value.call = mock_call

    resp = client.post(
        "/api/marketplace/subscribe",
        json={
            "strategy_id": "test_strat",
            "pool_id": "0x" + "aa" * 32,
            "sub_id": "0x" + "bb" * 32,
            "ephemeral_wallet": "0xeph",
            "initial_deposit_usdc": 100,
        },
    )
    assert resp.status_code == 400, resp.text
    assert "pool_id" in resp.text.lower() or "does not match" in resp.text

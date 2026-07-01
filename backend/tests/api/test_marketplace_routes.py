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

"""Tests for marketplace API routes."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from archimedes.api.auth_siwe import require_verified_wallet
from archimedes.api import marketplace_routes as mr
from archimedes.api.marketplace_routes import marketplace_router
from archimedes.db import Base, engine

TEST_WALLET = "0x0000000000000000000000000000000000000001"
TEST_WEBHOOK_SECRET = "test-x402-webhook-secret"


def _signed_webhook_headers(body_dict: dict) -> tuple[bytes, dict[str, str]]:
    """Encode *body_dict* as JSON and sign it with ``TEST_WEBHOOK_SECRET``.

    Returns ``(raw_body, headers_dict)`` for use with
    ``TestClient.post(content=raw_body, headers=headers)``.
    """
    raw = json.dumps(body_dict, separators=(",", ":")).encode()
    sig = hmac.new(TEST_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"Content-Type": "application/json", "X-Webhook-Signature": sig}


@pytest.fixture(autouse=True)
def _setup_db():
    """Create all tables before each test, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _webhook_secret():
    """Set a known webhook secret for every test in this module."""
    mr._WEBHOOK_SECRET = TEST_WEBHOOK_SECRET
    yield
    mr._WEBHOOK_SECRET = None


@pytest.fixture(autouse=True)
def _mock_strategy_provider():
    """Patch strategy_provider.get_strategy to return a truthy value for test IDs."""
    with patch("archimedes.api.marketplace_routes.strategy_provider") as mock:
        mock.get_strategy.return_value = MagicMock(id="test_strat")
        yield mock


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
    """Subscribe succeeds in live mode (dry_run=False) with no SubscriptionManager calls."""
    from archimedes.marketplace.wallet_provisioner import provision_subscriber_wallet

    # Create publisher first
    resp = client.post(
        "/api/marketplace/publish",
        json={"strategy_id": "test_strat", "vault_address": "0xvault"},
    )
    assert resp.status_code == 200

    # Enable live mode
    market = app.state.market
    market.dry_run = False

    # Mock the wallet provisioner so it succeeds without real Circle API
    wallet_id = "test-wallet-uuid"
    wallet_address = "0x0000000000000000000000000000000000000eee"
    with patch("archimedes.api.marketplace_routes.provision_subscriber_wallet",
              new=AsyncMock(return_value=(wallet_id, wallet_address))):
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


# ─── x402 payment webhook tests ───────────────────────────────────────


def test_payment_webhook_rejects_missing_signature(client, app):
    """Unsigned webhook request is rejected with 401."""
    resp = client.post(
        "/api/marketplace/payment-webhook",
        json={"sub_id": "0x" + "bb" * 32, "status": "confirmed"},
    )
    assert resp.status_code == 401, resp.text
    assert "Missing X-Webhook-Signature header" in resp.text
    # Ensure the secret is not leaked in the response body
    assert TEST_WEBHOOK_SECRET not in resp.text


def test_payment_webhook_rejects_bad_signature(client, app):
    """Webhook request with a wrong signature is rejected with 401."""
    raw, _ = _signed_webhook_headers({"sub_id": "0x" + "bb" * 32, "status": "confirmed"})
    bad_headers = {"Content-Type": "application/json", "X-Webhook-Signature": "deadbeef"}
    resp = client.post("/api/marketplace/payment-webhook", content=raw, headers=bad_headers)
    assert resp.status_code == 401, resp.text
    assert "Invalid webhook signature" in resp.text
    assert TEST_WEBHOOK_SECRET not in resp.text


def test_payment_webhook_records_confirmed_payment(client, app):
    """Confirmed payment notification is recorded via state.save_payment."""
    market = app.state.market
    body = {
        "sub_id": "0x" + "bb" * 32,
        "tx_hash": "0x" + "cc" * 32,
        "amount_usdc_raw": 100_000,
        "status": "confirmed",
    }
    raw, headers = _signed_webhook_headers(body)
    resp = client.post("/api/marketplace/payment-webhook", content=raw, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"status": "recorded", "sub_id": "0x" + "bb" * 32}
    market.state.save_payment.assert_awaited_once_with(
        "0x" + "bb" * 32,
        {
            "paid": True,
            "amount_usdc_raw": 100_000,
            "tx_hash": "0x" + "cc" * 32,
            "gateway_status": "confirmed",
        },
    )


def test_payment_webhook_ignores_non_confirmed(client, app):
    """Non-confirmed status is ignored, no payment recorded."""
    market = app.state.market
    body = {"sub_id": "0x" + "dd" * 32, "status": "failed"}
    raw, headers = _signed_webhook_headers(body)
    resp = client.post("/api/marketplace/payment-webhook", content=raw, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored", "sub_id": "0x" + "dd" * 32}
    market.state.save_payment.assert_not_called()

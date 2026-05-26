"""Unit coverage for CircleService — wallet status + integration metadata.

Mocks aiohttp.ClientSession + chain_client so no live HTTP fires.

Added 2026-05-24 as part of the #147 coverage-gate lift.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from archimedes.services.circle_service import CircleService


def _make_service(*, api_key: str = "", wallet_id: str = "", entity_secret: str = "") -> CircleService:
    """Build a fresh CircleService without polluting the module-level singleton."""
    svc = CircleService()
    svc._api_key = api_key
    svc._wallet_id = wallet_id
    svc._entity_secret = entity_secret
    return svc


class _MockResp:
    """aiohttp-like response object for `async with session.get(...)`."""

    def __init__(self, *, status: int, body: dict) -> None:
        self.status = status
        self._body = body

    async def json(self) -> dict:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _MockSession:
    """aiohttp-like session object for `async with aiohttp.ClientSession()`."""

    def __init__(self, response: _MockResp | Exception) -> None:
        self._response = response

    def get(self, *_args, **_kwargs):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class TestIsConfigured:
    def test_blank_keys_means_not_configured(self) -> None:
        assert _make_service().is_configured is False

    def test_full_keys_means_configured(self) -> None:
        assert _make_service(api_key="k", wallet_id="w").is_configured is True

    def test_partial_keys_means_not_configured(self) -> None:
        assert _make_service(api_key="k").is_configured is False


class TestGetWalletBalance:
    @pytest.mark.asyncio
    async def test_not_configured_returns_error_payload(self) -> None:
        result = await _make_service().get_wallet_balance()
        assert result["error"] == "not configured"
        assert result["chain"] == "ARC-TESTNET"

    @pytest.mark.asyncio
    async def test_happy_path_extracts_usdc_balance(self) -> None:
        svc = _make_service(api_key="k", wallet_id="walletXYZ")
        body = {
            "data": {
                "wallet": {
                    "address": "0xabc",
                    "balances": [
                        {"currency": "EUR", "amount": "999"},
                        {"currency": "USD", "amount": "42.0"},
                    ],
                    "blockchain": "ARC-TESTNET",
                    "custodyType": "DEVELOPER",
                }
            }
        }
        session = _MockSession(_MockResp(status=200, body=body))
        with patch("archimedes.services.circle_service.aiohttp.ClientSession", return_value=session):
            result = await svc.get_wallet_balance()
        assert result["balance_usdc"] == "42.0"
        assert result["address"] == "0xabc"
        assert result["wallet_id"] == "walletXYZ"

    @pytest.mark.asyncio
    async def test_no_usd_balance_defaults_to_zero(self) -> None:
        svc = _make_service(api_key="k", wallet_id="w")
        body = {"data": {"wallet": {"balances": []}}}
        session = _MockSession(_MockResp(status=200, body=body))
        with patch("archimedes.services.circle_service.aiohttp.ClientSession", return_value=session):
            result = await svc.get_wallet_balance()
        assert result["balance_usdc"] == "0"

    @pytest.mark.asyncio
    async def test_non_200_returns_error(self) -> None:
        svc = _make_service(api_key="k", wallet_id="w")
        session = _MockSession(_MockResp(status=500, body={}))
        with patch("archimedes.services.circle_service.aiohttp.ClientSession", return_value=session):
            result = await svc.get_wallet_balance()
        assert "500" in result["error"]

    @pytest.mark.asyncio
    async def test_request_exception_returns_error(self) -> None:
        svc = _make_service(api_key="k", wallet_id="w")
        session = _MockSession(RuntimeError("network down"))
        with patch("archimedes.services.circle_service.aiohttp.ClientSession", return_value=session):
            result = await svc.get_wallet_balance()
        assert "network down" in result["error"]


class TestGetIntegrationStatus:
    @pytest.mark.asyncio
    async def test_returns_full_tool_inventory_with_wallet_subreport(self) -> None:
        svc = _make_service(api_key="k", wallet_id="walletXYZ123")
        body = {"data": {"wallet": {"address": "0xabc", "balances": [{"currency": "USD", "amount": "100"}]}}}
        session = _MockSession(_MockResp(status=200, body=body))
        with (
            patch("archimedes.chain.client.chain_client") as cc,
            patch("archimedes.services.circle_service.aiohttp.ClientSession", return_value=session),
        ):
            cc.settings = SimpleNamespace(usdc_address="0xUSDC")
            result = await svc.get_integration_status()
        assert result["circle_tools_count"] >= 5
        assert "developer_controlled_wallets" in result["tools"]
        assert "smart_contracts" in result["tools"]
        # Was 10; updated to 11 after StrategyRegistry was added to the Arc-testnet
        # contract list (Issue #380 — Pi's `908cce9` bundle commit). Keep the assertion
        # explicit + commented so any future contract-count drift is caught here.
        assert result["tools"]["smart_contracts"]["count"] == 11
        assert result["tools"]["usdc_settlement"]["usdc_address"] == "0xUSDC"
        assert result["wallet"]["balance_usdc"] == "100"
        # `next_tools` + `rubric_score_estimate` were intentionally removed by Pi's
        # #380 fix — `rubric_score_estimate` was the headline judges-see-self-grading
        # bug, and `next_tools` was the unused companion field. Both gone now.

    @pytest.mark.asyncio
    async def test_unconfigured_wallet_id_displays_placeholder(self) -> None:
        svc = _make_service(api_key="", wallet_id="")
        with patch("archimedes.chain.client.chain_client") as cc:
            cc.settings = SimpleNamespace(usdc_address="0xUSDC")
            result = await svc.get_integration_status()
        # Empty wallet_id is shown as "not set" in the wallet sub-report
        assert result["tools"]["developer_controlled_wallets"]["wallet_id"] == "not set"

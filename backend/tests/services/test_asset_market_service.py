"""Tests for asset_market_service — on-chain oracle reads + stale guards.

Per issue #168: the service reads on-chain PriceOracle via chain_client as
the primary price source, falls back to yfinance for change/vol, and marks
stale when oracle data is >5 min old.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.services.asset_market_service import (
    AssetMarketService,
    _pct_change,
    _realized_vol_annual,
)

# ── Unit tests for stat math ──────────────────────────────────────────────


class TestStatMath:
    def test_pct_change_basic(self):
        assert _pct_change([100, 105], 1) == pytest.approx(5.0)

    def test_pct_change_insufficient_data(self):
        assert _pct_change([100], 1) is None

    def test_pct_change_zero_start(self):
        assert _pct_change([0, 105], 1) is None

    def test_realized_vol_basic(self):
        # Constant prices → zero vol
        prices = [100.0] * 32
        assert _realized_vol_annual(prices, 30) == pytest.approx(0.0)

    def test_realized_vol_insufficient(self):
        assert _realized_vol_annual([100, 101], 30) is None


# ── Oracle read tests (mocked chain_client) ────────────────────────────────


@pytest.fixture
def mock_chain_client():
    """Mock chain_client with oracle and synth addresses + ABI."""
    from pathlib import Path

    mock_settings = MagicMock()
    mock_settings.oracle_addresses = {
        "sSPY": "0xd8161a8eeab7c7100e2863abe3d5f346b5ff9e52",
        "sBTC": "0x6cc5f621c4e3b46152e69e5c9873689cbb4a85e8",
    }
    mock_settings.synth_addresses = {
        "sSPY": "0x6fea38dedea0c6bb66ce93e5383c34385d8b889f",
        "sBTC": "0x317e82be8f7cba6c162ab968fcf695d88e8e0359",
    }
    # Point to real ABI directory so Path resolution works
    abi_dir = str(Path(__file__).resolve().parents[2] / "contracts" / "abis")
    mock_settings.abi_dir = abi_dir

    mock_client = MagicMock()
    mock_client.settings = mock_settings
    mock_client.to_checksum = lambda addr: addr
    return mock_client


class TestOracleReads:
    @pytest.mark.asyncio
    async def test_read_oracle_prices_success(self, mock_chain_client):
        """On-chain getPrice returns price + timestamp; parsed correctly."""
        now = time.time()
        service = AssetMarketService()

        mock_contract = MagicMock()
        mock_contract.functions.getPrice.return_value.call = AsyncMock(
            return_value=(550_000_000, int(now)),  # $550 in 6 decimals
        )
        mock_client_w3 = MagicMock()
        mock_client_w3.eth.contract.return_value = mock_contract
        mock_chain_client.w3 = mock_client_w3

        with patch("archimedes.chain.client.chain_client", mock_chain_client):
            result = await service._read_oracle_prices(["sSPY"])

        assert "sSPY" in result
        assert result["sSPY"]["price"] == pytest.approx(550.0)
        assert result["sSPY"]["stale"] is False

    @pytest.mark.asyncio
    async def test_read_oracle_prices_stale(self, mock_chain_client):
        """Oracle timestamp >5 min old → stale=True."""
        old_ts = time.time() - 600  # 10 minutes ago
        service = AssetMarketService()

        mock_contract = MagicMock()
        mock_contract.functions.getPrice.return_value.call = AsyncMock(
            return_value=(100_000_000, int(old_ts)),  # $100, stale
        )
        mock_client_w3 = MagicMock()
        mock_client_w3.eth.contract.return_value = mock_contract
        mock_chain_client.w3 = mock_client_w3

        with patch("archimedes.chain.client.chain_client", mock_chain_client):
            result = await service._read_oracle_prices(["sSPY"])

        assert "sSPY" in result
        assert result["sSPY"]["stale"] is True

    @pytest.mark.asyncio
    async def test_read_oracle_prices_missing_symbol(self, mock_chain_client):
        """Symbol not in oracle_addresses → skipped, not error."""
        service = AssetMarketService()
        mock_client_w3 = MagicMock()
        mock_chain_client.w3 = mock_client_w3

        with patch("archimedes.chain.client.chain_client", mock_chain_client):
            result = await service._read_oracle_prices(["sUNKNOWN"])

        assert "sUNKNOWN" not in result
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_read_oracle_prices_chain_failure(self, mock_chain_client):
        """Chain read throws → symbol skipped, no crash."""
        service = AssetMarketService()

        mock_contract = MagicMock()
        mock_contract.functions.getPrice.return_value.call = AsyncMock(
            side_effect=Exception("RPC timeout"),
        )
        mock_client_w3 = MagicMock()
        mock_client_w3.eth.contract.return_value = mock_contract
        mock_chain_client.w3 = mock_client_w3

        with patch("archimedes.chain.client.chain_client", mock_chain_client):
            result = await service._read_oracle_prices(["sSPY"])

        assert "sSPY" not in result  # Failed read → omitted


class TestListAssets:
    @pytest.mark.asyncio
    async def test_oracle_primary_price_yfinance_fallback(self, mock_chain_client):
        """When oracle returns a price, it takes priority over yfinance."""
        now = time.time()
        service = AssetMarketService()

        mock_contract = MagicMock()
        mock_contract.functions.getPrice.return_value.call = AsyncMock(
            return_value=(550_000_000, int(now)),  # $550 from oracle
        )
        mock_client_w3 = MagicMock()
        mock_client_w3.eth.contract.return_value = mock_contract
        mock_chain_client.w3 = mock_client_w3

        mock_histories = {
            "sSPY": {
                "close": [540.0, 545.0, 548.0],  # yfinance shows ~$548
                "dates": ["2026-05-22", "2026-05-23", "2026-05-24"],
            }
        }

        with (
            patch("archimedes.chain.client.chain_client", mock_chain_client),
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", return_value=mock_histories),
            patch("archimedes.services.strategy_signal_evaluator.DEFAULT_SCAN_UNIVERSE", ["sSPY"]),
            patch(
                "archimedes.services.strategy_signal_evaluator.GLOBAL_ASSETS",
                {"sSPY": ("SPY", "SPY", "us_equity_etf", "NYSE")},
            ),
        ):
            resp = await service.list_assets()

        assert len(resp.assets) >= 1
        spy = next((a for a in resp.assets if a.symbol == "sSPY"), None)
        assert spy is not None
        assert spy.current_price == pytest.approx(550.0)  # Oracle price, not yfinance
        assert spy.is_stale is False

    @pytest.mark.asyncio
    async def test_no_oracle_marks_stale(self):
        """When oracle data is completely missing, asset is marked stale."""
        service = AssetMarketService()
        mock_histories = {
            "sSPY": {
                "close": [540.0, 545.0],
                "dates": ["2026-05-22", "2026-05-23"],
            }
        }

        with (
            patch.object(service, "_read_oracle_prices", return_value={}),
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", return_value=mock_histories),
            patch("archimedes.services.strategy_signal_evaluator.DEFAULT_SCAN_UNIVERSE", ["sSPY"]),
            patch(
                "archimedes.services.strategy_signal_evaluator.GLOBAL_ASSETS",
                {"sSPY": ("SPY", "SPY", "us_equity_etf", "NYSE")},
            ),
        ):
            resp = await service.list_assets()

        spy = next((a for a in resp.assets if a.symbol == "sSPY"), None)
        assert spy is not None
        assert spy.is_stale is True
        assert spy.current_price == pytest.approx(545.0)  # yfinance fallback

    @pytest.mark.asyncio
    async def test_cache_ttl(self):
        """Second call within TTL returns cached result."""
        service = AssetMarketService()

        with (
            patch.object(service, "_read_oracle_prices", return_value={}),
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", return_value={}),
            patch("archimedes.services.strategy_signal_evaluator.DEFAULT_SCAN_UNIVERSE", []),
            patch("archimedes.services.strategy_signal_evaluator.GLOBAL_ASSETS", {}),
        ):
            resp1 = await service.list_assets()
            resp2 = await service.list_assets()

        assert resp1 is resp2  # Same object (cached)

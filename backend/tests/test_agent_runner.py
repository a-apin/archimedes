"""Agent runner tests — trade computation and reasoning building.

Tests the deterministic parts of StrategyRunner (trade computation,
reasoning construction, weight conversion) without live chain or strategies.
Hermetic: no network, no testnet.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from archimedes.models.portfolio import (
    Portfolio,
    PortfolioHolding,
    TargetAllocation,
    TradeDirection,
)


def _make_portfolio(total_usdc=10000.0, holdings=None) -> Portfolio:
    """Create a test portfolio."""
    if holdings is None:
        holdings = [
            PortfolioHolding(symbol="USDC", token_address="0xusdc", amount=3000.0, weight=0.30, value_usdc=3000.0),
            PortfolioHolding(symbol="sSPY", token_address="0xsspy", amount=40.0, weight=0.40, value_usdc=4000.0),
            PortfolioHolding(symbol="sGOLD", token_address="0xsgold", amount=15.0, weight=0.30, value_usdc=3000.0),
        ]
    return Portfolio(
        vault_address="0xvault123",
        total_value_usdc=total_usdc,
        holdings=holdings,
        risk_profile="moderate",
    )


def _make_targets(**weights) -> list[TargetAllocation]:
    """Create target allocations from keyword args: symbol=weight."""
    targets = []
    synth_addrs = {
        "USDC": "0xusdc",
        "sSPY": "0xsspy",
        "sGOLD": "0xsgold",
        "sOIL": "0xsoil",
        "sNKY": "0xsnky",
        "sTSLA": "0xstsla",
    }
    for sym, weight in weights.items():
        targets.append(
            TargetAllocation(
                symbol=sym,
                token_address=synth_addrs.get(sym, ""),
                weight=weight,
                strategy_ids=[],
            )
        )
    return targets


class TestComputeTrades:
    """Test _compute_trades: diff current vs target → trade list."""

    @pytest.fixture()
    def runner(self):
        """Create a StrategyRunner with mocked chain dependencies."""
        with (
            patch("archimedes.chain.agent_runner.chain_client") as mock_client,
            patch("archimedes.chain.agent_runner.chain_executor"),
            patch("archimedes.chain.agent_runner.trace_publisher"),
            patch("archimedes.chain.agent_runner.default_provider"),
            patch("archimedes.chain.agent_runner.AgentStateStore"),
        ):
            mock_client.settings = MagicMock(
                synth_addresses={"sSPY": "0xsspy", "sGOLD": "0xsgold"},
                usdc_address="0xusdc",
            )
            from archimedes.chain.agent_runner import StrategyRunner

            return StrategyRunner()

    def test_no_drift_no_trades(self, runner):
        """When current weights match targets, no trades produced."""
        portfolio = _make_portfolio()
        targets = _make_targets(USDC=0.30, sSPY=0.40, sGOLD=0.30)
        trades = runner._compute_trades(portfolio, targets)
        assert len(trades) == 0

    def test_drift_produces_trades(self, runner):
        """When weights drift above threshold, trades are produced."""
        portfolio = _make_portfolio()  # USDC 30%, sSPY 40%, sGOLD 30%
        targets = _make_targets(USDC=0.10, sSPY=0.60, sGOLD=0.30)  # USDC 30→10 (-20%), sSPY 40→60 (+20%)
        trades = runner._compute_trades(portfolio, targets)
        assert len(trades) >= 1
        # Check the sSPY trade is a BUY (target > current)
        spy_trades = [t for t in trades if t.symbol == "sSPY"]
        assert len(spy_trades) == 1
        assert spy_trades[0].direction == TradeDirection.BUY

    def test_below_threshold_no_trades(self, runner):
        """Drift below 15% threshold produces no trades."""
        portfolio = _make_portfolio()  # USDC 30%, sSPY 40%, sGOLD 30%
        targets = _make_targets(USDC=0.32, sSPY=0.38, sGOLD=0.30)  # tiny drift
        trades = runner._compute_trades(portfolio, targets)
        assert len(trades) == 0

    def test_new_asset_buy(self, runner):
        """Adding a new asset not in portfolio produces a BUY trade."""
        portfolio = _make_portfolio()
        targets = _make_targets(USDC=0.20, sSPY=0.30, sGOLD=0.20, sOIL=0.30)
        trades = runner._compute_trades(portfolio, targets)
        oil_trades = [t for t in trades if t.symbol == "sOIL"]
        assert len(oil_trades) == 1
        assert oil_trades[0].direction == TradeDirection.BUY
        assert oil_trades[0].amount > 0

    def test_removed_asset_sell(self, runner):
        """Removing an asset from targets produces a SELL trade."""
        portfolio = _make_portfolio()  # has sGOLD
        targets = _make_targets(USDC=0.30, sSPY=0.70)  # no sGOLD
        trades = runner._compute_trades(portfolio, targets)
        gold_trades = [t for t in trades if t.symbol == "sGOLD"]
        assert len(gold_trades) == 1
        assert gold_trades[0].direction == TradeDirection.SELL


class TestWeightsToTargets:
    """Test _weights_to_targets: dict → TargetAllocation list."""

    @pytest.fixture()
    def runner(self):
        with (
            patch("archimedes.chain.agent_runner.chain_client") as mock_client,
            patch("archimedes.chain.agent_runner.chain_executor"),
            patch("archimedes.chain.agent_runner.trace_publisher"),
            patch("archimedes.chain.agent_runner.default_provider"),
            patch("archimedes.chain.agent_runner.AgentStateStore"),
        ):
            mock_client.settings = MagicMock(
                synth_addresses={"sSPY": "0xsspy", "sGOLD": "0xsgold", "sOIL": "0xsoil"},
                usdc_address="0xusdc",
            )
            from archimedes.chain.agent_runner import StrategyRunner

            return StrategyRunner()

    def test_converts_dict_to_targets(self, runner):
        weights = {"USDC": 0.20, "sSPY": 0.50, "sGOLD": 0.30}
        targets = runner._weights_to_targets(weights)
        assert len(targets) == 3
        symbols = {t.symbol for t in targets}
        assert symbols == {"USDC", "sSPY", "sGOLD"}
        # Check weights preserved
        for t in targets:
            assert t.weight == weights[t.symbol]

    def test_unknown_symbol_empty_address(self, runner):
        weights = {"USDC": 0.50, "UNKNOWN": 0.50}
        targets = runner._weights_to_targets(weights)
        unknown = next(t for t in targets if t.symbol == "UNKNOWN")
        assert unknown.token_address == ""

"""Agent runner tests — trade computation and reasoning building.

Tests the deterministic parts of StrategyRunner (trade computation,
reasoning construction, weight conversion) without live chain or strategies.
Hermetic: no network, no testnet.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_unpriced_holding_is_never_traded(self, runner):
        """#1080 review follow-up: a holding whose oracle price couldn't be
        read reports weight 0 BY CONSTRUCTION (priced=False) while its balance
        is real. Trading on that fake 0 would BUY more of the unpriceable
        asset every tick (current 0 vs target >0, forever). It must be
        skipped — in both directions — until the oracle prices it again."""
        holdings = [
            PortfolioHolding(symbol="USDC", token_address="0xusdc", amount=7000.0, weight=0.70, value_usdc=7000.0),
            PortfolioHolding(symbol="sSPY", token_address="0xsspy", amount=30.0, weight=0.30, value_usdc=3000.0),
            # Real balance, unreadable price: weight/value are fake zeros.
            PortfolioHolding(
                symbol="sGOLD", token_address="0xsgold", amount=15.0, weight=0.0, value_usdc=0.0, priced=False
            ),
        ]
        portfolio = _make_portfolio(holdings=holdings)
        # Target wants 30% sGOLD — naive diff would BUY (0 → 0.30).
        targets = _make_targets(USDC=0.40, sSPY=0.30, sGOLD=0.30)
        trades = runner._compute_trades(portfolio, targets)
        assert [t for t in trades if t.symbol == "sGOLD"] == [], (
            "unpriced holding must not be traded on its fake-zero weight"
        )
        # Other symbols still trade normally (USDC 0.70→0.40 is a real drift).
        assert any(t.symbol != "sGOLD" for t in trades)


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


class TestPerVaultScopingLegacyFallback:
    """Regression test for PR #324 / hotfix.

    PR #324 introduced per-vault strategy scoping via VaultMetadata.strategy_ids.
    The first version of #324 made vaults with no VaultMetadata row (legacy
    vaults — including all 6 vaults live on archimedes-arc.com at the time of
    the merge) hit an `is None → continue` branch that silently skipped every
    rebalance. This regression test enforces the recovered behavior: vaults
    with no metadata MUST be processed via the global-consensus fallback so
    existing deployments keep rebalancing.

    See: dead-code-audit-2026-05-24-v2.md § "Critical review of #324 / hotfix"
    """

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

    def test_get_vault_strategy_ids_returns_none_when_no_metadata(self, runner):
        """When no VaultMetadata row exists, the helper returns None — that's
        the signal the tick loop reads to take the legacy-fallback branch."""
        with patch("archimedes.db.get_session") as mock_session_cm:
            mock_session = MagicMock()
            mock_session.query.return_value.filter.return_value.first.return_value = None
            mock_session_cm.return_value.__enter__.return_value = mock_session

            result = runner._get_vault_strategy_ids("0xLegacyVault0000000000000000000000000000")
            assert result is None, "Missing VaultMetadata must return None (legacy-fallback signal), not []"

    def test_get_vault_strategy_ids_returns_none_when_metadata_empty(self, runner):
        """When VaultMetadata exists but strategy_ids is empty, also returns
        None so the legacy fallback fires (an empty-list selection is treated
        the same as no selection for this branch — the explicit-zero case is
        the next test)."""
        with patch("archimedes.db.get_session") as mock_session_cm:
            mock_meta = MagicMock()
            mock_meta.get_strategy_ids.return_value = []
            mock_session = MagicMock()
            mock_session.query.return_value.filter.return_value.first.return_value = mock_meta
            mock_session_cm.return_value.__enter__.return_value = mock_session

            result = runner._get_vault_strategy_ids("0xVaultWithEmptyMetadata")
            assert result is None, "Empty strategy_ids must return None so legacy fallback fires"

    def test_get_vault_strategy_ids_returns_list_when_populated(self, runner):
        """When metadata has populated strategy_ids, the helper returns the
        list and the tick loop takes the scoped path."""
        with patch("archimedes.db.get_session") as mock_session_cm:
            mock_meta = MagicMock()
            mock_meta.get_strategy_ids.return_value = ["faber_001", "tsmom_001"]
            mock_session = MagicMock()
            mock_session.query.return_value.filter.return_value.first.return_value = mock_meta
            mock_session_cm.return_value.__enter__.return_value = mock_session

            result = runner._get_vault_strategy_ids("0xVaultWithStrategies")
            assert result == ["faber_001", "tsmom_001"]


class TestClassifyMarketRegime:
    """The exogenous-regime helper wired in #660 — degrades to ('unknown')."""

    @pytest.fixture()
    def runner(self):
        with (
            patch("archimedes.chain.agent_runner.chain_client") as mock_client,
            patch("archimedes.chain.agent_runner.chain_executor"),
            patch("archimedes.chain.agent_runner.trace_publisher"),
            patch("archimedes.chain.agent_runner.default_provider"),
            patch("archimedes.chain.agent_runner.AgentStateStore"),
        ):
            mock_client.settings = MagicMock(synth_addresses={}, usdc_address="0xusdc")
            from archimedes.chain.agent_runner import StrategyRunner

            return StrategyRunner()

    @staticmethod
    def _snapshot_with_signals():
        from datetime import UTC, datetime

        from archimedes.models.asset import MarketSnapshot

        # vix + sp500_ma50 present → has_regime_signals is True.
        return MarketSnapshot(
            timestamp=datetime.now(UTC),
            prices={"sSPY": 5000.0},
            vix=12.0,
            sp500_ma50=4900.0,
            sp500_ma200=4800.0,
        )

    async def test_happy_path_returns_classification_and_regime(self, runner):
        from archimedes.models.regime import Regime

        runner.oracle.fetch_market_snapshot = AsyncMock(return_value=self._snapshot_with_signals())
        classification, regime = await runner._classify_market_regime("tick-1")
        assert classification is not None
        assert regime == Regime.RISK_ON.value
        assert classification.regime is Regime.RISK_ON

    async def test_snapshot_fetch_failure_degrades_to_unknown(self, runner):
        runner.oracle.fetch_market_snapshot = AsyncMock(side_effect=RuntimeError("oracle down"))
        classification, regime = await runner._classify_market_regime("tick-2")
        assert classification is None
        assert regime == "unknown"

    async def test_snapshot_without_regime_signals_degrades_to_unknown(self, runner):
        from datetime import UTC, datetime

        from archimedes.models.asset import MarketSnapshot

        # No VIX → has_regime_signals is False.
        bare = MarketSnapshot(timestamp=datetime.now(UTC), prices={"sSPY": 5000.0})
        runner.oracle.fetch_market_snapshot = AsyncMock(return_value=bare)
        classification, regime = await runner._classify_market_regime("tick-3")
        assert classification is None
        assert regime == "unknown"

    async def test_classifier_exception_degrades_to_unknown(self, runner):
        runner.oracle.fetch_market_snapshot = AsyncMock(return_value=self._snapshot_with_signals())
        runner.regime_detector.classify = MagicMock(side_effect=ValueError("bad signals"))
        classification, regime = await runner._classify_market_regime("tick-4")
        assert classification is None
        assert regime == "unknown"


class TestAlignedDecisionDedup:
    """_process_vault deduplicates identical no-trade ("aligned") decisions.

    Regression for the unreachable-dedup bug (CodeQL "Unreachable code"): the
    dedup guard used to sit in the rebalance branch behind ``and not trades``,
    which the earlier ``if not trades: return`` made statically unreachable — so
    identical "aligned" SKIP traces were re-anchored on-chain every tick. The
    dedup now lives on the no-trade path; a repeated identical decision is
    skipped (repeat counter incremented), not re-published.
    """

    @pytest.fixture()
    def runner(self):
        with (
            patch("archimedes.chain.agent_runner.chain_client") as mock_client,
            patch("archimedes.chain.agent_runner.chain_executor") as mock_executor,
            patch("archimedes.chain.agent_runner.trace_publisher"),
            patch("archimedes.chain.agent_runner.default_provider"),
            patch("archimedes.chain.agent_runner.AgentStateStore"),
        ):
            mock_client.settings = MagicMock(
                synth_addresses={"sSPY": "0xsspy", "sGOLD": "0xsgold"},
                usdc_address="0xusdc",
                oracle_addresses={},  # empty → no oracle-setting calls
            )
            mock_executor.read_portfolio = AsyncMock(return_value=_make_portfolio())
            mock_executor.set_token_oracles = AsyncMock()
            mock_executor.set_target_allocations = AsyncMock()
            from archimedes.chain.agent_runner import StrategyRunner

            # yield (not return) so the module-level patches stay active while
            # the test calls _process_vault, which reads chain_executor at call
            # time.
            yield StrategyRunner()

    @staticmethod
    def _consensus():
        from archimedes.models.regime import ConsensusLabel, EnsembleConsensus

        return EnsembleConsensus(flat_pct=0.2, signal_count=3, label=ConsensusLabel.RISK_ON)

    async def test_identical_aligned_decision_published_once(self, runner):
        # No drift → no trades → aligned SKIP path on both ticks.
        runner._compute_trades = MagicMock(return_value=[])
        runner._publish_trace = AsyncMock()
        call = {
            "vault_address": "0xvault123",
            "targets": _make_targets(USDC=0.30, sSPY=0.40, sGOLD=0.30),
            "all_signals": [],
            "market_regime": "unknown",
            "consensus": self._consensus(),
        }
        await runner._process_vault(tick_id="t1", **call)
        await runner._process_vault(tick_id="t2", **call)

        # First aligned tick publishes; the identical second tick is deduped.
        assert runner._publish_trace.await_count == 1
        assert runner._last_reasoning_count["0xvault123"] == 2

    async def test_changed_aligned_decision_republishes(self, runner):
        runner._compute_trades = MagicMock(return_value=[])
        runner._publish_trace = AsyncMock()
        base = {
            "vault_address": "0xvault123",
            "targets": _make_targets(USDC=0.30, sSPY=0.40, sGOLD=0.30),
            "all_signals": [],
            "consensus": self._consensus(),
        }
        await runner._process_vault(tick_id="t1", market_regime="risk_on", **base)
        # Different market regime → different reasoning string → must re-publish.
        await runner._process_vault(tick_id="t2", market_regime="risk_off", **base)

        assert runner._publish_trace.await_count == 2
        assert runner._last_reasoning_count["0xvault123"] == 1

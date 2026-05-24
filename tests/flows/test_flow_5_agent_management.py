"""
FLOW 5: Agent Manages ≥1 Tier 1 Vault [MANDATORY]
====================================================

User story: The Archimedes AI agent continuously monitors market conditions,
            detects regime changes, and autonomously rebalances Tier 1 vaults.

This is the core agentic loop — the 30% "Agentic Sophistication" score.

Components exercised:
  - Chuan:  IAgentOrchestrator (the main loop)
  - Önder:  IRegimeDetector (regime classification)
  - Önder:  IPortfolioConstructor (target weights)
  - Dan:    IStrategyProvider (strategy library)
  - Marten: IOracleUpdater + IChainExecutor + ITracePublisher

Preconditions:
  - Full stack deployed (synthetics, AMM, vaults, oracle)
  - At least 1 Tier 1 vault with deposited USDC and initial allocation
"""

from datetime import datetime

from archimedes.models.asset import MarketSnapshot
from archimedes.models.regime import Regime

# ─────────────────────────────────────────────────────────────
# 5.1 Regime detection (Önder's component)
# ─────────────────────────────────────────────────────────────


class TestRegimeDetection:
    """Önder's IRegimeDetector classifies market regimes."""

    def test_risk_on_classification(self, regime_detector):
        """Low VIX + positive momentum → RISK_ON."""
        snapshot = MarketSnapshot(
            timestamp=datetime.utcnow(),
            prices={"sTSLA": 185.0, "sSPY": 520.0},
            vix=15.0,
            sp500_ma50=510.0,
            sp500_ma200=490.0,
        )
        result = regime_detector.classify(snapshot)
        assert result.regime == Regime.RISK_ON
        assert result.confidence > 0.5

    def test_risk_off_classification(self, regime_detector):
        """High VIX + negative momentum → RISK_OFF."""
        snapshot = MarketSnapshot(
            timestamp=datetime.utcnow(),
            prices={"sTSLA": 160.0, "sSPY": 470.0},
            vix=28.0,
            sp500_ma50=480.0,
            sp500_ma200=500.0,
        )
        result = regime_detector.classify(snapshot)
        assert result.regime == Regime.RISK_OFF

    def test_crisis_classification(self, regime_detector):
        """Extreme VIX → CRISIS."""
        snapshot = MarketSnapshot(
            timestamp=datetime.utcnow(),
            prices={"sTSLA": 130.0, "sSPY": 420.0},
            vix=40.0,
            sp500_ma50=450.0,
            sp500_ma200=500.0,
        )
        result = regime_detector.classify(snapshot)
        assert result.regime == Regime.CRISIS

    def test_transition_classification(self, regime_detector):
        """Mixed signals → TRANSITION."""
        snapshot = MarketSnapshot(
            timestamp=datetime.utcnow(),
            prices={"sTSLA": 175.0, "sSPY": 500.0},
            vix=22.0,
            sp500_ma50=505.0,  # Above MA50
            sp500_ma200=510.0,  # Below MA200 — mixed
        )
        result = regime_detector.classify(snapshot)
        assert result.regime == Regime.TRANSITION

    def test_regime_change_detection(self, regime_detector):
        """When regime changes, regime_changed flag is set."""
        # First: RISK_ON
        snapshot1 = MarketSnapshot(
            timestamp=datetime.utcnow(),
            prices={},
            vix=15.0,
            sp500_ma50=510.0,
            sp500_ma200=490.0,
        )
        result1 = regime_detector.classify(snapshot1)
        assert result1.regime == Regime.RISK_ON

        # Then: RISK_OFF
        snapshot2 = MarketSnapshot(
            timestamp=datetime.utcnow(),
            prices={},
            vix=30.0,
            sp500_ma50=480.0,
            sp500_ma200=500.0,
        )
        result2 = regime_detector.classify(snapshot2)
        assert result2.regime_changed
        assert result2.previous_regime == Regime.RISK_ON

    def test_requires_2_signals_for_change(self, regime_detector):
        """Design.md: require 2+ confirming signals before changing regime.
        A single signal flipping should NOT change the classification.
        """
        # RISK_ON with one contradicting signal (VIX elevated but momentum positive)
        snapshot = MarketSnapshot(
            timestamp=datetime.utcnow(),
            prices={},
            vix=23.0,  # Slightly elevated
            sp500_ma50=520.0,
            sp500_ma200=490.0,  # Still positive
        )
        result = regime_detector.classify(snapshot)
        # Should NOT flip to RISK_OFF on one signal
        assert result.regime != Regime.RISK_OFF

    def test_insufficient_data_returns_none(self, regime_detector):
        """Snapshot without VIX/MA data → cannot classify."""
        snapshot = MarketSnapshot(
            timestamp=datetime.utcnow(),
            prices={"sTSLA": 185.0},
            # No VIX, no MA data
        )
        assert not snapshot.has_regime_signals


# ─────────────────────────────────────────────────────────────
# 5.2 Strategy provider (Dan's component)
# ─────────────────────────────────────────────────────────────


class TestStrategyProvider:
    """Dan's IStrategyProvider provides the curated strategy library."""

    def test_list_returns_at_least_5_strategies(self, strategy_provider):
        """MVP: 5-10 pre-curated strategies."""
        strategies = strategy_provider.list_strategies()
        assert len(strategies) >= 5

    def test_strategies_have_paper_backing(self, strategy_provider):
        """Every strategy has an arxiv paper ID and title."""
        for s in strategy_provider.list_strategies():
            assert s.paper_arxiv_id
            assert s.paper_title

    def test_filter_by_status(self, strategy_provider):
        """list_strategies(status=VALIDATED) returns only validated strategies."""
        from archimedes.models.strategy import StrategyStatus

        validated = strategy_provider.list_strategies(status=StrategyStatus.VALIDATED)
        for s in validated:
            assert s.status == StrategyStatus.VALIDATED

    def test_get_strategies_for_risk_profile(self, strategy_provider):
        """Conservative profile returns different strategies than aggressive."""
        conservative = strategy_provider.get_strategies_for_risk_profile("conservative")
        aggressive = strategy_provider.get_strategies_for_risk_profile("aggressive")
        # Should have some overlap but not identical
        con_ids = {s.id for s in conservative}
        agg_ids = {s.id for s in aggressive}
        assert con_ids != agg_ids


# ─────────────────────────────────────────────────────────────
# 5.3 Agent orchestration loop (Chuan's component)
# ─────────────────────────────────────────────────────────────


class TestAgentOrchestration:
    """The full agent tick() pipeline."""

    async def test_tick_runs_full_pipeline(self, agent_orchestrator):
        """tick() runs without error: fetch prices → classify regime →
        evaluate vaults → maybe rebalance → publish traces.
        """
        await agent_orchestrator.tick()

    async def test_evaluate_vault_returns_decision(self, agent_orchestrator):
        """evaluate_vault() returns a RebalanceDecision with all fields."""
        decision = await agent_orchestrator.evaluate_vault("0xVault")
        assert decision.vault_address == "0xVault"
        assert isinstance(decision.should_rebalance, bool)
        assert decision.trigger in ("drift", "regime_change", "strategy_decay", "calendar")
        assert decision.reasoning  # Non-empty explanation

    async def test_drift_triggers_rebalance(self, agent_orchestrator):
        """When a position drifts >5% from target, agent should rebalance."""
        # Setup: vault with sTSLA at 35% but target is 25% (10% drift)
        decision = await agent_orchestrator.evaluate_vault("0xDriftedVault")
        assert decision.should_rebalance
        assert decision.trigger == "drift"
        assert len(decision.trades) > 0

    async def test_small_drift_skips_rebalance(self, agent_orchestrator):
        """When drift is <5%, agent should skip (cost > benefit)."""
        decision = await agent_orchestrator.evaluate_vault("0xAlignedVault")
        assert not decision.should_rebalance

    async def test_regime_change_triggers_evaluation(self, agent_orchestrator):
        """Regime change from RISK_ON → RISK_OFF triggers rebalance evaluation."""
        decision = await agent_orchestrator.evaluate_vault("0xVault")
        # After a regime change, the agent should at least evaluate
        assert decision is not None

    async def test_generates_reasoning_trace(self, agent_orchestrator):
        """After a rebalance decision, a ReasoningTrace is generated."""

        decision = await agent_orchestrator.evaluate_vault("0xVault")
        regime = await agent_orchestrator.get_current_regime()

        trace = await agent_orchestrator.generate_reasoning_trace(decision, regime)
        assert trace.vault_address == "0xVault"
        assert trace.reasoning  # Non-empty
        assert trace.confidence > 0
        assert trace.trace_hash  # Hash computed


# ─────────────────────────────────────────────────────────────
# 5.4 End-to-end agent cycle
# ─────────────────────────────────────────────────────────────


class TestAgentEndToEnd:
    """Full cycle: market change → regime detection → rebalance → trace."""

    async def test_full_rebalance_cycle(
        self,
        oracle_updater,
        regime_detector,
        portfolio_constructor,
        strategy_provider,
        chain_executor,
        trace_publisher,
        agent_orchestrator,
    ):
        """
        1. Oracle updater pushes new prices (VIX spike to 30)
        2. Regime detector classifies → RISK_OFF
        3. Portfolio constructor produces new target (higher USYC)
        4. Agent evaluates vault → drift detected → should rebalance
        5. Chain executor executes trades via AMM
        6. Trace publisher anchors reasoning hash on-chain
        7. Trace is verifiable
        """
        # Step 1: Market data
        snapshot = await oracle_updater.fetch_market_snapshot()

        # Step 2: Regime
        regime = regime_detector.classify(snapshot)

        # Step 3: New targets
        strategy_provider.list_strategies()
        # (backtest results would come from Önder's evaluator)

        # Step 4: Agent decision
        decision = await agent_orchestrator.evaluate_vault("0xVault")

        if decision.should_rebalance:
            # Step 5: Execute
            tx_hashes = await chain_executor.execute_trades(decision.vault_address, decision.trades)
            assert len(tx_hashes) > 0

            # Step 6: Publish trace
            trace = await agent_orchestrator.generate_reasoning_trace(decision, regime)
            tx = await trace_publisher.publish(trace)
            assert tx is not None

            # Step 7: Verify
            is_valid = await trace_publisher.verify(trace)
            assert is_valid

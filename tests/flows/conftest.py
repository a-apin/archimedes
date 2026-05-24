"""
Shared fixtures for flow tests.

Each fixture provides a mock or stub implementation of an interface.
Replace with real implementations as components are built.

Pattern: each team member implements their interface, then swaps
the mock fixture here for the real implementation. If the tests
still pass, the integration works.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from archimedes.models.asset import AssetPrice, MarketSnapshot
from archimedes.models.backtest import BacktestResult
from archimedes.models.portfolio import (
    Portfolio,
    PortfolioHolding,
    RebalanceDecision,
    RiskProfile,
    TargetAllocation,
    TradeDirection,
    TradeOrder,
)
from archimedes.models.regime import Regime, RegimeClassification, RegimeSignals
from archimedes.models.strategy import (
    PositionSizing,
    RebalanceFrequency,
    Strategy,
    StrategyStatus,
)
from archimedes.models.trace import DecisionType, ReasoningTrace

# ─────────────────────────────────────────────────────────────
# Marten's stubs
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def oracle_updater():
    """Stub IOracleUpdater — replace with Marten's real implementation."""
    mock = AsyncMock()
    mock.fetch_prices.return_value = [
        AssetPrice(symbol="sTSLA", price_usd=185.00, timestamp=datetime.utcnow()),
        AssetPrice(symbol="sSPY", price_usd=520.00, timestamp=datetime.utcnow()),
        AssetPrice(symbol="sGLD", price_usd=2350.00, timestamp=datetime.utcnow()),
        AssetPrice(symbol="sBTC", price_usd=67000.00, timestamp=datetime.utcnow()),
        AssetPrice(symbol="USYC", price_usd=1.05, timestamp=datetime.utcnow()),
    ]
    mock.push_prices_on_chain.return_value = "0xabc123"
    mock.fetch_market_snapshot.return_value = MarketSnapshot(
        timestamp=datetime.utcnow(),
        prices={"sTSLA": 185.0, "sSPY": 520.0, "sGLD": 2350.0, "sBTC": 67000.0, "USYC": 1.05},
        vix=18.0,
        sp500_ma50=515.0,
        sp500_ma200=490.0,
    )
    return mock


@pytest.fixture
def chain_executor():
    """Stub IChainExecutor — replace with Marten's real implementation."""
    mock = AsyncMock()
    mock.execute_trades.return_value = ["0xtx1", "0xtx2"]
    mock.read_portfolio.return_value = Portfolio(
        vault_address="0xVault",
        holdings=[
            PortfolioHolding(symbol="sTSLA", token_address="0xsTSLA", amount=10.0, value_usdc=1850.0, weight=0.30),
            PortfolioHolding(symbol="sSPY", token_address="0xsSPY", amount=5.0, value_usdc=2600.0, weight=0.25),
            PortfolioHolding(symbol="USYC", token_address="0xUSYC", amount=2800.0, value_usdc=2800.0, weight=0.45),
        ],
        total_value_usdc=7250.0,
        risk_profile=RiskProfile.MODERATE,
    )
    mock.create_vault.return_value = "0xNewVault"
    return mock


@pytest.fixture
def trace_publisher():
    """Stub ITracePublisher — replace with Marten's real implementation."""
    mock = AsyncMock()

    async def publish_side_effect(trace: ReasoningTrace):
        trace.arc_tx_hash = "0xtrace_tx_123"
        return trace.arc_tx_hash

    mock.publish.side_effect = publish_side_effect
    mock.verify.return_value = True
    mock.get_trace_count.return_value = 5
    return mock


# ─────────────────────────────────────────────────────────────
# Önder's stubs
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def regime_detector():
    """Stub IRegimeDetector — replace with Önder's real implementation."""

    class StubRegimeDetector:
        def __init__(self):
            self._last: RegimeClassification | None = None

        def classify(self, snapshot: MarketSnapshot) -> RegimeClassification:
            if not snapshot.has_regime_signals:
                raise ValueError("Insufficient data for regime classification")

            vix = snapshot.vix or 20.0
            above_ma50 = (snapshot.sp500_ma50 or 0) < (snapshot.prices.get("sSPY", 0))
            above_ma200 = (snapshot.sp500_ma200 or 0) < (snapshot.prices.get("sSPY", 0))

            # Simple threshold logic (Önder will replace with proper model)
            if vix > 35:
                regime = Regime.CRISIS
            elif vix > 25 and not above_ma50:
                regime = Regime.RISK_OFF
            elif vix < 20 and above_ma50 and above_ma200:
                regime = Regime.RISK_ON
            else:
                regime = Regime.TRANSITION

            prev = self._last
            classification = RegimeClassification(
                regime=regime,
                confidence=0.75,
                signals=RegimeSignals(
                    vix_level=vix,
                    vix_rate_of_change=0.0,
                    sp500_above_ma50=above_ma50,
                    sp500_above_ma200=above_ma200,
                ),
                timestamp=snapshot.timestamp,
                previous_regime=prev.regime if prev else None,
                regime_changed=prev is not None and prev.regime != regime,
            )
            self._last = classification
            return classification

        def get_current_regime(self) -> RegimeClassification | None:
            return self._last

    return StubRegimeDetector()


@pytest.fixture
def portfolio_constructor():
    """Stub IPortfolioConstructor — replace with Önder's real implementation."""

    class StubPortfolioConstructor:
        def construct(self, risk_profile, strategies, backtest_results, regime, current_portfolio=None):
            from archimedes.models.portfolio import RISK_PROFILE_PARAMS

            params = RISK_PROFILE_PARAMS[risk_profile]
            usyc_floor = params["usyc_floor"]

            # In crisis/risk_off, push USYC higher
            if regime.regime in (Regime.CRISIS, Regime.RISK_OFF):
                usyc_weight = min(params["usyc_ceiling"], usyc_floor + 0.15)
            else:
                usyc_weight = usyc_floor

            equity_weight = 1.0 - usyc_weight
            # Split equity equally among available assets
            equity_assets = ["sTSLA", "sSPY", "sGLD", "sBTC"]
            per_asset = equity_weight / len(equity_assets)

            allocations = [
                TargetAllocation(symbol=sym, token_address=f"0x{sym}", weight=per_asset) for sym in equity_assets
            ]
            allocations.append(TargetAllocation(symbol="USYC", token_address="0xUSYC", weight=usyc_weight))
            return allocations

        def score_strategy(self, strategy, result, risk_profile):
            return result.sharpe_ratio * (1 - abs(result.correlation_to_spy))

    return StubPortfolioConstructor()


@pytest.fixture
def backtest_results():
    """Sample backtest results for 5 strategies."""
    return {
        f"strategy-{i}": BacktestResult(
            strategy_id=f"strategy-{i}",
            sharpe_ratio=1.2 + i * 0.3,
            sortino_ratio=1.5 + i * 0.2,
            max_drawdown=0.10 + i * 0.02,
            cagr=0.12 + i * 0.03,
            calmar_ratio=1.2,
            win_rate=0.55,
            profit_factor=1.8,
            total_trades=100 + i * 20,
            avg_holding_period_days=5.0,
            correlation_to_spy=0.3 + i * 0.1,
            correlation_to_btc=0.1 + i * 0.05,
        )
        for i in range(5)
    }


# ─────────────────────────────────────────────────────────────
# Dan's stubs
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def strategy_provider():
    """Stub IStrategyProvider — replace with Dan's real implementation."""

    class StubStrategyProvider:
        def __init__(self):
            self._strategies = [
                Strategy(
                    id=f"strategy-{i}",
                    paper_arxiv_id=f"2509.{11420 + i}",
                    paper_title=f"Sample Strategy Paper {i + 1}",
                    paper_authors=["Author A", "Author B"],
                    methodology_summary=f"A {['momentum', 'mean-reversion', 'trend-following', 'factor', 'volatility'][i]} strategy.",
                    asset_universe=["sTSLA", "sSPY", "sGLD", "sBTC"],
                    position_sizing=PositionSizing.EQUAL_WEIGHT,
                    rebalance_frequency=RebalanceFrequency.WEEKLY,
                    status=StrategyStatus.VALIDATED,
                )
                for i in range(5)
            ]

        def list_strategies(self, status=None, asset_universe=None):
            result = self._strategies
            if status:
                result = [s for s in result if s.status == status]
            return result

        def get_strategy(self, strategy_id):
            return next((s for s in self._strategies if s.id == strategy_id), None)

        def get_strategies_for_risk_profile(self, risk_profile_name):
            # Stub: return first 3 for conservative, all 5 for aggressive
            if risk_profile_name == "conservative":
                return self._strategies[:3]
            return self._strategies

        def extract_from_paper(self, arxiv_id):
            return None  # Demo feature, not implemented in stub

    return StubStrategyProvider()


@pytest.fixture
def strategies(strategy_provider):
    """Shortcut: list of all strategies."""
    return strategy_provider.list_strategies()


# ─────────────────────────────────────────────────────────────
# Regime fixtures
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def regime():
    """A RISK_ON regime for testing."""
    return RegimeClassification(
        regime=Regime.RISK_ON,
        confidence=0.85,
        signals=RegimeSignals(
            vix_level=15.0,
            vix_rate_of_change=-0.05,
            sp500_above_ma50=True,
            sp500_above_ma200=True,
        ),
        timestamp=datetime.utcnow(),
    )


@pytest.fixture
def crisis_regime():
    """A CRISIS regime for testing."""
    return RegimeClassification(
        regime=Regime.CRISIS,
        confidence=0.90,
        signals=RegimeSignals(
            vix_level=42.0,
            vix_rate_of_change=0.50,
            sp500_above_ma50=False,
            sp500_above_ma200=False,
        ),
        timestamp=datetime.utcnow(),
    )


# ─────────────────────────────────────────────────────────────
# Chuan's stubs
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def agent_orchestrator():
    """Stub IAgentOrchestrator — replace with Chuan's real implementation."""
    mock = AsyncMock()
    mock.get_managed_vaults.return_value = ["0xVault"]
    mock.get_current_regime.return_value = RegimeClassification(
        regime=Regime.RISK_ON,
        confidence=0.80,
        signals=RegimeSignals(
            vix_level=17.0,
            vix_rate_of_change=-0.02,
            sp500_above_ma50=True,
            sp500_above_ma200=True,
        ),
        timestamp=datetime.utcnow(),
    )
    mock.evaluate_vault.return_value = RebalanceDecision(
        vault_address="0xVault",
        should_rebalance=True,
        trigger="drift",
        current_portfolio=Portfolio(vault_address="0xVault", total_value_usdc=10000.0),
        target_allocations=[
            TargetAllocation(symbol="sTSLA", token_address="0xsTSLA", weight=0.25),
            TargetAllocation(symbol="USYC", token_address="0xUSYC", weight=0.35),
        ],
        trades=[
            TradeOrder(
                symbol="sTSLA",
                token_address="0xsTSLA",
                direction=TradeDirection.SELL,
                amount=2.0,
                estimated_usdc_value=370.0,
            ),
        ],
        estimated_cost_usdc=5.0,
        estimated_benefit=50.0,
        reasoning="Portfolio drifted 8% from target. Selling sTSLA to rebalance.",
        timestamp=datetime.utcnow(),
    )

    async def gen_trace(decision, regime):
        trace = ReasoningTrace(
            id="trace-auto",
            vault_address=decision.vault_address,
            decision_type=DecisionType.REBALANCE,
            trigger=decision.trigger,
            reasoning=decision.reasoning,
            confidence=0.80,
        )
        trace.compute_hash()
        return trace

    mock.generate_reasoning_trace.side_effect = gen_trace
    return mock


# ─────────────────────────────────────────────────────────────
# FastAPI test client
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def vault_address():
    """A known vault address for testing."""
    return "0x1234567890abcdef1234567890abcdef12345678"


@pytest.fixture
def client(vault_address):
    """AsyncClient for testing FastAPI endpoints.

    TODO: Replace with real FastAPI TestClient once Chuan implements routes.
    """
    mock = AsyncMock()

    async def _get(url, params=None):
        """Return appropriate mock response based on URL."""
        base = url.split("?")[0].rstrip("/")

        if base.startswith("/api/traces"):
            parts = base.split("/")
            is_list = len(parts) == 3  # /api/traces
            is_by_vault = (params or {}).get("vault_address")
            trace_id = parts[3] if len(parts) >= 4 else None

            sample_trace = {
                "id": trace_id or "trace-001",
                "vault_address": is_by_vault or vault_address,
                "decision_type": "rebalance",
                "trigger": "drift",
                "timestamp": "2026-05-15T10:30:00",
                "reasoning": "Test reasoning trace.",
                "confidence": 0.85,
                "trace_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
                "arc_tx_hash": "0xtrace_tx_123",
                "is_verified": True,
            }

            if is_list:
                return MagicMock(
                    status_code=200,
                    json=MagicMock(return_value={"traces": [sample_trace], "total": 1}),
                )
            else:
                return MagicMock(
                    status_code=200,
                    json=MagicMock(return_value=sample_trace),
                )

        return MagicMock(
            status_code=200,
            json=MagicMock(return_value={"vaults": [], "total": 0}),
        )

    mock.get.side_effect = _get
    return mock

"""Tests for the Kelly/Risk-Parity portfolio constructor.

Validates Kelly fraction computation, risk-parity weighting, regime-aware
deleveraging, USDC floor enforcement, and single-asset caps.
"""

from __future__ import annotations

from datetime import UTC, datetime

from archimedes.models.backtest import BacktestResult
from archimedes.models.paper_ref import PaperRef
from archimedes.models.portfolio import (
    RISK_PROFILE_PARAMS,
    Portfolio,
    PortfolioHolding,
    RiskProfile,
    TargetAllocation,
    TradeDirection,
)
from archimedes.models.regime import Regime, RegimeClassification, RegimeSignals
from archimedes.models.strategy import Strategy, StrategyStatus
from archimedes.services._deprecated.kelly_portfolio import KellyRiskParityConstructor

# Test addresses (no chain_client mocking needed)
_USDC_ADDR = "0xusdc"
_SYNTH_ADDRS = {"sSPY": "0xsspy", "sTSLA": "0xstsla", "sNVDA": "0xsnvda", "sBTC": "0xsbtc"}


def _make_strategy(
    strategy_id: str = "test_strat",
    sharpe: float = 1.2,
    cagr: float = 0.15,
    max_dd: float = 0.12,
    assets: list[str] | None = None,
) -> Strategy:
    """Create a test strategy with stub metrics."""
    return Strategy(
        id=strategy_id,
        papers=[PaperRef(title=f"Test Strategy {strategy_id}")],
        asset_universe=assets or ["SPY"],
        status=StrategyStatus.VALIDATED,
        stub_sharpe=sharpe,
        stub_cagr=cagr,
        stub_max_dd=max_dd,
    )


def _make_regime(regime: Regime = Regime.RISK_ON) -> RegimeClassification:
    """Create a test regime classification."""
    return RegimeClassification(
        regime=regime,
        confidence=0.8,
        signals=RegimeSignals(
            vix_level=15.0,
            vix_rate_of_change=0.0,
            sp500_above_ma50=True,
            sp500_above_ma200=True,
        ),
        timestamp=datetime.now(UTC),
    )


def _make_portfolio(total_usdc: float = 10000.0) -> Portfolio:
    """Create a test portfolio."""
    return Portfolio(
        vault_address="0x1234",
        holdings=[
            PortfolioHolding(symbol="USDC", token_address=_USDC_ADDR, amount=5000, value_usdc=5000, weight=0.5),
            PortfolioHolding(
                symbol="sSPY", token_address=_SYNTH_ADDRS["sSPY"], amount=2500, value_usdc=2500, weight=0.25
            ),
            PortfolioHolding(
                symbol="sTSLA", token_address=_SYNTH_ADDRS["sTSLA"], amount=2500, value_usdc=2500, weight=0.25
            ),
        ],
        total_value_usdc=total_usdc,
        risk_profile=RiskProfile.MODERATE,
    )


def _make_backtest_result(strategy_id: str, sharpe: float = 1.5, max_dd: float = 0.15) -> BacktestResult:
    """Create a test BacktestResult."""
    return BacktestResult(
        strategy_id=strategy_id,
        sharpe_ratio=sharpe,
        sortino_ratio=sharpe * 0.8,
        max_drawdown=max_dd,
        cagr=0.15,
        calmar_ratio=0.15 / max_dd,
        win_rate=0.55,
        profit_factor=1.3,
        total_trades=100,
        avg_holding_period_days=7.0,
        correlation_to_spy=0.7,
        correlation_to_btc=0.2,
    )


class TestKellyWeights:
    """Kelly Criterion position sizing."""

    def test_kelly_with_strong_sharpe(self) -> None:
        """Strong Sharpe → significant Kelly allocation."""
        constructor = KellyRiskParityConstructor()
        strategy = _make_strategy(sharpe=2.0, cagr=0.20, max_dd=0.10)
        regime = _make_regime(Regime.RISK_ON)
        portfolio = _make_portfolio()

        allocations = constructor.construct(
            current=portfolio,
            strategies=[strategy],
            regime=regime,
            risk_profile=RiskProfile.AGGRESSIVE,
            usdc_address=_USDC_ADDR,
            synth_addresses=_SYNTH_ADDRS,
        )

        # Should have USDC + at least one synth
        symbols = {a.symbol for a in allocations}
        assert "USDC" in symbols
        assert "sSPY" in symbols

        # Weights should sum to ~1.0
        total_weight = sum(a.weight for a in allocations)
        assert abs(total_weight - 1.0) < 0.02

    def test_kelly_with_weak_sharpe(self) -> None:
        """Weak Sharpe → strategy skipped (below threshold), fallback to equal weight."""
        constructor = KellyRiskParityConstructor()
        strategy = _make_strategy(sharpe=0.1)  # Below MIN_SHARPE_FOR_KELLY
        regime = _make_regime(Regime.RISK_ON)
        portfolio = _make_portfolio()

        allocations = constructor.construct(
            current=portfolio,
            strategies=[strategy],
            regime=regime,
            risk_profile=RiskProfile.MODERATE,
            usdc_address=_USDC_ADDR,
            synth_addresses=_SYNTH_ADDRS,
        )

        # Should fall back to equal weight
        assert len(allocations) > 0
        symbols = {a.symbol for a in allocations}
        assert "USDC" in symbols

    def test_multiple_strategies_diversified(self) -> None:
        """Multiple strategies → diversified across assets."""
        constructor = KellyRiskParityConstructor()
        strategies = [
            _make_strategy("strat_a", sharpe=1.5, assets=["SPY"]),
            _make_strategy("strat_b", sharpe=1.0, assets=["TSLA"]),
            _make_strategy("strat_c", sharpe=0.8, assets=["NVDA"]),
        ]
        regime = _make_regime(Regime.RISK_ON)
        portfolio = _make_portfolio()

        allocations = constructor.construct(
            current=portfolio,
            strategies=strategies,
            regime=regime,
            risk_profile=RiskProfile.MODERATE,
            usdc_address=_USDC_ADDR,
            synth_addresses=_SYNTH_ADDRS,
        )

        symbols = {a.symbol for a in allocations}
        assert "sSPY" in symbols
        assert "sTSLA" in symbols
        assert "sNVDA" in symbols


class TestRiskParity:
    """Risk-parity (inverse-vol) weighting."""

    def test_high_vol_gets_less_weight(self) -> None:
        """Higher volatility strategies should get lower risk-parity weight."""
        constructor = KellyRiskParityConstructor()

        scores = [
            type("MockScore", (), {"symbol": "sSPY", "risk_parity_weight": 1.0 / 0.10, "strategy_id": "a"})(),
            type("MockScore", (), {"symbol": "sTSLA", "risk_parity_weight": 1.0 / 0.30, "strategy_id": "b"})(),
        ]

        rp = constructor._risk_parity_weights(scores)

        # sSPY (lower vol) should get more weight than sTSLA (higher vol)
        assert rp["sSPY"] > rp["sTSLA"]

    def test_risk_parity_normalizes_to_one(self) -> None:
        """Risk-parity weights should sum to 1.0."""
        constructor = KellyRiskParityConstructor()

        scores = [
            type("MockScore", (), {"symbol": "a", "risk_parity_weight": 2.0, "strategy_id": "a"})(),
            type("MockScore", (), {"symbol": "b", "risk_parity_weight": 3.0, "strategy_id": "b"})(),
            type("MockScore", (), {"symbol": "c", "risk_parity_weight": 5.0, "strategy_id": "c"})(),
        ]

        rp = constructor._risk_parity_weights(scores)
        assert abs(sum(rp.values()) - 1.0) < 1e-10


class TestRegimeDeleveraging:
    """Regime-aware USDC floor computation."""

    def test_crisis_high_usdc_floor(self) -> None:
        """Crisis regime should dramatically increase USDC allocation."""
        constructor = KellyRiskParityConstructor()
        params = RISK_PROFILE_PARAMS[RiskProfile.MODERATE]

        risk_on_floor = constructor._compute_usdc_floor(_make_regime(Regime.RISK_ON), params)
        crisis_floor = constructor._compute_usdc_floor(_make_regime(Regime.CRISIS), params)

        assert crisis_floor > risk_on_floor
        assert crisis_floor > 0.5  # More than half in cash during crisis

    def test_risk_on_low_usdc_floor(self) -> None:
        """Risk-on regime should minimize cash allocation."""
        constructor = KellyRiskParityConstructor()
        params = RISK_PROFILE_PARAMS[RiskProfile.AGGRESSIVE]

        floor = constructor._compute_usdc_floor(_make_regime(Regime.RISK_ON), params)
        assert floor < 0.1  # Very low cash in risk-on + aggressive

    def test_conservative_profile_high_floor(self) -> None:
        """Conservative profile should have higher base floor."""
        constructor = KellyRiskParityConstructor()

        params_cons = RISK_PROFILE_PARAMS[RiskProfile.CONSERVATIVE]
        params_aggr = RISK_PROFILE_PARAMS[RiskProfile.AGGRESSIVE]

        cons_floor = constructor._compute_usdc_floor(_make_regime(Regime.TRANSITION), params_cons)
        aggr_floor = constructor._compute_usdc_floor(_make_regime(Regime.TRANSITION), params_aggr)

        assert cons_floor > aggr_floor

    def test_regime_deleverage_in_allocation(self) -> None:
        """Crisis regime should produce higher USDC weight than risk_on."""
        constructor = KellyRiskParityConstructor()
        strategy = _make_strategy(sharpe=1.5)
        portfolio = _make_portfolio()

        risk_on_allocs = constructor.construct(
            current=portfolio,
            strategies=[strategy],
            regime=_make_regime(Regime.RISK_ON),
            risk_profile=RiskProfile.MODERATE,
            usdc_address=_USDC_ADDR,
            synth_addresses=_SYNTH_ADDRS,
        )

        crisis_allocs = constructor.construct(
            current=portfolio,
            strategies=[strategy],
            regime=_make_regime(Regime.CRISIS),
            risk_profile=RiskProfile.MODERATE,
            usdc_address=_USDC_ADDR,
            synth_addresses=_SYNTH_ADDRS,
        )

        risk_on_usdc = next(a.weight for a in risk_on_allocs if a.symbol == "USDC")
        crisis_usdc = next(a.weight for a in crisis_allocs if a.symbol == "USDC")

        assert crisis_usdc > risk_on_usdc


class TestWeightConstraints:
    """Weight capping and normalization."""

    def test_single_asset_cap(self) -> None:
        """No single asset should exceed the cap."""
        constructor = KellyRiskParityConstructor()

        weights = {"sSPY": 0.6, "sTSLA": 0.3, "sNVDA": 0.1}
        capped = constructor._cap_weights(weights, 0.35)

        for w in capped.values():
            assert w <= 0.35 + 1e-6

    def test_cap_preserves_total(self) -> None:
        """Capping should preserve total weight."""
        constructor = KellyRiskParityConstructor()

        weights = {"a": 0.5, "b": 0.4, "c": 0.1}
        capped = constructor._cap_weights(weights, 0.35)

        assert abs(sum(capped.values()) - sum(weights.values())) < 1e-6

    def test_blend_normalizes(self) -> None:
        """Blended weights should normalize to 1.0."""
        constructor = KellyRiskParityConstructor()

        kelly = {"a": 0.7, "b": 0.3}
        rp = {"a": 0.4, "b": 0.6}

        blended = constructor._blend_weights(kelly, rp)
        assert abs(sum(blended.values()) - 1.0) < 1e-10


class TestScoreStrategy:
    """Strategy scoring."""

    def test_high_sharpe_high_score(self) -> None:
        """Higher Sharpe → higher score."""
        constructor = KellyRiskParityConstructor()
        strategy = _make_strategy(sharpe=2.0, cagr=0.30, max_dd=0.10)

        score = constructor.score_strategy(strategy, risk_profile=RiskProfile.MODERATE)
        assert score > 0.5

    def test_zero_sharpe_zero_score(self) -> None:
        """Zero Sharpe → zero score."""
        constructor = KellyRiskParityConstructor()
        strategy = _make_strategy(sharpe=0.0)

        score = constructor.score_strategy(strategy, risk_profile=RiskProfile.MODERATE)
        assert score == 0.0

    def test_backtest_result_overrides_stubs(self) -> None:
        """BacktestResult metrics should override stub metrics."""
        constructor = KellyRiskParityConstructor()
        strategy = _make_strategy(sharpe=0.5)

        # Stub score
        stub_score = constructor.score_strategy(strategy, risk_profile=RiskProfile.MODERATE)

        # With strong backtest result
        result = _make_backtest_result("test_strat", sharpe=2.5, max_dd=0.10)
        bt_score = constructor.score_strategy(strategy, result=result, risk_profile=RiskProfile.MODERATE)

        assert bt_score > stub_score


class TestTradeComputation:
    """Trade diff computation."""

    def test_no_trades_when_aligned(self) -> None:
        """No trades when current weights match targets."""
        constructor = KellyRiskParityConstructor()
        portfolio = _make_portfolio()

        targets = [
            TargetAllocation(symbol="USDC", token_address=_USDC_ADDR, weight=0.50),
            TargetAllocation(symbol="sSPY", token_address=_SYNTH_ADDRS["sSPY"], weight=0.25),
            TargetAllocation(symbol="sTSLA", token_address=_SYNTH_ADDRS["sTSLA"], weight=0.25),
        ]

        trades = constructor.compute_trades(portfolio, targets)
        assert len(trades) == 0

    def test_generates_trades_on_drift(self) -> None:
        """Generates trades when weights drift from targets."""
        constructor = KellyRiskParityConstructor()
        portfolio = _make_portfolio()

        targets = [
            TargetAllocation(symbol="USDC", token_address=_USDC_ADDR, weight=0.70),
            TargetAllocation(symbol="sSPY", token_address=_SYNTH_ADDRS["sSPY"], weight=0.15),
            TargetAllocation(symbol="sTSLA", token_address=_SYNTH_ADDRS["sTSLA"], weight=0.15),
        ]

        trades = constructor.compute_trades(portfolio, targets)
        assert len(trades) > 0

        # Should have a BUY for USDC (drift positive)
        usdc_trade = next((t for t in trades if t.symbol == "USDC"), None)
        assert usdc_trade is not None
        assert usdc_trade.direction == TradeDirection.BUY

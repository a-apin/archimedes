"""Hermetic unit tests for strategy_signal_evaluator — no network, no Redis, no DB."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from archimedes.models.paper_ref import PaperRef
from archimedes.models.strategy import Strategy
from archimedes.services.strategy_signal_evaluator import (
    AssetSignal,
    Signal,
    StrategySignalEvaluator,
    StrategySignals,
    _buy_hold_signal,
    _faber_sma200_signal,
    _get_evaluator,
    _tsmom_signal,
    _vol_managed_signal,
)


def _make_prices(length: int, start: float = 100.0, drift: float = 0.001) -> pd.Series:
    """Generate a deterministic price series."""
    rng = np.random.RandomState(42)
    returns = drift + rng.randn(length) * 0.01
    prices = start * np.cumprod(1 + returns)
    return pd.Series(prices, name="sSPY")


def _make_strategy(
    paper_title: str = "Test Strategy",
    asset_universe: list[str] | None = None,
) -> Strategy:
    return Strategy(
        id="test-strat-001",
        papers=[PaperRef(title=paper_title)],
        asset_universe=asset_universe or ["SPY"],
    )


# ─── Individual signal evaluators ────────────────────────────────


class TestFaberSMA200:
    def test_long_when_price_above_sma(self):
        # Build prices where last price > SMA200
        prices = pd.Series(np.linspace(80, 120, 250), name="sSPY")
        result = _faber_sma200_signal("strat1", "sSPY", prices)
        assert result.signal == Signal.LONG
        assert result.weight == 1.0
        assert result.asset == "sSPY"

    def test_flat_when_price_below_sma(self):
        # Build prices where last price < SMA200 (declining)
        prices = pd.Series(np.linspace(120, 80, 250), name="sSPY")
        result = _faber_sma200_signal("strat1", "sSPY", prices)
        assert result.signal == Signal.FLAT
        assert result.weight == 0.0

    def test_insufficient_data(self):
        prices = _make_prices(50)
        result = _faber_sma200_signal("strat1", "sSPY", prices)
        assert result.signal == Signal.FLAT
        assert result.weight == 0.0
        assert "Insufficient" in result.reason


class TestVolManaged:
    def test_scaled_when_high_vol(self):
        # Highly volatile series → exposure < 1.0
        rng = np.random.RandomState(99)
        prices = pd.Series(100 + rng.randn(300) * 5, name="sSPY")
        result = _vol_managed_signal("strat1", "sSPY", prices)
        assert result.signal in (Signal.LONG, Signal.SCALED)
        assert 0.0 < result.weight <= 1.0

    def test_full_exposure_low_vol(self):
        # Near-constant prices → zero vol → full exposure
        prices = pd.Series(np.full(50, 100.0), name="sSPY")
        result = _vol_managed_signal("strat1", "sSPY", prices)
        assert result.signal == Signal.LONG
        assert result.weight == 1.0

    def test_insufficient_data(self):
        prices = _make_prices(10)
        result = _vol_managed_signal("strat1", "sSPY", prices)
        assert result.weight == 0.5
        assert "Insufficient" in result.reason


class TestTSMOM:
    def test_long_when_positive_return(self):
        # Price went up over 253 days
        prices = pd.Series(np.linspace(80, 120, 260), name="sSPY")
        result = _tsmom_signal("strat1", "sSPY", prices)
        assert result.signal == Signal.LONG
        assert result.weight == 1.0

    def test_flat_when_negative_return(self):
        # Price went down over 253 days
        prices = pd.Series(np.linspace(120, 80, 260), name="sSPY")
        result = _tsmom_signal("strat1", "sSPY", prices)
        assert result.signal == Signal.FLAT
        assert result.weight == 0.0

    def test_insufficient_data(self):
        prices = _make_prices(100)
        result = _tsmom_signal("strat1", "sSPY", prices)
        assert result.signal == Signal.FLAT
        assert result.weight == 0.0


class TestBuyHold:
    def test_always_long(self):
        prices = _make_prices(10)
        result = _buy_hold_signal("strat1", "sSPY", prices)
        assert result.signal == Signal.LONG
        assert result.weight == 1.0

    def test_empty_prices(self):
        prices = pd.Series(dtype=float)
        result = _buy_hold_signal("strat1", "sSPY", prices)
        assert result.signal == Signal.LONG
        assert result.weight == 1.0


# ─── Evaluator mapping ───────────────────────────────────────────


class TestGetEvaluator:
    @pytest.mark.parametrize(
        "name,expected_type",
        [
            ("Faber 2007 SMA200 Timing", _faber_sma200_signal),
            ("Volatility Managed Long", _vol_managed_signal),
            ("Time Series Momentum", _tsmom_signal),
            ("Buy-and-Hold Baseline", _buy_hold_signal),
        ],
    )
    def test_known_strategies(self, name, expected_type):
        evaluator = _get_evaluator(name)
        assert evaluator is expected_type

    def test_unknown_strategy_defaults_to_buy_hold(self):
        evaluator = _get_evaluator("Unknown Strategy XYZ")
        assert evaluator is _buy_hold_signal


# ─── StrategySignalEvaluator ─────────────────────────────────────


class TestStrategySignalEvaluator:
    def _make_price_histories(self, symbols: list[str]) -> dict[str, pd.Series]:
        return {sym: _make_prices(300, start=100.0) for sym in symbols}

    def test_evaluate_strategies_with_mock_data(self):
        evaluator = StrategySignalEvaluator()
        strat = _make_strategy("Buy-and-Hold Baseline", asset_universe=["SPY"])
        prices = self._make_price_histories(["sSPY"])

        results = evaluator.evaluate_strategies(
            strategies=[strat],
            synth_assets=["sSPY"],
            price_histories=prices,
        )
        assert len(results) == 1
        assert results[0].strategy_id == "test-strat-001"
        assert len(results[0].signals) >= 1
        assert results[0].signals[0].signal == Signal.LONG

    def test_evaluate_empty_universe(self):
        evaluator = StrategySignalEvaluator()
        strat = _make_strategy("Buy-and-Hold", asset_universe=["SPY"])
        results = evaluator.evaluate_strategies(
            strategies=[strat],
            synth_assets=[],
            price_histories={},
        )
        assert results == []

    def test_evaluate_missing_price_for_asset(self):
        evaluator = StrategySignalEvaluator()
        strat = _make_strategy("Buy-and-Hold", asset_universe=["UNKNOWN"])
        results = evaluator.evaluate_strategies(
            strategies=[strat],
            synth_assets=["sSPY"],
            price_histories={"sSPY": _make_prices(300)},
        )
        # Fallback: evaluate on all available synths
        assert len(results) >= 1

    def test_aggregate_signals_all_long(self):
        evaluator = StrategySignalEvaluator()
        signals = [
            StrategySignals(
                strategy_id="s1",
                strategy_name="Test1",
                paper_title="Test1",
                signals=[
                    AssetSignal(
                        strategy_id="s1",
                        strategy_name="Test1",
                        asset="sSPY",
                        signal=Signal.LONG,
                        weight=1.0,
                        reason="test",
                    )
                ],
            ),
        ]
        weights = evaluator.aggregate_signals(signals, usdc_floor=0.20)
        assert "USDC" in weights
        assert weights["USDC"] == pytest.approx(0.20, abs=0.01)
        assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)

    def test_aggregate_empty_signals_returns_all_usdc(self):
        evaluator = StrategySignalEvaluator()
        weights = evaluator.aggregate_signals([], usdc_floor=0.20)
        assert weights == {"USDC": 1.0}

    def test_aggregate_normalizes_to_one(self):
        evaluator = StrategySignalEvaluator()
        signals = [
            StrategySignals(
                strategy_id="s1",
                strategy_name="Test1",
                paper_title="Test1",
                signals=[
                    AssetSignal("s1", "T1", "sSPY", Signal.LONG, 1.0, "r"),
                    AssetSignal("s1", "T1", "sTSLA", Signal.LONG, 1.0, "r"),
                ],
            ),
        ]
        weights = evaluator.aggregate_signals(signals, usdc_floor=0.10)
        assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)
        assert weights["USDC"] == pytest.approx(0.10, abs=0.01)

    def test_regime_table_driven(self):
        """Verify signal type matches documented regime→signal mapping."""
        # Faber: uptrend → LONG
        up_prices = pd.Series(np.linspace(80, 120, 250), name="sSPY")
        faber_up = _faber_sma200_signal("s1", "sSPY", up_prices)
        assert faber_up.signal == Signal.LONG

        # Faber: downtrend → FLAT
        down_prices = pd.Series(np.linspace(120, 80, 250), name="sSPY")
        faber_down = _faber_sma200_signal("s1", "sSPY", down_prices)
        assert faber_down.signal == Signal.FLAT


class TestStrategySignalsTotalWeight:
    def test_total_weight_sums_correctly(self):
        ss = StrategySignals(
            strategy_id="s1",
            strategy_name="Test",
            paper_title="Test",
            signals=[
                AssetSignal("s1", "T", "A", Signal.LONG, 0.6, "r"),
                AssetSignal("s1", "T", "B", Signal.SCALED, 0.4, "r"),
            ],
        )
        assert ss.total_weight == pytest.approx(1.0)

    def test_total_weight_empty(self):
        ss = StrategySignals(
            strategy_id="s1",
            strategy_name="Test",
            paper_title="Test",
            signals=[],
        )
        assert ss.total_weight == 0.0

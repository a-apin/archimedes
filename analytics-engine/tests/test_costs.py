"""Tests for the transaction-cost + turnover model (costs.py + engine wiring).

Hermetic: all data is synthetic, no network. Validates the cost accounting
against hand-computed expectations, the gross-vs-net invariants, per-symbol
cost overrides, and the no-trade-band turnover penalty.
"""

from __future__ import annotations

import math

import backtrader as bt
import pandas as pd
import pytest
from archimedes_analytics_engine.costs import (
    DEFAULT_COST_MODEL,
    CostModel,
    cost_model_fingerprint,
    no_trade_band,
    position_weight,
)
from archimedes_analytics_engine.engine import run_backtest, run_multi_backtest


def _flat_prices(periods: int, price: float = 100.0, start: str = "2020-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq="D")
    return pd.DataFrame(
        {
            "Open": [price] * periods,
            "High": [price] * periods,
            "Low": [price] * periods,
            "Close": [price] * periods,
            "Volume": [1_000] * periods,
        },
        index=idx,
    )


def _wavy_prices(
    periods: int, start: str = "2020-01-01", base: float = 100.0, drift: float = 0.1, phase: float = 0.0
) -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq="D")
    closes = [base + drift * i + 5.0 * math.sin(i / 7.0 + phase) for i in range(periods)]
    return pd.DataFrame(
        {
            "Open": closes,  # Open == Close so executions happen at known prices
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [1_000] * periods,
        },
        index=idx,
    )


class _OneRoundTrip(bt.Strategy):
    """Buy 50 shares on bar 2, sell them on bar 10. Two known executions."""

    def next(self) -> None:
        if len(self) == 2:
            self.buy(size=50)
        elif len(self) == 10:
            self.sell(size=50)


class _TradeEveryBar(bt.Strategy):
    """Alternate between 30% and 70% exposure every bar — deliberately churny."""

    def next(self) -> None:
        target = 0.3 if len(self) % 2 == 0 else 0.7
        self.order_target_percent(target=target)


class _SmaFlipper(bt.Strategy):
    """Has a real warm-up period (10-bar SMA) and trades on crossings."""

    def __init__(self) -> None:
        self.sma = bt.indicators.SMA(self.data.close, period=10)

    def next(self) -> None:
        if self.data.close[0] > self.sma[0] and not self.position:
            self.order_target_percent(target=0.9)
        elif self.data.close[0] < self.sma[0] and self.position:
            self.order_target_percent(target=0.0)


class _BandedRebalancer(bt.Strategy):
    """Chases a noisy target weight (0.500 <-> 0.504) through no_trade_band."""

    params = (("band", 0.0),)

    def next(self) -> None:
        target = 0.500 if len(self) % 2 == 0 else 0.504
        current = position_weight(self, self.data)
        filtered = no_trade_band(current, target, band=self.params.band)
        if filtered != current:
            self.order_target_percent(target=filtered)


class _TightBandedRebalancer(_BandedRebalancer):
    params = (("band", 0.02),)


# ── CostModel unit behaviour ──────────────────────────────────────────────────


def test_cost_model_per_symbol_resolution() -> None:
    model = CostModel(default_bps=10.0, per_symbol={"EEM": 25.0})
    assert model.per_side_bps("EEM") == 25.0
    assert model.per_side_bps("SPY") == 10.0
    assert model.per_side_bps() == 10.0


def test_cost_model_rejects_negative_bps() -> None:
    with pytest.raises(ValueError, match="default_bps"):
        CostModel(default_bps=-1.0)
    with pytest.raises(ValueError, match="slippage_bps"):
        CostModel(slippage_bps=-1.0)
    with pytest.raises(ValueError, match="per_symbol"):
        CostModel(per_symbol={"SPY": -5.0})


def test_no_trade_band_scalar_behaviour() -> None:
    assert no_trade_band(0.250, 0.254, band=0.005) == 0.250  # inside band: hold
    assert no_trade_band(0.250, 0.260, band=0.005) == 0.260  # outside band: trade
    assert no_trade_band(0.250, 0.260, band=0.0) == 0.260  # zero band: always trade
    with pytest.raises(ValueError, match="band"):
        no_trade_band(0.25, 0.26, band=-0.1)


# ── Turnover accounting (hand-computed expectations) ─────────────────────────


def test_turnover_accounting_one_round_trip() -> None:
    # Flat prices at 100: both executions at exactly 100. Two-way notional =
    # 50 shares x 100 x 2 sides = 10_000; commission at 10 bps/side = 10.0.
    result = run_backtest(
        _flat_prices(20),
        strategy_cls=_OneRoundTrip,
        initial_cash=100_000.0,
        transaction_cost_bps=10,
    )

    assert result.traded_notional == pytest.approx(10_000.0)
    assert result.total_commission_paid == pytest.approx(10.0)

    # One-way annualized turnover = (10_000 / 2) / avg_equity / (20/252).
    years = 20 / 252
    expected_turnover = (10_000.0 / 2.0) / 100_000.0 / years
    assert result.turnover_annualized == pytest.approx(expected_turnover, rel=1e-3)

    # Flat prices: the only P&L is commission, so gross return is ~0 and the
    # break-even cost (the per-side bps a zero-alpha strategy can afford) is ~0.
    assert result.break_even_cost_bps == pytest.approx(0.0, abs=0.5)


def test_no_trades_means_zero_turnover() -> None:
    class _Sleeper(bt.Strategy):
        def next(self) -> None:
            pass

    result = run_backtest(_wavy_prices(30), strategy_cls=_Sleeper, initial_cash=100_000.0)
    assert result.traded_notional == 0.0
    assert result.total_commission_paid == 0.0
    assert result.turnover_annualized == 0.0
    assert result.cost_drag_annual_pct == 0.0
    assert result.break_even_cost_bps is None  # no turnover -> undefined


# ── Gross-vs-net invariants ───────────────────────────────────────────────────


def test_zero_cost_gross_sharpe_matches_net() -> None:
    result = run_backtest(
        _wavy_prices(120),
        strategy_cls=_TradeEveryBar,
        initial_cash=100_000.0,
        transaction_cost_bps=0,
    )
    assert result.total_commission_paid == 0.0
    assert result.sharpe_ratio is not None
    assert result.gross_sharpe_ratio is not None
    # Zero commissions: gross return series == net return series, and the
    # gross Sharpe replicates the bt.analyzers.SharpeRatio convention exactly.
    assert result.gross_sharpe_ratio == pytest.approx(result.sharpe_ratio, rel=1e-6)


def test_costs_only_hurt() -> None:
    free = run_backtest(_wavy_prices(120), strategy_cls=_TradeEveryBar, initial_cash=100_000.0, transaction_cost_bps=0)
    costly = run_backtest(
        _wavy_prices(120), strategy_cls=_TradeEveryBar, initial_cash=100_000.0, transaction_cost_bps=50
    )
    assert costly.final_value < free.final_value
    assert costly.total_commission_paid > 0
    assert costly.cost_drag_annual_pct is not None and costly.cost_drag_annual_pct > 0
    assert costly.gross_sharpe_ratio is not None and costly.sharpe_ratio is not None
    assert costly.gross_sharpe_ratio > costly.sharpe_ratio


def test_gross_metrics_survive_strategy_warm_up_period() -> None:
    # _SmaFlipper has a 10-bar minimum period; the per-bar commission series
    # must stay positionally aligned with the daily-return series regardless.
    result = run_backtest(
        _wavy_prices(150),
        strategy_cls=_SmaFlipper,
        initial_cash=100_000.0,
        transaction_cost_bps=10,
    )
    assert result.total_trades > 0
    assert result.total_commission_paid > 0
    assert result.gross_sharpe_ratio is not None
    assert result.turnover_annualized is not None and result.turnover_annualized > 0


# ── CostModel through the runners ─────────────────────────────────────────────


def test_cost_model_flat_default_matches_legacy_bps() -> None:
    frames = [_wavy_prices(60, base=100.0), _wavy_prices(60, base=80.0, phase=1.0)]

    class _EqualWeight(bt.Strategy):
        def next(self) -> None:
            if len(self) != 3:
                return
            for d in self.datas:
                self.order_target_percent(data=d, target=0.45)

    legacy = run_multi_backtest(frames, strategy_cls=_EqualWeight, initial_cash=100_000.0, transaction_cost_bps=10)
    modeled = run_multi_backtest(
        frames,
        strategy_cls=_EqualWeight,
        initial_cash=100_000.0,
        names=["SPY", "GLD"],
        cost_model=CostModel(default_bps=10.0),
    )
    assert modeled.final_value == pytest.approx(legacy.final_value)
    assert modeled.transaction_cost_bps == 10


def test_cost_model_per_symbol_override_charges_more() -> None:
    frames = [_wavy_prices(60, base=100.0), _wavy_prices(60, base=80.0, phase=1.0)]

    class _EqualWeight(bt.Strategy):
        def next(self) -> None:
            if len(self) != 3:
                return
            for d in self.datas:
                self.order_target_percent(data=d, target=0.45)

    cheap = run_multi_backtest(
        frames,
        strategy_cls=_EqualWeight,
        initial_cash=100_000.0,
        names=["SPY", "EEM"],
        cost_model=CostModel(default_bps=10.0),
    )
    expensive = run_multi_backtest(
        frames,
        strategy_cls=_EqualWeight,
        initial_cash=100_000.0,
        names=["SPY", "EEM"],
        cost_model=CostModel(default_bps=10.0, per_symbol={"EEM": 200.0}),
    )
    assert expensive.total_commission_paid > cheap.total_commission_paid
    assert expensive.final_value < cheap.final_value


# ── Turnover penalty in a live strategy ───────────────────────────────────────


def test_no_trade_band_reduces_turnover_in_strategy() -> None:
    prices = _wavy_prices(80)
    unbanded = run_backtest(prices, strategy_cls=_BandedRebalancer, initial_cash=100_000.0)
    banded = run_backtest(prices, strategy_cls=_TightBandedRebalancer, initial_cash=100_000.0)
    assert banded.traded_notional < unbanded.traded_notional
    assert banded.total_commission_paid < unbanded.total_commission_paid


# ── Cost SSOT: the floor every engine charges ─────────────────────────────────
#
# Three engines write into one backtest_results table and one gate grades them
# without knowing which produced a row. Before DEFAULT_COST_MODEL existed, this
# engine charged commission AND slippage while the two backend engines charged
# commission only, so the path that grades *generated* strategies flattered
# itself against the curated library it is ranked beside.


def test_default_cost_model_charges_both_legs() -> None:
    """A zero slippage leg is the exact defect this constant closed."""
    assert DEFAULT_COST_MODEL.default_bps > 0
    assert DEFAULT_COST_MODEL.slippage_bps > 0


def test_apply_to_broker_installs_commission_and_slippage() -> None:
    """Both legs reach the broker when an engine goes through the shared model.

    grep for set_slippage returned exactly two hits before this work, both in
    this engine. Pinning it here means an engine that adopts the model cannot
    silently end up with commission only.
    """
    calls: list[str] = []

    class _Broker:
        def setcommission(self, **_kwargs: object) -> None:
            calls.append("commission")

        def set_slippage_perc(self, **_kwargs: object) -> None:
            calls.append("slippage")

    class _Cerebro:
        def __init__(self) -> None:
            self.broker = _Broker()

    cerebro = _Cerebro()
    DEFAULT_COST_MODEL.apply_to_broker(cerebro, ["SPY"])
    assert calls.count("commission") >= 1
    assert calls.count("slippage") == 1


def test_zero_cost_model_installs_no_slippage() -> None:
    """The zero-cost model stays zero-cost so the regression baseline holds."""
    calls: list[str] = []

    class _Broker:
        def setcommission(self, **_kwargs: object) -> None:
            calls.append("commission")

        def set_slippage_perc(self, **_kwargs: object) -> None:
            calls.append("slippage")

    class _Cerebro:
        def __init__(self) -> None:
            self.broker = _Broker()

    cerebro = _Cerebro()
    CostModel(default_bps=0.0, slippage_bps=0.0).apply_to_broker(cerebro, ["SPY"])
    assert "slippage" not in calls


def test_fingerprint_is_stable_across_dict_ordering() -> None:
    a = CostModel(default_bps=10, slippage_bps=5, per_symbol={"SPY": 2.0, "BIL": 1.0})
    b = CostModel(default_bps=10, slippage_bps=5, per_symbol={"BIL": 1.0, "SPY": 2.0})
    assert cost_model_fingerprint(a) == cost_model_fingerprint(b)


def test_fingerprint_differs_when_costs_differ() -> None:
    base = CostModel(default_bps=10, slippage_bps=5)
    assert cost_model_fingerprint(base) != cost_model_fingerprint(CostModel(default_bps=10, slippage_bps=0))
    assert cost_model_fingerprint(base) != cost_model_fingerprint(CostModel(default_bps=20, slippage_bps=5))
    assert cost_model_fingerprint(base) != cost_model_fingerprint(
        CostModel(default_bps=10, slippage_bps=5, per_symbol={"SPY": 2.0})
    )


def test_costed_run_is_strictly_below_zero_cost_run() -> None:
    """The floor has to actually cost something on an identical trade list."""
    prices = _wavy_prices(120)
    free = run_backtest(
        prices,
        strategy_cls=_BandedRebalancer,
        initial_cash=100_000.0,
        cost_model=CostModel(default_bps=0.0, slippage_bps=0.0),
    )
    costed = run_backtest(
        prices,
        strategy_cls=_BandedRebalancer,
        initial_cash=100_000.0,
        cost_model=DEFAULT_COST_MODEL,
    )
    assert costed.final_value < free.final_value


def test_slippage_is_a_separable_leg() -> None:
    """Slippage must bite on its own, not be folded into the commission."""
    prices = _wavy_prices(120)
    commission_only = run_backtest(
        prices,
        strategy_cls=_BandedRebalancer,
        initial_cash=100_000.0,
        cost_model=CostModel(default_bps=DEFAULT_COST_MODEL.default_bps, slippage_bps=0.0),
    )
    both_legs = run_backtest(
        prices,
        strategy_cls=_BandedRebalancer,
        initial_cash=100_000.0,
        cost_model=DEFAULT_COST_MODEL,
    )
    assert both_legs.final_value < commission_only.final_value

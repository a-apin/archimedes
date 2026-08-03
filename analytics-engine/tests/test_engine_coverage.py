"""Coverage tests for engine.py metric-helper functions and edge cases.

Hermetic: all data is synthetic in-memory pandas frames, no network, no
yfinance. These exercise the pure helper functions directly (the existing
test_engine.py only drives the full run_buy_and_hold path) plus a few
edge-case engine runs (always-flat strategy, single-bar feed).
"""

from __future__ import annotations

import math
import statistics

import backtrader as bt
import pandas as pd
import pytest
from archimedes_analytics_engine.engine import (
    ANNUALIZATION,
    RF_ANNUAL,
    RF_DAILY,
    BacktestResult,
    BuyAndHoldStrategy,
    _build_equity_curve,
    _compute_cagr,
    _compute_sortino,
    _max_drawdown_from_curve,
    _safe_get,
    _sharpe_bt_convention,
    _trade_stats,
    combine_universe_results,
    run_backtest,
    run_buy_and_hold,
)


def _flat_prices(periods: int, price: float = 100.0, start: str = "2021-01-01") -> pd.DataFrame:
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


# ── _compute_sortino ──────────────────────────────────────────────────────────


def test_sortino_empty_list_returns_none() -> None:
    assert _compute_sortino([]) is None


def test_sortino_all_positive_returns_none() -> None:
    # No downside returns at all → denominator undefined → None.
    assert _compute_sortino([0.01, 0.02, 0.005, 0.03]) is None


def test_sortino_all_negative_is_finite_and_negative() -> None:
    returns = [-0.01, -0.02, -0.015]
    result = _compute_sortino(returns)
    assert result is not None
    # mean is below the daily risk-free rate, so the ratio is negative.
    assert result < 0


def test_sortino_rms_of_negatives_convention() -> None:
    # Verify the downside-deviation denominator convention explicitly.
    # Updated for corrected Sortino denominator: divides by the TOTAL sample
    # size (len(returns)), not just the count of negative days, per the
    # standard Sortino/target-semideviation convention (e.g.
    # empyrical.downside_risk) — bug M1 fix.
    returns = [0.05, -0.02, 0.03, -0.04]
    downside = [r for r in returns if r < 0]
    dd_rms = math.sqrt(sum(r * r for r in downside) / len(returns))
    mean = statistics.fmean(returns)
    expected = ((mean - RF_DAILY) / dd_rms) * math.sqrt(ANNUALIZATION)
    assert _compute_sortino(returns) == pytest.approx(expected)


def test_sortino_single_negative_observation() -> None:
    # One downside observation: dd_rms == |that return|; not None, not zero.
    result = _compute_sortino([0.10, -0.05])
    assert result is not None


# ── _compute_cagr ─────────────────────────────────────────────────────────────


def test_cagr_zero_or_negative_initial_returns_none() -> None:
    assert _compute_cagr(0.0, 100.0, 252) is None
    assert _compute_cagr(-10.0, 100.0, 252) is None


def test_cagr_nonpositive_final_returns_none() -> None:
    assert _compute_cagr(100.0, 0.0, 252) is None
    assert _compute_cagr(100.0, -5.0, 252) is None


def test_cagr_zero_bars_returns_none() -> None:
    assert _compute_cagr(100.0, 200.0, 0) is None


def test_cagr_one_year_doubling() -> None:
    # Exactly one year (252 bars), doubling → CAGR == 1.0 (100%).
    assert _compute_cagr(100.0, 200.0, ANNUALIZATION) == pytest.approx(1.0)


def test_cagr_multi_year_annualization() -> None:
    # 2 years, 4x growth → annualized rate is 2x - 1 = 1.0.
    result = _compute_cagr(100.0, 400.0, 2 * ANNUALIZATION)
    assert result == pytest.approx(1.0)


# ── _sharpe_bt_convention ─────────────────────────────────────────────────────


def test_sharpe_bt_convention_too_short_returns_none() -> None:
    assert _sharpe_bt_convention([]) is None
    assert _sharpe_bt_convention([0.01]) is None


def test_sharpe_bt_convention_constant_returns_none() -> None:
    # Constant series → zero population stddev → None.
    assert _sharpe_bt_convention([0.01, 0.01, 0.01, 0.01]) is None


def test_sharpe_bt_convention_normal_series_matches_formula() -> None:
    returns = [0.01, -0.005, 0.02, 0.0, 0.015, -0.01]
    rf_daily_geo = (1.0 + RF_ANNUAL) ** (1.0 / ANNUALIZATION) - 1.0
    excess = [r - rf_daily_geo for r in returns]
    expected = (statistics.fmean(excess) / statistics.pstdev(excess)) * math.sqrt(ANNUALIZATION)
    assert _sharpe_bt_convention(returns) == pytest.approx(expected)


# ── _build_equity_curve ───────────────────────────────────────────────────────


def test_build_equity_curve_empty_pairs_is_just_initial() -> None:
    assert _build_equity_curve(10_000.0, []) == [10_000.0]


def test_build_equity_curve_compounds_daily_pairs() -> None:
    # (date, return) pairs — dates are ignored by the builder.
    pairs = [("2021-01-01", 0.10), ("2021-01-02", -0.05), ("2021-01-03", 0.20)]
    curve = _build_equity_curve(1_000.0, pairs)
    assert curve[0] == pytest.approx(1_000.0)
    assert curve[1] == pytest.approx(1_100.0)
    assert curve[2] == pytest.approx(1_100.0 * 0.95)
    assert curve[3] == pytest.approx(1_100.0 * 0.95 * 1.20)
    assert len(curve) == 4


# ── _safe_get ─────────────────────────────────────────────────────────────────


def test_safe_get_nested_hit_and_misses() -> None:
    d = {"a": {"b": {"c": 42}}}
    assert _safe_get(d, "a", "b", "c") == 42
    assert _safe_get(d, "a", "x", default="fallback") == "fallback"
    # Traversal hits a non-dict before exhausting keys → default.
    assert _safe_get(d, "a", "b", "c", "d", default=None) is None
    assert _safe_get("not-a-dict", "a", default=7) == 7


# ── _trade_stats ──────────────────────────────────────────────────────────────


def test_trade_stats_no_closed_trades() -> None:
    assert _trade_stats({}) == (0, None, None, None)
    assert _trade_stats({"total": {"closed": 0}}) == (0, None, None, None)


def test_trade_stats_computes_win_rate_and_profit_factor() -> None:
    trade = {
        "total": {"closed": 4},
        "won": {"total": 3, "pnl": {"total": 300.0}},
        "lost": {"pnl": {"total": -100.0}},
        "len": {"average": 5.5},
    }
    total, win_rate, profit_factor, avg_len = _trade_stats(trade)
    assert total == 4
    assert win_rate == pytest.approx(0.75)
    assert profit_factor == pytest.approx(3.0)
    assert avg_len == pytest.approx(5.5)


def test_trade_stats_profit_factor_none_when_no_losses() -> None:
    # lost_pnl == 0 → profit_factor undefined → None (avoids div-by-zero).
    trade = {
        "total": {"closed": 2},
        "won": {"total": 2, "pnl": {"total": 50.0}},
        "lost": {"pnl": {"total": 0.0}},
    }
    total, win_rate, profit_factor, avg_len = _trade_stats(trade)
    assert total == 2
    assert win_rate == pytest.approx(1.0)
    assert profit_factor is None
    assert avg_len is None


# ── BacktestResult dataclass ──────────────────────────────────────────────────


def test_backtest_result_defaults_and_construction() -> None:
    result = BacktestResult(
        final_value=12_000.0,
        total_return_pct=20.0,
        equity_curve=[10_000.0, 12_000.0],
    )
    # Defaulted fields.
    assert result.sharpe_ratio is None
    assert result.total_trades == 0
    assert result.transaction_cost_bps == 10
    assert result.slippage_bps == 0
    assert result.look_ahead_audit_passed is False
    assert result.backtest_engine == "backtrader"
    assert result.monthly_returns == []
    assert result.daily_returns == []
    assert result.daily_return_dates == []
    # Mutable defaults are independent instances, not shared.
    other = BacktestResult(final_value=1.0, total_return_pct=0.0, equity_curve=[])
    result.daily_returns.append(0.01)
    assert other.daily_returns == []


# ── Engine edge cases ─────────────────────────────────────────────────────────


class _NeverTrades(bt.Strategy):
    """An always-flat strategy: never opens a position."""

    def next(self) -> None:
        pass


def test_always_flat_strategy_preserves_capital() -> None:
    result = run_backtest(
        _flat_prices(30),
        strategy_cls=_NeverTrades,
        initial_cash=10_000.0,
    )
    assert result.look_ahead_audit_passed is True
    assert result.final_value == pytest.approx(10_000.0)
    assert result.total_trades == 0
    assert result.traded_notional == 0.0
    assert result.total_commission_paid == 0.0


def test_single_bar_buy_and_hold_runs() -> None:
    # A single-bar feed: buy-and-hold cannot complete a trade but must still
    # return a well-formed BacktestResult with sane scalar fields.
    result = run_buy_and_hold(_flat_prices(1), initial_cash=5_000.0)
    assert isinstance(result, BacktestResult)
    assert result.bars == 1
    assert result.final_value > 0
    assert result.backtest_start is not None
    assert result.backtest_end is not None


def test_buy_and_hold_deploys_capital_on_rising_feed() -> None:
    idx = pd.date_range("2021-01-01", periods=12, freq="D")
    closes = [100.0 + 3.0 * i for i in range(12)]
    # Open below Close so the next-bar entry fits in cash net of commission
    # (the BuyAndHold sizing uses close[0] but fills at the next bar's open).
    prices = pd.DataFrame(
        {
            "Open": [c - 2 for c in closes],
            "High": [c + 1 for c in closes],
            "Low": [c - 3 for c in closes],
            "Close": closes,
            "Volume": [1_000] * 12,
        },
        index=idx,
    )
    result = run_buy_and_hold(prices, initial_cash=10_000.0)
    # A rising buy-and-hold should grow capital and record the entry notional.
    assert result.final_value > 10_000.0
    assert result.total_return_pct > 0
    assert result.traded_notional > 0.0
    assert len(result.equity_curve) == result.bars + 1


# ── _max_drawdown_from_curve duration semantics ─────────────────────────────
#
# Regression coverage: the duration counter must reset whenever the CURRENT
# bar sits exactly AT the running peak (drawdown == 0), matching backtrader's
# own DrawDown analyzer (`r.len = r.len + 1 if drawdown else 0`) — not only on
# a STRICTLY NEW all-time high. The old `value > peak` reset condition left a
# bar that merely equals the prior peak still counted as "in drawdown".


def test_max_drawdown_from_curve_duration_resets_when_curve_retouches_peak() -> None:
    """Hand-verified against backtrader's own reset rule. The curve dips from
    its opening peak (100), partially recovers, RETOUCHES that same peak
    (100) without ever exceeding it, dips again, retouches it a second time,
    then finally breaks to a genuine new high (110).

    Correct (backtrader) semantics: the counter resets to 0 every time the
    curve is back at 100 (drawdown == 0), so the longest streak is the
    3-bar stretch [90, 80, 90] before the first retouch — max_len == 3.

    The pre-fix implementation only reset on a STRICTLY new high
    (`value > peak`), so retouching a prior peak without exceeding it did NOT
    reset the counter: it kept incrementing through both retouches and every
    down-day in between, hand-computed to max_len == 8 on this exact curve —
    proving this test fails without the fix.
    """
    curve = [100, 90, 80, 90, 100, 95, 90, 100, 110, 100, 95]

    max_dd, max_len = _max_drawdown_from_curve(curve)

    assert max_dd == pytest.approx(20.0)  # peak-to-trough 100 -> 80, unaffected by the duration bug
    assert max_len == 3
    # The bug this pins: the old `value > peak`-only reset would have produced 8 here.
    assert max_len != 8


def test_max_drawdown_from_curve_duration_matches_backtrader_dd_analyzer() -> None:
    """Cross-validate the reimplementation against a REAL cerebro run's own
    ``bt.analyzers.DrawDown`` output (the ground truth _max_drawdown_from_curve
    is standing in for, per its own docstring) via the single-asset composite
    identity: ``combine_universe_results`` on a ONE-constituent list must
    reproduce ``max_drawdown_duration_bars`` exactly, not just the equity
    curve and daily returns the pre-existing composite test already pins.
    """
    idx = pd.date_range("2022-01-01", periods=11, freq="D")
    # Same shape as the hand-verified curve above (dip, partial recovery
    # retouching the open, dip again, retouch again, then a genuine new high)
    # expressed as a price series so a REAL BuyAndHold cerebro run exercises
    # backtrader's own DrawDown analyzer on it.
    closes = [100.0, 100.0, 90.0, 80.0, 90.0, 100.0, 95.0, 90.0, 100.0, 110.0, 100.0]
    prices = pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": closes,
            "Volume": [1_000] * len(closes),
        },
        index=idx,
    )

    solo = run_backtest(prices, strategy_cls=BuyAndHoldStrategy, initial_cash=10_000.0, transaction_cost_bps=0)
    assert solo.max_drawdown_duration_bars is not None  # the curve does draw down; guard against a vacuous pass

    composite = combine_universe_results([("SOLO", solo)], initial_cash=10_000.0)

    assert composite.max_drawdown_duration_bars == solo.max_drawdown_duration_bars

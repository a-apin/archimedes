"""Tests for the N-asset (cross-sectional / portfolio) backtest runner —
engine.run_multi_backtest.

Hermetic: all data is synthetic, no network. Validates the N-feed plumbing,
N-way date alignment, and metric extraction shared with the single- and
two-asset runners. Mirrors test_pairs_engine.py.
"""

from __future__ import annotations

import logging
import math

import backtrader as bt
import pandas as pd
import pytest
from archimedes_analytics_engine.engine import BacktestResult, run_multi_backtest


def _synthetic_prices(
    periods: int, start: str = "2020-01-01", base: float = 100.0, drift: float = 0.1, phase: float = 0.0
) -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq="D")
    closes = [base + drift * i + 5.0 * math.sin(i / 7.0 + phase) for i in range(periods)]
    return pd.DataFrame(
        {
            "Open": [c - 0.5 for c in closes],
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [1_000] * periods,
        },
        index=idx,
    )


class _EqualWeightStrategy(bt.Strategy):
    """Minimal N-feed strategy: equal-weight all feeds on the first eligible bar."""

    def next(self) -> None:
        if len(self) != 5:
            return
        weight = 0.9 / len(self.datas)
        equity = float(self.broker.getvalue())
        for data in self.datas:
            size = int((equity * weight) // float(data.close[0]))
            if size > 0:
                self.order_target_size(data=data, target=size)


def test_run_multi_backtest_returns_result() -> None:
    frames = [_synthetic_prices(40, base=b, drift=0.05, phase=i) for i, b in enumerate([100.0, 50.0, 75.0, 120.0])]
    result = run_multi_backtest(frames, strategy_cls=_EqualWeightStrategy, initial_cash=100_000.0)

    assert isinstance(result, BacktestResult)
    assert result.final_value > 0
    assert result.bars == 40
    assert result.look_ahead_audit_passed is True
    assert isinstance(result.daily_returns, list)
    assert len(result.daily_returns) > 0


def test_run_multi_backtest_aligns_on_common_dates() -> None:
    # Feeds start on staggered dates → only the fully-overlapping window is backtested.
    frames = [
        _synthetic_prices(40, start="2020-01-01", base=100.0),
        _synthetic_prices(40, start="2020-01-06", base=50.0),
        _synthetic_prices(40, start="2020-01-11", base=75.0),
    ]
    result = run_multi_backtest(frames, strategy_cls=_EqualWeightStrategy, initial_cash=100_000.0)

    common = frames[0].index
    for f in frames[1:]:
        common = common.intersection(f.index)
    assert result.bars == len(common)
    # 40 bars, latest start is +10 days → 30 overlapping bars.
    assert result.bars == 30


def test_run_multi_backtest_raises_on_disjoint_dates() -> None:
    frames = [
        _synthetic_prices(20, start="2020-01-01"),
        _synthetic_prices(20, start="2020-01-01", base=50.0),
        _synthetic_prices(20, start="2022-01-01", base=75.0),  # disjoint from the others
    ]
    with pytest.raises(ValueError, match="no common dates"):
        run_multi_backtest(frames, strategy_cls=_EqualWeightStrategy, initial_cash=100_000.0)


def test_run_multi_backtest_raises_on_empty_list() -> None:
    with pytest.raises(ValueError, match="empty"):
        run_multi_backtest([], strategy_cls=_EqualWeightStrategy, initial_cash=100_000.0)


def test_run_multi_backtest_raises_on_names_length_mismatch() -> None:
    frames = [_synthetic_prices(20), _synthetic_prices(20, base=50.0)]
    with pytest.raises(ValueError, match="names has"):
        run_multi_backtest(frames, strategy_cls=_EqualWeightStrategy, initial_cash=100_000.0, names=["only_one"])


def test_run_multi_backtest_honors_named_feeds() -> None:
    frames = [_synthetic_prices(30, base=100.0), _synthetic_prices(30, base=50.0)]
    # Named feeds must run and produce a valid result (names are passthrough labels).
    result = run_multi_backtest(frames, strategy_cls=_EqualWeightStrategy, initial_cash=100_000.0, names=["SPY", "GLD"])
    assert isinstance(result, BacktestResult)
    assert result.bars == 30


def test_run_multi_backtest_warns_loudly_on_material_truncation(caplog: pytest.LogCaptureFixture) -> None:
    """One feed with much shorter available history than the others (a late
    listing, a yfinance gap, ...) must produce a WARNING, not a silent N-way
    intersection down to that feed's short window (item 3, extended to the
    N-feed runner for the same reason combine_universe_results needs it)."""
    long_feed = _synthetic_prices(50, start="2020-01-01", base=100.0)
    short_feed = _synthetic_prices(5, start="2020-01-01", base=50.0)  # only 5 of 50 bars

    with caplog.at_level(logging.WARNING, logger="archimedes_analytics_engine.engine"):
        result = run_multi_backtest(
            [long_feed, short_feed], strategy_cls=_EqualWeightStrategy, initial_cash=100_000.0, names=["LONG", "SHORT"]
        )

    assert result.bars == 5
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "retained 5 bars" in warnings[0].getMessage()
    assert "50 bars" in warnings[0].getMessage()


def test_run_multi_backtest_stays_quiet_on_full_overlap(caplog: pytest.LogCaptureFixture) -> None:
    frames = [_synthetic_prices(40, base=100.0), _synthetic_prices(40, base=50.0)]
    with caplog.at_level(logging.WARNING, logger="archimedes_analytics_engine.engine"):
        run_multi_backtest(frames, strategy_cls=_EqualWeightStrategy, initial_cash=100_000.0)
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_run_multi_backtest_matches_pairs_for_two_feeds() -> None:
    """For N=2 the multi runner must agree with run_pairs_backtest (same plumbing)."""
    from archimedes_analytics_engine.engine import run_pairs_backtest

    a = _synthetic_prices(60, base=100.0, drift=0.1)
    b = _synthetic_prices(60, base=80.0, drift=0.12, phase=1.0)
    multi = run_multi_backtest([a, b], strategy_cls=_EqualWeightStrategy, initial_cash=100_000.0)
    pair = run_pairs_backtest(a, b, strategy_cls=_EqualWeightStrategy, initial_cash=100_000.0)
    assert multi.bars == pair.bars
    assert multi.final_value == pytest.approx(pair.final_value)

"""Tests for the multi-asset (pairs) backtest runner — engine.run_pairs_backtest.

Hermetic: all data is synthetic, no network. Validates the two-feed plumbing,
date alignment, and metric extraction shared with the single-asset runner.
"""

from __future__ import annotations

import math

import backtrader as bt
import pandas as pd
import pytest
from archimedes_analytics_engine.engine import BacktestResult, FeedArityError, run_backtest, run_pairs_backtest


def _synthetic_prices(periods: int, start: str = "2020-01-01", base: float = 100.0, drift: float = 0.1) -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq="D")
    closes = [base + drift * i + 5.0 * math.sin(i / 7.0) for i in range(periods)]
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


class _BothLegsStrategy(bt.Strategy):
    """Minimal two-feed strategy: long leg A and short leg B on the first eligible bar."""

    def next(self) -> None:
        if len(self) != 5:
            return
        cash = float(self.broker.getvalue())
        size_a = int((cash * 0.4) // float(self.datas[0].close[0]))
        size_b = int((cash * 0.4) // float(self.datas[1].close[0]))
        if size_a > 0:
            self.order_target_size(data=self.datas[0], target=size_a)
        if size_b > 0:
            self.order_target_size(data=self.datas[1], target=-size_b)


def test_run_pairs_backtest_returns_result() -> None:
    a = _synthetic_prices(40)
    b = _synthetic_prices(40, base=50.0, drift=0.05)
    result = run_pairs_backtest(a, b, strategy_cls=_BothLegsStrategy, initial_cash=100_000.0)

    assert isinstance(result, BacktestResult)
    assert result.final_value > 0
    assert result.bars == 40
    assert result.look_ahead_audit_passed is True
    assert isinstance(result.daily_returns, list)
    assert len(result.daily_returns) > 0


def test_run_pairs_backtest_aligns_on_common_dates() -> None:
    # Leg B starts 10 days later → only the overlapping window is backtested.
    a = _synthetic_prices(40, start="2020-01-01")
    b = _synthetic_prices(40, start="2020-01-11", base=50.0)
    result = run_pairs_backtest(a, b, strategy_cls=_BothLegsStrategy, initial_cash=100_000.0)

    common = a.index.intersection(b.index)
    assert result.bars == len(common)
    assert result.bars == 30


def test_run_pairs_backtest_raises_on_disjoint_dates() -> None:
    a = _synthetic_prices(20, start="2020-01-01")
    b = _synthetic_prices(20, start="2021-01-01")
    with pytest.raises(ValueError, match="no common dates"):
        run_pairs_backtest(a, b, strategy_cls=_BothLegsStrategy, initial_cash=100_000.0)


def test_real_pairs_strategy_runs_without_error() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent / "strategies"))
    from gatev_2006_pairs_distance import PairsDistanceTrading

    # Enough bars to warm up the 252-bar z-score lookback and exercise next().
    a = _synthetic_prices(300, base=100.0, drift=0.1)
    b = _synthetic_prices(300, base=80.0, drift=0.12)
    result = run_pairs_backtest(a, b, strategy_cls=PairsDistanceTrading, initial_cash=100_000.0)

    assert isinstance(result, BacktestResult)
    assert result.bars == 300
    assert result.look_ahead_audit_passed is True


# Phase 1.3 economic pairs — each reuses PairsDistanceTrading via import. These
# tests prove the new files load to the shared class and run end-to-end on
# synthetic data (no network), guarding against a broken cross-import or bad
# metadata block.
@pytest.mark.parametrize(
    "stem",
    [
        "gatev_2006_pairs_ko_pep",
        "gatev_2006_pairs_ewa_ewc",
        "gatev_2006_pairs_gld_slv",
    ],
)
def test_second_wave_pair_files_load_and_run(stem: str) -> None:
    import sys
    from pathlib import Path

    strategies_dir = Path(__file__).parent.parent / "strategies"
    sys.path.insert(0, str(strategies_dir))
    sys.path.insert(0, str(strategies_dir.parent / "src"))
    from archimedes_analytics_engine.strategy_loader import load_strategy

    bundle = load_strategy(strategies_dir / f"{stem}.py")
    # All three reuse the flagship class — the loader must resolve a single candidate.
    assert bundle.cls.__name__ == "PairsDistanceTrading"

    a = _synthetic_prices(300, base=100.0, drift=0.1)
    b = _synthetic_prices(300, base=80.0, drift=0.12)
    result = run_pairs_backtest(a, b, strategy_cls=bundle.cls, initial_cash=100_000.0)
    assert isinstance(result, BacktestResult)
    assert result.bars == 300
    assert result.look_ahead_audit_passed is True


# Issue #887 bug 1: the single-feed runner used to let two-feed strategies
# crash mid-run with a bare IndexError on self.datas[1]. It must now fail
# closed with the typed FeedArityError before the run starts.
@pytest.mark.parametrize(
    "stem",
    [
        "gatev_2006_pairs_distance",
        "engle_granger_1987_cointegration_pairs",
        "elliott_2005_kalman_pairs",
    ],
)
def test_single_feed_runner_fails_closed_for_pairs_classes(stem: str) -> None:
    import sys
    from pathlib import Path

    strategies_dir = Path(__file__).parent.parent / "strategies"
    sys.path.insert(0, str(strategies_dir.parent / "src"))
    from archimedes_analytics_engine.strategy_loader import load_strategy

    bundle = load_strategy(strategies_dir / f"{stem}.py")
    with pytest.raises(FeedArityError, match="needs >= 2"):
        run_backtest(_synthetic_prices(40), strategy_cls=bundle.cls, initial_cash=100_000.0)


def _cointegrated_pair(n: int = 600, seed: int = 7, beta: float = 1.2, alpha: float = 4.0, half_life: float = 20.0):
    """Deterministic cointegrated pair: B is a random walk, the A-vs-B residual
    is a mean-reverting OU process. Used to exercise the trade path of the
    stat-arb strategies hermetically (seeded RNG, no network)."""
    import numpy as np

    rng = np.random.RandomState(seed)
    b = 60.0 + np.cumsum(rng.normal(0.0, 0.6, n))
    phi = float(np.exp(-np.log(2.0) / half_life))
    spread = np.zeros(n)
    for t in range(1, n):
        spread[t] = phi * spread[t - 1] + rng.normal(0.0, 1.5)
    a = alpha + beta * b + spread

    def _frame(close):
        idx = pd.date_range("2015-01-01", periods=n, freq="D")
        return pd.DataFrame(
            {
                "Open": close - 0.2,
                "High": close + 0.4,
                "Low": close - 0.4,
                "Close": close,
                "Volume": [1_000] * n,
            },
            index=idx,
        )

    return _frame(a), _frame(b)


# Phase 1.1/1.2 stat-arb strategies — new trading logic on the existing 2-feed
# engine. Each test loads the strategy file (proving the metadata block + single
# strategy class resolve) and runs it on a deterministic cointegrated pair where
# the trade path is guaranteed to fire (no network).
@pytest.mark.parametrize(
    ("stem", "expected_cls"),
    [
        ("engle_granger_1987_cointegration_pairs", "CointegrationPairsTrading"),
        ("elliott_2005_kalman_pairs", "KalmanPairsTrading"),
    ],
)
def test_statarb_pair_files_load_and_trade(stem: str, expected_cls: str) -> None:
    import sys
    from pathlib import Path

    strategies_dir = Path(__file__).parent.parent / "strategies"
    sys.path.insert(0, str(strategies_dir))
    sys.path.insert(0, str(strategies_dir.parent / "src"))
    from archimedes_analytics_engine.strategy_loader import load_strategy

    bundle = load_strategy(strategies_dir / f"{stem}.py")
    assert bundle.cls.__name__ == expected_cls

    a, b = _cointegrated_pair()
    result = run_pairs_backtest(a, b, strategy_cls=bundle.cls, initial_cash=100_000.0)
    assert isinstance(result, BacktestResult)
    assert result.bars == 600
    assert result.look_ahead_audit_passed is True
    # On a genuinely mean-reverting cointegrated pair, both strategies must open
    # at least one spread position — i.e. the trade path is actually exercised.
    assert result.total_trades > 0

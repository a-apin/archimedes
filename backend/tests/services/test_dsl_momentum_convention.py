"""F1 regression guard: DSL ``momentum_N`` is a trailing RETURN, not a price ratio.

The defect this pins down (interpreter-divergence audit, 2026-08-03): the
backtest interpreter computed ``momentum_N = close[0] / close[-N]`` — a ratio
centred on 1.0 — while the live signal evaluator, ``_tsmom_signal`` and
``rank_market`` all compute ``close[0] / close[-N] - 1.0``, a trailing return
centred on 0.0. Every hand-authored momentum condition in the repo is written
as ``{"gt": ["momentum_N", 0]}``, i.e. for the return convention. Close prices
are always positive, so under the ratio convention that entry filter was a
TAUTOLOGY: the backtest entered long on a 10% decline (ratio 0.9 > 0), and
every published momentum backtest described a strategy whose entry filter
never filtered anything.

These tests are behavioural, run through the real ``run_dsl_backtest`` path on
controlled synthetic feeds, and are DISCRIMINATING by construction: each
"stays flat" case was verified to FAIL against the pre-fix ratio convention
(the old code enters and the equity curve moves) before being trusted — per
the pre-merge conventions, a guard is only worth trusting once it has been
shown to reject something.
"""

from __future__ import annotations

import backtrader as bt
import pandas as pd
import pytest
from archimedes.services.fusion_evaluator import run_dsl_backtest
from archimedes.services.strategy_dsl import validate_strategy_spec

_CASH = 100_000.0


def _feed(daily_factor: float, bars: int = 300) -> bt.feeds.PandasData:
    """Synthetic OHLCV feed: price compounds by ``daily_factor`` each bar."""
    idx = pd.bdate_range("2020-01-01", periods=bars)
    close = pd.Series([100.0 * (daily_factor**i) for i in range(bars)], index=idx)
    df = pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.001,
            "Low": close * 0.999,
            "Close": close,
            "Volume": 1_000_000.0,
        },
        index=idx,
    )
    return bt.feeds.PandasData(dataname=df)


def _spec(entry: dict, exit_: dict) -> object:
    return validate_strategy_spec(
        {
            "name": "momentum convention probe",
            "asset_universe": ["SPY"],
            "rebalance_frequency": "daily",
            "entry": entry,
            "exit": exit_,
            "position_sizing": {"type": "full_invested_when_in_market"},
            "source_arxiv_ids": ["0706.1497"],
            "look_ahead_safe": True,
        }
    )


def _run(spec: object, feed: bt.feeds.PandasData):
    return run_dsl_backtest(
        spec,
        data_feed=feed,
        data_source_label="synthetic-momentum-probe",
        initial_cash=_CASH,
        tx_cost_bps=0,
    )


def test_momentum_gt_zero_does_not_enter_a_declining_market():
    """THE tautology case. Price declines 0.15%/bar, so the trailing 20-bar
    return is ~-3% every bar: ``momentum_20 > 0`` must never fire and the
    account must stay in cash for the whole run.

    Under the old ratio convention this exact setup ENTERED (ratio ~0.97 > 0)
    and rode the decline — verified: the pre-fix code fails this test with a
    final value materially below initial cash.
    """
    metrics = _run(
        _spec(entry={"gt": ["momentum_20", 0]}, exit_={"lt": ["momentum_20", -0.99]}),
        _feed(0.9985),
    )
    assert metrics.total_trades == 0
    assert metrics.equity_curve[-1] == pytest.approx(_CASH)


def test_momentum_threshold_is_on_the_return_scale_not_the_ratio_scale():
    """A threshold of 0.5 means "+50% trailing return", not "ratio above 0.5"
    (which any positive price series satisfies). Price rises 0.1%/bar, so the
    trailing 20-bar return is ~+2% — far below 0.5: no entry.

    Under the old ratio convention (~1.02 > 0.5) this entered immediately —
    verified failing pre-fix.
    """
    metrics = _run(
        _spec(entry={"gt": ["momentum_20", 0.5]}, exit_={"lt": ["momentum_20", -0.99]}),
        _feed(1.001),
    )
    assert metrics.total_trades == 0
    assert metrics.equity_curve[-1] == pytest.approx(_CASH)


def test_momentum_gt_zero_still_enters_a_rising_market():
    """The fix must not make momentum entries unreachable: on a rising series
    the trailing return is genuinely positive, so the strategy enters and the
    long position gains. Guards the direction of the fix (subtracting 1.0),
    not just its presence — an over-correction (e.g. subtracting 2.0) would
    fail here while passing the two flat cases."""
    metrics = _run(
        _spec(entry={"gt": ["momentum_20", 0]}, exit_={"lt": ["momentum_20", -0.99]}),
        _feed(1.001),
    )
    assert metrics.equity_curve[-1] > _CASH

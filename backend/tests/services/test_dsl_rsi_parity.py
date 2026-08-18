"""F5 regression guard: live RSI equals backtrader's RSI on the same series.

The defect (interpreter-divergence audit, 2026-08-03, F5): the live signal
evaluator computed RSI with a plain ``rolling(period).mean()`` of gains and
losses — Cutler's RSI — while the backtest's ``bt.indicators.RSI`` uses
Wilder's recursive SMMA (SMA seed, then ``avg = (avg*(N-1)+cur)/N``).
Backtrader ships the rolling-mean variant as a *separate* indicator
(``RSI_SMA``/"Cutler") precisely because they are different indicators: on
short windows they sit several points apart, so a graded backtest could pass
``rsi > 65`` where the live path fails it on identical data.

The parity oracle here is deliberately NOT a hand-written Wilder formula (a
reimplementation could share a bug with the code under test — the
same-literal-both-sides trap). It is ``bt.indicators.RSI`` itself, run over
the same series: the actual other side of the divergence.
"""

from __future__ import annotations

import backtrader as bt
import pandas as pd
import pytest
from archimedes.services.strategy_signal_evaluator import _compute_indicator_value

# Mixed gains and losses so neither average is zero and the two smoothing
# conventions measurably disagree (this exact series is the audit's worked
# example, extended so the recursion has bars to differentiate on).
_SERIES = [100.0, 102.0, 101.0, 105.0, 103.0, 108.0, 107.0, 110.0, 109.0, 113.0, 111.0, 116.0]
_PERIOD = 3


def _bt_rsi(values: list[float], period: int) -> float:
    """Final RSI value from backtrader's own indicator over ``values``."""
    idx = pd.bdate_range("2024-01-01", periods=len(values))
    close = pd.Series(values, index=idx)
    df = pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 0.0},
        index=idx,
    )

    captured: list[float] = []

    class Probe(bt.Strategy):
        def __init__(self) -> None:
            self.rsi = bt.indicators.RSI(self.data.close, period=period)

        def next(self) -> None:
            captured.append(float(self.rsi[0]))

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df))
    cerebro.addstrategy(Probe)
    cerebro.run()
    return captured[-1]


def test_live_rsi_matches_backtrader_rsi_to_float_precision():
    """The parity contract itself: same series, same period, same number —
    because the backtest is what gets graded, and the live value must be
    computed the way the graded value was."""
    live = _compute_indicator_value("rsi", _PERIOD, pd.Series(_SERIES))
    reference = _bt_rsi(_SERIES, _PERIOD)
    assert live == pytest.approx(reference, rel=1e-9)


def test_the_parity_oracle_discriminates_against_cutler():
    """Guard on the guard: prove the backtrader reference actually rejects the
    OLD implementation. If Wilder and Cutler happened to coincide on this
    series, the parity test above would pass against the pre-fix code too and
    assert nothing — so pin that they measurably disagree here."""
    prices = pd.Series(_SERIES)
    delta = prices.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.rolling(_PERIOD).mean().iloc[-1]
    avg_loss = loss.rolling(_PERIOD).mean().iloc[-1]
    cutler = float(100.0 - 100.0 / (1.0 + avg_gain / avg_loss))
    assert _bt_rsi(_SERIES, _PERIOD) != pytest.approx(cutler, abs=1.0)


def test_rsi_all_gains_saturates_at_100():
    """Degenerate branch kept from the old implementation: with no losses in
    the window, avg_loss is 0 and RSI reads 100 rather than dividing by zero."""
    rising = [100.0 + i for i in range(10)]
    assert _compute_indicator_value("rsi", _PERIOD, pd.Series(rising)) == 100.0


def test_rsi_insufficient_history_is_nan_not_a_number():
    """Fewer moves than the period → NaN (matches the old rolling-mean
    behaviour of having no defined value), never a fabricated reading."""
    import math

    assert math.isnan(_compute_indicator_value("rsi", 5, pd.Series([100.0, 101.0, 102.0])))

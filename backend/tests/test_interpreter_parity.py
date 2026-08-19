"""Interpreter parity: the backtest and the live evaluator, same spec, same numbers.

THE ROOT CAUSE THIS EXISTS FOR. The 2026-08-03 divergence audit confirmed 21
ways the two interpreters of one strategy language disagreed — momentum was a
ratio in the backtest and a return live (F1, a published-number tautology),
RSI was Wilder in the backtest and Cutler live (F5), and so on. Each was a
symptom of the same structural fact: two implementations, zero tests holding
them together. This file is the tether. Every indicator the DSL exposes is
computed BOTH ways over the same series, per-bar, and must agree — so the
next convention drift fails CI instead of shipping into published numbers.

SCOPE, stated exactly (the honest part):
  * PINNED EQUAL, per-bar: sma, rsi (post-F5 Wilder), momentum (post-F1
    trailing return), and flat-state daily entry decisions.
  * PINNED CONVERGENT: ema — backtrader seeds with an SMA, the live evaluator
    seeds ewm from the first value (audit F10). The seeds differ; the decay
    factor (1 - 2/(N+1))^k drives them together, so parity is asserted after
    a burn-in with a tight tolerance. If either side's seeding changes, the
    burn-in bound breaks here first.
  * PINNED ASYMMETRIC: realized_vol computes live and RAISES in the backtest
    (audit F6). The asymmetry itself is asserted, so both the silent arrival
    of a backtest implementation (must then be promoted to PINNED EQUAL) and
    a live-side removal are caught.
  * DELIBERATELY OUT: stateful entry/exit parity (audit F2 — the live path is
    stateless entry-AND-NOT-exit, the backtest is a position FSM) and
    rebalance cadence (F3 — the live path never reads it). Both are
    live-money behaviour changes prepared and HELD for Dan's explicit
    go-ahead; when those land, their parity cases belong here.

Mechanics: the backtrader side runs a probe strategy that records each
indicator's value at every bar; the live side recomputes on the price prefix
up to that bar — exactly the history a live tick sees.
"""

from __future__ import annotations

import math

import backtrader as bt
import pandas as pd
import pytest
from archimedes.services.dsl_to_backtrader import _make_indicator
from archimedes.services.strategy_dsl import DSLError
from archimedes.services.strategy_signal_evaluator import _compute_indicator_value

# Deterministic two-regime series: a drift-up half then a drift-down half,
# with bar-level zig-zag inside each. Two regimes are load-bearing, not
# flavour — the decision-parity test's anti-vacuity check requires the entry
# condition to genuinely flip both ways (a single-drift fixture kept
# momentum_20 > 0 on every bar and the check rejected it). No randomness:
# seed drift is how parity tests go flaky.
_N_BARS = 260
_SERIES = [100.0]
for _i in range(1, _N_BARS):
    zig = 0.004 if (_i * 7919) % 13 < 6 else -0.003
    drift = 0.0012 if _i < _N_BARS // 2 else -0.0015
    _SERIES.append(round(_SERIES[-1] * (1.0 + zig + drift), 10))


def _bt_indicator_series(name: str, period: int) -> list[float]:
    """Per-bar values of the BACKTEST implementation of one indicator."""
    idx = pd.bdate_range("2024-01-02", periods=_N_BARS)
    close = pd.Series(_SERIES, index=idx)
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 0.0}, index=idx)

    captured: list[float] = []

    class Probe(bt.Strategy):
        def __init__(self) -> None:
            self.ind = _make_indicator(self.data.close, name, period)

        def next(self) -> None:
            try:
                captured.append(float(self.ind[0]))
            except (IndexError, TypeError):
                captured.append(float("nan"))

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df))
    cerebro.addstrategy(Probe)
    cerebro.run()
    # backtrader only starts calling next() once the indicator's minimum
    # period is filled, so `captured` is END-aligned: captured[i] is bar
    # (N - len + i). Left-pad with NaN to restore per-bar index alignment
    # with the live series — off-by-warmup misalignment here would compare
    # different bars and make every "parity" assertion meaningless.
    return [float("nan")] * (_N_BARS - len(captured)) + captured


def _live_indicator_series(name: str, period: int) -> list[float]:
    """Per-bar values of the LIVE implementation — recomputed on each price
    prefix, exactly the history a live tick sees."""
    out: list[float] = []
    for t in range(_N_BARS):
        prefix = pd.Series(_SERIES[: t + 1])
        try:
            out.append(float(_compute_indicator_value(name, period, prefix)))
        except Exception:
            out.append(float("nan"))
    return out


def _compare(name: str, period: int, *, burn_in: int, rel: float) -> None:
    bt_vals = _bt_indicator_series(name, period)
    live_vals = _live_indicator_series(name, period)
    assert len(bt_vals) == len(live_vals) == _N_BARS
    compared = 0
    for t in range(burn_in, _N_BARS):
        b, lv = bt_vals[t], live_vals[t]
        if math.isnan(b) or math.isnan(lv):
            continue
        assert lv == pytest.approx(b, rel=rel), (
            f"{name}_{period} diverges at bar {t}: backtest={b!r} live={lv!r} — "
            "the two interpreters of one spec language have stopped agreeing; "
            "see the divergence-audit scope note in this file's docstring."
        )
        compared += 1
    # Anti-vacuity: a parity loop that compared nothing proves nothing.
    assert compared >= (_N_BARS - burn_in) * 0.8, f"only {compared} bars compared for {name}_{period}"


# ── Pinned EQUAL ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("period", [10, 50, 200])
def test_sma_parity_per_bar(period):
    _compare("sma", period, burn_in=period + 1, rel=1e-9)


@pytest.mark.parametrize("period", [3, 14])
def test_rsi_parity_per_bar(period):
    """Post-F5: both sides are Wilder SMMA. Pre-F5 this failed by whole points."""
    _compare("rsi", period, burn_in=period + 2, rel=1e-9)


@pytest.mark.parametrize("period", [5, 20, 60])
def test_momentum_parity_per_bar(period):
    """Post-F1: both sides are the trailing return. Pre-F1 the backtest was a
    price ratio offset by exactly 1.0 — mutation-verified: reverting the -1.0
    in dsl_to_backtrader fails every bar here."""
    _compare("momentum", period, burn_in=period + 1, rel=1e-9)


def test_flat_state_daily_entry_decision_parity():
    """When flat, on daily cadence, both interpreters enter iff entry(bar) —
    the one slice of decision logic that is convention-identical today (the
    stateful exit/cadence halves are audit F2/F3, held for Dan). Evaluated
    through the SHARED _eval_condition on both sides' own indicator values, so
    a divergent indicator value flips a decision here, not just a number."""
    from archimedes.services.dsl_to_backtrader import _eval_condition

    period = 20
    entry = {"gt": [f"momentum_{period}", 0]}
    bt_vals = _bt_indicator_series("momentum", period)
    live_vals = _live_indicator_series("momentum", period)
    decisions = 0
    entered_bt = entered_live = 0
    for t in range(period + 1, _N_BARS):
        if math.isnan(bt_vals[t]) or math.isnan(live_vals[t]):
            continue
        d_bt = _eval_condition(entry, {f"momentum_{period}": bt_vals[t]})
        d_live = _eval_condition(entry, {f"momentum_{period}": live_vals[t]})
        assert d_bt == d_live, f"entry decision diverges at bar {t}"
        decisions += 1
        entered_bt += d_bt
        entered_live += d_live
    assert decisions > 100
    # Anti-vacuity: the series must actually exercise BOTH decision branches.
    assert 0 < entered_bt < decisions, "series never flips the entry decision — test asserts nothing"


# ── Pinned CONVERGENT (F10) ─────────────────────────────────────────────────


@pytest.mark.parametrize("period", [12, 26])
def test_ema_converges_after_seed_burn_in(period):
    """Backtrader seeds EMA with an SMA; the live path seeds from the first
    value (audit F10, documented-benign). The gap decays by (1-2/(N+1))^k, so
    after 6 periods of burn-in the two must agree tightly. A seeding change on
    EITHER side moves the convergence rate and breaks this bound."""
    _compare("ema", period, burn_in=period * 6, rel=1e-4)


# ── Pinned ASYMMETRIC (F6) ──────────────────────────────────────────────────


def test_realized_vol_is_live_only_and_that_asymmetry_is_pinned():
    """realized_vol validates in the DSL grammar, computes on the live path,
    and RAISES in the backtest interpreter (audit F6). Pinning the asymmetry
    cuts both ways: if a backtest implementation quietly lands, this fails and
    the indicator must be promoted to the pinned-EQUAL section above; if the
    live side loses it, the strategy-affecting removal is caught too."""
    live = _compute_indicator_value("realized_vol", 20, pd.Series(_SERIES[:60]))
    assert isinstance(live, float) and not math.isnan(live) and live > 0

    with pytest.raises(DSLError):
        _make_indicator(None, "realized_vol", 20)

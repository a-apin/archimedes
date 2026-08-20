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
    trailing return), flat-state daily entry decisions, the STATEFUL position
    path (post-F2 — the live evaluator replays the spec's FSM instead of
    recomputing "entry AND NOT exit", so a band strategy holds through the dead
    zone on both sides), and REBALANCE CADENCE (post-F3 — the live replay runs
    the same cadence gate the backtest does, so a monthly spec re-decides on the
    same bars in both interpreters). Each indicator's whole-series form is also
    pinned against its per-prefix scalar form, because the position replay reads
    the series and a live tick reads the scalar.
  * PINNED CONVERGENT: ema — backtrader seeds with an SMA, the live evaluator
    seeds ewm from the first value (audit F10). The seeds differ; the decay
    factor (1 - 2/(N+1))^k drives them together, so parity is asserted after
    a burn-in with a tight tolerance. If either side's seeding changes, the
    burn-in bound breaks here first.
  * PINNED ASYMMETRIC: realized_vol computes live and RAISES in the backtest
    (audit F6). The asymmetry itself is asserted, so both the silent arrival
    of a backtest implementation (must then be promoted to PINNED EQUAL) and
    a live-side removal are caught.
  * PINNED WITH A ONE-BAR EXECUTION OFFSET: the stateful/cadence cases compare
    DECISIONS, not fills. backtrader submits the order on the decision bar and
    the broker fills it at the next bar's open, so the backtest's observable
    position at bar t+1 is the state its FSM decided at bar t. That offset is an
    execution convention, not an interpreter divergence, and the helpers below
    state it explicitly rather than hiding it in a fudge factor.
  * DELIBERATELY OUT: volatility_target SIZING parity — the backtest sizes off a
    20-bar RMS of returns capped at 2.0x, the live path off a 22-bar sample
    stdev capped at 1.0x. Both sides now agree on WHEN a vol-targeted strategy
    is in the market (the cases below cover that); how much they buy once in is
    a separate, still-open divergence and is not asserted here.
  * DELIBERATELY OUT: window-age position membership — the live replay derives
    position from the visible rolling window, so an entry older than the
    window's left edge is invisible (backtest long, live flat). Unreachable
    until a strategy approaches the fetch window's age; guarded LOUDLY by the
    created_at-vs-window warning in evaluate_strategies (pinned in
    test_live_position_fsm.py::TestWindowAgeGuard), and the fetch must be
    re-anchored at strategy inception before it ever becomes reachable.

Mechanics: the backtrader side runs a probe strategy that records each
indicator's value at every bar; the live side recomputes on the price prefix
up to that bar — exactly the history a live tick sees.
"""

from __future__ import annotations

import math

import backtrader as bt
import pandas as pd
import pytest
from archimedes.services.dsl_to_backtrader import _eval_condition, _make_indicator, interpret_spec
from archimedes.services.strategy_dsl import DSLError, validate_strategy_spec
from archimedes.services.strategy_signal_evaluator import (
    Signal,
    _compute_indicator_series,
    _compute_indicator_value,
    _spec_signal,
)

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


@pytest.mark.parametrize(
    ("name", "period"),
    [("sma", 50), ("ema", 12), ("rsi", 14), ("momentum", 20), ("realized_vol", 20)],
)
def test_indicator_series_matches_per_prefix_value(name, period):
    """The whole-series indicator form equals the per-prefix scalar form, bar
    for bar. Load-bearing, not bookkeeping: the position replay (F2/F3) reads
    ``_compute_indicator_series`` while a plain live tick reads
    ``_compute_indicator_value``, so a drift between the two would put the
    replayed position and the value the same tick reports on different numbers —
    the exact failure mode this whole file exists to prevent, reintroduced
    inside one module."""
    series = _compute_indicator_series(name, period, pd.Series(_SERIES))
    assert len(series) == _N_BARS
    compared = 0
    for t in range(period + 2, _N_BARS):
        vec = float(series.iloc[t])
        scalar = float(_compute_indicator_value(name, period, pd.Series(_SERIES[: t + 1])))
        if math.isnan(vec) and math.isnan(scalar):
            continue
        assert vec == pytest.approx(scalar, rel=1e-12), (
            f"{name}_{period} series/scalar disagree at bar {t}: series={vec!r} scalar={scalar!r}"
        )
        compared += 1
    assert compared >= (_N_BARS - period - 2) * 0.8, f"only {compared} bars compared for {name}_{period}"


# ── Pinned EQUAL: stateful position path (F2) + cadence (F3) ────────────────
#
# A second deterministic series, purpose-built for the band case. `_SERIES`
# above keeps RSI(14) inside a narrow 65–83 corridor, which never crosses a
# 30/70 band and would make every band assertion below vacuous. This one is a
# 20-bar-down / 20-bar-up sawtooth with a bar-level zig, so RSI(14) sweeps the
# full 0–83 range: ~67 bars below 30, ~70 above 70, and ~169 bars in the DEAD
# ZONE between them where entry and exit are BOTH false — which is precisely
# where the pre-F2 stateless live rule and the backtest FSM disagreed.
_BAND_N_BARS = 320
_BAND_SERIES = [100.0]
for _i in range(1, _BAND_N_BARS):
    _leg = (_i // 20) % 2  # 0 = 20-bar down leg, 1 = 20-bar up leg
    _step = 0.010 if _leg else -0.010
    _zig = 0.002 if (_i * 7919) % 7 < 3 else -0.001
    _BAND_SERIES.append(round(_BAND_SERIES[-1] * (1.0 + _step + _zig), 10))

_RSI_PERIOD = 14
_ENTRY = {"lt": [f"rsi_{_RSI_PERIOD}", 30]}
_EXIT = {"gt": [f"rsi_{_RSI_PERIOD}", 70]}


def _band_spec(rebalance_frequency: str) -> dict:
    """RSI 30/70 band spec — the canonical F2 case: long below 30, out above 70,
    HOLD in between."""
    return {
        "name": f"RSI band ({rebalance_frequency})",
        "asset_universe": ["SPY"],
        "rebalance_frequency": rebalance_frequency,
        "entry": dict(_ENTRY),
        "exit": dict(_EXIT),
        "position_sizing": {"type": "full_invested_when_in_market"},
        "source_arxiv_ids": ["0000.0000"],
        "look_ahead_safe": True,
    }


def _bt_position_path(spec_dict: dict) -> dict[int, bool]:
    """Per-bar in-market state of the REAL backtest FSM.

    Runs the generated ``DSLStrategy`` unmodified and records
    ``self.position.size > 0`` at the top of each ``next()`` — the state the FSM
    reads BEFORE deciding on that bar. backtrader submits the order on the
    decision bar and the broker fills it at the NEXT bar's open, so the state
    the FSM decided at bar t is what shows up at bar t+1; callers compare
    ``path[t + 1]`` against the live decision at bar t.

    ``exposure_fraction`` is dialled down from the 0.99 default on purpose: at
    99% notional an entry order sized on bar t's close can be MARGIN-REJECTED
    when bar t+1's open gaps up, and the FSM then silently stays flat. That is
    an execution-layer artifact of this fixture's 1%/bar sawtooth, not an
    interpreter divergence, and letting it into the comparison would have this
    test failing for a reason it does not claim to be about.
    """
    idx = pd.bdate_range("2024-01-02", periods=_BAND_N_BARS)
    close = pd.Series(_BAND_SERIES, index=idx)
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 0.0}, index=idx)

    captured: dict[int, bool] = {}
    strategy_cls = interpret_spec(validate_strategy_spec(spec_dict))

    class Probe(strategy_cls):
        def next(self) -> None:
            captured[len(self) - 1] = self.position.size > 0
            super().next()

    cerebro = bt.Cerebro()
    cerebro.broker.setcash(1_000_000.0)
    cerebro.adddata(bt.feeds.PandasData(dataname=df))
    cerebro.addstrategy(Probe, exposure_fraction=0.5)
    cerebro.run()
    return captured


def _live_position_path(spec_dict: dict) -> dict[int, bool]:
    """Per-bar in-market decision of the LIVE evaluator — ``_spec_signal`` on
    each growing price prefix, exactly the history a live tick sees."""
    close = pd.Series(_BAND_SERIES)
    out: dict[int, bool] = {}
    for t in range(_RSI_PERIOD, _BAND_N_BARS):
        sig = _spec_signal("parity", "sSPY", close.iloc[: t + 1], dict(spec_dict))
        out[t] = sig.signal is not Signal.FLAT
    return out


def _stateless_in_market(t: int) -> bool:
    """The PRE-F2 live rule: ``entry(bar) and not exit(bar)``, no memory."""
    rsi = _compute_indicator_value("rsi", _RSI_PERIOD, pd.Series(_BAND_SERIES[: t + 1]))
    bar = {f"rsi_{_RSI_PERIOD}": rsi}
    return _eval_condition(_ENTRY, bar) and not _eval_condition(_EXIT, bar)


def test_stateful_dead_zone_position_parity():
    """F2: an RSI 30/70 band strategy holds its position through the dead zone
    in BOTH interpreters, per bar.

    Entry is only tested while flat and exit only while in position, so the
    ~169 bars where 30 <= rsi <= 70 are a HOLD — the backtest stays long there
    and, post-fix, so does the live evaluator. The anti-vacuity block at the end
    is also the regression demonstration: it asserts the dead zone is actually
    exercised AND that the pre-F2 stateless rule (`entry and not exit`) gives a
    different answer on those bars, which is what made this strategy long in the
    backtest and in cash live on the same bar."""
    spec = _band_spec("daily")
    bt_path = _bt_position_path(spec)
    live_path = _live_position_path(spec)

    compared = 0
    live_long = 0
    for t in range(_RSI_PERIOD, _BAND_N_BARS - 1):
        expected = bt_path.get(t + 1)  # +1: broker fills the decision at the next bar
        if expected is None:
            continue
        assert live_path[t] == expected, (
            f"position state diverges at bar {t}: backtest={expected} live={live_path[t]} — "
            "the live evaluator and the backtest FSM disagree about whether this strategy "
            "holds its position; see the divergence-audit scope note in this file's docstring."
        )
        compared += 1
        live_long += live_path[t]

    assert compared > 250, f"only {compared} bars compared — fixture stopped exercising the FSM"
    assert 0 < live_long < compared, "series never flips the position — test asserts nothing"

    # Anti-vacuity + regression demonstration: bars where BOTH conditions are
    # false and the FSM is long. These are exactly the bars the stateless rule
    # got wrong, so this count must be non-trivial or the test proves nothing.
    dead_zone_holds = 0
    for t in range(_RSI_PERIOD, _BAND_N_BARS - 1):
        if bt_path.get(t + 1) and live_path[t] and not _stateless_in_market(t):
            dead_zone_holds += 1
    assert dead_zone_holds > 50, (
        f"only {dead_zone_holds} dead-zone hold bars — the fixture no longer separates the "
        "position FSM from the pre-F2 stateless rule, so this test would pass on the old code"
    )


def test_monthly_cadence_decision_parity():
    """F3: a monthly spec re-decides on the SAME bars in both interpreters, and
    holds its position in between.

    Cadence is a 21-trading-bar modulus anchored on the first post-warm-up bar
    (``DSLStrategy._rebalance_period`` / ``_rebal_counter = period - 1``), and
    the gate runs BEFORE both branches — an off-cadence bar is not even
    exit-tested. The last block is the regression demonstration: the identical
    spec on daily cadence must change state on bars the monthly one holds
    through, so a live path that ignored ``rebalance_frequency`` (as it did
    pre-F3) cannot pass this."""
    spec = _band_spec("monthly")
    bt_path = _bt_position_path(spec)
    live_path = _live_position_path(spec)

    compared = 0
    for t in range(_RSI_PERIOD, _BAND_N_BARS - 1):
        expected = bt_path.get(t + 1)
        if expected is None:
            continue
        assert live_path[t] == expected, (
            f"monthly-cadence position diverges at bar {t}: backtest={expected} live={live_path[t]}"
        )
        compared += 1
    assert compared > 250, f"only {compared} bars compared — fixture stopped exercising the FSM"

    # The live side changes state ONLY on cadence-eligible bars, and there are
    # several of them (a single flip would not distinguish cadence from luck).
    changes = [t for t in range(_RSI_PERIOD + 1, _BAND_N_BARS) if live_path[t] != live_path[t - 1]]
    assert len(changes) >= 3, f"only {len(changes)} state changes — cadence spacing is not exercised"
    for t in changes:
        assert (t - _RSI_PERIOD) % 21 == 0, (
            f"live position changed at bar {t}, which is not a monthly cadence boundary "
            f"(offset {(t - _RSI_PERIOD) % 21} from the 21-bar grid)"
        )

    # Anti-vacuity: the cadence gate must actually suppress decisions the same
    # spec would take on daily cadence.
    daily_path = _live_position_path(_band_spec("daily"))
    suppressed = sum(1 for t in range(_RSI_PERIOD, _BAND_N_BARS) if daily_path[t] != live_path[t])
    assert suppressed > 20, (
        f"monthly and daily cadence differ on only {suppressed} bars — the fixture no longer "
        "distinguishes a cadence-gated live path from one that ignores rebalance_frequency"
    )


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

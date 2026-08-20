"""Live position-state FSM + rebalance cadence (divergence audit F2 and F3).

Target: ``archimedes.services.strategy_signal_evaluator._spec_signal`` and the
``_replay_position_state`` it now runs.

WHAT BROKE. The live evaluator was stateless — ``in_market = entry(bar) and not
exit(bar)``, recomputed from scratch on every tick with no memory of the
previous one. The backtest is a position FSM: entry is only tested while flat,
exit only while in position, and a bar where neither fires is a HOLD. On an RSI
30/70 band strategy the whole dead zone (30 <= rsi <= 70) is a hold, so the same
bar had the strategy long in the backtest and in cash live (F2). Separately, the
live path never read ``rebalance_frequency`` at all, so a spec declaring monthly
cadence was re-decided on every one of the ~288 ticks a day (F3).

WHAT THE FIX IS. Position state is REPLAY-DERIVED: every tick recomputes the
position by replaying the spec's own FSM — cadence gate included — over the same
price window the signals are computed from. No persistence, so nothing to reset
on restart, nothing to double-advance when one strategy is bound to several
vaults, and nothing a vendor data gap can contradict. These tests pin both the
FSM semantics and the property that makes the approach safe (no memory between
calls).

Bar-for-bar agreement with the REAL backtrader FSM is pinned separately, in
test_interpreter_parity.py; this file pins the live side's semantics on their
own, in cases small enough to read.

Hermetic: pure functions over in-memory price Series. No network, no Redis, no
DB, no yfinance.
"""

from __future__ import annotations

import pandas as pd
import pytest
from archimedes.services.dsl_to_backtrader import _eval_condition, rebalance_period_bars
from archimedes.services.strategy_dsl import validate_strategy_spec
from archimedes.services.strategy_signal_evaluator import (
    Signal,
    _compute_indicator_value,
    _replay_position_state,
    _spec_signal,
)

# ── Fixtures ──────────────────────────────────────────────────────────────
#
# A 20-bar-down / 20-bar-up sawtooth with a bar-level zig. Deterministic (seeded
# drift is how these go flaky) and shaped so RSI(14) sweeps the full 0–83 range:
# it spends long stretches below 30, long stretches above 70, and the majority of
# its bars in the DEAD ZONE between them, which is the region F2 is about.

_N = 320
_RSI_PERIOD = 14
_WARMUP = _RSI_PERIOD  # first bar the FSM can act on == max indicator period


def _band_prices(n: int = _N) -> pd.Series:
    vals = [100.0]
    for i in range(1, n):
        leg = (i // 20) % 2  # 0 = down leg, 1 = up leg
        step = 0.010 if leg else -0.010
        zig = 0.002 if (i * 7919) % 7 < 3 else -0.001
        vals.append(round(vals[-1] * (1.0 + step + zig), 10))
    return pd.Series(vals, name="sSPY")


_PRICES = _band_prices()

_ENTRY = {"lt": [f"rsi_{_RSI_PERIOD}", 30]}
_EXIT = {"gt": [f"rsi_{_RSI_PERIOD}", 70]}


def _band_spec(rebalance_frequency: str = "daily", **overrides) -> dict:
    """RSI 30/70 band: long below 30, out above 70, HOLD in between."""
    spec = {
        "name": f"RSI band ({rebalance_frequency})",
        "asset_universe": ["SPY"],
        "rebalance_frequency": rebalance_frequency,
        "entry": dict(_ENTRY),
        "exit": dict(_EXIT),
        "position_sizing": {"type": "full_invested_when_in_market"},
        "source_arxiv_ids": ["0000.0000"],
        "look_ahead_safe": True,
    }
    spec.update(overrides)
    return spec


def _in_market(spec: dict, upto: int) -> bool:
    """The live decision at bar ``upto`` — a tick whose history ends there."""
    return _spec_signal("s1", "sSPY", _PRICES.iloc[: upto + 1], dict(spec)).signal is not Signal.FLAT


def _stateless_in_market(upto: int) -> bool:
    """The PRE-F2 rule: ``entry(bar) and not exit(bar)``, no position memory."""
    rsi = _compute_indicator_value("rsi", _RSI_PERIOD, _PRICES.iloc[: upto + 1])
    bar = {f"rsi_{_RSI_PERIOD}": rsi}
    return _eval_condition(_ENTRY, bar) and not _eval_condition(_EXIT, bar)


def _dead_zone_hold_bars(spec: dict) -> list[int]:
    """Bars where the FSM is long but the stateless rule said flat."""
    return [t for t in range(_WARMUP, _N) if _in_market(spec, t) and not _stateless_in_market(t)]


# ── F2: position-state semantics ──────────────────────────────────────────


class TestPositionStateSemantics:
    def test_holds_position_through_the_dead_zone(self):
        """The core F2 case. On these bars entry is false AND exit is false, so
        the FSM holds — while the old stateless rule reported FLAT and put the
        vault in cash on a bar the published backtest was long."""
        holds = _dead_zone_hold_bars(_band_spec("daily"))
        assert len(holds) > 50, f"only {len(holds)} dead-zone hold bars — fixture no longer exercises F2"

        for t in holds[:25]:
            rsi = _compute_indicator_value("rsi", _RSI_PERIOD, _PRICES.iloc[: t + 1])
            # These really are dead-zone bars: neither condition fires.
            assert 30 <= rsi <= 70, f"bar {t} is not in the dead zone (rsi={rsi})"
            sig = _spec_signal("s1", "sSPY", _PRICES.iloc[: t + 1], _band_spec("daily"))
            assert sig.signal is Signal.LONG and sig.weight == 1.0

    def test_stays_flat_in_the_dead_zone_when_it_never_entered(self):
        """Holding is not the same as inventing a position: a window whose FSM
        never saw an entry is FLAT in the dead zone, not long."""
        spec = _band_spec("daily")
        # First dead-zone bar reached without any prior entry.
        candidates = [
            t
            for t in range(_WARMUP, 60)
            if 30 <= _compute_indicator_value("rsi", _RSI_PERIOD, _PRICES.iloc[: t + 1]) <= 70
            and not _replay_position_state(validate_strategy_spec(spec), _PRICES.iloc[: t + 1], _WARMUP).entered_index
        ]
        assert candidates, "fixture no longer has a never-entered dead-zone bar"
        t = candidates[0]
        sig = _spec_signal("s1", "sSPY", _PRICES.iloc[: t + 1], spec)
        assert sig.signal is Signal.FLAT and sig.weight == 0.0

    def test_exit_condition_still_closes_the_position(self):
        """Hold-through-the-dead-zone must not become never-exit."""
        spec = _band_spec("daily")
        exits = [
            t for t in range(_WARMUP, _N) if _compute_indicator_value("rsi", _RSI_PERIOD, _PRICES.iloc[: t + 1]) > 70
        ]
        assert exits, "fixture never crosses the exit band"
        for t in exits[:10]:
            sig = _spec_signal("s1", "sSPY", _PRICES.iloc[: t + 1], spec)
            assert sig.signal is Signal.FLAT, f"bar {t}: exit condition true but signal is {sig.signal}"

    def test_entry_branch_wins_when_both_conditions_are_true_on_the_same_bar(self):
        """The FSM's branches are mutually exclusive on the position flag: while
        flat, only entry is evaluated. The stateless rule ANDed the two, so a
        spec whose exit is true on the entry bar could never be long at all."""
        spec = {
            "name": "both-true",
            "asset_universe": ["SPY"],
            "rebalance_frequency": "daily",
            "entry": {"gt": ["close", "sma_5"]},
            "exit": {"gt": ["close", 0]},  # always true
            "position_sizing": {"type": "full_invested_when_in_market"},
            "source_arxiv_ids": ["0000.0000"],
            "look_ahead_safe": True,
        }
        # Exactly one decision bar (index == max_period == 5), rising → entry true.
        prices = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 105.0], name="sSPY")
        sig = _spec_signal("s1", "sSPY", prices, spec)
        assert sig.signal is Signal.LONG
        # The pre-fix rule on the same bar: entry AND NOT exit → False.
        bar = {"close": 105.0, "sma_5": float(prices.iloc[1:].mean())}
        assert _eval_condition(spec["entry"], bar) and _eval_condition(spec["exit"], bar)

    def test_two_bar_minimum_for_a_round_trip(self):
        """A round trip needs two decision bars: entry fires on one, exit can
        only be tested on the next. Same convention as the backtest."""
        spec = {
            "name": "round-trip",
            "asset_universe": ["SPY"],
            "rebalance_frequency": "daily",
            "entry": {"gt": ["close", "sma_5"]},
            "exit": {"gt": ["close", 0]},
            "position_sizing": {"type": "full_invested_when_in_market"},
            "source_arxiv_ids": ["0000.0000"],
            "look_ahead_safe": True,
        }
        prices = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0], name="sSPY")
        one_bar = _spec_signal("s1", "sSPY", prices.iloc[:6], spec)
        two_bars = _spec_signal("s1", "sSPY", prices, spec)
        assert one_bar.signal is Signal.LONG  # decision bar 1: entered
        assert two_bars.signal is Signal.FLAT  # decision bar 2: exit tested, closed


class TestReplayDerivedStateIsRestartSafe:
    """The property that made replay the right choice over persisted state: the
    answer is a pure function of the price window, so a restart, a redeploy or a
    second reader can never see a different position than the first."""

    def test_repeated_evaluation_is_identical(self):
        spec = _band_spec("daily")
        holds = _dead_zone_hold_bars(spec)
        assert holds, "fixture no longer produces a dead-zone hold — see F2 note above"
        t = holds[len(holds) // 2]
        first = _spec_signal("s1", "sSPY", _PRICES.iloc[: t + 1], spec)
        second = _spec_signal("s1", "sSPY", _PRICES.iloc[: t + 1], spec)
        assert (first.signal, first.weight) == (second.signal, second.weight)

    def test_evaluation_order_does_not_change_any_answer(self):
        """Evaluating a longer window first must not colour the shorter one —
        the failure mode a per-strategy cached/persisted position would have."""
        spec = _band_spec("daily")
        short_first = _spec_signal("s1", "sSPY", _PRICES.iloc[:120], spec).signal
        _spec_signal("s1", "sSPY", _PRICES, spec)
        short_after = _spec_signal("s1", "sSPY", _PRICES.iloc[:120], spec).signal
        assert short_first is short_after

    def test_state_does_not_leak_across_assets_of_one_strategy(self):
        """Signals are produced once per (strategy, asset) but read by N vaults;
        a shared mutable position would cross-contaminate assets here."""
        spec = _band_spec("daily")
        holds = _dead_zone_hold_bars(spec)
        assert holds, "fixture no longer produces a dead-zone hold — see F2 note above"
        t = holds[len(holds) // 2]
        long_window = _PRICES.iloc[: t + 1]
        # A monotonically rising window can never satisfy rsi < 30 → never enters.
        never_enters = pd.Series([100.0 + i for i in range(t + 1)], name="sQQQ")
        assert _spec_signal("s1", "sSPY", long_window, spec).signal is Signal.LONG
        assert _spec_signal("s1", "sQQQ", never_enters, spec).signal is Signal.FLAT
        assert _spec_signal("s1", "sSPY", long_window, spec).signal is Signal.LONG


# ── F3: rebalance cadence ─────────────────────────────────────────────────


class TestRebalanceCadenceGate:
    def test_cadence_table_is_shared_with_the_backtest(self):
        """One definition of the cadence vocabulary, imported by both
        interpreters — the anti-drift discipline this whole area exists for."""
        assert rebalance_period_bars("daily") == 1
        assert rebalance_period_bars("weekly") == 5
        assert rebalance_period_bars("monthly") == 21
        assert rebalance_period_bars("hourly") == 1  # unknown degrades to every bar

    def test_monthly_holds_its_signal_between_cadence_boundaries(self):
        """The literal F3 case: a monthly spec must not re-decide on a bar the
        backtest would have skipped."""
        monthly = _band_spec("monthly")
        daily = _band_spec("daily")
        changed_off_cadence = 0
        differs_from_daily = 0
        for t in range(_WARMUP + 1, _N):
            monthly_now = _in_market(monthly, t)
            if monthly_now != _in_market(monthly, t - 1) and (t - _WARMUP) % 21 != 0:
                changed_off_cadence += 1
            if monthly_now != _in_market(daily, t):
                differs_from_daily += 1
        assert changed_off_cadence == 0, f"monthly spec changed state on {changed_off_cadence} off-cadence bars"
        # Anti-vacuity: the gate must actually be suppressing decisions.
        assert differs_from_daily > 20, (
            f"monthly and daily agree on all but {differs_from_daily} bars — the fixture no longer "
            "distinguishes a cadence-gated path from one that ignores rebalance_frequency"
        )

    def test_weekly_boundaries_are_five_bars_apart(self):
        weekly = _band_spec("weekly")
        changes = [t for t in range(_WARMUP + 1, _N) if _in_market(weekly, t) != _in_market(weekly, t - 1)]
        assert len(changes) >= 3, f"only {len(changes)} state changes — spacing is not exercised"
        for t in changes:
            assert (t - _WARMUP) % 5 == 0, f"weekly spec changed at bar {t}, off the 5-bar grid"

    def test_daily_cadence_still_decides_every_bar(self):
        """Control: the gate must not throttle a spec that declared daily."""
        daily = _band_spec("daily")
        changes = [t for t in range(_WARMUP + 1, _N) if _in_market(daily, t) != _in_market(daily, t - 1)]
        assert len(changes) >= 10
        assert any((t - _WARMUP) % 21 != 0 for t in changes), "daily spec is behaving as if gated"

    def test_repeated_intraday_ticks_report_one_stable_weight(self):
        """A 300s tick loop evaluates a daily-bar window ~288 times a day. Every
        one of those must report the SAME weight, or the drift threshold
        downstream is the only thing between a monthly spec and a trade."""
        monthly = _band_spec("monthly")
        holds = _dead_zone_hold_bars(monthly)
        assert holds, "fixture no longer produces a held monthly position"
        window = _PRICES.iloc[: holds[len(holds) // 2] + 1]
        results = {(s.signal, s.weight) for s in (_spec_signal("s1", "sSPY", window, monthly) for _ in range(288))}
        assert len(results) == 1, f"288 ticks on one window produced {len(results)} distinct weights"

    def test_vol_target_weight_is_frozen_between_boundaries(self):
        """Sizing is evaluated as of the last decision bar too — otherwise an
        off-cadence bar moves the target weight and re-opens the F3 hole through
        the sizing term instead of the signal term."""
        spec = _band_spec("monthly", position_sizing={"type": "volatility_target", "annual_pct": 0.15})
        # Find a boundary bar where the position is open, then walk the bars after it.
        boundaries = [t for t in range(_WARMUP, _N - 5) if (t - _WARMUP) % 21 == 0]
        held = [b for b in boundaries if _in_market(spec, b)]
        assert held, "fixture no longer opens a vol-targeted position on a boundary"
        b = held[len(held) // 2]
        base = _spec_signal("s1", "sSPY", _PRICES.iloc[: b + 1], spec)
        for t in range(b + 1, min(b + 21, _N)):
            off = _spec_signal("s1", "sSPY", _PRICES.iloc[: t + 1], spec)
            assert off.weight == base.weight, f"vol-target weight moved on off-cadence bar {t}"
        assert 0.0 < base.weight <= 1.0

    def test_reason_states_the_cadence_and_the_hold(self):
        """Claim integrity: the published reasoning must say WHY the position is
        what it is, not imply a fresh decision on every tick."""
        monthly = _band_spec("monthly")
        holds = _dead_zone_hold_bars(monthly)
        assert holds, "fixture no longer produces a dead-zone hold — see F2 note above"
        t = holds[len(holds) // 2]
        sig = _spec_signal("s1", "sSPY", _PRICES.iloc[: t + 1], monthly)
        assert "monthly cadence" in sig.reason
        assert "held" in sig.reason
        assert "in market since bar" in sig.reason


# ── Degradation: data gaps must not look like exits ───────────────────────


class TestDegradesHonestly:
    def test_short_window_is_insufficient_data_not_an_exit(self):
        spec = _band_spec("daily")
        sig = _spec_signal("s1", "sSPY", _PRICES.iloc[:10], spec)
        assert sig.signal is Signal.FLAT
        assert "insufficient data" in sig.reason

    def test_exactly_one_decision_bar_of_history_is_evaluable(self):
        """The warm-up boundary: max_period + 1 bars gives exactly one decision
        bar. It must decide, not raise and not silently buy-and-hold."""
        spec = _band_spec("daily")
        sig = _spec_signal("s1", "sSPY", _PRICES.iloc[: _WARMUP + 1], spec)
        assert sig.signal in (Signal.LONG, Signal.FLAT)
        assert "insufficient data" not in sig.reason
        replay = _replay_position_state(validate_strategy_spec(spec), _PRICES.iloc[: _WARMUP + 1], _WARMUP)
        assert replay.decision_count == 1 and replay.decision_index == _WARMUP

    def test_unsupported_indicator_is_a_loud_flat(self):
        spec = _band_spec("daily")
        spec["entry"] = {"gt": ["close", "bogus_10"]}
        sig = _spec_signal("s1", "sSPY", _PRICES, spec)
        assert sig.signal is Signal.FLAT
        assert sig.weight == 0.0

    def test_invalid_spec_is_still_a_loud_flat_not_buy_and_hold(self):
        sig = _spec_signal("s1", "sSPY", _PRICES, {"name": "broken"})
        assert sig.signal is Signal.FLAT
        assert sig.reason.startswith("spec invalid:")


@pytest.mark.parametrize("frequency", ["daily", "weekly", "monthly"])
def test_replay_decision_bars_match_the_declared_cadence(frequency):
    """Structural pin on the replay itself: the number of bars it evaluates is
    the post-warm-up bar count divided by the declared cadence period."""
    spec = validate_strategy_spec(_band_spec(frequency))
    replay = _replay_position_state(spec, _PRICES, _WARMUP)
    period = rebalance_period_bars(frequency)
    expected = ((_N - _WARMUP) + period - 1) // period
    assert replay.decision_count == expected
    assert replay.decision_index == _WARMUP + (expected - 1) * period

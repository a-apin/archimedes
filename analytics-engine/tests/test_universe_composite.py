"""Tests for engine.combine_universe_results — the equal-weighted,
date-intersection-aligned composite across a strategy's declared
ASSET_UNIVERSE (backtest-vol audit).

Hermetic: operates directly on hand-built BacktestResult objects, no
backtrader/network involved — isolates the aggregation math from the
per-asset backtest runners already covered by test_engine.py /
test_multi_engine.py.
"""

from __future__ import annotations

import logging

import pytest
from archimedes_analytics_engine.engine import (
    BacktestResult,
    combine_universe_results,
    coverage_summary,
    universe_date_coverage,
    warn_on_truncated_coverage,
)


def _result(dates: list[str], returns: list[float], *, tx_bps: int = 10, slip_bps: int = 5) -> BacktestResult:
    equity = [100_000.0]
    for r in returns:
        equity.append(equity[-1] * (1.0 + r))
    return BacktestResult(
        final_value=equity[-1],
        total_return_pct=((equity[-1] / equity[0]) - 1.0) * 100.0,
        equity_curve=equity,
        daily_returns=list(returns),
        daily_return_dates=list(dates),
        transaction_cost_bps=tx_bps,
        slippage_bps=slip_bps,
        look_ahead_audit_passed=True,
        bars=len(returns),
        backtest_start=dates[0] if dates else None,
        backtest_end=dates[-1] if dates else None,
    )


def test_single_asset_universe_is_numerically_identical_to_the_single_run() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    returns = [0.01, -0.02, 0.015, 0.0]
    solo = _result(dates, returns)

    composite = combine_universe_results([("BIL", solo)], initial_cash=100_000.0)

    assert composite.daily_return_dates == solo.daily_return_dates
    assert composite.daily_returns == pytest.approx(solo.daily_returns)
    assert composite.equity_curve == pytest.approx(solo.equity_curve)
    assert composite.final_value == pytest.approx(solo.final_value)
    assert composite.bars == solo.bars
    assert composite.backtest_start == solo.backtest_start
    assert composite.backtest_end == solo.backtest_end


def test_two_asset_composite_is_equal_weighted_mean_on_full_overlap() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    a = _result(dates, [0.02, 0.00, -0.01])
    b = _result(dates, [0.00, 0.04, 0.03])

    composite = combine_universe_results([("A", a), ("B", b)], initial_cash=100_000.0)

    assert composite.daily_return_dates == dates
    assert composite.daily_returns == pytest.approx([0.01, 0.02, 0.01])
    assert composite.bars == 3


def test_composite_aligns_on_date_intersection_not_position() -> None:
    """The exact regression this composite exists to prevent: two feeds whose
    calendars genuinely differ (e.g. ^N225 vs CL=F) must be aligned by DATE,
    never by list position — a positional zip would silently splice unrelated
    trading days together and produce a plausible-but-wrong series."""
    # Asset A (e.g. a Tokyo-calendar feed): trades Mon/Tue/Wed/Thu.
    dates_a = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
    returns_a = [0.10, 0.20, 0.30, 0.40]
    # Asset B (e.g. a futures feed): trades Tue/Wed/Thu/Fri — offset by one day,
    # same LENGTH as A, so a positional zip would "align" but be wrong.
    dates_b = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    returns_b = [1.0, 2.0, 3.0, 4.0]

    a = _result(dates_a, returns_a)
    b = _result(dates_b, returns_b)

    composite = combine_universe_results([("A", a), ("B", b)], initial_cash=100_000.0)

    # Only the overlapping dates (01-02, 01-03, 01-04) survive.
    assert composite.daily_return_dates == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert composite.bars == 3
    # Correct (date-aligned) means: (0.20+1.0)/2, (0.30+2.0)/2, (0.40+3.0)/2.
    assert composite.daily_returns == pytest.approx([0.6, 1.15, 1.7])
    # A naive positional zip (index 0 of A with index 0 of B, etc.) would
    # instead produce (0.10+1.0)/2, (0.20+2.0)/2, (0.30+3.0)/2, ... — assert
    # the composite is NOT that wrong series.
    wrong_positional = [(0.10 + 1.0) / 2, (0.20 + 2.0) / 2, (0.30 + 3.0) / 2]
    assert composite.daily_returns != pytest.approx(wrong_positional)


def test_no_common_dates_raises() -> None:
    a = _result(["2024-01-02"], [0.01])
    b = _result(["2024-06-01"], [0.02])

    with pytest.raises(ValueError, match="no common trading dates"):
        combine_universe_results([("A", a), ("B", b)], initial_cash=100_000.0)


def test_combine_universe_results_requires_at_least_one_result() -> None:
    with pytest.raises(ValueError, match="at least one"):
        combine_universe_results([], initial_cash=100_000.0)


def test_look_ahead_audit_passed_is_and_of_constituents() -> None:
    dates = ["2024-01-02", "2024-01-03"]
    clean = _result(dates, [0.01, 0.02])
    dirty = _result(dates, [0.01, 0.02])
    dirty.look_ahead_audit_passed = False

    composite = combine_universe_results([("A", clean), ("B", dirty)], initial_cash=100_000.0)
    assert composite.look_ahead_audit_passed is False

    both_clean = combine_universe_results([("A", clean), ("B", clean)], initial_cash=100_000.0)
    assert both_clean.look_ahead_audit_passed is True


# ── Date-coverage recording + loud-truncation warning (item 3) ──────────────


def test_coverage_summary_shape() -> None:
    summary = coverage_summary({"A": 100, "B": 80}, 75)
    assert summary == {
        "per_constituent_bars": {"A": 100, "B": 80},
        "intersection_bars": 75,
        "longest_constituent_bars": 100,
        "intersection_ratio": pytest.approx(0.75),
    }


def test_coverage_summary_empty_constituents_has_no_ratio() -> None:
    summary = coverage_summary({}, 0)
    assert summary["longest_constituent_bars"] == 0
    assert summary["intersection_ratio"] is None


def test_universe_date_coverage_matches_combine_universe_results_intersection() -> None:
    a = _result(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"], [0.01, 0.02, 0.01, 0.0])
    b = _result(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"], [0.01, 0.02, 0.01, 0.0])

    coverage = universe_date_coverage([("A", a), ("B", b)])

    assert coverage["per_constituent_bars"] == {"A": 4, "B": 4}
    assert coverage["intersection_bars"] == 3  # 01-02, 01-03, 01-04 overlap
    assert coverage["longest_constituent_bars"] == 4
    assert coverage["intersection_ratio"] == pytest.approx(0.75)


def test_combine_universe_results_warns_loudly_on_material_truncation(caplog: pytest.LogCaptureFixture) -> None:
    """A constituent with much shorter available history than the others must
    produce a WARNING naming the retained/longest bar counts — distinguishing
    "legitimate limited overlap" from "one feed is truncated or
    data-quality-broken" (item 3)."""
    long_dates = [f"2024-{m:02d}-01" for m in range(1, 11)]  # 10 dates
    short_dates = long_dates[:2]  # only the first 2 overlap — 20% retained
    long_run = _result(long_dates, [0.01] * len(long_dates))
    short_run = _result(short_dates, [0.02] * len(short_dates))

    with caplog.at_level(logging.WARNING, logger="archimedes_analytics_engine.engine"):
        composite = combine_universe_results([("LONG", long_run), ("SHORT", short_run)], initial_cash=100_000.0)

    assert composite.bars == 2
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "retained 2 bars" in message
    assert "10 bars" in message
    assert "20.0%" in message


def test_combine_universe_results_stays_quiet_on_ordinary_calendar_noise(caplog: pytest.LogCaptureFixture) -> None:
    """Losing a small, ordinary fraction of bars (different market holidays
    between two liquid feeds) must NOT trigger the loud-truncation warning —
    only material loss should."""
    dates_a = [f"2024-01-{d:02d}" for d in range(1, 21)]  # 20 dates
    dates_b = [f"2024-01-{d:02d}" for d in range(1, 19)]  # 18 dates — 90% overlap
    a = _result(dates_a, [0.01] * len(dates_a))
    b = _result(dates_b, [0.01] * len(dates_b))

    with caplog.at_level(logging.WARNING, logger="archimedes_analytics_engine.engine"):
        combine_universe_results([("A", a), ("B", b)], initial_cash=100_000.0)

    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_warn_on_truncated_coverage_is_a_noop_below_threshold(caplog: pytest.LogCaptureFixture) -> None:
    coverage = coverage_summary({"A": 100, "B": 95}, 95)  # 95% retained
    with caplog.at_level(logging.WARNING, logger="archimedes_analytics_engine.engine"):
        warn_on_truncated_coverage("test-context", coverage)
    assert not caplog.records


def test_warn_on_truncated_coverage_handles_zero_longest_without_error(caplog: pytest.LogCaptureFixture) -> None:
    coverage = coverage_summary({}, 0)
    with caplog.at_level(logging.WARNING, logger="archimedes_analytics_engine.engine"):
        warn_on_truncated_coverage("test-context", coverage)
    assert not caplog.records

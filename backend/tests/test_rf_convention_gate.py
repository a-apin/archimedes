"""Integration tests: the historical T-bill rf series threaded through the
rigor gate (issue #1409).

Covers the acceptance list's machine-checkable items that live above the
``rf_series`` module itself (unit-tested in ``test_rf_series.py``):
  1. rf_convention disclosure: dates-inside-coverage -> "excess_tbill_series",
     no-dates -> "excess_flat_fallback" — asserted both at the low-level
     ``_rigor_helpers`` functions AND at ``run_rigor_gate`` (the same payload
     path ``dsr_convention`` already rides).
  2. A window straddling the 2015 near-zero-rf era grades measurably
     differently from the flat 5% fallback, with a hand-computed expected
     excess mean. Mutation-proof performed manually (see the PR body
     transcript): reverting `_rigor_helpers.py` to the flat-only code path
     makes ``test_2015_window_...`` fail.
  3. PBO's per-split Sharpe ranking (``compute_pbo`` / ``_sharpe_per_col``)
     also takes the dated rf array — the fourth subtraction site the issue
     names (":810" in the pre-#1409 file).
  4. Existing (no-`dates`) callers are byte-identical to pre-#1409 behavior.

Hermetic: no network, no DB — everything here runs against the vendored CSV
(repo data) and synthetic/seeded return series.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from archimedes.services import rf_series
from archimedes.services._rigor_helpers import (
    _ANNUALIZATION,
    _RF_DAILY,
    _sharpe_dsr_inputs,
    compute_dsr,
    compute_dsr_hac_and_iid,
    compute_in_sample_sharpe,
    compute_oos_sharpe,
    compute_pbo,
)
from archimedes.services.rigor_evaluator import run_rigor_gate


# 40 consecutive dates ACTUALLY PUBLISHED in the vendored series within the
# 2015 near-zero-rf window the decision record cites (rf≈0.02-0.05%) — pulled
# directly from the loaded series so this test has zero dependency on
# business-day/holiday assumptions (every date here is an exact hit, no
# forward-fill involved).
def _2015_dates(n: int = 40) -> list[str]:
    series = rf_series._load_csv()
    return sorted(d for d in series if d.startswith("2015-0"))[:n]


def _seeded_returns(n: int, seed: int = 1409, mu: float = 0.0006, sigma: float = 0.010) -> list[float]:
    return np.random.default_rng(seed).normal(mu, sigma, size=n).tolist()


# ─── 1. rf_convention disclosure — series vs fallback ───────────────────


def test_run_rigor_gate_rf_convention_series_vs_dates_none() -> None:
    """Acceptance item 1: the SAME return series graded (a) with a date index
    inside the vendored series' coverage and (b) with no date index produces
    rf_convention == "excess_tbill_series" vs "excess_flat_fallback"
    respectively — both on the RigorGateResult attribute and gate_details
    (the same payload path dsr_convention rides)."""
    dates = _2015_dates(60)
    returns = _seeded_returns(len(dates))

    with_dates = run_rigor_gate("s-with-dates", returns, num_trials=1, dates=dates)
    without_dates = run_rigor_gate("s-without-dates", returns, num_trials=1)

    assert with_dates.rf_convention == rf_series.RF_CONVENTION_SERIES
    assert with_dates.gate_details["rf_convention"] == rf_series.RF_CONVENTION_SERIES

    assert without_dates.rf_convention == rf_series.RF_CONVENTION_FALLBACK
    assert without_dates.gate_details["rf_convention"] == rf_series.RF_CONVENTION_FALLBACK


def test_run_rigor_gate_rf_convention_falls_back_beyond_coverage() -> None:
    """A date index that exists but falls outside the vendored series' coverage
    (>14 days past the last published date) still discloses the fallback
    honestly, rather than claiming the series convention it didn't use."""
    returns = _seeded_returns(60)
    far_future_dates = [f"2999-01-{(i % 28) + 1:02d}" for i in range(60)]

    result = run_rigor_gate("s-far-future", returns, num_trials=1, dates=far_future_dates)
    assert result.rf_convention == rf_series.RF_CONVENTION_FALLBACK


def test_compute_dsr_rf_convention_via_direct_resolution() -> None:
    """Same disclosure, checked directly against rf_series (what run_rigor_gate
    itself calls) rather than through the gate, for a tighter unit boundary."""
    dates = _2015_dates(10)
    _rates, convention = rf_series.resolve_annual_rf_for_dates(dates)
    assert convention == rf_series.RF_CONVENTION_SERIES

    _rates, convention = rf_series.resolve_annual_rf_for_dates(None)
    assert convention == rf_series.RF_CONVENTION_FALLBACK


# ─── 2. 2015 window regression, hand-computed, mutation-proof (manual) ──


# Hand-transcribed DGS3MO published rates (annualized PERCENT) for the FIRST
# 60 "2015-0*" dates in the vendored series, in `_2015_dates(60)`'s own sort
# order — 2026-08-21 round-4 review fix. Pinned as a LITERAL, sourced by a
# plain independent line-split of `DGS3MO.csv`'s raw text (NOT by calling
# `rf_series._parse_csv_text`/`_load_csv`, the module under test), so a
# parser-level defect in `rf_series.py` cannot move both sides of the
# hand-computed comparison below together — the specific gap a prior version
# of this test left open (loading `hand_rf_daily` via `rf_series._load_csv()`
# shares the exact parser the production path also uses). Historical FRED
# values never change once published (only new dates append), so this literal
# does not go stale across a `scripts/refresh_rf_series.py` run.
_2015_FIRST_60_DGS3MO_PCT = [
    0.02, 0.03, 0.03, 0.03, 0.03, 0.02, 0.03, 0.03, 0.04, 0.03,
    0.03, 0.03, 0.03, 0.03, 0.02, 0.03, 0.02, 0.02, 0.03, 0.02,
    0.02, 0.02, 0.01, 0.02, 0.02, 0.01, 0.01, 0.01, 0.02, 0.01,
    0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.03, 0.02, 0.02,
    0.02, 0.01, 0.02, 0.01, 0.02, 0.02, 0.03, 0.03, 0.03, 0.05,
    0.05, 0.03, 0.03, 0.01, 0.03, 0.02, 0.04, 0.03, 0.04, 0.04,
]  # fmt: skip


def test_2015_window_regresses_from_flat_5pct_with_hand_computed_excess_mean() -> None:
    """A window straddling 2015 (rf≈0.02-0.05% annual) grades MEASURABLY
    differently from the flat-5% convention — hand-computed expected excess
    mean, checked against `_sharpe_dsr_inputs`'s actual output.

    Mutation-proof (performed manually, transcript in the PR body): reverting
    `_rigor_helpers._sharpe_dsr_inputs` to the pre-#1409 flat-only code path
    (deleting the `if dates is not None:` branch) makes this test fail, because
    `dates_inputs.mean` would then equal `flat_inputs.mean` exactly instead of
    differing by the hand-computed delta asserted below.
    """
    dates = _2015_dates(60)
    returns = _seeded_returns(len(dates))
    arr = np.asarray(returns, dtype=float)

    # Independence check (2026-08-21 review fix): the conversion constant below
    # is a LITERAL, not `_ANNUALIZATION` imported from the module under test —
    # importing it would let a future change to `_ANNUALIZATION` silently move
    # both sides of this "hand-computed" comparison together (mutation-proofed
    # below: the assertion at :134 fails when `_ANNUALIZATION` diverges from
    # this literal, see the PR body's round-3 transcript). `_RF_DAILY` is
    # pinned explicitly too, since it is borrowed from production at several
    # OTHER assertions in this file (:148/:156/:161/:216) — those are
    # deliberately checking BYTE-IDENTITY of the untouched flat-rate path
    # against production's own constant, not standing in for an independent
    # hand-computation, so they are left as-is; this assertion is what makes
    # that borrowing safe by pinning the constant itself.
    assert _ANNUALIZATION == 252, "the literal 252 below assumes this; update both together if it ever changes"
    assert pytest.approx(0.05 / 252.0, abs=1e-15) == _RF_DAILY

    # Sanity: the pinned literal really is what `_2015_dates(60)` returns from
    # the CURRENTLY vendored CSV — guards against the literal silently going
    # stale relative to `_2015_dates`'s own date selection (not against a
    # parser defect, which is the whole point of NOT reading this via
    # `rf_series._load_csv()` below).
    series_for_sanity_check_only = rf_series._load_csv()
    assert [series_for_sanity_check_only[d] for d in dates] == _2015_FIRST_60_DGS3MO_PCT

    # Hand-computed: the ACTUAL published DGS3MO rate for every date in the
    # window, from the PINNED LITERAL above (not the module under test),
    # converted to a daily fraction the same way the decision record
    # specifies: rate/100/252 (252 is a LITERAL here, not the imported
    # `_ANNUALIZATION` — see the independence-check comment above).
    hand_rf_daily = np.array(_2015_FIRST_60_DGS3MO_PCT, dtype=float) / 100.0 / 252.0
    hand_excess_mean = float((arr - hand_rf_daily).mean())

    dates_inputs = _sharpe_dsr_inputs(returns, dates=dates)
    flat_inputs = _sharpe_dsr_inputs(returns, dates=None)
    assert dates_inputs is not None and flat_inputs is not None
    arr_dates, _t_d, _sr_d, _sigma_d, mean_dates, _g3_d, _g4_d = dates_inputs
    _arr_f, _t_f, _sr_f, _sigma_f, mean_flat, _g3_f, _g4_f = flat_inputs

    # The hand-computed excess mean matches _sharpe_dsr_inputs's dates-aware
    # mean to float precision.
    assert mean_dates == pytest.approx(hand_excess_mean, abs=1e-12)

    # The series returned alongside it is the EXCESS series, not raw returns —
    # load-bearing for compute_dsr_hac_and_iid's influence-function variance,
    # which computes z-scores against this exact array (see the comment at the
    # `dates is not None` branch's `return excess, ...` in _rigor_helpers.py).
    np.testing.assert_allclose(arr_dates, arr - hand_rf_daily, atol=1e-15)

    # The flat-fallback path is BYTE-IDENTICAL to pre-#1409: `mean` there is
    # the RAW series mean (only SR_hat subtracts the flat rf, matching the
    # original formula `SR_hat = (mean - _RF_DAILY) / sigma`) — confirm it,
    # so a future edit that starts returning an excess mean on the flat path
    # too (breaking that byte-identity) is caught here.
    assert mean_flat == pytest.approx(float(arr.mean()), abs=1e-12)
    assert _sr_f == pytest.approx((float(arr.mean()) - _RF_DAILY) / _sigma_f, abs=1e-12)

    # 2015's real rf (~0.02-0.05%/yr) is dramatically below the flat 5%
    # fallback, so the 2015-aligned excess mean must be MATERIALLY higher than
    # the excess mean the flat-5% convention would have produced (NOT
    # `mean_flat` itself, which — per the byte-identity assertion just above —
    # is the RAW mean; the flat path's own excess figure is `raw_mean -
    # _RF_DAILY`, computed explicitly here for a like-for-like comparison).
    mean_flat_excess = float(arr.mean()) - _RF_DAILY
    delta = mean_dates - mean_flat_excess
    assert delta > 1e-5, f"2015-window rf substitution produced too small a delta ({delta}) to be real"
    # And it should be close to the hand-computed rf-only delta (real 2015 rf
    # minus the flat 5% rate), not some unrelated magnitude.
    assert delta == pytest.approx(_RF_DAILY - float(hand_rf_daily.mean()), abs=1e-9)

    # And the DSR / p-value computed through the public API move the same way.
    dsr_dates, p_dates = compute_dsr(returns, num_trials=1, dates=dates)
    dsr_flat, p_flat = compute_dsr(returns, num_trials=1)
    assert dsr_dates is not None and dsr_flat is not None
    assert dsr_dates > dsr_flat, "2015's near-zero true rf must raise the DSR versus the over-subtracting flat rate"
    assert p_dates > p_flat


def test_flat_5pct_vs_live_rate_delta_matches_decision_record_direction() -> None:
    """Sanity-checks the decision record's own headline number: the vendored
    series' most recent rate sits materially BELOW the flat 5% fallback (the
    record cites ~3.79-3.87%, i.e. the flat rate over-subtracts ~110-120bps).

    2026-08-21 review fix: this used to also assert a narrow `3.0 < rate <
    4.5` band around the decision record's cited value. That couples CI to
    wherever the 3M T-bill happens to trade after a future
    `scripts/refresh_rf_series.py` run (over the vendored series' own
    history, the rate has spent 35.8% of all 180-day windows moving by more
    than the 64bp of headroom that band had left) — a market move, not a
    code regression, would turn CI red. The load-bearing claim is just "still
    materially below the flat fallback"; a non-negativity floor is the only
    other half of the wide sanity bound
    (`test_rf_series.py::test_vendored_csv_loads_and_is_nonempty`) that can
    still bind here, since `< 5.0` already subsumes that bound's upper end —
    asserting `<= 25.0` alongside it would be dead code, not a second guard."""
    series = rf_series._load_csv()
    latest_date = max(series)
    latest_rate_pct = series[latest_date]
    assert latest_rate_pct < 5.0
    assert latest_rate_pct >= 0.0  # a negative published T-bill rate would mean a parsing bug


def test_hac_dsr_uses_the_same_excess_series_for_influence_variance() -> None:
    """run_rigor_gate's actual production call (compute_dsr_hac_and_iid with
    hac_lags="auto") must produce a FINITE, sane HAC p-value on the dates
    path — this catches the specific incoherent-z-score bug where the
    influence function was computed against RAW returns but an EXCESS
    mean/sigma (a real bug caught during this issue's own test-writing): that
    bug does not crash, it silently produces a wrong (but finite-looking)
    p-value, so this compares the HAC p-value's SIGN/magnitude against the
    already-verified IID p-value from `compute_dsr` on the same dated input
    rather than merely checking "did not raise"."""
    dates = _2015_dates(120)
    returns = _seeded_returns(len(dates), mu=0.0006, sigma=0.010)

    dsr_hac, p_hac, dsr_iid, p_iid = compute_dsr_hac_and_iid(returns, num_trials=1, dates=dates, hac_lags="auto")
    assert dsr_hac is not None and p_hac is not None
    assert dsr_iid is not None and p_iid is not None
    assert np.isfinite(dsr_hac) and 0.0 <= p_hac <= 1.0
    # HAC and IID SEs both derive from the SAME (correctly excess) series, so
    # for a short-memory synthetic series (no real autocorrelation injected)
    # they must land close together — a coherence check that would fail hard
    # if the two SE paths were secretly looking at different series.
    assert abs(p_hac - p_iid) < 0.05


# ─── 3. Existing (no-`dates`) callers are byte-identical ────────────────


def test_no_dates_is_byte_identical_to_pre_1409_formula() -> None:
    """Every pre-#1409 call site (dates omitted) must see EXACTLY the same
    numbers as before — the flat-rate code path is untouched, not merely
    approximated by the new branch."""
    returns = _seeded_returns(300, seed=7)
    arr = np.asarray(returns, dtype=float)
    sigma = float(arr.std(ddof=1))
    expected_sr_hat = (float(arr.mean()) - _RF_DAILY) / sigma

    inputs = _sharpe_dsr_inputs(returns)
    assert inputs is not None
    _arr, _t, sr_hat, _sigma, _mean, _g3, _g4 = inputs
    assert sr_hat == pytest.approx(expected_sr_hat, abs=1e-15)

    oos_dates_none = compute_oos_sharpe(returns)
    oos_no_dates_kw = compute_oos_sharpe(returns, dates=None)
    assert oos_dates_none == oos_no_dates_kw

    is_dates_none = compute_in_sample_sharpe(returns)
    is_no_dates_kw = compute_in_sample_sharpe(returns, dates=None)
    assert is_dates_none == is_no_dates_kw


# ─── 4. OOS / in-sample Sharpe also thread dates correctly (sliced) ────


def test_oos_sharpe_uses_the_oos_slice_of_dates_not_the_full_window() -> None:
    """compute_oos_sharpe must slice `dates` to the SAME chronological OOS
    tail it slices `daily_returns` to — using the full-window dates for the
    OOS-only rf lookup would silently misalign rf against returns."""
    dates = _2015_dates(80)
    returns = _seeded_returns(len(dates))

    oos_dates = compute_oos_sharpe(returns, dates=dates)
    oos_flat = compute_oos_sharpe(returns)
    assert oos_dates is not None and oos_flat is not None
    assert oos_dates != oos_flat

    # A deliberately misaligned (reversed) date order should, in general,
    # resolve to different rf values than the correctly-ordered slice —
    # guards against a caller (or a future edit) silently passing the wrong
    # slice through.
    oos_dates_reversed = compute_oos_sharpe(returns, dates=list(reversed(dates)))
    assert oos_dates_reversed != oos_dates


def test_in_sample_sharpe_uses_the_is_slice_of_dates() -> None:
    dates = _2015_dates(80)
    returns = _seeded_returns(len(dates))

    is_dates = compute_in_sample_sharpe(returns, dates=dates)
    is_flat = compute_in_sample_sharpe(returns)
    assert is_dates is not None and is_flat is not None
    assert is_dates != is_flat


# ─── 5. PBO's per-split Sharpe ranking also takes the dated rf array ────


def test_compute_pbo_accepts_dates_without_changing_shape() -> None:
    """PBO is the fourth subtraction site the issue names (_sharpe_per_col).
    Threading `dates` must not change compute_pbo's return shape/contract —
    only the per-split Sharpe ranking's rf input."""
    dates = _2015_dates(64)
    matrix = {
        "a": _seeded_returns(len(dates), seed=101, mu=0.0008),
        "b": _seeded_returns(len(dates), seed=102, mu=0.0002),
        "c": _seeded_returns(len(dates), seed=103, mu=-0.0003),
    }
    scores_flat = compute_pbo(matrix, s_partitions=8)
    scores_dated = compute_pbo(matrix, s_partitions=8, dates=dates)

    assert set(scores_flat) == set(scores_dated) == set(matrix)
    for v in scores_dated.values():
        assert 0.0 <= v <= 1.0


def test_compute_pbo_dates_none_is_byte_identical_to_pre_1409() -> None:
    dates = _2015_dates(64)
    matrix = {
        "a": _seeded_returns(len(dates), seed=201, mu=0.0008),
        "b": _seeded_returns(len(dates), seed=202, mu=0.0002),
    }
    a = compute_pbo(matrix, s_partitions=8)
    b = compute_pbo(matrix, s_partitions=8, dates=None)
    assert a == b


# ─── 6. Mismatched-length dates falls back loudly instead of crashing ──


def test_mismatched_length_dates_falls_back_instead_of_crashing(caplog: pytest.LogCaptureFixture) -> None:
    returns = _seeded_returns(30)
    short_dates = _2015_dates(5)  # deliberately wrong length vs. 30 returns

    with caplog.at_level("WARNING"):
        dsr, p = compute_dsr(returns, num_trials=1, dates=short_dates)
    dsr_flat, p_flat = compute_dsr(returns, num_trials=1)
    assert dsr == pytest.approx(dsr_flat)
    assert p == pytest.approx(p_flat)
    assert any("length" in rec.message.lower() for rec in caplog.records)


# ─── 7. run_rigor_gate: the disclosed marker matches every metric's ─────
#        arithmetic, by construction (2026-08-20 review fix).
#
# Prior to this fix, `rf_convention` was derived from a SECOND, independent
# call over the raw `dates` (never validated against the return series'
# length, and computed over the FULL window while DSR/OOS/IS resolve their
# OWN slices). Two concrete ways that silently disagreed with the arithmetic:
#   - a `dates` list whose length didn't match `daily_returns` made the
#     marker compute from the mismatched `dates` while every metric silently
#     fell back to flat via `_resolve_rf_daily_array`'s own length guard;
#   - a window whose tail ran past the vendored series' 14-day grace window
#     made the FULL-list marker resolution fail (-> disclosed FALLBACK) while
#     the IS head slice, not containing the stale tail, resolved fine on its
#     own -> in_sample_sharpe silently used the T-bill series anyway.
# Both are exactly the "silent partial substitution" issue #1409 design item
# 4 forbids. `_resolve_gate_rf` (rigor_evaluator.py) closes this by resolving
# ONE convention up front and forcing every downstream call to `dates=None`
# whenever that resolution is FALLBACK — so the whole grade can never
# disagree with what it discloses.


def test_length_mismatch_forces_fallback_and_flat_arithmetic() -> None:
    """A `dates` list whose length disagrees with `daily_returns` must report
    rf_convention=FALLBACK AND every metric must equal the dates=None (flat)
    run byte-for-byte — not merely "close", and not silently grade on the
    series while claiming flat."""
    returns = _seeded_returns(60)
    mismatched_dates = _2015_dates(5)  # 5 dates for 60 returns — deliberate mismatch

    with_bad_dates = run_rigor_gate("s-length-mismatch", returns, num_trials=1, dates=mismatched_dates)
    flat = run_rigor_gate("s-flat-equivalent", returns, num_trials=1)

    assert with_bad_dates.rf_convention == rf_series.RF_CONVENTION_FALLBACK
    assert with_bad_dates.gate_details["rf_convention"] == rf_series.RF_CONVENTION_FALLBACK
    assert with_bad_dates.deflated_sharpe == flat.deflated_sharpe
    assert with_bad_dates.dsr_p_value == flat.dsr_p_value
    assert with_bad_dates.oos_sharpe == flat.oos_sharpe
    assert with_bad_dates.in_sample_sharpe == flat.in_sample_sharpe


def test_grace_window_straddle_forces_fallback_for_every_metric() -> None:
    """A window whose tail runs past the vendored series' 14-day forward-fill
    grace window makes the FULL-list resolution fail — but the IS head slice,
    resolved on its own, would have succeeded (asserted explicitly below, so
    this is a genuine straddle, not a case where everything fails and the
    test would pass vacuously). Before the fix this made
    rf_convention=FALLBACK while in_sample_sharpe silently used the T-bill
    series anyway. Self-triggers as the vendored CSV ages — no mutation
    needed to hit the underlying condition, only a stale-enough tail date.

    2026-08-21 review fix: the tail used to be a HARDCODED "2026-11-*" date
    range, reasoned as "well past 2026-08-18 + 14d" against the vendored
    series' date AT THE TIME THIS TEST WAS WRITTEN. That premise silently
    inverts the moment someone runs `scripts/refresh_rf_series.py` (this PR's
    own documented maintenance action) and the CSV's last published date
    crosses 2026-11-14ish — CI goes red on a maintenance day, not a
    regression. The tail is now derived from the vendored series' OWN max
    published date, so this test stays a genuine straddle regardless of how
    far the CSV has been refreshed."""
    n_head, n_tail = 70, 30
    head_dates = _2015_dates(n_head)
    series = rf_series._load_csv()
    last_published = date.fromisoformat(max(series))
    # Comfortably past the forward-fill grace window (14 days) so every tail
    # date is guaranteed unresolvable against whatever the series covers today.
    stale_start = last_published + timedelta(days=rf_series.MAX_FORWARD_FILL_DAYS + 16)
    tail_dates = [(stale_start + timedelta(days=i)).isoformat() for i in range(n_tail)]
    dates = head_dates + tail_dates
    returns = _seeded_returns(len(dates))

    result = run_rigor_gate("s-stale-tail", returns, num_trials=1, dates=dates)
    flat = run_rigor_gate("s-stale-tail-flat-equivalent", returns, num_trials=1)

    assert result.rf_convention == rf_series.RF_CONVENTION_FALLBACK
    assert result.gate_details["rf_convention"] == rf_series.RF_CONVENTION_FALLBACK
    assert result.deflated_sharpe == flat.deflated_sharpe
    assert result.dsr_p_value == flat.dsr_p_value
    assert result.oos_sharpe == flat.oos_sharpe
    assert result.in_sample_sharpe == flat.in_sample_sharpe

    # Prove the straddle is real: the IS head slice ALONE (which is what the
    # pre-fix code independently re-resolved for in_sample_sharpe) resolves
    # fine against the vendored series.
    split = int(len(returns) * 0.70)
    is_dates_only = dates[:split]
    _rates, is_only_convention = rf_series.resolve_annual_rf_for_dates(is_dates_only)
    assert is_only_convention == rf_series.RF_CONVENTION_SERIES


def test_run_rigor_gate_dates_produces_measurably_different_values_from_flat() -> None:
    """POSITIVE-direction regression guard for `run_rigor_gate`'s three
    gate-level `dates` threadings (2026-08-21 review finding): every other
    test in this file that exercises `run_rigor_gate` with in-coverage dates
    asserts EQUALITY with the flat run (the fallback/mismatch/straddle
    tests above) or only the `rf_convention` marker string
    (`test_run_rigor_gate_rf_convention_series_vs_dates_none`) — none pins a
    NUMBER on the dates-vs-flat DIFFERENCE for DSR, OOS, or in-sample Sharpe
    at the gate level. That gap meant each of the three `dates=resolved_dates`
    call sites inside `run_rigor_gate` (DSR at :1276-1278, OOS at :1320, the
    inline in-sample block at :1365) could be reverted to the flat path
    INDEPENDENTLY while `rf_convention` still reported
    `excess_tbill_series` and the whole suite stayed green — i.e. the PR
    body's round-2 claim "the marker and the arithmetic can never disagree,
    by construction" was not enforced by any test.

    Fix: pin the actual with-dates values (computed once against this
    branch's head and asserted here, not merely `!=`) so each of the three
    call sites is independently guarded. >=120 in-coverage dates are used
    deliberately — at 60 (the length several sibling tests above use)
    `oos_sharpe` is `None` on both runs, which would NOT catch the OOS
    mutation.

    Mutation-proof (transcript in the PR body, round 3): deleting
    `dates=resolved_dates` from any ONE of the three call sites above makes
    exactly the corresponding assertion below fail (the with-dates value
    collapses to the flat one), while the other two + `rf_convention` keep
    passing — proving each site is independently guarded, not just the
    aggregate."""
    dates = _2015_dates(120)
    returns = _seeded_returns(len(dates))

    with_dates = run_rigor_gate("s-gate-dates-value", returns, num_trials=1, dates=dates)
    flat = run_rigor_gate("s-gate-dates-value-flat", returns, num_trials=1)

    assert with_dates.rf_convention == rf_series.RF_CONVENTION_SERIES
    assert with_dates.gate_details["rf_convention"] == rf_series.RF_CONVENTION_SERIES
    assert flat.rf_convention == rf_series.RF_CONVENTION_FALLBACK

    # Pinned with-dates values, computed once against this branch's head
    # (seed=1409, mu=0.0006, sigma=0.010, the same 120 real 2015 DGS3MO dates
    # `_2015_dates(120)` returns). Each is asserted against the FLAT run's
    # actual value too, so a future edit that moves the flat path can't
    # silently make this test meaningless by moving both sides together.
    assert with_dates.deflated_sharpe == pytest.approx(2.075778, abs=1e-6)
    assert flat.deflated_sharpe == pytest.approx(1.730572, abs=1e-6)
    assert with_dates.deflated_sharpe != flat.deflated_sharpe
    assert with_dates.deflated_sharpe - flat.deflated_sharpe == pytest.approx(0.345206, abs=1e-5)

    assert with_dates.dsr_p_value == pytest.approx(0.918208, abs=1e-6)
    assert flat.dsr_p_value == pytest.approx(0.876839, abs=1e-6)
    assert with_dates.dsr_p_value != flat.dsr_p_value

    assert with_dates.oos_sharpe is not None and flat.oos_sharpe is not None
    assert with_dates.oos_sharpe == pytest.approx(0.297985, abs=1e-6)
    assert flat.oos_sharpe == pytest.approx(-0.039113, abs=1e-6)
    assert with_dates.oos_sharpe != flat.oos_sharpe

    assert with_dates.in_sample_sharpe is not None and flat.in_sample_sharpe is not None
    assert with_dates.in_sample_sharpe == pytest.approx(2.859112, abs=1e-5)
    assert flat.in_sample_sharpe == pytest.approx(2.511074, abs=1e-5)
    assert with_dates.in_sample_sharpe != flat.in_sample_sharpe


# ─── 8. gate_details["rf_convention"] reports MISSING when nothing at all ──
#        was computed (2026-08-21 round-4 review fix)


def test_gate_details_rf_convention_is_missing_when_no_excess_metric_computed() -> None:
    """A too-short return series (< 4 bars) computes NO DSR (T<4 guard), NO
    OOS Sharpe (T<10 guard), and NO in-sample Sharpe (`run_rigor_gate`'s own
    inline in-sample block requires `len(daily_returns) >= 4`) at all —
    `_resolve_gate_rf` still unconditionally resolves `rf_convention` from
    `dates` alone (here, `dates=None` -> `excess_flat_fallback`), independent
    of whether any metric downstream actually used it. Disclosing
    `excess_flat_fallback` in that case implies a flat-rate COMPUTATION
    genuinely ran, when none did — the same distinction `LibraryPbo.rf_convention`
    already draws ("MISSING", not the raw resolution, when there is no PBO
    value to attribute a convention to). `gate_details["rf_convention"]` must
    report `"MISSING"` here, not the raw (possibly misleading)
    `self.rf_convention`."""
    too_short_returns = [0.001, 0.002, -0.001]  # T=3: below every one of the three metrics' own length floors

    result = run_rigor_gate("s-degenerate-no-metrics", too_short_returns, num_trials=1)

    assert result.deflated_sharpe is None
    assert result.oos_sharpe is None
    assert result.in_sample_sharpe is None
    # The raw attribute is untouched (still reflects `_resolve_gate_rf`'s
    # resolution, for any consumer that wants it) — only the DISCLOSURE layer
    # (`gate_details`) substitutes "MISSING".
    assert result.rf_convention == rf_series.RF_CONVENTION_FALLBACK
    assert result.gate_details["rf_convention"] == "MISSING"


def test_gate_details_rf_convention_stays_real_when_any_one_metric_computed() -> None:
    """The MISSING substitution above must NOT fire just because ONE of the
    three metrics is None (e.g. a series too short for OOS but long enough
    for DSR) — only when ALL THREE are None. A normal, healthy series with
    dates in coverage must still disclose the real convention."""
    dates = _2015_dates(60)
    returns = _seeded_returns(len(dates))

    result = run_rigor_gate("s-healthy", returns, num_trials=1, dates=dates)

    assert result.deflated_sharpe is not None  # at least DSR computed
    assert result.rf_convention == rf_series.RF_CONVENTION_SERIES
    assert result.gate_details["rf_convention"] == rf_series.RF_CONVENTION_SERIES


# ─── 9. PBO's dates threading has a real, mutation-provable value effect ──


def _business_days(start: str, end: str) -> list[str]:
    import datetime as _dt

    start_d = _dt.date.fromisoformat(start)
    end_d = _dt.date.fromisoformat(end)
    out: list[str] = []
    d = start_d
    while d <= end_d:
        if d.weekday() < 5:  # Mon-Fri
            out.append(d.isoformat())
        d += _dt.timedelta(days=1)
    return out


def test_compute_pbo_dates_materially_changes_the_value() -> None:
    """`test_compute_pbo_accepts_dates_without_changing_shape` (above) checks
    shape only — it passes even if `dates` threading were entirely dead code
    (verified: neutering `_sharpe_per_col`'s `rf_daily is not None` branch and
    both `dates is not None` ternaries in `compute_pbo` leaves the whole
    module's test suite green, see the PR body's round-2 mutation
    transcript). This pins an actual VALUE effect on a real, low-rate window
    (2008-2010 business days, where the true rf sits well below the flat 5%
    fallback) with a seed pinned to a value known not to tie — PBO is a rank
    statistic quantized to 1/S-sized steps, and some seeds land on the same
    bucket either way; this one measurably doesn't."""
    dates = _business_days("2008-01-02", "2010-12-31")
    rng = np.random.default_rng(0)
    matrix = {
        "a": rng.normal(0.0004, 0.01, len(dates)).tolist(),
        "b": rng.normal(0.0002, 0.012, len(dates)).tolist(),
        "c": rng.normal(0.0006, 0.009, len(dates)).tolist(),
        "d": rng.normal(-0.0001, 0.011, len(dates)).tolist(),
    }

    flat = compute_pbo(matrix, s_partitions=8)
    dated = compute_pbo(matrix, s_partitions=8, dates=dates)

    flat_v = next(iter(flat.values()))
    dated_v = next(iter(dated.values()))
    assert flat_v == pytest.approx(0.828571, abs=1e-6)
    assert dated_v == pytest.approx(0.814286, abs=1e-6)
    assert flat_v != dated_v


def test_compute_pbo_dates_axis_truncates_from_head_not_tail() -> None:
    """2026-08-21 round-4 review finding: `compute_pbo` truncates `dates` the
    SAME way it truncates the returns matrix (`list(dates)[:T]` at
    `_rigor_helpers.py`, matching `R`'s own `[:T]` per-series truncation) --
    the FIRST T entries, never the last. No prior test distinguished head-
    from tail-truncation, so flipping that slice to `[-T:]` would silently
    misalign the rf array against the return rows while the whole suite
    stayed green.

    Reuses `test_compute_pbo_dates_materially_changes_the_value`'s exact
    matrix/dates/seed above -- its two pinned values (0.814286 dates-aligned,
    0.828571 flat-fallback) are the two outcomes this test distinguishes
    between -- and pads `dates` with unresolvable far-future entries so the
    array handed to `compute_pbo` is longer than T. Correct (head)
    truncation recovers exactly the real business-day dates -> the SERIES
    value (0.814286); tail truncation would recover only the unresolvable
    padding -> every date unresolvable -> the FLAT-fallback value (0.828571).
    """
    dates = _business_days("2008-01-02", "2010-12-31")
    T = len(dates)
    padding = [f"2999-01-{(i % 28) + 1:02d}" for i in range(T)]  # >= T unresolvable dates
    padded_dates = dates + padding  # length 2T; [:T] == dates, [-T:] == padding

    rng = np.random.default_rng(0)
    matrix = {
        "a": rng.normal(0.0004, 0.01, T).tolist(),
        "b": rng.normal(0.0002, 0.012, T).tolist(),
        "c": rng.normal(0.0006, 0.009, T).tolist(),
        "d": rng.normal(-0.0001, 0.011, T).tolist(),
    }

    result_padded = compute_pbo(matrix, s_partitions=8, dates=padded_dates)
    v_padded = next(iter(result_padded.values()))

    # Correct (head-truncation) behavior: padding the TAIL of `dates` must not
    # change the result from the un-padded, dates-aligned run.
    assert v_padded == pytest.approx(0.814286, abs=1e-6)
    # And it must NOT collapse to what tail-truncation (`dates[-T:]` ==
    # `padding`, all unresolvable) would have produced.
    assert v_padded != pytest.approx(0.828571, abs=1e-6)

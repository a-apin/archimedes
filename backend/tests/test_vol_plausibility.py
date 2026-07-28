"""Hermetic unit tests for services/vol_plausibility.py — the mechanical detector
for the confirmed "backtesting the wrong asset" defect class (audit 2026-07-27).

No DB, no network. ``classify_ticker``/``classify_universe`` read
``archimedes.universe.SYNTHETIC_UNIVERSE``, which is itself a pure JSON-file read at
import time (no DB/network) — see backend/archimedes/universe.py.
"""

from __future__ import annotations

import numpy as np
from archimedes.services import vol_plausibility as vp

# Deterministic synthetic daily-return series standing in for real persisted
# data. Seeded so the exact numeric assertions below are reproducible.


def _spy_like_returns(n: int = 5659, seed: int = 20260727) -> list[float]:
    """~18-19% annualized vol, positive drift — stands in for the real,
    confirmed-correct pipeline_buy_hold series (18.65% vol per the audit)."""
    rng = np.random.default_rng(seed)
    return rng.normal(loc=0.00045, scale=0.01175, size=n).tolist()


def _near_zero_vol_returns(n: int = 5659, seed: int = 7) -> list[float]:
    """~0.2-0.4% annualized vol — stands in for a real T-bill-proxy series."""
    rng = np.random.default_rng(seed)
    return rng.normal(loc=0.00007, scale=0.0006, size=n).tolist()


# ─── classify_ticker / classify_universe ──────────────────────────────────


def test_classify_ticker_resolves_via_onchain_ssot() -> None:
    assert vp.classify_ticker("SPY") == "broad_equity"
    assert vp.classify_ticker("BIL") == "cash_tbill"
    assert vp.classify_ticker("TLT") == "long_bond"
    assert vp.classify_ticker("GLD") == "commodity"


def test_classify_ticker_is_case_insensitive_and_strips_whitespace() -> None:
    assert vp.classify_ticker("  spy ") == "broad_equity"
    assert vp.classify_ticker("bil") == "cash_tbill"


def test_classify_ticker_resolves_supplemental_legacy_aliases() -> None:
    # NIKKEI/GOLD/TREASURY/OIL predate the on-chain SSOT (analytics-engine
    # "operation" aliases); KO/PEP are single-name Gatev-pairs legs. All five
    # strategy files in the repo that declare these must resolve, not silently
    # fall through to "unclassified".
    assert vp.classify_ticker("NIKKEI") == "broad_equity"
    assert vp.classify_ticker("GOLD") == "commodity"
    assert vp.classify_ticker("TREASURY") == "long_bond"
    assert vp.classify_ticker("OIL") == "commodity"
    assert vp.classify_ticker("KO") == "single_name_equity"
    assert vp.classify_ticker("PEP") == "single_name_equity"


def test_classify_ticker_unknown_ticker_is_unclassified_not_guessed() -> None:
    assert vp.classify_ticker("ZZZNOTATICKER") is None


def test_classify_universe_single_bucket_gets_a_tight_band() -> None:
    result = vp.classify_universe(["BIL"])
    assert result.buckets == ("cash_tbill",)
    assert result.band == (0.0, 0.03)
    assert result.is_diversified is False
    assert result.is_fully_classified is True


def test_classify_universe_multi_bucket_combines_to_widest_band() -> None:
    result = vp.classify_universe(["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"])
    assert set(result.buckets) == {"broad_equity", "commodity", "long_bond"}
    assert result.is_diversified is True
    lo, hi = result.band
    assert lo == min(0.08, 0.10, 0.02)  # broad_equity.lo, commodity.lo, long_bond.lo
    assert hi == max(0.35, 0.55, 0.20)  # commodity.hi is the widest leg


def test_classify_universe_any_unclassified_ticker_blanks_the_band() -> None:
    result = vp.classify_universe(["SPY", "NOT_A_REAL_TICKER"])
    assert result.band is None
    assert result.unclassified == ("NOT_A_REAL_TICKER",)
    assert not result.is_fully_classified


# ─── compute_vol_stats ─────────────────────────────────────────────────────


def test_compute_vol_stats_matches_portfolio_backtester_convention() -> None:
    """252 trading days, ddof=1 — same formula as
    services/portfolio_backtester.py::_annualized_metrics, so numbers agree
    repo-wide."""
    returns = _spy_like_returns()
    stats = vp.compute_vol_stats(returns)

    arr = np.asarray(returns)
    expected_vol = float(arr.std(ddof=1)) * (252**0.5)

    assert stats.n_obs == len(returns)
    assert stats.degenerate is False
    assert stats.insufficient_data is False
    assert stats.annualized_vol == expected_vol
    assert stats.annualized_return is not None


def test_compute_vol_stats_degenerate_all_zero_series() -> None:
    """The exact defect noted in the audit: 5 strategies have realized vol
    EXACTLY 0.00000 across thousands of observations — a constant series. Must
    be reported as its own category, not a crash and not a silent 'pass'."""
    stats = vp.compute_vol_stats([0.0] * 5659)
    assert stats.degenerate is True
    assert stats.annualized_vol is None
    assert stats.annualized_return is None
    assert stats.n_obs == 5659


def test_compute_vol_stats_degenerate_constant_nonzero_series() -> None:
    """np.ptp-based check (matching _rigor_helpers.compute_dsr) catches ANY
    constant series, not just all-zero — floating-point std(ddof=1) can be a
    tiny nonzero float for identical values, which is exactly why range (ptp),
    not std, is the check."""
    stats = vp.compute_vol_stats([0.001] * 100)
    assert stats.degenerate is True


def test_compute_vol_stats_insufficient_data_short_series() -> None:
    stats = vp.compute_vol_stats([0.01, -0.02, 0.005])
    assert stats.insufficient_data is True
    assert stats.degenerate is False
    assert stats.annualized_vol is None


def test_compute_vol_stats_empty_series_is_insufficient_not_a_crash() -> None:
    stats = vp.compute_vol_stats([])
    assert stats.insufficient_data is True
    assert stats.n_obs == 0


def test_compute_vol_stats_pathological_wipeout_return_is_none_not_a_crash() -> None:
    """A >= -100% day drives the synthetic equity curve to <= 0 — CAGR is
    undefined there. Must degrade to None, never raise/NaN silently."""
    returns = [0.01] * 20 + [-1.5] + [0.01] * 20
    stats = vp.compute_vol_stats(returns)
    assert stats.annualized_return is None
    assert stats.annualized_vol is not None  # vol is still well-defined


def test_daily_returns_from_equity_curve_matches_pct_change() -> None:
    curve = [100.0, 101.0, 99.99]
    out = vp.daily_returns_from_equity_curve(curve)
    assert out == [0.01, (99.99 - 101.0) / 101.0]


def test_daily_returns_from_equity_curve_too_short_returns_empty() -> None:
    assert vp.daily_returns_from_equity_curve([100.0]) == []
    assert vp.daily_returns_from_equity_curve([]) == []


# ─── assess_strategy — the combined verdict ───────────────────────────────


def test_assess_strategy_clean_strategy_passes() -> None:
    """pipeline_buy_hold itself: single-ticker SPY, matches the baseline by
    construction (it IS the baseline) — must be 'clean', not flagged, even
    though Rule B's whole purpose is to catch equity-baseline matches."""
    returns = _spy_like_returns()
    baseline_vol = vp.compute_vol_stats(returns).annualized_vol

    verdict = vp.assess_strategy(["SPY"], returns, baseline_vol=baseline_vol)

    assert verdict.status == "clean"
    assert verdict.reasons == ()
    assert verdict.is_clean is True
    assert verdict.needs_attention is False


def test_assess_strategy_flags_bil_declared_strategy_with_equity_vol() -> None:
    """The confirmed defect: capital_preservation_tbill declares ASSET_UNIVERSE
    = ["BIL"] but its persisted series reads ~18-19% vol — pure equity, not a
    T-bill ETF (real-world T-bill vol ~0.2%). Must be flagged by Rule A alone
    (self-contained — no baseline needed) AND, when a baseline is supplied, by
    Rule B too."""
    equity_returns = _spy_like_returns()

    # Rule A alone (no cross-strategy baseline — the write-time-guard shape).
    verdict_rule_a_only = vp.assess_strategy(["BIL"], equity_returns)
    assert verdict_rule_a_only.status == "flagged"
    assert any("plausible band" in r for r in verdict_rule_a_only.reasons)

    # Rule A + Rule B (the full audit-script shape).
    baseline_vol = vp.compute_vol_stats(equity_returns).annualized_vol
    verdict_full = vp.assess_strategy(["BIL"], equity_returns, baseline_vol=baseline_vol)
    assert verdict_full.status == "flagged"
    assert any("equity baseline" in r for r in verdict_full.reasons)
    assert verdict_full.needs_attention is True


def test_assess_strategy_does_not_flag_a_genuine_low_vol_tbill_series() -> None:
    """A BIL-declared strategy whose persisted vol IS actually near-zero (the
    correct, non-defective case) must come back clean — the detector should
    not cry wolf on the strategy it exists to vindicate."""
    real_bil_returns = _near_zero_vol_returns()
    verdict = vp.assess_strategy(["BIL"], real_bil_returns)
    assert verdict.status == "clean"


def test_assess_strategy_flags_diversified_universe_matching_equity_baseline() -> None:
    """The second confirmed defect: maillard_2010_risk_parity declares a
    5-asset, risk-parity-weighted universe (which by construction should have
    LOWER vol than pure equity) but persists ~18.46% vol — statistically
    indistinguishable from the pure-SPY baseline (18.65%). Rule A's wide
    multi-bucket band alone does NOT catch this (by design — see module
    docstring); Rule B does."""
    baseline_returns = _spy_like_returns(seed=1)
    baseline_vol = vp.compute_vol_stats(baseline_returns).annualized_vol

    # A slightly different, still near-identical-vol series for the "risk
    # parity" strategy — the real-world maillard delta was ~1% relative.
    near_match_returns = _spy_like_returns(seed=2)

    universe = ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]

    verdict_rule_a_only = vp.assess_strategy(universe, near_match_returns)
    assert verdict_rule_a_only.status == "clean", (
        "Rule A's wide multi-bucket band should NOT catch this on its own — "
        "documented limitation, exactly why Rule B exists"
    )

    verdict_full = vp.assess_strategy(universe, near_match_returns, baseline_vol=baseline_vol)
    assert verdict_full.status == "flagged"
    assert any("equity baseline" in r for r in verdict_full.reasons)


def test_assess_strategy_degenerate_series_is_its_own_category() -> None:
    """Not a pass, not a crash — a distinct 'degenerate' status."""
    verdict = vp.assess_strategy(["BIL"], [0.0] * 5659)
    assert verdict.status == "degenerate"
    assert verdict.needs_attention is True
    assert verdict.vol_stats.annualized_vol is None


def test_assess_strategy_no_returns_is_insufficient_data_not_a_crash() -> None:
    """The module-level contract for an empty series (the script layer maps
    this to its own 'skipped' status for a strategy with no persisted backtest
    at all — see test_audit_backtest_universe.py)."""
    verdict = vp.assess_strategy(["BIL"], [])
    assert verdict.status == "insufficient_data"
    assert verdict.needs_attention is False


def test_assess_strategy_unclassified_universe_flags_for_human_review() -> None:
    equity_returns = _spy_like_returns()
    verdict = vp.assess_strategy(["NOT_A_REAL_TICKER"], equity_returns)
    assert verdict.status == "unclassified"
    assert verdict.needs_attention is True
    assert "NOT_A_REAL_TICKER" in verdict.reasons[0]


def test_assess_strategy_no_baseline_available_degrades_to_rule_a_only() -> None:
    """baseline_vol=None must never raise — degrade gracefully to Rule A."""
    equity_returns = _spy_like_returns()
    verdict = vp.assess_strategy(["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"], equity_returns, baseline_vol=None)
    assert verdict.baseline_vol is None
    assert verdict.baseline_delta is None
    # Clean because Rule A's wide band doesn't trip and Rule B was skipped.
    assert verdict.status == "clean"


def test_margin_override_changes_rule_a_sensitivity() -> None:
    """A caller-supplied stricter margin should be able to flag a strategy the
    default margin would pass, and vice versa — proves margin is a real,
    load-bearing parameter, not decorative."""
    # Vol just barely inside the default-margin band for a single broad_equity
    # ticker (0.35 * 1.5 = 0.525) but outside a tight 1.0x margin (0.35).
    rng = np.random.default_rng(99)
    returns = rng.normal(loc=0.0004, scale=0.40 / (252**0.5), size=200).tolist()

    default_verdict = vp.assess_strategy(["SPY"], returns, margin=1.5)
    strict_verdict = vp.assess_strategy(["SPY"], returns, margin=1.0)

    assert default_verdict.status == "clean"
    assert strict_verdict.status == "flagged"

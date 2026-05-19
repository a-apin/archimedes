"""Tests for rigor_evaluator — DSR, PBO, and OOS Sharpe.

Pinned to the three sanity-check cases from the spec:

  docs/specs/selection-bias-corrections-spec.md
  § "Numerical sanity-check examples (for unit test seed)"

| Case | SR_ann | T    | skew  | ex_kurt | N    | SR_zero   | z      | dsr_p_value |
|------|--------|------|-------|---------|------|-----------|--------|-------------|
| A    | 1.8    | 2520 | -0.4  | 3.2     | 10   | 0.0314    | 4.013  | ~1.0000     |
| B    | 0.9    | 1260 | -0.2  | 2.0     | 20   | 0.0536    | 0.110  | ~0.5439     |
| C    | 0.3    |  504 |  0.0  | 0.0     | 1000 | 0.1451    | -2.831 | ~0.0023     |

No network, no database, no on-chain dependencies.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from archimedes.services.rigor_evaluator import (
    _dsr_from_stats,
    compute_dsr,
    compute_kelly_fraction,
    compute_oos_sharpe,
    compute_pbo,
)

_ANNUALIZATION = 252


# ─── DSR formula — pinned to spec sanity-check cases ─────────────────


@pytest.mark.parametrize(
    "SR_ann, T, skew, ex_kurt, N, expected_p, p_tol",
    [
        # Case A — strong: long, smooth backtest, small library → DSR clears gate
        (1.8, 2520, -0.4, 3.2, 10, 1.0000, 0.001),
        # Case B — borderline: credibly positive but below the 0.95 bar
        (0.9, 1260, -0.2, 2.0, 20, 0.5439, 0.005),
        # Case C — failure: weak Sharpe from large selection → gate must reject
        (0.3, 504, 0.0, 0.0, 1000, 0.0023, 0.001),
    ],
)
def test_dsr_formula_spec_cases(
    SR_ann, T, skew, ex_kurt, N, expected_p, p_tol
):
    """_dsr_from_stats reproduces the spec's reference values within tolerance."""
    SR_hat = SR_ann / math.sqrt(_ANNUALIZATION)
    dsr, p_val = _dsr_from_stats(SR_hat, T, skew, ex_kurt, N)

    assert dsr is not None, "DSR should not be None for valid inputs"
    assert p_val is not None, "p_value should not be None for valid inputs"
    assert abs(p_val - expected_p) <= p_tol, (
        f"p_value {p_val:.6f} differs from expected {expected_p} by more than {p_tol}"
    )


def test_dsr_case_a_clears_rigor_gate():
    """Case A p_value should exceed the 0.95 gate threshold."""
    SR_hat = 1.8 / math.sqrt(_ANNUALIZATION)
    _, p_val = _dsr_from_stats(SR_hat, 2520, -0.4, 3.2, 10)
    assert p_val is not None
    assert p_val >= 0.95, f"Case A should clear the 0.95 gate, got {p_val:.4f}"


def test_dsr_case_b_below_rigor_gate():
    """Case B p_value should be below the 0.95 gate threshold."""
    SR_hat = 0.9 / math.sqrt(_ANNUALIZATION)
    _, p_val = _dsr_from_stats(SR_hat, 1260, -0.2, 2.0, 20)
    assert p_val is not None
    assert p_val < 0.95, f"Case B should NOT clear the 0.95 gate, got {p_val:.4f}"


def test_dsr_case_c_fails_gate():
    """Case C (thousand-strategy selection, weak Sharpe) should fail hard."""
    SR_hat = 0.3 / math.sqrt(_ANNUALIZATION)
    _, p_val = _dsr_from_stats(SR_hat, 504, 0.0, 0.0, 1000)
    assert p_val is not None
    assert p_val < 0.05, f"Case C should fail the gate convincingly, got {p_val:.6f}"


def test_dsr_no_correction_when_n_equals_1():
    """With N=1, E_max_N = 0 and DSR equals the raw annualized Sharpe."""
    SR_hat = 1.0 / math.sqrt(_ANNUALIZATION)
    T = 252
    dsr, _ = _dsr_from_stats(SR_hat, T, 0.0, 0.0, 1)
    assert dsr is not None
    # SR_zero = 0 when N=1, so deflated SR = SR_hat * sqrt(252) ≈ 1.0
    assert abs(dsr - 1.0) < 0.01, f"N=1 DSR should approximate raw annualized SR, got {dsr:.4f}"


def test_dsr_returns_none_for_short_series():
    assert compute_dsr([0.01, 0.02, 0.01], num_trials=5) == (None, None)


def test_dsr_returns_none_for_zero_vol():
    returns = [0.001] * 100  # constant returns → zero std
    dsr, p_val = compute_dsr(returns, num_trials=5)
    assert dsr is None
    assert p_val is None


def test_dsr_higher_n_lowers_p_value():
    """More trials in selection → more conservative (lower p_value)."""
    SR_hat = 0.8 / math.sqrt(_ANNUALIZATION)
    T = 1000
    _, p_low_n = _dsr_from_stats(SR_hat, T, 0.0, 0.0, 5)
    _, p_high_n = _dsr_from_stats(SR_hat, T, 0.0, 0.0, 500)
    assert p_low_n is not None and p_high_n is not None
    assert p_low_n > p_high_n, "Higher N must reduce the DSR p-value"


# ─── PBO ─────────────────────────────────────────────────────────────


def test_pbo_single_strategy_returns_zero():
    """PBO is undefined for N=1; we return 0 (no overfitting detectable)."""
    result = compute_pbo({"strat_a": [0.001] * 256})
    assert result == {"strat_a": 0.0}


def test_pbo_dominant_strategy_has_low_score():
    """A strategy that dominates OOS on every split should yield low PBO."""
    rng = np.random.default_rng(42)
    T = 512

    # strat_a: strong positive drift; strat_b: weak / noisy
    returns_a = rng.normal(0.001, 0.01, T).tolist()
    returns_b = rng.normal(0.0, 0.02, T).tolist()

    result = compute_pbo({"a": returns_a, "b": returns_b}, s_partitions=8)
    assert "a" in result and "b" in result
    # Same PBO for all strategies in the library (library-level metric)
    assert result["a"] == result["b"]
    # Dominant strategy → low PBO
    assert result["a"] < 0.5, f"Dominant strategy should have PBO < 0.5, got {result['a']}"


def test_pbo_noise_only_strategies_have_high_score():
    """When all strategies are pure noise, PBO should be near 0.5."""
    rng = np.random.default_rng(7)
    T = 512
    n_strats = 8
    matrix = {f"s{i}": rng.normal(0.0, 0.01, T).tolist() for i in range(n_strats)}

    result = compute_pbo(matrix, s_partitions=8)
    pbo = list(result.values())[0]
    # All noise: PBO should cluster around 0.5
    assert 0.3 <= pbo <= 0.8, f"Noise strategies should have PBO ≈ 0.5, got {pbo}"


def test_pbo_all_scores_identical():
    """All strategies in a library run get the same PBO score."""
    rng = np.random.default_rng(99)
    T = 256
    matrix = {f"s{i}": rng.normal(0.0001 * i, 0.01, T).tolist() for i in range(4)}

    result = compute_pbo(matrix, s_partitions=4)
    scores = list(result.values())
    assert len(set(scores)) == 1, f"All PBO scores must be identical, got {set(scores)}"


def test_pbo_returns_zero_for_insufficient_data():
    """Too few rows per block → graceful zero, no crash."""
    matrix = {"a": [0.01] * 3, "b": [0.02] * 3}
    result = compute_pbo(matrix, s_partitions=16)
    assert all(v == 0.0 for v in result.values())


# ─── OOS Sharpe ──────────────────────────────────────────────────────


def test_oos_sharpe_returns_none_for_short_series():
    assert compute_oos_sharpe([0.001] * 5) is None


def test_oos_sharpe_positive_for_consistently_positive_returns():
    rng = np.random.default_rng(1)
    # Strong positive drift + small noise → OOS Sharpe should be positive
    returns = (rng.normal(0.002, 0.005, 200)).tolist()
    oos = compute_oos_sharpe(returns, train_fraction=0.70)
    assert oos is not None
    assert oos > 0.0


def test_oos_sharpe_negative_for_consistently_negative_returns():
    rng = np.random.default_rng(2)
    # Strong negative drift + small noise → OOS Sharpe should be negative
    returns = (rng.normal(-0.002, 0.005, 200)).tolist()
    oos = compute_oos_sharpe(returns, train_fraction=0.70)
    assert oos is not None
    assert oos < 0.0


def test_oos_sharpe_respects_train_fraction():
    """OOS Sharpe should only use the last (1 - train_fraction) of the series."""
    rng = np.random.default_rng(3)
    n = 300
    # IS slice: strong negative drift; OOS slice: strong positive drift
    is_part = rng.normal(-0.003, 0.005, int(n * 0.70)).tolist()
    oos_part = rng.normal(0.003, 0.005, int(n * 0.30)).tolist()
    returns = is_part + oos_part
    oos = compute_oos_sharpe(returns, train_fraction=0.70)
    assert oos is not None
    assert oos > 0.0, "OOS slice has positive drift; Sharpe should be positive"


# ─── Kelly Criterion ─────────────────────────────────────────────────


def test_kelly_returns_none_for_short_series():
    assert compute_kelly_fraction([0.001] * 3) is None


def test_kelly_returns_none_for_zero_vol():
    returns = [0.001] * 100  # constant → zero std
    assert compute_kelly_fraction(returns) is None


def test_kelly_returns_zero_for_negative_excess_return():
    """Strategy with negative excess return → Kelly says don't bet."""
    rng = np.random.default_rng(10)
    # Mean ≈ -0.001/day → annualized ≈ -25% → well below 5% rf
    returns = rng.normal(-0.001, 0.01, 252).tolist()
    f = compute_kelly_fraction(returns, rf_annual=0.05)
    assert f is not None
    assert f == 0.0, "Negative excess return → Kelly fraction should be 0"


def test_kelly_positive_for_strong_positive_returns():
    """A high-drift strategy should get a positive half-Kelly allocation."""
    rng = np.random.default_rng(11)
    # Mean ≈ 0.002/day → annualized ≈ 50%, vol ≈ 1% daily ≈ 16% ann
    # Full Kelly ≈ (0.50 - 0.05) / 0.16² ≈ 17.6  (capped to 1.0 after fractional)
    returns = rng.normal(0.002, 0.01, 500).tolist()
    f = compute_kelly_fraction(returns, rf_annual=0.05, fractional=0.5)
    assert f is not None
    assert f > 0.0, "High-drift strategy should have positive Kelly fraction"
    assert f <= 1.0, "Kelly fraction must not exceed 1.0 (no leverage)"


def test_kelly_half_kelly_is_smaller_than_full():
    """half-Kelly must be strictly less than full-Kelly when neither is capped."""
    rng = np.random.default_rng(12)
    # High vol (5% daily) keeps full-Kelly below 1.0 so neither value is capped.
    # μ_ann ≈ 0.5, σ_ann² ≈ 0.63 → f_full ≈ 0.72; f_half ≈ 0.36
    returns = rng.normal(0.002, 0.05, 500).tolist()
    f_half = compute_kelly_fraction(returns, rf_annual=0.05, fractional=0.5)
    f_full = compute_kelly_fraction(returns, rf_annual=0.05, fractional=1.0)
    assert f_half is not None and f_full is not None
    assert f_full <= 1.0, "full-Kelly should not be capped for this series"
    assert f_half < f_full, "half-Kelly must be smaller than full-Kelly"
    assert f_half > 0.0, "half-Kelly must be positive for this series"


def test_kelly_is_clipped_to_unit_interval():
    """Extremely high-drift series → fractional Kelly is clipped to 1.0."""
    # very high mean, very low vol → f* >> 1 before clipping
    returns = [0.01] * 499 + [0.009]  # near-constant high drift
    f = compute_kelly_fraction(returns, rf_annual=0.05, fractional=1.0)
    assert f is not None
    assert f <= 1.0, "Kelly fraction must be clipped to ≤ 1.0"
    assert f >= 0.0

"""Tests for rigor_evaluator — DSR, PBO, and OOS Sharpe.

Pinned to the three sanity-check cases from the spec:

  docs/specs/selection-bias-corrections-spec.md
  § "Numerical sanity-check examples (for unit test seed)"

Note: ``gamma_4`` below is **raw (Pearson) kurtosis** (normal = 3.0), matching
Bailey-LdP (2014) eq. 8 directly. Previous versions of this table used Fisher
excess kurtosis with the raw-kurtosis coefficient, biasing the DSR denominator.

| Case | SR_ann | T    | skew  | raw_kurt | N    | SR_zero   | z      | dsr_p_value |
|------|--------|------|-------|----------|------|-----------|--------|-------------|
| A    | 1.8    | 2520 | -0.4  | 6.2      | 10   | 0.0314    | 3.994  | ~1.0000     |
| B    | 0.9    | 1260 | -0.2  | 5.0      | 20   | 0.0536    | 0.110  | ~0.5439     |
| C    | 0.3    |  504 |  0.0  | 3.0      | 1000 | 0.1451    | -2.831 | ~0.0023     |

No network, no database, no on-chain dependencies.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from archimedes.services.rigor_evaluator import (
    RigorGateResult,
    _dsr_from_stats,
    compute_dsr,
    compute_kelly_fraction,
    compute_oos_sharpe,
    compute_pbo,
    compute_sharpe_ci,
    look_ahead_audit,
    run_rigor_gate,
)

_ANNUALIZATION = 252


# ─── DSR formula — pinned to spec sanity-check cases ─────────────────


@pytest.mark.parametrize(
    "SR_ann, T, skew, raw_kurt, N, expected_p, p_tol",
    [
        # Case A — strong: long, smooth backtest, small library → DSR clears gate
        (1.8, 2520, -0.4, 6.2, 10, 1.0000, 0.001),
        # Case B — borderline: credibly positive but below the 0.95 bar
        (0.9, 1260, -0.2, 5.0, 20, 0.5439, 0.005),
        # Case C — failure: weak Sharpe from large selection → gate must reject
        (0.3, 504, 0.0, 3.0, 1000, 0.0023, 0.001),
    ],
)
def test_dsr_formula_spec_cases(SR_ann, T, skew, raw_kurt, N, expected_p, p_tol):
    """_dsr_from_stats reproduces the spec's reference values within tolerance."""
    SR_hat = SR_ann / math.sqrt(_ANNUALIZATION)
    dsr, p_val = _dsr_from_stats(SR_hat, T, skew, raw_kurt, N)

    assert dsr is not None, "DSR should not be None for valid inputs"
    assert p_val is not None, "p_value should not be None for valid inputs"
    assert abs(p_val - expected_p) <= p_tol, (
        f"p_value {p_val:.6f} differs from expected {expected_p} by more than {p_tol}"
    )


def test_dsr_case_a_clears_rigor_gate():
    """Case A p_value should exceed the 0.95 gate threshold."""
    SR_hat = 1.8 / math.sqrt(_ANNUALIZATION)
    _, p_val = _dsr_from_stats(SR_hat, 2520, -0.4, 6.2, 10)
    assert p_val is not None
    assert p_val >= 0.95, f"Case A should clear the 0.95 gate, got {p_val:.4f}"


def test_dsr_case_b_below_rigor_gate():
    """Case B p_value should be below the 0.95 gate threshold."""
    SR_hat = 0.9 / math.sqrt(_ANNUALIZATION)
    _, p_val = _dsr_from_stats(SR_hat, 1260, -0.2, 5.0, 20)
    assert p_val is not None
    assert p_val < 0.95, f"Case B should NOT clear the 0.95 gate, got {p_val:.4f}"


def test_dsr_case_c_fails_gate():
    """Case C (thousand-strategy selection, weak Sharpe) should fail hard."""
    SR_hat = 0.3 / math.sqrt(_ANNUALIZATION)
    _, p_val = _dsr_from_stats(SR_hat, 504, 0.0, 3.0, 1000)
    assert p_val is not None
    assert p_val < 0.05, f"Case C should fail the gate convincingly, got {p_val:.6f}"


def test_dsr_no_correction_when_n_equals_1():
    """With N=1, E_max_N = 0 and DSR equals the raw annualized Sharpe."""
    SR_hat = 1.0 / math.sqrt(_ANNUALIZATION)
    T = 252
    dsr, _ = _dsr_from_stats(SR_hat, T, 0.0, 3.0, 1)
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
    _, p_low_n = _dsr_from_stats(SR_hat, T, 0.0, 3.0, 5)
    _, p_high_n = _dsr_from_stats(SR_hat, T, 0.0, 3.0, 500)
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
    pbo = next(iter(result.values()))
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


# ─── Sharpe CI (Lo 2002) ─────────────────────────────────────────────


def test_sharpe_ci_symmetric_around_point_estimate():
    """CI must be symmetric: point estimate is the midpoint of (lower, upper)."""
    sr = 1.0
    lower, upper = compute_sharpe_ci(sr, n_obs_daily=252, confidence=0.95)
    mid = (lower + upper) / 2
    assert abs(mid - sr) < 1e-9, f"CI midpoint {mid:.6f} must equal SR {sr}"


def test_sharpe_ci_wider_with_fewer_obs():
    """Fewer observations → wider confidence interval."""
    sr = 0.8
    lo_wide, hi_wide = compute_sharpe_ci(sr, n_obs_daily=252)
    lo_narrow, hi_narrow = compute_sharpe_ci(sr, n_obs_daily=2520)
    assert (hi_wide - lo_wide) > (hi_narrow - lo_narrow), "CI with 252 obs must be wider than CI with 2520 obs"


def test_sharpe_ci_wider_with_higher_confidence():
    """Higher confidence level → wider interval."""
    sr = 0.7
    lo_95, hi_95 = compute_sharpe_ci(sr, n_obs_daily=500, confidence=0.95)
    lo_99, hi_99 = compute_sharpe_ci(sr, n_obs_daily=500, confidence=0.99)
    assert (hi_99 - lo_99) > (hi_95 - lo_95), "99% CI must be wider than 95% CI"


def test_sharpe_ci_lo2002_formula_pinned():
    """Pin Lo (2002) SE against a manually computed reference value.

    SR_annual = 1.0, n = 252, confidence = 0.95.
    SR_daily = 1/sqrt(252)
    SE = sqrt((1 + 0.5*(1/252)) * 252 / 252) = sqrt(1 + 0.5/252) ≈ 1.001
    z_0.975 ≈ 1.96 → half-width ≈ 1.96 * 1.001 ≈ 1.962
    """
    import math

    sr = 1.0
    n = 252
    sr_daily = sr / math.sqrt(252)
    se = math.sqrt((1 + 0.5 * sr_daily**2) * 252 / n)
    from scipy.stats import norm

    z = norm.ppf(0.975)
    expected_lower = sr - z * se
    expected_upper = sr + z * se

    lower, upper = compute_sharpe_ci(sr, n_obs_daily=n, confidence=0.95)
    assert abs(lower - expected_lower) < 1e-9
    assert abs(upper - expected_upper) < 1e-9


def test_sharpe_ci_rejects_invalid_n():
    """n_obs_daily ≤ 0 must raise ValueError."""
    import pytest

    with pytest.raises(ValueError, match="n_obs_daily"):
        compute_sharpe_ci(1.0, n_obs_daily=0)
    with pytest.raises(ValueError, match="n_obs_daily"):
        compute_sharpe_ci(1.0, n_obs_daily=-5)


def test_sharpe_ci_rejects_invalid_confidence():
    """Confidence outside (0, 1) must raise ValueError."""
    import pytest

    with pytest.raises(ValueError, match="confidence"):
        compute_sharpe_ci(1.0, n_obs_daily=252, confidence=0.0)
    with pytest.raises(ValueError, match="confidence"):
        compute_sharpe_ci(1.0, n_obs_daily=252, confidence=1.0)
    with pytest.raises(ValueError, match="confidence"):
        compute_sharpe_ci(1.0, n_obs_daily=252, confidence=1.5)


# ─── Look-ahead audit (migrated from selection_bias.py) ──────────────


class TestLookAheadAudit:
    def test_clean_code(self) -> None:
        code = """
class MyStrategy(bt.Strategy):
    def next(self):
        if self.data.close[0] > self.data.close[-1]:
            self.buy()
"""
        passed, warnings = look_ahead_audit(code)
        assert passed
        assert len(warnings) == 0

    def test_positive_index(self) -> None:
        code = """
class MyStrategy(bt.Strategy):
    def next(self):
        future_price = self.data.close[1]
        if future_price > self.data.close[0]:
            self.buy()
"""
        passed, warnings = look_ahead_audit(code)
        assert not passed
        assert any("positive" in w.lower() for w in warnings)

    def test_suspicious_function(self) -> None:
        code = """
class MyStrategy(bt.Strategy):
    def next(self):
        predicted = self.predict(self.data.close[0])
        if predicted > 100:
            self.buy()
"""
        passed, warnings = look_ahead_audit(code)
        assert not passed
        assert any("predict" in w.lower() for w in warnings)

    def test_invalid_syntax(self) -> None:
        passed, warnings = look_ahead_audit("def broken(:")
        assert not passed
        assert any("parse" in w.lower() for w in warnings)


# ─── Rigor gate composite (migrated from selection_bias.py) ──────────


class TestRigorGate:
    def test_passes_all(self) -> None:
        rng = np.random.default_rng(42)
        returns = rng.normal(0.001, 0.008, size=500).tolist()

        result = run_rigor_gate(
            strategy_id="test_good",
            daily_returns=returns,
            num_trials=5,
            pbo_scores={"test_good": 0.15},
            strategy_code="class S: def next(self): self.buy()",
        )

        assert isinstance(result, RigorGateResult)
        assert isinstance(result.passes_all, bool)
        assert isinstance(result.gate_details, dict)
        assert "dsr" in result.gate_details
        assert "pbo" in result.gate_details
        assert "oos_sharpe" in result.gate_details
        assert "look_ahead" in result.gate_details

    def test_fails_without_pbo(self) -> None:
        rng = np.random.default_rng(42)
        returns = rng.normal(0.001, 0.008, size=500).tolist()

        result = run_rigor_gate(
            strategy_id="test_no_pbo",
            daily_returns=returns,
            num_trials=5,
            pbo_scores=None,
            strategy_code="class S: def next(self): self.buy()",
        )

        assert not result.passes_all
        assert result.gate_details["pbo"] == "MISSING"

    def test_fails_with_high_pbo(self) -> None:
        result = RigorGateResult(
            strategy_id="test",
            dsr_p_value=0.99,
            pbo_score=0.6,
            oos_sharpe=1.0,
            look_ahead_passed=True,
            in_sample_sharpe=1.5,
        )
        assert not result.passes_all

    def test_fails_with_low_oos_ratio(self) -> None:
        result = RigorGateResult(
            strategy_id="test",
            dsr_p_value=0.99,
            pbo_score=0.2,
            oos_sharpe=0.3,
            look_ahead_passed=True,
            in_sample_sharpe=1.5,
        )
        assert not result.passes_all

    def test_explicit_look_ahead_override(self) -> None:
        rng = np.random.default_rng(42)
        returns = rng.normal(0.001, 0.008, size=500).tolist()

        result = run_rigor_gate(
            strategy_id="test_override",
            daily_returns=returns,
            num_trials=5,
            pbo_scores={"test_override": 0.15},
            look_ahead_audit_passed=True,
        )
        assert result.look_ahead_passed is True

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

No network, no on-chain dependencies. TestLoadDailyReturnsStore (#774) uses a
tmp, isolated SQLite session — never a real network DB.
"""

from __future__ import annotations

import ast
import math

import numpy as np
import pytest
from archimedes.services._rigor_helpers import (
    _dsr_from_stats,
    _sharpe_per_col,
    compute_average_pairwise_correlation,
    compute_cpcv_oos_sharpe,
    compute_dsr,
    compute_kelly_fraction,
    compute_oos_sharpe,
    compute_pbo,
    compute_sharpe_ci,
)
from archimedes.services.rigor_evaluator import (
    RigorGateResult,
    _get_func_name,
    align_returns_store,
    compute_library_pbo,
    load_daily_returns_store,
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


def test_sharpe_ci_returns_plain_python_float():
    """Regression: the CI must be plain ``float``, never ``np.float64``.

    ``norm.ppf`` returns a numpy scalar, so without an explicit cast the tuple
    leaks ``np.float64`` into ``sharpe_ci_lower``/``sharpe_ci_upper`` on the
    strategy passport. psycopg2 then renders it as ``np.float64(-0.33)`` and
    parses ``np`` as a schema, raising ``InvalidSchemaName: schema "np" does
    not exist`` and rolling back the passport DB sync. ``isinstance(x, float)``
    does NOT catch this (np.float64 subclasses float) — assert the exact type.
    """
    lower, upper = compute_sharpe_ci(1.0, n_obs_daily=252, confidence=0.95)
    assert type(lower) is float, f"lower is {type(lower).__name__}, must be plain float"
    assert type(upper) is float, f"upper is {type(upper).__name__}, must be plain float"


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
        # Only index [0] (current bar) — no negative indices, no positive indices.
        code = """
class MyStrategy(bt.Strategy):
    def next(self):
        if self.data.close[0] > 100:
            self.buy()
"""
        passed, warnings = look_ahead_audit(code)
        assert passed
        assert len(warnings) == 0

    def test_negative_index_emits_warning(self) -> None:
        # Negative indices are safe in backtrader (bars-ago) but would be
        # last-row future data in pandas.  The audit flags them for human review.
        code = """
class MyStrategy(bt.Strategy):
    def next(self):
        if self.data.close[0] > self.data.close[-1]:
            self.buy()
"""
        passed, warnings = look_ahead_audit(code)
        assert not passed
        assert any("negative index" in w.lower() for w in warnings)

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
        assert result.gate_details["pbo"] == "MISSING (source=cohort)"

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


# ─── Additional coverage: gate_details branches + run_rigor_gate paths ──────

# Deterministic return series (no np.random to avoid VoidDType issue).
# _RETURNS_50: DSR p=0.917 (below 0.95 gate) — used for structural tests.
# _RETURNS_80: DSR p=1.0 (clears gate) — used where a strong series is needed.
_RETURNS_50 = [0.01 * ((-1) ** i) * 0.5 + 0.001 for i in range(50)]
_RETURNS_80 = [0.01, -0.005, 0.008, 0.003] * 20


# ─── DSR edge-cases not hit by the first 40 tests ────────────────────


def test_dsr_from_stats_returns_none_for_t_less_than_4():
    """_dsr_from_stats with T < 4 must return (None, None) before any math."""
    assert _dsr_from_stats(0.01, 3, 0.0, 3.0, 1) == (None, None)
    assert _dsr_from_stats(0.05, 1, 0.0, 3.0, 5) == (None, None)
    assert _dsr_from_stats(0.02, 2, -0.2, 5.0, 10) == (None, None)


def test_dsr_from_stats_returns_none_when_denom_sq_nonpositive():
    """denom_sq = 1 - gamma_3*SR + (gamma_4-1)/4*SR^2 <= 0 must return (None, None).

    With SR_hat=1.0, gamma_3=3.0, gamma_4=3.0 (raw Pearson kurtosis):
      denom_sq = 1 - 3*1 + (3-1)/4*1^2 = 1 - 3 + 0.5 = -1.5 <= 0
    """
    result = _dsr_from_stats(1.0, 100, 3.0, 3.0, 1)
    assert result == (None, None)


def test_dsr_from_stats_returns_none_when_denom_sq_strictly_negative():
    """A larger SR amplifies the negative denom_sq further.

    With SR_hat=2.0, gamma_3=3.0, gamma_4=3.0:
      denom_sq = 1 - 3*2 + (3-1)/4*4 = 1 - 6 + 2 = -3.0 (unambiguously < 0)
    This is a distinct parameter set from the SR=1.0 case, verifying that the
    guard fires for different magnitudes of negative denom_sq.
    """
    result = _dsr_from_stats(2.0, 100, 3.0, 3.0, 1)
    assert result == (None, None)


def test_compute_dsr_minimal_valid_series():
    """compute_dsr with exactly T=4 non-constant returns must produce a result."""
    returns = [0.01, 0.02, -0.01, 0.03]
    dsr, p_val = compute_dsr(returns, num_trials=1)
    assert dsr is not None
    assert p_val is not None
    # Pinned reference: T=4 with these values yields p=0.855 (verified manually).
    assert p_val == pytest.approx(0.855, abs=0.01)


def test_compute_dsr_with_five_returns_and_multiple_trials():
    """compute_dsr with a slightly larger series exercises the full lines 85-88 path."""
    returns = [0.005, -0.003, 0.010, -0.002, 0.007]
    dsr, p_val = compute_dsr(returns, num_trials=3)
    assert dsr is not None
    assert p_val is not None
    assert isinstance(dsr, float)
    assert isinstance(p_val, float)


# ─── OOS Sharpe edge-cases ────────────────────────────────────────────


def test_oos_sharpe_returns_none_when_oos_slice_too_short():
    """Line 246: OOS slice < 5 bars after split must return None.

    T=12, train_fraction=0.7 -> split=8, oos=4 items < 5.
    """
    returns = [0.01, 0.02] * 6  # 12 items
    result = compute_oos_sharpe(returns, train_fraction=0.7)
    assert result is None


def test_oos_sharpe_returns_none_for_constant_oos_slice():
    """Line 249: OOS slice with zero variance (ptp == 0) must return None.

    T=20, train_fraction=0.6 -> split=12, oos=8 items all identical.
    """
    returns = [0.01] * 12 + [1.0] * 8  # oos is constant 1.0
    result = compute_oos_sharpe(returns, train_fraction=0.6)
    assert result is None


def test_oos_sharpe_returns_none_for_constant_oos_slice_with_varied_train():
    """Variant: varied IS slice but constant OOS still hits the ptp==0 guard."""
    is_part = [0.01 * (i % 5 - 2) for i in range(15)]  # 15 varied items
    oos_part = [0.005] * 5  # exactly 5 constant items
    returns = is_part + oos_part  # T=20, split=15 with fraction=0.75
    result = compute_oos_sharpe(returns, train_fraction=0.75)
    assert result is None


# ─── _sharpe_per_col single-row guard ────────────────────────────────


def test_sharpe_per_col_single_row_returns_zeros():
    """Line 338: R.shape[0] < 2 must return zero vector of length n_cols."""
    R = np.array([[0.01, 0.02, 0.03]])  # shape (1, 3)
    result = _sharpe_per_col(R)
    assert result.shape == (3,)
    assert np.all(result == 0.0)


def test_sharpe_per_col_single_row_single_col():
    """Single-row, single-column matrix also returns [0.0]."""
    R = np.array([[0.05]])  # shape (1, 1)
    result = _sharpe_per_col(R)
    assert result.shape == (1,)
    assert result[0] == 0.0


# ─── _get_func_name — all three branches ─────────────────────────────


def test_get_func_name_ast_name_returns_id():
    """Line 407: _get_func_name(ast.Name) must return the identifier string."""
    call_node = ast.parse("future(x)").body[0].value
    assert isinstance(call_node.func, ast.Name)
    assert _get_func_name(call_node.func) == "future"


def test_get_func_name_ast_attribute_returns_attr():
    """Line 410: _get_func_name(ast.Attribute) must return the attribute name."""
    call_node = ast.parse("self.predict(x)").body[0].value
    assert isinstance(call_node.func, ast.Attribute)
    assert _get_func_name(call_node.func) == "predict"


def test_get_func_name_unknown_node_returns_none():
    """Line 411: _get_func_name with a non-Name, non-Attribute node must return None."""
    const_node = ast.Constant(value=42)
    assert _get_func_name(const_node) is None


def test_look_ahead_audit_bare_function_name_triggers_warning():
    """Bare function named 'future' (ast.Name path) must be flagged."""
    code = "result = future(prices)"
    passed, warnings = look_ahead_audit(code)
    assert not passed
    assert any("future" in w for w in warnings)


def test_look_ahead_audit_bare_look_ahead_function():
    """Bare function named 'look_ahead' (ast.Name) must be flagged."""
    code = "val = look_ahead(bar)"
    passed, warnings = look_ahead_audit(code)
    assert not passed
    assert len(warnings) == 1


# ─── RigorGateResult.passes_all — every early-return branch ─────────


class TestPassesAllBranches:
    def test_dsr_p_value_none_returns_false(self):
        """Line 444: dsr_p_value is None -> passes_all is False."""
        r = RigorGateResult("s", dsr_p_value=None)
        assert r.passes_all is False

    def test_dsr_p_value_below_threshold_returns_false(self):
        """Line 446: dsr_p_value < 0.95 -> passes_all is False."""
        r = RigorGateResult("s", dsr_p_value=0.80)
        assert r.passes_all is False

    def test_dsr_p_value_exactly_threshold_not_blocked_by_dsr(self):
        """dsr_p_value == 0.95 clears the DSR gate (does not hit line 446 return)."""
        r = RigorGateResult("s", dsr_p_value=0.95, pbo_score=None)
        # Falls through DSR check but blocked by missing pbo -> still False
        assert r.passes_all is False

    def test_pbo_score_none_returns_false(self):
        """Line 448: pbo_score is None (even with passing DSR) -> passes_all is False."""
        r = RigorGateResult("s", dsr_p_value=0.97, pbo_score=None)
        assert r.passes_all is False

    def test_pbo_score_at_boundary_fails(self):
        """pbo_score == 0.5 hits the >= 0.5 branch -> passes_all is False."""
        r = RigorGateResult("s", dsr_p_value=0.97, pbo_score=0.5)
        assert r.passes_all is False

    def test_oos_sharpe_none_returns_false(self):
        """Line 452: oos_sharpe is None (passing DSR + PBO) -> passes_all is False."""
        r = RigorGateResult("s", dsr_p_value=0.97, pbo_score=0.2, oos_sharpe=None)
        assert r.passes_all is False

    def test_look_ahead_passed_true_returns_true(self):
        """Line 455: all checks clear + look_ahead_passed=True -> passes_all is True."""
        r = RigorGateResult(
            "s",
            dsr_p_value=0.97,
            pbo_score=0.2,
            oos_sharpe=1.5,
            look_ahead_passed=True,
            in_sample_sharpe=2.0,  # ratio 0.75 >= 0.5
        )
        assert r.passes_all is True

    def test_look_ahead_passed_false_returns_false_at_line_455(self):
        """Line 455: all checks clear but look_ahead_passed=False -> passes_all is False."""
        r = RigorGateResult(
            "s",
            dsr_p_value=0.97,
            pbo_score=0.2,
            oos_sharpe=1.5,
            look_ahead_passed=False,
            in_sample_sharpe=2.0,
        )
        assert r.passes_all is False

    def test_passes_all_no_in_sample_sharpe_skips_ratio_check(self):
        """When in_sample_sharpe is None the OOS/IS ratio check is skipped entirely."""
        r = RigorGateResult(
            "s",
            dsr_p_value=0.97,
            pbo_score=0.2,
            oos_sharpe=0.1,  # very low, but ratio check is skipped
            look_ahead_passed=True,
            in_sample_sharpe=None,
        )
        assert r.passes_all is True

    def test_passes_all_negative_in_sample_sharpe_skips_ratio_check(self):
        """When in_sample_sharpe <= 0 the condition in_sample_sharpe > 0 is False,
        so the OOS/IS ratio check is bypassed."""
        r = RigorGateResult(
            "s",
            dsr_p_value=0.97,
            pbo_score=0.2,
            oos_sharpe=0.1,
            look_ahead_passed=True,
            in_sample_sharpe=-0.5,  # negative -> ratio check skipped
        )
        assert r.passes_all is True

    # NaN-hardening: every IEEE-754 comparison against NaN is False, so a NaN
    # metric (not None) used to skip its fail branch and let the gate continue
    # toward PASS. Each numeric metric must now fail on a non-finite value.
    def test_nan_dsr_p_value_fails(self):
        r = RigorGateResult("s", dsr_p_value=float("nan"), pbo_score=0.2, oos_sharpe=1.0, look_ahead_passed=True)
        assert r.passes_all is False

    def test_nan_pbo_score_fails(self):
        r = RigorGateResult("s", dsr_p_value=0.97, pbo_score=float("nan"), oos_sharpe=1.0, look_ahead_passed=True)
        assert r.passes_all is False

    def test_nan_oos_sharpe_fails(self):
        r = RigorGateResult("s", dsr_p_value=0.97, pbo_score=0.2, oos_sharpe=float("nan"), look_ahead_passed=True)
        assert r.passes_all is False

    def test_nan_cpcv_positive_fraction_fails(self):
        r = RigorGateResult(
            "s",
            dsr_p_value=0.97,
            pbo_score=0.2,
            oos_sharpe=1.0,
            look_ahead_passed=True,
            in_sample_sharpe=2.0,
            cpcv_positive_fraction=float("nan"),
        )
        assert r.passes_all is False


# ─── RigorGateResult.gate_details — every branch ─────────────────────


class TestGateDetailsBranches:
    def test_dsr_pass_branch(self):
        """dsr_p_value >= 0.95 renders 'PASS (p=...)'."""
        r = RigorGateResult("s", dsr_p_value=0.9700)
        assert r.gate_details["dsr"] == "PASS (p=0.9700)"

    def test_dsr_fail_branch(self):
        """dsr_p_value < 0.95 but not None renders 'FAIL (p=..., need >= 0.95)'
        using the Unicode greater-than-or-equal sign (U+2265) as in the source."""
        r = RigorGateResult("s", dsr_p_value=0.8000)
        assert r.gate_details["dsr"] == "FAIL (p=0.8000, need ≥ 0.95)"

    def test_dsr_missing_branch(self):
        """dsr_p_value is None renders 'MISSING'."""
        r = RigorGateResult("s", dsr_p_value=None)
        assert r.gate_details["dsr"] == "MISSING"

    def test_pbo_pass_branch(self):
        """pbo_score < 0.5 renders 'PASS (PBO=..., source=...)' (#546)."""
        r = RigorGateResult("s", pbo_score=0.3000)
        assert r.gate_details["pbo"] == "PASS (PBO=0.3000, source=cohort)"

    def test_pbo_fail_branch(self):
        """pbo_score >= 0.5 but not None renders 'FAIL (..., source=...)' (#546)."""
        r = RigorGateResult("s", pbo_score=0.6000)
        assert r.gate_details["pbo"] == "FAIL (PBO=0.6000, need < 0.5, source=cohort)"

    def test_pbo_missing_branch(self):
        """pbo_score is None renders 'MISSING (source=...)' (#546)."""
        r = RigorGateResult("s", pbo_score=None)
        assert r.gate_details["pbo"] == "MISSING (source=cohort)"

    def test_pbo_source_library_label(self):
        """A library-level PBO labels the detail source=library (#546)."""
        r = RigorGateResult("s", pbo_score=0.3000, pbo_source="library")
        assert r.gate_details["pbo"] == "PASS (PBO=0.3000, source=library)"

    def test_oos_sharpe_pass_ratio(self):
        """oos_sharpe set, in_sample_sharpe > 0, ratio >= 0.5 renders 'PASS (OOS/IS=...)'."""
        r = RigorGateResult("s", oos_sharpe=1.5, in_sample_sharpe=2.0)
        detail = r.gate_details["oos_sharpe"]
        assert detail.startswith("PASS (OOS/IS=")
        assert "0.75" in detail

    def test_oos_sharpe_fail_ratio(self):
        """oos_sharpe set, in_sample_sharpe > 0, ratio < 0.5 renders 'FAIL (OOS/IS=...)'
        using the Unicode >= sign (U+2265) as in the source f-string."""
        r = RigorGateResult("s", oos_sharpe=0.3, in_sample_sharpe=2.0)
        detail = r.gate_details["oos_sharpe"]
        assert detail.startswith("FAIL (OOS/IS=")
        assert "need ≥ 0.50" in detail

    def test_oos_sharpe_set_no_is_reference(self):
        """Line 482: oos_sharpe is set but in_sample_sharpe is None renders 'SET (OOS=...)'."""
        r = RigorGateResult("s", oos_sharpe=1.5, in_sample_sharpe=None)
        detail = r.gate_details["oos_sharpe"]
        assert detail == "SET (OOS=1.5000, no IS reference)"

    def test_oos_sharpe_set_with_negative_in_sample(self):
        """oos_sharpe is set but in_sample_sharpe <= 0 falls through to SET branch."""
        r = RigorGateResult("s", oos_sharpe=0.8, in_sample_sharpe=-0.5)
        detail = r.gate_details["oos_sharpe"]
        assert detail.startswith("SET (OOS=")
        assert "no IS reference" in detail

    def test_oos_sharpe_missing_branch(self):
        """Line 484: oos_sharpe is None renders 'MISSING'."""
        r = RigorGateResult("s", oos_sharpe=None)
        assert r.gate_details["oos_sharpe"] == "MISSING"

    def test_look_ahead_pass(self):
        """look_ahead_passed=True renders 'PASS'."""
        r = RigorGateResult("s", look_ahead_passed=True)
        assert r.gate_details["look_ahead"] == "PASS"

    def test_look_ahead_fail(self):
        """look_ahead_passed=False (default) renders 'FAIL'."""
        r = RigorGateResult("s")
        assert r.gate_details["look_ahead"] == "FAIL"

    def test_gate_details_returns_all_four_keys(self):
        """gate_details must contain the four gate keys + DSR convention (#547) +
        the DSR SE model (#621 follow-up) + the IID advisory (#621)."""
        r = RigorGateResult("s")
        keys = set(r.gate_details.keys())
        assert keys == {
            "dsr",
            "dsr_convention",
            "dsr_se",
            "pbo",
            "oos_sharpe",
            "look_ahead",
            "cpcv",
            "iid",
            "regime_robustness",
        }
        assert r.gate_details["dsr_convention"] == "excess"
        # Default RigorGateResult carries no HAC verdict -> classical IID SE label.
        assert r.gate_details["dsr_se"] == "IID (classical Bailey-LdP)"
        # Advisories are unset by default (no return series) -> MISSING.
        assert r.gate_details["iid"] == "MISSING"
        assert r.gate_details["regime_robustness"] == "MISSING"


# ─── run_rigor_gate — all branches in lines 509-555 ──────────────────


class TestRunRigorGatePaths:
    def test_strategy_code_none_sets_la_passed_false(self):
        """Line 521: strategy_code=None -> la_passed defaults to False."""
        result = run_rigor_gate("s", _RETURNS_50, strategy_code=None)
        assert result.look_ahead_passed is False

    def test_look_ahead_audit_passed_override_true(self):
        """Line 524: look_ahead_audit_passed=True overrides the computed la_passed."""
        result = run_rigor_gate("s", _RETURNS_50, strategy_code=None, look_ahead_audit_passed=True)
        assert result.look_ahead_passed is True

    def test_look_ahead_audit_passed_override_false_overrides_clean_code(self):
        """Line 524: look_ahead_audit_passed=False overrides even clean code audit."""
        clean_code = "class S:\n    def next(self):\n        self.buy()"
        result = run_rigor_gate("s", _RETURNS_80, strategy_code=clean_code, look_ahead_audit_passed=False)
        assert result.look_ahead_passed is False

    def test_strategy_code_with_look_ahead_warning_logs_and_fails(self):
        """Lines 517-519: code with a look-ahead warning -> la_passed=False + logged."""
        code_with_warning = "price = data[2]"  # positive index triggers warning
        result = run_rigor_gate("s", _RETURNS_80, strategy_code=code_with_warning)
        assert result.look_ahead_passed is False

    def test_in_sample_sharpe_derived_when_not_provided(self):
        """Lines 527-531: in_sample_sharpe is None and returns have variance -> derived."""
        result = run_rigor_gate("s", _RETURNS_80, in_sample_sharpe=None)
        assert result.in_sample_sharpe is not None
        assert isinstance(result.in_sample_sharpe, float)

    def test_in_sample_sharpe_explicit_not_overwritten(self):
        """When in_sample_sharpe is provided explicitly it must be preserved unchanged."""
        result = run_rigor_gate("s", _RETURNS_80, in_sample_sharpe=2.5)
        assert result.in_sample_sharpe == 2.5

    def test_in_sample_sharpe_none_for_single_item_series(self):
        """Lines 527: len(daily_returns) < 2 -> in_sample_sharpe remains None."""
        result = run_rigor_gate("s", [0.01])
        assert result.in_sample_sharpe is None

    def test_in_sample_sharpe_none_for_zero_variance_series(self):
        """Lines 529-531: sigma == 0.0 exactly -> in_sample_sharpe remains None.

        [1.0]*20 gives std(ddof=1) == 0.0 exactly (exact IEEE-754 representation).
        """
        result = run_rigor_gate("s", [1.0] * 20)
        assert result.in_sample_sharpe is None

    def test_pbo_score_looked_up_from_dict(self):
        """Line 509: pbo_scores dict present -> pbo_score is fetched by strategy_id."""
        result = run_rigor_gate("my_strat", _RETURNS_80, pbo_scores={"my_strat": 0.3})
        assert result.pbo_score == 0.3

    def test_pbo_score_none_when_id_missing_from_dict(self):
        """pbo_scores dict present but strategy_id absent -> pbo_score is None."""
        result = run_rigor_gate("missing_id", _RETURNS_80, pbo_scores={"other_strat": 0.3})
        assert result.pbo_score is None

    def test_pbo_score_none_when_no_dict(self):
        """pbo_scores=None -> pbo_score is None."""
        result = run_rigor_gate("s", _RETURNS_80, pbo_scores=None)
        assert result.pbo_score is None

    def test_paper_claimed_sharpe_stored(self):
        """paper_claimed_sharpe is passed through to the result object unchanged."""
        result = run_rigor_gate("s", _RETURNS_80, paper_claimed_sharpe=1.8)
        assert result.paper_claimed_sharpe == 1.8

    def test_result_has_strategy_id(self):
        """run_rigor_gate result.strategy_id must match the input strategy_id."""
        result = run_rigor_gate("unique_id_xyz", _RETURNS_50)
        assert result.strategy_id == "unique_id_xyz"

    def test_result_is_rigor_gate_result_instance(self):
        """run_rigor_gate must always return a RigorGateResult."""
        result = run_rigor_gate("s", _RETURNS_50)
        assert isinstance(result, RigorGateResult)

    def test_gate_details_populated_by_run_rigor_gate(self):
        """gate_details on the returned result must have the four gate keys + DSR
        convention (#547) + DSR SE model (#621 follow-up) + IID (#621)."""
        result = run_rigor_gate("s", _RETURNS_80)
        assert set(result.gate_details.keys()) == {
            "dsr",
            "dsr_convention",
            "dsr_se",
            "pbo",
            "oos_sharpe",
            "look_ahead",
            "cpcv",
            "iid",
            "regime_robustness",
        }
        # run_rigor_gate computes the gating DSR with the HAC-robust SE (#621 follow-up).
        assert result.dsr_se_method == "hac"
        assert "HAC" in result.gate_details["dsr_se"]

    def test_regime_robustness_surfaced_but_advisory(self):
        """Regime-robustness is computed for a long-enough series, surfaced, and never gates."""
        # 80 bars >= 63 → regime classification runs and robustness is populated.
        result = run_rigor_gate("s", _RETURNS_80, num_trials=1, pbo_scores={"s": 0.1}, look_ahead_audit_passed=True)
        assert result.regime_robustness is not None
        assert result.gate_details["regime_robustness"].startswith("ADVISORY")
        # toggling the advisory does not move passes_all
        before = result.passes_all
        result.regime_robustness = {"min_regime_sharpe": -9.0, "consistency": 0.0, "robust": False}
        assert result.passes_all == before
        # a short series simply reports MISSING (no crash)
        short = run_rigor_gate("s2", [0.01, -0.01, 0.02, 0.0, 0.01])
        assert short.gate_details["regime_robustness"] == "MISSING"

    def test_iid_diagnostic_surfaced_but_advisory(self):
        """#621: run_rigor_gate computes + surfaces the IID diagnostic, but it never gates pass/fail.

        Autocorrelated returns are the edge for trend/momentum strategies, so an IID
        violation must be advisory only.
        """
        result = run_rigor_gate("s", _RETURNS_80)
        # computed + surfaced (not None / not dropped)
        assert result.iid_diagnostics is not None
        assert "iid_assumption_violated" in result.iid_diagnostics
        assert result.gate_details["iid"].startswith("ADVISORY")
        # A clearly autocorrelated series (strong trend) must NOT, by itself, fail the gate.
        trending = [0.01 * (1 + 0.5 * (i % 3 == 0)) for i in range(80)]
        r2 = run_rigor_gate("s2", trending, num_trials=1, pbo_scores={"s2": 0.1}, look_ahead_audit_passed=True)
        # whatever passes_all decides, it is decided WITHOUT reference to the IID flag:
        # toggling the advisory flag on the result does not change passes_all.
        before = r2.passes_all
        r2.iid_assumption_violated = not r2.iid_assumption_violated
        assert r2.passes_all == before

    def test_num_trials_stored_on_result(self):
        """num_trials argument must be stored on the result."""
        result = run_rigor_gate("s", _RETURNS_80, num_trials=7)
        assert result.num_trials == 7


# ─── CPCV Edge Cases ──────────────────────────────────────────────────


def test_cpcv_returns_none_for_empty_array():
    assert compute_cpcv_oos_sharpe([]) is None


def test_cpcv_returns_none_for_single_asset_zero_variance():
    assert compute_cpcv_oos_sharpe([[0.01] * 100] * 15) is None


def test_cpcv_returns_none_for_infinite_values():
    # Numpy calculations on inf cause warnings and usually return nan/inf std
    res = compute_cpcv_oos_sharpe([[0.01] * 50 + [np.inf] * 50] * 15)
    assert res is None or res["mean_oos_sharpe"] is None or math.isnan(res["mean_oos_sharpe"])


def test_cpcv_returns_none_for_insufficient_splits():
    assert compute_cpcv_oos_sharpe([[0.01, -0.01] * 2] * 15, n_groups=6, test_groups=2) is None


# ─── Effective-N correlation wiring (DSR) ────────────────────────────


class TestAveragePairwiseCorrelation:
    """compute_average_pairwise_correlation — the input to the DSR effective-N
    correction that was previously never computed by any caller."""

    def test_identical_series_correlation_is_one(self):
        s = [0.01, -0.02, 0.03, 0.0, 0.015, -0.005]
        assert compute_average_pairwise_correlation({"a": s, "b": s}) == pytest.approx(1.0, abs=1e-9)

    def test_independent_series_correlation_near_zero(self):
        rng = np.random.default_rng(0)
        m = {f"s{i}": list(rng.normal(0.0, 0.01, 600)) for i in range(8)}
        assert 0.0 <= compute_average_pairwise_correlation(m) < 0.15

    def test_negative_correlation_clamped_to_zero(self):
        s = [0.01, -0.02, 0.03, -0.01, 0.02, -0.03]
        anti = [-x for x in s]
        # Perfectly anti-correlated → raw mean corr = -1 → clamped to 0 (no relief).
        assert compute_average_pairwise_correlation([s, anti]) == 0.0

    def test_fewer_than_two_series_returns_zero(self):
        assert compute_average_pairwise_correlation({"only": [0.01, 0.02, 0.03]}) == 0.0
        assert compute_average_pairwise_correlation([]) == 0.0

    def test_zero_variance_rows_dropped(self):
        flat = [0.01] * 100
        live = list(np.random.default_rng(1).normal(0.0, 0.01, 100))
        # One live + one flat → < 2 usable series after dropping the flat row.
        assert compute_average_pairwise_correlation([flat, live]) == 0.0


class TestDsrCorrelationRelaxesPenalty:
    """Higher trial correlation → fewer effective independent trials → smaller
    best-of-N null → higher (less-penalized) deflated Sharpe. This proves the
    average_correlation parameter actually flows through compute_dsr."""

    def test_correlated_trials_raise_deflated_sharpe(self):
        rng = np.random.default_rng(7)
        rets = list(rng.normal(0.0012, 0.01, 600))
        dsr_indep, p_indep = compute_dsr(rets, num_trials=25, average_correlation=0.0)
        dsr_corr, p_corr = compute_dsr(rets, num_trials=25, average_correlation=0.9)
        assert dsr_indep is not None and dsr_corr is not None
        assert dsr_corr > dsr_indep
        assert p_corr >= p_indep

    def test_single_trial_correlation_is_inert(self):
        # With N=1 there is no multiple-testing penalty, so correlation can't change it.
        rng = np.random.default_rng(8)
        rets = list(rng.normal(0.001, 0.01, 400))
        assert compute_dsr(rets, num_trials=1, average_correlation=0.9) == compute_dsr(
            rets, num_trials=1, average_correlation=0.0
        )

    def test_full_correlation_collapses_to_single_effective_trial(self):
        # ρ=1 under the effective-N model means N_eff = 1: all trials are the
        # same test, so there is no selection bias to deflate. The DSR must
        # equal the N=1 (no-penalty) result — not vanish via an undocumented
        # sqrt(1−ρ) factor.
        rng = np.random.default_rng(9)
        rets = list(rng.normal(0.0012, 0.01, 600))
        fully_correlated = compute_dsr(rets, num_trials=25, average_correlation=1.0)
        no_penalty = compute_dsr(rets, num_trials=1, average_correlation=0.0)
        assert fully_correlated == no_penalty

    def test_intermediate_correlation_lies_between_endpoints(self):
        # Effective-N is monotonic in ρ: a partially-correlated grid deflates
        # less than an independent one (ρ=0) and more than a degenerate one (ρ=1).
        rng = np.random.default_rng(10)
        rets = list(rng.normal(0.0012, 0.01, 600))
        dsr_indep = compute_dsr(rets, num_trials=25, average_correlation=0.0)[0]
        dsr_mid = compute_dsr(rets, num_trials=25, average_correlation=0.5)[0]
        dsr_full = compute_dsr(rets, num_trials=25, average_correlation=1.0)[0]
        assert dsr_indep < dsr_mid < dsr_full

    def test_uncorrelated_path_unchanged_from_nominal_n(self):
        # ρ=0 must deflate by the full nominal N (the change only touches the
        # correlated relief, leaving independent-trial deflation untouched).
        rng = np.random.default_rng(12)
        rets = list(rng.normal(0.001, 0.01, 500))
        dsr_n10 = compute_dsr(rets, num_trials=10, average_correlation=0.0)[0]
        dsr_n50 = compute_dsr(rets, num_trials=50, average_correlation=0.0)[0]
        # More independent trials → larger best-of-N null → lower deflated Sharpe.
        assert dsr_n50 < dsr_n10


# ─── CPCV wiring into run_rigor_gate ─────────────────────────────────


class TestRunRigorGateCpcvWiring:
    """run_rigor_gate previously passed a 1-D series to a 2-D-only CPCV function,
    so CPCV always returned None. These prove the corrected wiring: CPCV fires on
    a real combinatorial matrix and is honestly None without one."""

    def test_cpcv_fires_with_combinatorial_matrix(self):
        rng = np.random.default_rng(11)
        # 15 rows = C(6, 2) splits; 90 cols ≥ 5 bars/block for 6 groups.
        matrix = rng.normal(0.0006, 0.01, size=(15, 90))
        daily = list(rng.normal(0.001, 0.01, 400))
        result = run_rigor_gate("s1", daily, num_trials=6, cv_returns_matrix=matrix)
        assert result.cpcv_positive_fraction is not None
        assert 0.0 <= result.cpcv_positive_fraction <= 1.0
        assert result.cpcv_mean_oos_sharpe is not None

    def test_cpcv_honestly_none_without_matrix(self):
        rng = np.random.default_rng(12)
        daily = list(rng.normal(0.001, 0.01, 400))
        result = run_rigor_gate("s1", daily, num_trials=6)
        assert result.cpcv_positive_fraction is None
        assert result.cpcv_mean_oos_sharpe is None

    def test_run_rigor_gate_average_correlation_flows_to_dsr(self):
        rng = np.random.default_rng(13)
        daily = list(rng.normal(0.0012, 0.01, 600))
        r_indep = run_rigor_gate("s1", daily, num_trials=25, average_correlation=0.0)
        r_corr = run_rigor_gate("s1", daily, num_trials=25, average_correlation=0.9)
        assert r_corr.deflated_sharpe > r_indep.deflated_sharpe


class TestMonteCarloDSR:
    """Tests for monte_carlo_dsr_pvalue — NULL-imposed circular block bootstrap.

    The test is a one-sided bootstrap hypothesis test: H0 Sharpe ≤ threshold.
    The null is imposed by shifting the series mean (Davison & Hinkley 1997 §4.2),
    so a genuinely high-Sharpe series produces a LOW p-value and a zero-skill
    series produces p ≈ 0.5.
    """

    def test_returns_expected_keys(self):
        from archimedes.services.rigor_evaluator import monte_carlo_dsr_pvalue

        rng = np.random.default_rng(0)
        daily = list(rng.normal(0.001, 0.01, 300))
        result = monte_carlo_dsr_pvalue(daily, dsr_threshold=0.0, n_trials=200, seed=42)
        for key in (
            "pvalue",
            "observed_sharpe",
            "bootstrap_sharpe_mean",
            "bootstrap_sharpe_std",
            "n_trials",
            "passes_at_5pct",
        ):
            assert key in result, f"Missing key: {key}"

    def test_pvalue_in_unit_interval(self):
        from archimedes.services.rigor_evaluator import monte_carlo_dsr_pvalue

        rng = np.random.default_rng(1)
        daily = list(rng.normal(0.001, 0.01, 400))
        result = monte_carlo_dsr_pvalue(daily, dsr_threshold=0.0, n_trials=200, seed=7)
        assert 0.0 <= result["pvalue"] <= 1.0

    def test_strong_positive_series_low_pvalue(self):
        """A series with very high Sharpe is rarely reproduced by the zero-skill
        null world → low p-value. This only works because the null is imposed
        (mean shifted to rf); resampling the raw series would give p ≈ 0.5."""
        from archimedes.services.rigor_evaluator import monte_carlo_dsr_pvalue

        # 0.3% daily return, 0.5% daily vol → annualised Sharpe ≈ 9
        rng = np.random.default_rng(2)
        daily = list(rng.normal(0.003, 0.005, 500))
        result = monte_carlo_dsr_pvalue(daily, dsr_threshold=0.0, n_trials=500, seed=99)
        assert result["pvalue"] < 0.05, f"Expected low p-value for high-Sharpe series, got {result['pvalue']}"
        assert result["observed_sharpe"] > 5.0

    def test_zero_skill_series_pvalue_near_half(self):
        """A mean-zero-excess series tested against a zero-Sharpe null: observed
        Sharpe ≈ null Sharpe, so p-value should sit around 0.5 (not extreme)."""
        from archimedes.services.rigor_evaluator import monte_carlo_dsr_pvalue

        rng = np.random.default_rng(11)
        # mean exactly the daily rf so excess Sharpe ≈ 0
        rf_daily = 0.05 / 252
        daily = list(rng.normal(rf_daily, 0.01, 600))
        result = monte_carlo_dsr_pvalue(daily, dsr_threshold=0.0, n_trials=500, seed=5)
        assert 0.2 < result["pvalue"] < 0.8, f"Zero-skill series should give p≈0.5, got {result['pvalue']}"

    def test_higher_threshold_raises_pvalue(self):
        """Pinning the null to a higher Sharpe hurdle makes a fixed observed
        Sharpe less significant → larger p-value (monotone in threshold)."""
        from archimedes.services.rigor_evaluator import monte_carlo_dsr_pvalue

        rng = np.random.default_rng(4)
        daily = list(rng.normal(0.0015, 0.01, 500))
        p_low = monte_carlo_dsr_pvalue(daily, dsr_threshold=0.0, n_trials=500, seed=3)["pvalue"]
        p_high = monte_carlo_dsr_pvalue(daily, dsr_threshold=2.0, n_trials=500, seed=3)["pvalue"]
        assert p_high >= p_low, f"Higher null hurdle must not lower the p-value ({p_high} < {p_low})"

    def test_null_bootstrap_centered_on_threshold(self):
        """By construction the null bootstrap Sharpe distribution centers near
        the threshold it was pinned to."""
        from archimedes.services.rigor_evaluator import monte_carlo_dsr_pvalue

        rng = np.random.default_rng(8)
        daily = list(rng.normal(0.002, 0.01, 600))
        result = monte_carlo_dsr_pvalue(daily, dsr_threshold=0.0, n_trials=800, seed=2)
        # Null pinned to Sharpe 0 → bootstrap mean Sharpe should be near 0
        assert abs(result["bootstrap_sharpe_mean"]) < 0.75

    def test_degenerate_constant_series_returns_nan(self):
        from archimedes.services.rigor_evaluator import monte_carlo_dsr_pvalue

        # Constant series: std = 0, Sharpe undefined
        daily = [0.001] * 300
        result = monte_carlo_dsr_pvalue(daily, dsr_threshold=0.0, n_trials=100, seed=0)
        assert result["pvalue"] != result["pvalue"] or result["bootstrap_sharpe_std"] == pytest.approx(0.0, abs=1e-9)

    def test_passes_at_5pct_consistent_with_pvalue(self):
        from archimedes.services.rigor_evaluator import monte_carlo_dsr_pvalue

        rng = np.random.default_rng(3)
        daily = list(rng.normal(0.001, 0.01, 300))
        result = monte_carlo_dsr_pvalue(daily, dsr_threshold=0.0, n_trials=300, seed=1)
        expected = result["pvalue"] < 0.05
        assert result["passes_at_5pct"] == expected


class TestBenjaminiHochbergFDR:
    """Tests for benjamini_hochberg_fdr — BH step-up procedure."""

    def test_all_significant(self):
        """Very small p-values should all be rejected."""
        from archimedes.services.rigor_evaluator import benjamini_hochberg_fdr

        pvalues = [0.001, 0.002, 0.003, 0.004, 0.005]
        result = benjamini_hochberg_fdr(pvalues, fdr_level=0.05)
        assert result["n_rejected"] == 5
        assert all(result["rejected"])

    def test_all_insignificant(self):
        """Large p-values should not be rejected."""
        from archimedes.services.rigor_evaluator import benjamini_hochberg_fdr

        pvalues = [0.80, 0.85, 0.90, 0.95]
        result = benjamini_hochberg_fdr(pvalues, fdr_level=0.05)
        assert result["n_rejected"] == 0
        assert not any(result["rejected"])

    def test_mixed_pvalues_correct_threshold(self):
        """BH threshold check: k/m * q at rank k."""
        from archimedes.services.rigor_evaluator import benjamini_hochberg_fdr

        # m=5, q=0.05 → thresholds [0.01, 0.02, 0.03, 0.04, 0.05]
        pvalues = [0.009, 0.019, 0.04, 0.06, 0.10]
        result = benjamini_hochberg_fdr(pvalues, fdr_level=0.05)
        # Sorted: 0.009 ≤ 0.01 ✓, 0.019 ≤ 0.02 ✓, 0.04 > 0.03 ✗ → k*=2
        assert result["n_rejected"] == 2

    def test_output_keys(self):
        from archimedes.services.rigor_evaluator import benjamini_hochberg_fdr

        result = benjamini_hochberg_fdr([0.01, 0.05, 0.10], fdr_level=0.05)
        for key in ("rejected", "bh_critical_values", "n_rejected", "adjusted_pvalues"):
            assert key in result

    def test_adjusted_pvalues_monotone(self):
        """BH adjusted p-values should be monotone non-decreasing in sorted order."""
        from archimedes.services.rigor_evaluator import benjamini_hochberg_fdr

        rng = np.random.default_rng(5)
        pvalues = list(rng.uniform(0, 1, 20))
        result = benjamini_hochberg_fdr(pvalues, fdr_level=0.05)
        adj = result["adjusted_pvalues"]
        # All adjusted p-values must be in [0,1]
        assert all(0.0 <= p <= 1.0 for p in adj)
        # adjusted_pvalues is returned in original input order; re-order by
        # ascending p-value (BH's natural rank order) and check the
        # running-minimum step enforced non-decreasing values.
        sorted_adj = [a for _, a in sorted(zip(pvalues, adj, strict=True))]
        assert all(sorted_adj[i] <= sorted_adj[i + 1] for i in range(len(sorted_adj) - 1))


class TestBonferroniCorrection:
    """Tests for bonferroni_correction — family-wise error rate control."""

    def test_single_pvalue_unchanged(self):
        """Single hypothesis: Bonferroni correction multiplies by 1."""
        from archimedes.services.rigor_evaluator import bonferroni_correction

        result = bonferroni_correction([0.03], alpha=0.05)
        assert result["adjusted_pvalues"][0] == pytest.approx(0.03)
        assert result["rejected"][0] is True

    def test_multiple_corrections_multiply(self):
        """With m=10, each p-value is multiplied by 10."""
        from archimedes.services.rigor_evaluator import bonferroni_correction

        pvalues = [0.004, 0.006, 0.04, 0.10]
        result = bonferroni_correction(pvalues, alpha=0.05)
        # Adjusted: 0.016, 0.024, 0.16, 0.40
        # Reject at 0.05: first two only
        assert result["n_rejected"] == 2
        assert result["rejected"] == [True, True, False, False]

    def test_adjusted_capped_at_one(self):
        from archimedes.services.rigor_evaluator import bonferroni_correction

        pvalues = [0.9, 0.95]  # adjusted would exceed 1.0
        result = bonferroni_correction(pvalues, alpha=0.05)
        assert all(p <= 1.0 for p in result["adjusted_pvalues"])

    def test_output_keys(self):
        from archimedes.services.rigor_evaluator import bonferroni_correction

        result = bonferroni_correction([0.01, 0.05], alpha=0.05)
        for key in ("rejected", "adjusted_pvalues", "n_rejected"):
            assert key in result

    def test_bonferroni_stricter_than_bh(self):
        """Bonferroni should reject fewer hypotheses than BH for correlated tests."""
        from archimedes.services.rigor_evaluator import benjamini_hochberg_fdr, bonferroni_correction

        rng = np.random.default_rng(6)
        pvalues = list(rng.uniform(0.01, 0.10, 15))
        bh_result = benjamini_hochberg_fdr(pvalues, fdr_level=0.05)
        bonf_result = bonferroni_correction(pvalues, alpha=0.05)
        # Bonferroni is always at least as conservative as BH
        assert bonf_result["n_rejected"] <= bh_result["n_rejected"]


# ─── Library-level PBO (criterion-4 input, #546) ─────────────────────


class TestAlignReturnsStore:
    """Date-aligned inner-join of the daily-returns store (parity with the
    offline analytics-engine/scripts/compute_library_pbo.py::build_aligned_matrix)."""

    def test_aligns_on_joint_dates_in_order(self) -> None:
        store = {
            "a": {"dates": ["2020-01-01", "2020-01-02", "2020-01-03"], "daily_returns": [0.1, 0.2, 0.3]},
            "b": {"dates": ["2020-01-02", "2020-01-03", "2020-01-04"], "daily_returns": [0.5, 0.6, 0.7]},
        }
        matrix = align_returns_store(store)
        # Joint dates are {01-02, 01-03}, sorted; each row maps to the right value.
        assert matrix == {"a": [0.2, 0.3], "b": [0.5, 0.6]}

    def test_fewer_than_two_series_returns_empty(self) -> None:
        store = {"a": {"dates": ["2020-01-01"], "daily_returns": [0.1]}}
        assert align_returns_store(store) == {}

    def test_empty_date_intersection_returns_empty(self) -> None:
        store = {
            "a": {"dates": ["2020-01-01", "2020-01-02"], "daily_returns": [0.1, 0.2]},
            "b": {"dates": ["2021-06-01", "2021-06-02"], "daily_returns": [0.5, 0.6]},
        }
        assert align_returns_store(store) == {}

    def test_drops_series_missing_dates_or_returns(self) -> None:
        store = {
            "a": {"dates": ["2020-01-01", "2020-01-02"], "daily_returns": [0.1, 0.2]},
            "b": {"dates": [], "daily_returns": []},  # unusable → dropped
            "c": {"dates": ["2020-01-02", "2020-01-03"], "daily_returns": [0.5, 0.6]},
        }
        matrix = align_returns_store(store)
        assert set(matrix.keys()) == {"a", "c"}
        assert matrix == {"a": [0.2], "c": [0.5]}

    def test_malformed_record_length_mismatch_raises(self) -> None:
        # strict=True (parity with the offline build_aligned_matrix): a record
        # whose dates and daily_returns lengths disagree fails loud rather than
        # silently producing a misaligned row.
        store = {
            "a": {"dates": ["2020-01-01", "2020-01-02"], "daily_returns": [0.1, 0.2]},
            "b": {"dates": ["2020-01-01", "2020-01-02", "2020-01-03"], "daily_returns": [0.5, 0.6]},
        }
        with pytest.raises(ValueError):
            align_returns_store(store)


class TestComputeLibraryPbo:
    """Single library-wide CSCV PBO over the whole selection set (#546)."""

    @staticmethod
    def _series(seed: int, n: int = 256) -> dict[str, list]:
        rng = np.random.default_rng(seed)
        dates = [f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]
        return {"dates": dates, "daily_returns": rng.normal(0.0005, 0.01, n).tolist()}

    def test_returns_float_in_unit_interval(self) -> None:
        store = {f"s{i}": self._series(i) for i in range(4)}
        pbo = compute_library_pbo(store, s_partitions=8)
        assert pbo is not None
        assert 0.0 <= pbo <= 1.0

    def test_fewer_than_two_series_returns_none(self) -> None:
        store = {"a": self._series(1)}
        assert compute_library_pbo(store) is None

    def test_empty_store_returns_none(self) -> None:
        assert compute_library_pbo({}) is None

    def test_short_joint_window_fails_closed(self) -> None:
        # A joint window shorter than s_partitions is non-computable: must fail
        # closed to None, NOT return compute_pbo's all-0.0 PASS sentinel (#546).
        dates = [f"2020-01-{d:02d}" for d in range(1, 6)]  # 5 dates < default S=16
        store = {
            "a": {"dates": dates, "daily_returns": [0.01] * 5},
            "b": {"dates": dates, "daily_returns": [0.02] * 5},
        }
        assert compute_library_pbo(store) is None

    def test_empty_pbo_scores_fail_closed(self, monkeypatch) -> None:
        # If compute_pbo yields no scores, fail closed to None (not silent pass).
        store = {f"s{i}": self._series(i) for i in range(2)}
        monkeypatch.setattr("archimedes.services.rigor_evaluator.compute_pbo", lambda *a, **k: {})
        assert compute_library_pbo(store) is None

    def test_non_finite_pbo_fails_closed(self, monkeypatch) -> None:
        # A NaN/inf library PBO must not pass criterion 4 — fail closed to None.
        store = {f"s{i}": self._series(i) for i in range(2)}
        monkeypatch.setattr(
            "archimedes.services.rigor_evaluator.compute_pbo",
            lambda *a, **k: dict.fromkeys(store, float("nan")),
        )
        assert compute_library_pbo(store) is None


class TestRigorGateLibraryPbo:
    """run_rigor_gate prefers the library PBO and labels its provenance (#546)."""

    @staticmethod
    def _returns() -> list[float]:
        rng = np.random.default_rng(42)
        return rng.normal(0.001, 0.008, size=500).tolist()

    def test_library_pbo_takes_precedence_and_labels_source(self) -> None:
        result = run_rigor_gate(
            strategy_id="s",
            daily_returns=self._returns(),
            num_trials=5,
            pbo_scores={"s": 0.9},  # cohort score would FAIL...
            library_pbo=0.2,  # ...but the library PBO (0.2) is the real input
            strategy_code="class S: def next(self): self.buy()",
        )
        assert result.pbo_source == "library"
        assert result.pbo_score == 0.2
        assert "source=library" in result.gate_details["pbo"]

    def test_falls_back_to_cohort_when_no_library_pbo(self) -> None:
        result = run_rigor_gate(
            strategy_id="s",
            daily_returns=self._returns(),
            num_trials=5,
            pbo_scores={"s": 0.2},
            strategy_code="class S: def next(self): self.buy()",
        )
        assert result.pbo_source == "cohort"
        assert result.pbo_score == 0.2
        assert "source=cohort" in result.gate_details["pbo"]

    def test_none_library_pbo_with_no_cohort_fails_closed(self) -> None:
        result = run_rigor_gate(
            strategy_id="s",
            daily_returns=self._returns(),
            num_trials=5,
            pbo_scores=None,
            library_pbo=None,
            strategy_code="class S: def next(self): self.buy()",
        )
        assert not result.passes_all
        assert result.gate_details["pbo"] == "MISSING (source=cohort)"


# ─── Daily-returns store loader (#546) ───────────────────────────────


class TestLoadDailyReturnsStore:
    """load_daily_returns_store: read the store into compute_library_pbo's shape,
    surface the max data_vintage, and degrade gracefully (never raise). #774:
    DB-backed (strategy_daily_returns table) — a tmp, isolated SQLite session
    stands in for production, passed explicitly so no shared-engine
    monkeypatching of archimedes.db is needed."""

    @staticmethod
    def _session(tmp_path):
        from archimedes.db import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(
            f"sqlite:///{tmp_path / 'daily_returns_test.db'}", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(bind=engine)
        return sessionmaker(bind=engine)()

    @staticmethod
    def _insert(session, stem: str, *, vintage: str | None, n: int = 4) -> None:
        from datetime import date as date_cls

        from archimedes.models.daily_returns_store import StrategyDailyReturn

        for i in range(n):
            session.add(
                StrategyDailyReturn(
                    stem=stem,
                    date=date_cls(2020, 1, 1 + i),
                    daily_return=0.001 * (i + 1),
                    data_vintage=vintage,
                )
            )
        session.commit()

    def test_loads_shape_and_max_vintage(self, tmp_path) -> None:
        session = self._session(tmp_path)
        self._insert(session, "alpha", vintage="2026-06-10")
        self._insert(session, "beta", vintage="2026-06-11")
        self._insert(session, "gamma", vintage="2026-06-09")

        store, vintage = load_daily_returns_store(session=session)

        assert set(store.keys()) == {"alpha", "beta", "gamma"}
        # Each entry projected onto exactly {dates, daily_returns}.
        for rec in store.values():
            assert set(rec.keys()) == {"dates", "daily_returns"}
            assert len(rec["dates"]) == len(rec["daily_returns"]) == 4
        # Max vintage across stems.
        assert vintage == "2026-06-11"

    def test_empty_table_returns_empty_no_raise(self, tmp_path) -> None:
        session = self._session(tmp_path)
        store, vintage = load_daily_returns_store(session=session)
        assert store == {}
        assert vintage is None

    def test_db_read_failure_degrades_gracefully(self) -> None:
        """Mirrors the pre-#774 'never raise' contract: a broken session (DB
        unreachable, table missing) must degrade to ({}, None), not raise."""
        from unittest.mock import Mock

        broken_session = Mock()
        broken_session.query.side_effect = RuntimeError("connection lost")

        store, vintage = load_daily_returns_store(session=broken_session)
        assert store == {}
        assert vintage is None

    def test_record_without_vintage_yields_none_vintage(self, tmp_path) -> None:
        session = self._session(tmp_path)
        self._insert(session, "novintage", vintage=None, n=2)

        store, vintage = load_daily_returns_store(session=session)
        assert set(store.keys()) == {"novintage"}
        assert vintage is None

    def test_default_session_falls_back_to_get_session(self, tmp_path, monkeypatch) -> None:
        """No explicit session → uses archimedes.db.get_session() (production
        default). Verified by patching get_session to return our tmp session."""
        import contextlib

        import archimedes.db as db_module

        session = self._session(tmp_path)
        self._insert(session, "solo", vintage="2026-07-03")

        @contextlib.contextmanager
        def _fake_get_session():
            yield session

        monkeypatch.setattr(db_module, "get_session", _fake_get_session)

        store, vintage = load_daily_returns_store()
        assert set(store.keys()) == {"solo"}
        assert vintage == "2026-07-03"

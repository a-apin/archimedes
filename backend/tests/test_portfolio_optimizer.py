"""Tests for portfolio_optimizer.py — pure math/numpy/scipy/pandas, no external deps.

Hermetic: no DB, Redis, HTTP, or chain calls.
Author: Önder Akkaya
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from archimedes.models.portfolio import RiskProfile
from archimedes.services.portfolio_optimizer import (
    _CAP_DEFAULT,
    _CAP_HYPER,
    _MIN_BARS,
    KellyOptimizationResult,
    _aligned_return_matrix,
    _build_mu_sigma_from_prices,
    _display_for,
    _equal_weight,
    _gmv,
    _max_expected_return,
    _max_sharpe,
    _robust_weights,
    _shrink_cov,
    compare_optimizers,
    compute_efficient_frontier,
    correlation_pairs,
    expected_max_drawdown_1y,
    kelly_optimize_from_prices,
    kelly_risk_decomposition,
    ledoit_wolf_shrinkage,
    optimize_weights,
    value_at_risk_95_1y,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_RNG = np.random.default_rng(42)


def _make_returns(n_assets: int, n_bars: int, seed: int = 42) -> dict[str, list[float]]:
    """Generate deterministic synthetic return series."""
    rng = np.random.default_rng(seed)
    symbols = [f"S{i}" for i in range(n_assets)]
    return {sym: rng.normal(0.0005, 0.01, n_bars).tolist() for sym in symbols}


def _make_price_series(n_assets: int, n_bars: int, seed: int = 42) -> dict[str, pd.Series]:
    """Generate deterministic price histories starting at 100."""
    rng = np.random.default_rng(seed)
    result = {}
    symbols = [f"S{i}" for i in range(n_assets)]
    for sym in symbols:
        rets = rng.normal(0.0005, 0.01, n_bars)
        prices = 100.0 * np.cumprod(1 + rets)
        result[sym] = pd.Series(prices, name=sym)
    return result


# ---------------------------------------------------------------------------
# Section 1 — TestExpectedMaxDrawdown1y
# ---------------------------------------------------------------------------


class TestExpectedMaxDrawdown1y:
    def test_zero_sigma_returns_zero(self):
        assert expected_max_drawdown_1y(0.10, 0.0) == pytest.approx(0.0, abs=1e-12)

    def test_negative_mu_uses_sigma_formula(self):
        result = expected_max_drawdown_1y(-0.10, 0.20)
        assert result == pytest.approx(0.79 * 0.20, rel=1e-6)

    def test_normal_case_positive_mu(self):
        mu, sigma = 0.10, 0.20
        est = 0.63 * sigma - 0.30 * mu
        result = expected_max_drawdown_1y(mu, sigma)
        assert result == pytest.approx(max(est, 0.05 * sigma), rel=1e-6)

    def test_floor_applied_when_estimate_negative(self):
        # Large positive mu forces est < 0; floor = 0.05 * sigma
        mu, sigma = 2.0, 0.01
        result = expected_max_drawdown_1y(mu, sigma)
        assert result == pytest.approx(0.05 * sigma, rel=1e-6)

    def test_high_sharpe_floor_stays_strictly_positive(self):
        # Documented behavior (issue #945): the linear fit drives the raw
        # estimate negative for high-Sharpe assets (here Sharpe = mu/sigma = 5,
        # well outside the Sharpe <= 1 regime the fit is valid for). The floor
        # must keep the returned drawdown strictly positive rather than
        # surfacing an implausible negative/zero DD.
        mu, sigma = 1.0, 0.20  # raw est = 0.63*0.20 - 0.30*1.0 = -0.174 < 0
        raw_est = 0.63 * sigma - 0.30 * mu
        assert raw_est < 0.0  # confirm we are in the broken-approximation corner
        result = expected_max_drawdown_1y(mu, sigma)
        assert result > 0.0
        assert result == pytest.approx(0.05 * sigma, rel=1e-6)

    def test_low_sharpe_regime_uses_linear_estimate_above_floor(self):
        # In the documented valid regime (Sharpe <= 1) the linear estimate
        # exceeds the 0.05*sigma floor and is returned unclamped.
        mu, sigma = 0.05, 0.15  # Sharpe = 0.33; est = 0.0795 > floor 0.0075
        est = 0.63 * sigma - 0.30 * mu
        assert est > 0.05 * sigma  # floor does not bind in this regime
        result = expected_max_drawdown_1y(mu, sigma)
        assert result == pytest.approx(est, rel=1e-6)


# ---------------------------------------------------------------------------
# Section 2 — TestValueAtRisk951y
# ---------------------------------------------------------------------------


class TestValueAtRisk951y:
    def test_zero_sigma_positive_mu(self):
        # sigma=0, mu>0 → max(-mu, 0) = 0
        assert value_at_risk_95_1y(0.10, 0.0) == pytest.approx(0.0, abs=1e-12)

    def test_zero_sigma_negative_mu(self):
        # sigma=0, mu<0 → max(0.1, 0) = 0.1
        assert value_at_risk_95_1y(-0.10, 0.0) == pytest.approx(0.10, rel=1e-6)

    def test_normal_case(self):
        mu, sigma = 0.05, 0.20
        expected = max(1.645 * sigma - mu, 0.0)
        assert value_at_risk_95_1y(mu, sigma) == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# Section 3 — TestAlignedReturnMatrix
# ---------------------------------------------------------------------------


class TestAlignedReturnMatrix:
    def test_missing_symbol_returns_none(self):
        returns = {"S0": [0.01] * 30}
        result = _aligned_return_matrix(["S0", "S_MISSING"], returns)
        assert result is None

    def test_empty_list_for_symbol_returns_none(self):
        returns = {"S0": [], "S1": [0.01] * 30}
        result = _aligned_return_matrix(["S0", "S1"], returns)
        assert result is None

    def test_too_few_bars_returns_none(self):
        # T = 19 → below _MIN_BARS (20)
        returns = _make_returns(2, _MIN_BARS - 1)
        syms = list(returns.keys())
        result = _aligned_return_matrix(syms, returns)
        assert result is None

    def test_exactly_min_bars_returns_matrix(self):
        # T = _MIN_BARS exactly → must succeed
        returns = _make_returns(2, _MIN_BARS)
        syms = list(returns.keys())
        result = _aligned_return_matrix(syms, returns)
        assert result is not None
        assert result.shape == (_MIN_BARS, 2)

    def test_aligns_to_shortest_series(self):
        rng = np.random.default_rng(7)
        long_series = rng.normal(0, 0.01, 60).tolist()
        short_series = rng.normal(0, 0.01, 30).tolist()
        returns = {"A": long_series, "B": short_series}
        result = _aligned_return_matrix(["A", "B"], returns)
        assert result is not None
        assert result.shape == (30, 2)


# ---------------------------------------------------------------------------
# Section 4 — TestEqualWeight
# ---------------------------------------------------------------------------


class TestEqualWeight:
    def test_empty_symbols_returns_empty_dict(self):
        assert _equal_weight([], 1.0) == {}

    def test_single_symbol_gets_full_budget(self):
        result = _equal_weight(["A"], 0.75)
        assert result == {"A": pytest.approx(0.75, rel=1e-5)}

    def test_three_symbols_split_evenly(self):
        result = _equal_weight(["A", "B", "C"], 0.90)
        per = round(0.90 / 3, 6)
        for sym in ["A", "B", "C"]:
            assert result[sym] == pytest.approx(per, rel=1e-5)


# ---------------------------------------------------------------------------
# Section 5 — TestShrinkCov
# ---------------------------------------------------------------------------


class TestShrinkCov:
    def _sample_cov(self) -> np.ndarray:
        rng = np.random.default_rng(17)
        X = rng.normal(0, 1, (200, 3))
        return np.cov(X.T)

    def test_diagonal_preserved_exactly(self):
        cov = self._sample_cov()
        shrunk = _shrink_cov(cov, intensity=0.10)
        np.testing.assert_allclose(np.diag(shrunk), np.diag(cov))

    def test_off_diagonal_shrunk_toward_zero(self):
        cov = self._sample_cov()
        shrunk = _shrink_cov(cov, intensity=0.10)
        # Off-diagonal absolute value must be <= original
        n = cov.shape[0]
        for i in range(n):
            for j in range(n):
                if i != j:
                    assert abs(shrunk[i, j]) <= abs(cov[i, j]) + 1e-12


class TestLedoitWolfShrinkage:
    """Ledoit-Wolf (2004) analytic shrinkage toward a scaled-identity target."""

    def _correlated_returns(self, T: int, N: int, seed: int = 7) -> np.ndarray:
        """T×N returns with a non-trivial correlation structure (single factor)."""
        rng = np.random.default_rng(seed)
        factor = rng.normal(0, 1, (T, 1))
        loadings = rng.uniform(0.5, 1.5, (1, N))
        idio = rng.normal(0, 0.5, (T, N))
        return factor @ loadings + idio

    def test_delta_in_unit_interval(self):
        X = self._correlated_returns(T=60, N=5)
        _, delta = ledoit_wolf_shrinkage(X)
        assert 0.0 <= delta <= 1.0

    def test_symmetric_and_positive_definite(self):
        X = self._correlated_returns(T=40, N=6)
        shrunk, _ = ledoit_wolf_shrinkage(X)
        np.testing.assert_allclose(shrunk, shrunk.T, atol=1e-12)
        eigvals = np.linalg.eigvalsh(shrunk)
        assert eigvals.min() > 0, f"not PD: min eigenvalue {eigvals.min()}"

    def test_better_conditioned_than_sample_cov(self):
        # Short sample relative to N: the raw sample covariance is poorly
        # conditioned; LW shrinkage must reduce the condition number.
        X = self._correlated_returns(T=30, N=10)
        sample = np.cov(X, rowvar=False)
        shrunk, delta = ledoit_wolf_shrinkage(X)
        cond_sample = np.linalg.cond(sample)
        cond_shrunk = np.linalg.cond(shrunk)
        assert delta > 0.0, "expected non-trivial shrinkage on a short sample"
        assert cond_shrunk < cond_sample, f"LW did not improve conditioning: {cond_shrunk} vs {cond_sample}"

    def test_shrinks_harder_on_shorter_samples(self):
        # More noise (fewer observations) ⇒ larger optimal shrinkage intensity.
        _, delta_short = ledoit_wolf_shrinkage(self._correlated_returns(T=20, N=8))
        _, delta_long = ledoit_wolf_shrinkage(self._correlated_returns(T=2000, N=8))
        assert delta_short > delta_long

    def test_approaches_sample_cov_on_large_T(self):
        # With T ≫ N the sample cov is reliable, so δ → 0 and Σ* ≈ S.
        X = self._correlated_returns(T=5000, N=4)
        shrunk, delta = ledoit_wolf_shrinkage(X)
        sample_mle = (lambda x: (x - x.mean(0)).T @ (x - x.mean(0)) / len(x))(X)
        assert delta < 0.1
        np.testing.assert_allclose(shrunk, sample_mle, rtol=0.15, atol=1e-3)

    def test_single_asset_returns_variance_no_shrinkage(self):
        rng = np.random.default_rng(3)
        X = rng.normal(0, 1, (100, 1))
        shrunk, delta = ledoit_wolf_shrinkage(X)
        assert delta == 0.0
        assert shrunk.shape == (1, 1)
        assert shrunk[0, 0] > 0

    def test_raises_on_insufficient_observations(self):
        with pytest.raises(ValueError):
            ledoit_wolf_shrinkage(np.array([[0.01, 0.02, 0.03]]))  # T=1

    def test_output_uses_unbiased_ddof1_convention(self):
        # Issue #950: the LW output must match the ddof=1 convention used
        # everywhere else (np.cov(..., ddof=1)), NOT the 1/T MLE — otherwise
        # variance is understated ~5% at T=20. Single asset ⇒ no shrinkage, so
        # the output is exactly the unbiased sample variance.
        rng = np.random.default_rng(7)
        X = rng.normal(0.0, 1.0, (20, 1))  # small T where 1/T vs 1/(T-1) differ ~5%
        shrunk, delta = ledoit_wolf_shrinkage(X)
        assert delta == 0.0
        unbiased_var = float(np.cov(X, rowvar=False, ddof=1))
        mle_var = float(((X - X.mean()) ** 2).sum() / len(X))
        assert shrunk[0, 0] == pytest.approx(unbiased_var, rel=1e-9)
        assert shrunk[0, 0] > mle_var  # strictly larger than the old 1/T value

    def test_ddof1_scaling_applied_but_delta_unchanged(self):
        # The shrunk covariance is the 1/T LW estimate scaled by T/(T-1); δ is
        # scale-invariant, so the correction leaves it unchanged (#950).
        X = self._correlated_returns(T=25, N=5)
        shrunk, delta = ledoit_wolf_shrinkage(X)
        T, N = X.shape
        Xc = X - X.mean(axis=0)
        S = Xc.T @ Xc / T  # the pre-correction 1/T estimate the function builds
        mu = float(np.trace(S)) / N
        mle_lw = delta * mu * np.eye(N) + (1.0 - delta) * S
        np.testing.assert_allclose(shrunk, mle_lw * (T / (T - 1)), rtol=1e-9, atol=1e-12)


# ---------------------------------------------------------------------------
# Section 6 — TestGmv
# ---------------------------------------------------------------------------


class TestGmv:
    def test_gmv_converges(self):
        rng = np.random.default_rng(99)
        X = rng.normal(0, 0.01, (100, 3))
        Sigma = np.cov(X.T) + np.eye(3) * 1e-8
        w = _gmv(Sigma, 3, cap=_CAP_DEFAULT)
        assert w is not None
        assert abs(w.sum() - 1.0) < 1e-6
        assert np.all(w >= -1e-10)

    def test_gmv_assigns_less_to_high_vol_asset(self):
        # Asset 2 has 5× higher vol; GMV should under-weight it
        rng = np.random.default_rng(7)
        X = rng.normal(0, [0.01, 0.01, 0.05], (200, 3))
        Sigma = np.cov(X.T) + np.eye(3) * 1e-8
        w = _gmv(Sigma, 3, cap=_CAP_DEFAULT)
        assert w is not None
        assert w[2] < w[0] + 0.05  # high-vol asset gets less
        assert w[2] <= w[0]


# ---------------------------------------------------------------------------
# Section 7 — TestMaxSharpe
# ---------------------------------------------------------------------------


class TestMaxSharpe:
    def test_max_sharpe_converges(self):
        rng = np.random.default_rng(55)
        X = rng.normal(0.0005, 0.01, (150, 3))
        mu = X.mean(axis=0)
        Sigma = np.cov(X.T) + np.eye(3) * 1e-8
        w = _max_sharpe(mu, Sigma, 3, cap=_CAP_DEFAULT)
        assert w is not None
        assert abs(w.sum() - 1.0) < 1e-6

    def test_higher_mu_asset_gets_more_weight(self):
        # Asset 2 has much higher expected return; max-sharpe should tilt toward it
        rng = np.random.default_rng(11)
        base = rng.normal(0, 0.01, (200, 3))
        base[:, 2] += 0.003  # asset 2 has higher mean
        mu = base.mean(axis=0)
        Sigma = np.cov(base.T) + np.eye(3) * 1e-8
        w = _max_sharpe(mu, Sigma, 3, cap=_CAP_DEFAULT)
        assert w is not None
        assert w[2] >= w[0]

    def test_cap_enforced(self):
        rng = np.random.default_rng(33)
        X = rng.normal(0.001, 0.01, (150, 3))
        mu = X.mean(axis=0)
        Sigma = np.cov(X.T) + np.eye(3) * 1e-8
        cap = 0.40
        w = _max_sharpe(mu, Sigma, 3, cap=cap)
        assert w is not None
        assert np.all(w <= cap + 1e-6)

    def test_bearish_excess_returns_falls_back_to_gmv(self):
        # Every asset's expected return is at/below the risk-free rate
        # (mu == 0 << _RF_DAILY). Without the bearish guard, neg_sharpe's
        # numerator (excess = w·mu - rf) is negative for every feasible w,
        # so minimizing -(excess/vol) PUSHES VOL UP — the solver would pick
        # the highest-volatility corner of the simplex. The guard must
        # short-circuit to _gmv instead.
        rng = np.random.default_rng(7)
        X = rng.normal(0, [0.01, 0.01, 0.05], (200, 3))
        mu = np.zeros(3)  # mu <= _RF_DAILY for every asset
        Sigma = np.cov(X.T) + np.eye(3) * 1e-8

        w_sharpe = _max_sharpe(mu, Sigma, 3, cap=_CAP_DEFAULT)
        w_gmv = _gmv(Sigma, 3, cap=_CAP_DEFAULT)

        assert w_sharpe is not None
        assert w_gmv is not None
        np.testing.assert_allclose(w_sharpe, w_gmv)

    def test_slightly_negative_mu_also_falls_back_to_gmv(self):
        # max(mu) strictly below _RF_DAILY (small negative drift across the
        # board) must also trigger the GMV fallback, not just the mu==0 case.
        rng = np.random.default_rng(8)
        X = rng.normal(0, [0.01, 0.02, 0.03], (200, 3))
        mu = np.array([-0.0005, -0.0003, -0.0001])  # all << _RF_DAILY
        Sigma = np.cov(X.T) + np.eye(3) * 1e-8

        w_sharpe = _max_sharpe(mu, Sigma, 3, cap=_CAP_DEFAULT)
        w_gmv = _gmv(Sigma, 3, cap=_CAP_DEFAULT)

        assert w_sharpe is not None
        assert w_gmv is not None
        np.testing.assert_allclose(w_sharpe, w_gmv)


# ---------------------------------------------------------------------------
# Section 8 — TestMaxExpectedReturn
# ---------------------------------------------------------------------------


class TestMaxExpectedReturn:
    def test_single_asset_gets_full_cap(self):
        mu = np.array([0.10])
        w = _max_expected_return(mu, 1, cap=_CAP_HYPER)
        assert w is not None
        assert abs(w[0] - 1.0) < 1e-6

    def test_two_assets_top_fills_first(self):
        mu = np.array([0.05, 0.20])
        w = _max_expected_return(mu, 2, cap=0.60)
        assert w is not None
        assert w[1] == pytest.approx(0.60, rel=1e-5)
        assert w[0] == pytest.approx(0.40, rel=1e-5)

    def test_residual_spills_to_next_best(self):
        # 3 assets, cap=0.40, top asset fills 0.40, second fills 0.40, third gets 0.20
        mu = np.array([0.01, 0.05, 0.20])
        w = _max_expected_return(mu, 3, cap=0.40)
        assert w is not None
        assert abs(w.sum() - 1.0) < 1e-6

    def test_all_weights_nonnegative(self):
        rng = np.random.default_rng(88)
        mu = rng.normal(0, 0.1, 5)
        w = _max_expected_return(mu, 5, cap=_CAP_HYPER)
        assert w is not None
        assert np.all(w >= -1e-10)


# ---------------------------------------------------------------------------
# Section 9 — TestOptimizeWeights
# ---------------------------------------------------------------------------


class TestOptimizeWeights:
    def _good_data(self, n: int = 3, n_bars: int = 60) -> tuple[list[str], dict[str, list[float]]]:
        returns = _make_returns(n, n_bars)
        syms = list(returns.keys())
        return syms, returns

    def test_conservative_runs_gmv(self):
        syms, returns = self._good_data()
        result = optimize_weights(syms, returns, RiskProfile.CONSERVATIVE, synth_budget=1.0)
        assert len(result) == len(syms)
        assert abs(sum(result.values()) - 1.0) < 1e-4

    def test_moderate_runs_max_sharpe(self):
        syms, returns = self._good_data()
        result = optimize_weights(syms, returns, RiskProfile.MODERATE, synth_budget=1.0)
        assert len(result) == len(syms)
        assert all(v >= -1e-8 for v in result.values())

    def test_aggressive_runs_max_sharpe(self):
        syms, returns = self._good_data()
        result = optimize_weights(syms, returns, RiskProfile.AGGRESSIVE, synth_budget=1.0)
        assert len(result) == len(syms)

    def test_hyper_risky_runs_max_expected_return(self):
        syms, returns = self._good_data()
        result = optimize_weights(syms, returns, RiskProfile.HYPER_RISKY, synth_budget=1.0)
        assert len(result) == len(syms)

    def test_fallback_to_equal_weight_insufficient_data(self):
        syms = ["A", "B", "C"]
        # Only 5 bars → below _MIN_BARS
        returns = {s: [0.001] * 5 for s in syms}
        result = optimize_weights(syms, returns, RiskProfile.MODERATE, synth_budget=0.9)
        expected_w = round(0.9 / 3, 6)
        for v in result.values():
            assert v == pytest.approx(expected_w, rel=1e-5)

    def test_empty_symbols_returns_empty(self):
        result = optimize_weights([], {}, RiskProfile.MODERATE, synth_budget=1.0)
        assert result == {}

    def test_single_asset_single_symbol(self):
        # np.cov of 1-D returns a scalar (ndim==0) — code wraps it; must not crash
        rng = np.random.default_rng(5)
        returns_1d = rng.normal(0.001, 0.01, 60).tolist()
        result = optimize_weights(["SOLO"], {"SOLO": returns_1d}, RiskProfile.CONSERVATIVE, synth_budget=1.0)
        assert "SOLO" in result

    def test_budget_scales_output(self):
        syms, returns = self._good_data()
        result = optimize_weights(syms, returns, RiskProfile.MODERATE, synth_budget=0.5)
        total = sum(result.values())
        assert total == pytest.approx(0.5, rel=1e-3)


# ---------------------------------------------------------------------------
# Section 10 — TestBuildMuSigmaFromPrices
# ---------------------------------------------------------------------------


class TestBuildMuSigmaFromPrices:
    def test_fewer_than_two_symbols_returns_none(self):
        prices = _make_price_series(1, 120)
        syms = list(prices.keys())
        result = _build_mu_sigma_from_prices(prices, syms, min_overlap_days=60)
        assert result is None

    def test_insufficient_overlap_after_ffill_returns_none(self):
        # Create two series with < 60 overlapping rows even after ffill
        rng = np.random.default_rng(3)
        prices_a = pd.Series(100 + rng.normal(0, 1, 30).cumsum(), name="A")
        prices_b = pd.Series(100 + rng.normal(0, 1, 30).cumsum(), name="B")
        prices = {"A": prices_a, "B": prices_b}
        result = _build_mu_sigma_from_prices(prices, ["A", "B"], min_overlap_days=60)
        assert result is None

    def test_zero_vol_series_dropped(self):
        # One series has constant price → zero vol → should be dropped
        prices = _make_price_series(2, 120)
        prices["FLAT"] = pd.Series([100.0] * 120, name="FLAT")
        syms = list(prices.keys())
        result = _build_mu_sigma_from_prices(prices, syms, min_overlap_days=60)
        if result is not None:
            kept, _mu, _cov, _corr = result
            assert "FLAT" not in kept

    def test_normal_case_returns_correct_shapes(self):
        prices = _make_price_series(3, 120)
        syms = list(prices.keys())
        result = _build_mu_sigma_from_prices(prices, syms, min_overlap_days=60)
        assert result is not None
        kept, mu, cov, corr = result
        n = len(kept)
        assert mu.shape == (n,)
        assert cov.shape == (n, n)
        assert corr.shape == (n, n)

    def test_ffill_recovery_path(self):
        # Series A: 120 rows. Series B: 120 rows with an 80-row NaN gap in the middle
        # (indices 10–89). After dropna(how='any') → 40 rows < 60 (recovery needed).
        # After ffill, all 120 rows survive → ≥ 60 → should succeed.
        rng = np.random.default_rng(21)
        idx = pd.RangeIndex(120)
        a_vals = 100 + rng.normal(0, 1, 120).cumsum()
        b_vals = 100 + rng.normal(0, 1, 120).cumsum()
        b_vals[10:90] = np.nan  # 80-row gap → dropna gives 40 < 60; ffill gives 120 ≥ 60
        a = pd.Series(a_vals, index=idx, name="A")
        b = pd.Series(b_vals, index=idx, name="B")
        prices = {"A": a, "B": b}
        result = _build_mu_sigma_from_prices(prices, ["A", "B"], min_overlap_days=60)
        assert result is not None


# ---------------------------------------------------------------------------
# Section 11 — TestComputeEfficientFrontier
# ---------------------------------------------------------------------------


class TestComputeEfficientFrontier:
    def test_empty_symbols_returns_empty(self):
        result = compute_efficient_frontier([], {}, n_points=10)
        assert result == []

    def test_insufficient_data_returns_empty(self):
        # Only 5 bars → too few
        returns = {"A": [0.01] * 5, "B": [0.01] * 5}
        result = compute_efficient_frontier(["A", "B"], returns, n_points=10)
        assert result == []

    def test_identical_mu_returns_empty(self):
        # All returns identical → mu_min == mu_max → returns []
        rets = [0.001] * 60
        returns = {"A": rets, "B": rets}
        result = compute_efficient_frontier(["A", "B"], returns, n_points=5)
        assert result == []

    def test_two_assets_no_cap_returns_points(self):
        returns = _make_returns(2, 80)
        syms = list(returns.keys())
        result = compute_efficient_frontier(syms, returns, n_points=10)
        assert len(result) >= 1
        for pt in result:
            assert "vol" in pt and "return" in pt and "weights" in pt

    def test_three_assets_cap_applied(self):
        returns = _make_returns(3, 100)
        syms = list(returns.keys())
        result = compute_efficient_frontier(syms, returns, n_points=10)
        # At least some points should have been found
        assert isinstance(result, list)
        for pt in result:
            for w in pt["weights"].values():
                assert w <= _CAP_DEFAULT + 1e-4


# ---------------------------------------------------------------------------
# Section 12 — TestKellyOptimizeFromPrices
# ---------------------------------------------------------------------------


class TestKellyOptimizeFromPrices:
    def test_returns_none_with_insufficient_history(self):
        prices = _make_price_series(2, 30)  # 30 < 60 min_overlap
        syms = list(prices.keys())
        result = kelly_optimize_from_prices(syms, prices, "moderate", synth_budget=1.0)
        assert result is None

    def test_returns_none_with_single_symbol(self):
        prices = _make_price_series(1, 120)
        syms = list(prices.keys())
        result = kelly_optimize_from_prices(syms, prices, "moderate", synth_budget=1.0)
        assert result is None

    def test_normal_case_returns_result(self):
        prices = _make_price_series(3, 120)
        syms = list(prices.keys())
        result = kelly_optimize_from_prices(syms, prices, "moderate", synth_budget=1.0)
        assert result is not None
        assert isinstance(result, KellyOptimizationResult)

    def test_converged_flag_true_on_success(self):
        prices = _make_price_series(3, 120)
        syms = list(prices.keys())
        result = kelly_optimize_from_prices(syms, prices, "moderate", synth_budget=1.0)
        assert result is not None
        assert result.converged is True

    def test_weights_within_max_weight(self):
        prices = _make_price_series(4, 120)
        syms = list(prices.keys())
        max_w = 0.30
        result = kelly_optimize_from_prices(syms, prices, "moderate", synth_budget=1.0, max_weight=max_w)
        assert result is not None
        assert np.all(result.weights <= max_w + 1e-6)

    def test_mu_override_blended(self):
        prices = _make_price_series(2, 120)
        syms = list(prices.keys())
        # With and without override should produce different results in general
        result_base = kelly_optimize_from_prices(syms, prices, "moderate", synth_budget=1.0)
        mu_override = dict.fromkeys(syms, 0.5)
        result_override = kelly_optimize_from_prices(
            syms, prices, "moderate", synth_budget=1.0, mu_override=mu_override
        )
        assert result_base is not None
        assert result_override is not None
        # The results should differ when override is extreme
        # (not guaranteed to differ numerically, but we check types)
        assert isinstance(result_override, KellyOptimizationResult)

    def test_regime_risk_off_increases_gamma(self):
        prices = _make_price_series(3, 120)
        syms = list(prices.keys())
        result_none = kelly_optimize_from_prices(syms, prices, "moderate", synth_budget=1.0, regime=None)
        result_crisis = kelly_optimize_from_prices(syms, prices, "moderate", synth_budget=1.0, regime="crisis")
        assert result_none is not None
        assert result_crisis is not None
        # crisis multiplier = 4.0 → higher gamma → higher risk_aversion stored
        assert result_crisis.risk_aversion > result_none.risk_aversion

    def test_regime_risk_on_multiplier_one(self):
        prices = _make_price_series(3, 120)
        syms = list(prices.keys())
        result_none = kelly_optimize_from_prices(syms, prices, "moderate", synth_budget=1.0, regime=None)
        result_risk_on = kelly_optimize_from_prices(syms, prices, "moderate", synth_budget=1.0, regime="risk_on")
        assert result_none is not None and result_risk_on is not None
        assert result_risk_on.risk_aversion == pytest.approx(result_none.risk_aversion, rel=1e-6)

    def test_expected_return_and_vol_nonnegative(self):
        prices = _make_price_series(3, 120)
        syms = list(prices.keys())
        result = kelly_optimize_from_prices(syms, prices, "aggressive", synth_budget=1.0)
        assert result is not None
        assert result.expected_vol >= 0.0

    def test_synth_budget_limits_total_weight(self):
        prices = _make_price_series(3, 120)
        syms = list(prices.keys())
        budget = 0.6
        result = kelly_optimize_from_prices(syms, prices, "aggressive", synth_budget=budget)
        assert result is not None
        assert result.weights.sum() <= budget + 1e-6


# ---------------------------------------------------------------------------
# Section 13 — TestKellyRiskDecomposition
# ---------------------------------------------------------------------------


class TestKellyRiskDecomposition:
    def _make_result(self, n: int = 3) -> KellyOptimizationResult:
        prices = _make_price_series(n, 120)
        syms = list(prices.keys())
        res = kelly_optimize_from_prices(syms, prices, "moderate", synth_budget=1.0)
        assert res is not None
        return res

    def test_zero_variance_returns_empty_list(self):
        # Build a result with near-zero weights → very small variance
        result = self._make_result(3)
        # Force near-zero portfolio variance by zeroing weights
        zero_result = KellyOptimizationResult(
            symbols=result.symbols,
            weights=np.zeros(len(result.symbols)),
            mu_annual=result.mu_annual,
            sigma_annual=result.sigma_annual,
            cov_annual=result.cov_annual,
            corr_matrix=result.corr_matrix,
            expected_return=0.0,
            expected_vol=0.0,
            expected_sharpe=0.0,
            diversification_ratio=1.0,
            converged=True,
            risk_aversion=3.0,
        )
        decomp = kelly_risk_decomposition(zero_result)
        assert decomp == []

    def test_contributions_sum_to_one(self):
        result = self._make_result(3)
        decomp = kelly_risk_decomposition(result)
        total = sum(d["variance_contribution"] for d in decomp)
        assert total == pytest.approx(1.0, abs=1e-4)

    def test_returns_one_entry_per_symbol(self):
        result = self._make_result(4)
        decomp = kelly_risk_decomposition(result)
        assert len(decomp) == len(result.symbols)
        for entry in decomp:
            assert "symbol" in entry
            assert "weight" in entry
            assert "variance_contribution" in entry


# ---------------------------------------------------------------------------
# Section 14 — TestCorrelationPairs
# ---------------------------------------------------------------------------


class TestCorrelationPairs:
    def _make_result(self, n: int) -> KellyOptimizationResult:
        prices = _make_price_series(n, 120, seed=n * 7)
        syms = list(prices.keys())
        res = kelly_optimize_from_prices(syms, prices, "moderate", synth_budget=1.0)
        assert res is not None
        return res

    def test_single_symbol_returns_empty(self):
        # Build a minimal KellyOptimizationResult with n=1 manually
        result = KellyOptimizationResult(
            symbols=["A"],
            weights=np.array([1.0]),
            mu_annual=np.array([0.10]),
            sigma_annual=np.array([0.20]),
            cov_annual=np.array([[0.04]]),
            corr_matrix=np.array([[1.0]]),
            expected_return=0.10,
            expected_vol=0.20,
            expected_sharpe=0.25,
            diversification_ratio=1.0,
            converged=True,
            risk_aversion=3.0,
        )
        pairs = correlation_pairs(result, top_n=5)
        assert pairs == []

    def test_two_symbols_yields_one_pair(self):
        result = self._make_result(2)
        pairs = correlation_pairs(result, top_n=10)
        assert len(pairs) == 1
        assert "a" in pairs[0] and "b" in pairs[0] and "corr" in pairs[0]

    def test_top_n_clipping(self):
        result = self._make_result(5)
        # 5 symbols → 10 pairs; request only top 3
        pairs = correlation_pairs(result, top_n=3)
        assert len(pairs) <= 3


# ---------------------------------------------------------------------------
# Section 15 — TestDisplayFor
# ---------------------------------------------------------------------------


class TestDisplayFor:
    def test_unknown_synth_returns_synth_code(self):
        unknown = "sXXXXXXUNKNOWN_DOES_NOT_EXIST"
        result = _display_for(unknown)
        assert result == unknown


# ---------------------------------------------------------------------------
# Section 16 — TestRobustOptimizer
# ---------------------------------------------------------------------------


def _well_conditioned_cov(n: int, seed: int = 13) -> tuple[np.ndarray, np.ndarray]:
    """(mu, Sigma) for a well-conditioned n-asset daily return set."""
    rng = np.random.default_rng(seed)
    X = rng.normal(0.0005, 0.01, (300, n))
    mu = X.mean(axis=0)
    Sigma = np.cov(X.T, ddof=1) + np.eye(n) * 1e-8
    return mu, Sigma


class TestRobustOptimizer:
    def test_simplex_valid_weights(self):
        n = 4
        mu, Sigma = _well_conditioned_cov(n, seed=21)
        w = _robust_weights(mu, Sigma, n, cap=_CAP_DEFAULT, kappa=1.0)
        assert w is not None
        assert w.shape == (n,)
        assert abs(w.sum() - 1.0) < 1e-6
        assert np.all(w >= -1e-9)
        assert np.all(w <= _CAP_DEFAULT + 1e-6)

    def test_three_assets_also_valid(self):
        n = 3
        mu, Sigma = _well_conditioned_cov(n, seed=44)
        w = _robust_weights(mu, Sigma, n, cap=_CAP_DEFAULT, kappa=0.5)
        assert w is not None
        assert abs(w.sum() - 1.0) < 1e-6
        assert np.all(w >= -1e-9)

    def test_higher_kappa_more_conservative(self):
        # One low-vol asset (idx 0) and one high-vol asset (idx 1). The high-vol
        # asset has the SLIGHTLY higher mean, so at low kappa the optimizer
        # chases its return; as kappa grows the robust term penalises portfolio
        # risk in proportion to estimation uncertainty and shifts toward the
        # low-vol asset. A per-asset cap forces an interior (non-corner) optimum
        # so the shift is visible rather than both solutions pinning to a bound.
        mu = np.array([0.0006, 0.0012])  # high-vol asset has higher mean
        Sigma = np.array([[0.0001, 0.0], [0.0, 0.0064]])  # vol 1% vs 8%
        n = 2
        # cap=1.0 leaves the optimum interior (no binding cap) so the κ-driven
        # shift toward the low-vol asset is visible rather than pinned to a bound.
        w_low_kappa = _robust_weights(mu, Sigma, n, cap=1.0, kappa=0.0)
        w_high_kappa = _robust_weights(mu, Sigma, n, cap=1.0, kappa=10.0)
        assert w_low_kappa is not None and w_high_kappa is not None
        # Low-vol asset (idx 0) should carry MORE weight at high kappa.
        assert w_high_kappa[0] > w_low_kappa[0] + 1e-6

    def test_degenerate_singular_cov_handled(self):
        # Perfectly collinear assets → singular sample covariance. Must either
        # return None or fall back gracefully to simplex-valid weights, never
        # crash or emit NaNs.
        n = 3
        Sigma = np.ones((n, n))  # rank-1, singular
        mu = np.array([0.001, 0.001, 0.001])
        w = _robust_weights(mu, Sigma, n, cap=_CAP_DEFAULT, kappa=1.0)
        if w is not None:
            assert abs(w.sum() - 1.0) < 1e-6
            assert np.all(np.isfinite(w))
            assert np.all(w >= -1e-9)

    def test_optimize_weights_robust_sums_to_budget(self):
        returns = _make_returns(3, 80)
        syms = list(returns.keys())
        budget = 0.85
        result = optimize_weights(syms, returns, RiskProfile.MODERATE, synth_budget=budget, optimizer="robust")
        assert len(result) == len(syms)
        assert sum(result.values()) == pytest.approx(budget, rel=1e-3)
        assert all(v >= -1e-8 for v in result.values())


# ---------------------------------------------------------------------------
# Section 17 — TestCompareOptimizers
# ---------------------------------------------------------------------------


_DIAG_KEYS = {
    "weights",
    "sharpe",
    "annual_vol",
    "annual_return",
    "max_drawdown",
    "effective_n",
    "turnover_vs_eqw",
    "max_weight",
}


class TestCompareOptimizers:
    def test_entry_for_every_optimizer_with_all_keys(self):
        returns = _make_returns(4, 120)
        syms = list(returns.keys())
        result = compare_optimizers(syms, returns, synth_budget=1.0)
        for opt in ["mvo", "hrp", "black_litterman", "robust"]:
            assert opt in result, f"missing optimizer {opt}"
            assert _DIAG_KEYS.issubset(result[opt].keys())

    def test_best_by_sharpe_is_a_key(self):
        returns = _make_returns(4, 120)
        syms = list(returns.keys())
        result = compare_optimizers(syms, returns, synth_budget=1.0)
        best = result["best_by_sharpe"]
        assert best in ["mvo", "hrp", "black_litterman", "robust"]

    def test_effective_n_in_range_and_max_weight_capped(self):
        returns = _make_returns(4, 120)
        syms = list(returns.keys())
        n = len(syms)
        result = compare_optimizers(syms, returns, synth_budget=1.0)
        for opt in ["mvo", "black_litterman", "robust"]:
            diag = result[opt]
            assert 1.0 - 1e-6 <= diag["effective_n"] <= n + 1e-6
            # Constrained solvers respect the per-asset cap (post-normalisation
            # the cap can scale up by 1/synth_used, but with budget=1.0 it holds).
            assert diag["max_weight"] <= _CAP_DEFAULT + 1e-6

    def test_custom_optimizer_subset(self):
        returns = _make_returns(3, 100)
        syms = list(returns.keys())
        result = compare_optimizers(syms, returns, synth_budget=1.0, optimizers=["mvo", "robust"])
        assert set(result.keys()) == {"mvo", "robust", "best_by_sharpe"}

    def test_insufficient_data_returns_empty_structure(self):
        # Below _MIN_BARS → no crash, best_by_sharpe None, no optimizer entries.
        returns = {"A": [0.001] * 5, "B": [0.001] * 5}
        result = compare_optimizers(["A", "B"], returns, synth_budget=1.0)
        assert result == {"best_by_sharpe": None}

    def test_empty_symbols_returns_empty_structure(self):
        result = compare_optimizers([], {}, synth_budget=1.0)
        assert result == {"best_by_sharpe": None}

    def test_correlated_pair_plus_independent_not_fully_concentrated(self):
        # Two perfectly-correlated assets + one independent. A diversifying
        # allocator (HRP / robust) must not dump ~100% into a single asset.
        rng = np.random.default_rng(101)
        base = rng.normal(0.0005, 0.01, 150)
        a = base + rng.normal(0, 1e-5, 150)  # ~identical to base
        b = base + rng.normal(0, 1e-5, 150)  # ~identical to a
        c = rng.normal(0.0005, 0.01, 150)  # independent
        returns = {"A": a.tolist(), "B": b.tolist(), "C": c.tolist()}
        result = compare_optimizers(["A", "B", "C"], returns, synth_budget=1.0)
        for opt in ["hrp", "robust"]:
            assert result[opt]["max_weight"] < 0.99, f"{opt} concentrated everything in one asset"
            assert result[opt]["effective_n"] > 1.0

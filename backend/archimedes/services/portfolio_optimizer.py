"""Mean-variance portfolio optimizer for the three vault risk profiles.

Maps RiskProfile → optimization objective:

  CONSERVATIVE  → Global Minimum Variance: min w'Σw
  MODERATE      → Max Sharpe: max (μ-rf)'w / sqrt(w'Σw)
  AGGRESSIVE    → Max Sharpe (same objective, looser USYC floor applied upstream)
  HYPER_RISKY   → Max Expected Return: max μ'w  (LP — concentrates in top-μ assets)

All objectives are solved on the long-only unit simplex with a per-asset weight
cap to prevent degenerate concentration. Falls back to equal weight if scipy
optimization fails or price history is too short (< 20 bars).

Owner: Önder (math lane)
Spec:  docs/specs/ecosystem-design-spec.md § 3.3, models/portfolio.py
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.optimize import minimize

from archimedes.models.portfolio import RiskProfile

logger = logging.getLogger(__name__)

_ANNUALIZATION = 252
_RF_DAILY = 0.05 / _ANNUALIZATION  # 5% annual risk-free rate

# Per-asset caps — prevent degenerate corner solutions
_CAP_DEFAULT = 0.40  # Conservative / Moderate / Aggressive
_CAP_HYPER = 0.60  # Hyper-Risky: higher concentration is the intent

_MIN_BARS = 20  # Minimum history required before MVO is meaningful


# ─── Kelly mean-variance: γ-mapped risk aversion ──────────────────
# γ = 2 reproduces half-Kelly (Bell & Cover 1980); γ → 0 = full Kelly;
# γ → ∞ collapses to minimum-variance.  Risk profile controls γ.
RISK_AVERSION: dict[str, float] = {
    "fixed_income": 12.0,
    "conservative": 6.0,
    "moderate": 3.0,
    "aggressive": 2.0,
    "hyper_risky": 1.5,
}

# ─── Regime-conditional risk aversion multiplier ─────────────────
# Effective γ = profile_γ × regime_multiplier.  In stressed regimes the
# investor's effective risk aversion should rise (the optimizer pulls
# toward minimum-variance), independent of their declared profile.  This
# is the standard adaptive-Markowitz adjustment from Ang & Bekaert 2002,
# "International Asset Allocation With Regime Shifts" (Review of
# Financial Studies) — they show that regime-conditioned weights
# strictly dominate static weights across reasonable γ specifications.
#
# Calibration intent (deliberately coarse):
#  - risk_on:    1.0  — the declared profile is appropriate
#  - transition: 1.0  — uncertain regime; do not overreact
#  - risk_off:   2.0  — double effective γ ≈ halve effective Kelly
#  - crisis:     4.0  — quadruple effective γ ≈ minvar-leaning
#
# These multipliers are conservative for a hackathon-stage system and
# are intentionally below typical research-paper values (which sometimes
# go 6–10× in tail regimes) to avoid producing wildly different
# allocations every time the regime detector flips.
REGIME_GAMMA_MULTIPLIER: dict[str, float] = {
    "risk_on": 1.0,
    "transition": 1.0,
    "risk_off": 2.0,
    "crisis": 4.0,
}


@dataclass
class KellyOptimizationResult:
    """Output of the constrained Kelly mean-variance optimizer."""

    symbols: list[str]  # synth codes (e.g. 'sNVDA')
    weights: np.ndarray  # weights, sum ≤ synth_budget
    mu_annual: np.ndarray  # per-asset annualized GROSS return
    sigma_annual: np.ndarray  # per-asset annualized volatility
    cov_annual: np.ndarray  # annualized covariance matrix
    corr_matrix: np.ndarray  # correlation matrix
    expected_return: float  # wᵀμ (gross)
    expected_vol: float  # √(wᵀΣw)
    expected_sharpe: float  # (μ_excess) / σ
    diversification_ratio: float  # weighted_avg_vol / portfolio_vol
    converged: bool
    risk_aversion: float


def expected_max_drawdown_1y(mu_ann: float, sigma_ann: float) -> float:
    """Approximate 1-year expected max drawdown for a GBM with drift.

    Motivated by Magdon-Ismail & Atiya (2004) "Maximum Drawdown".  For a
    Brownian motion with annual drift μ and volatility σ over horizon
    T=1, the expected max-DD is a nonlinear function of the dimensionless
    Sharpe α = μ·√T / σ.  Here we use a **linear approximation** of that
    relationship, calibrated for the low-Sharpe regime:

        E[max-DD over 1y] ≈ 0.63·σ - 0.30·μ        (μ ≥ 0)

    **Validity — read before trusting the output.**  The 0.63/0.30 linear
    fit tracks the Magdon-Ismail curve reasonably only for **Sharpe α ≲ 1**.
    It is NOT accurate for high-Sharpe assets: because the μ term enters
    linearly, the estimate keeps shrinking as μ grows and eventually goes
    negative (e.g. μ = 2·σ ⇒ 0.63·σ - 0.60·σ < 0), which is not a real
    drawdown — the true expected max-DD stays strictly positive for any
    σ > 0.  For α > 1 read the number as a rough lower-bound placeholder,
    not a calibrated estimate; a proper fix (Magdon-Ismail table lookup or
    Monte Carlo) is tracked in issue #945.

    To keep the output honest in that high-Sharpe corner we clamp the
    estimate to a floor of 0.05·σ (5% of annual vol) rather than returning
    a negative or near-zero drawdown.  The floor is a deliberate "never
    claim less than this" guard, not a modelled value; when it binds, the
    caller is in the α > 1 regime where this linear approximation has
    broken down.

    Improves on the previous ``2·σ`` heuristic (the median 1y down-move
    for a zero-Sharpe asset, not a max drawdown).  Returns a POSITIVE
    decimal (e.g. 0.15 = 15% DD).

    Reference: Magdon-Ismail & Atiya (2004), Wilmott Magazine.
    """
    if sigma_ann <= 1e-9:
        return 0.0
    if mu_ann < 0:
        # Negative-drift assets: floor at the no-drift expected max-DD ≈ 0.79σ
        # (the η→0 limit in Magdon-Ismail's table).
        return float(0.79 * sigma_ann)
    est = 0.63 * sigma_ann - 0.30 * mu_ann
    # High-Sharpe guard: the linear fit drives `est` toward (and past) zero
    # as μ grows, so floor at 5% of σ — never claim < 5% of σ.  When this
    # floor binds we are outside the Sharpe ≲ 1 regime the fit is valid for
    # (see docstring / issue #945).
    return float(max(est, 0.05 * sigma_ann))


def value_at_risk_95_1y(mu_ann: float, sigma_ann: float) -> float:
    """Parametric 1-year 95% VaR under a normal-return assumption.

    VaR_95 = -(μ − 1.645·σ).  Returns a POSITIVE decimal (loss size).
    Easy to explain to a non-quant: "5%-chance you lose at least this
    much in a year, assuming returns are normal".
    """
    if sigma_ann <= 1e-9:
        return max(-mu_ann, 0.0)
    return float(max(1.645 * sigma_ann - mu_ann, 0.0))


def optimize_weights(
    symbols: list[str],
    daily_returns: dict[str, list[float]],
    risk_profile: RiskProfile,
    synth_budget: float,
    optimizer: str = "mvo",
) -> dict[str, float]:
    """Compute optimal synth-asset weights for a vault risk profile.

    Args:
        symbols: Ordered list of synth symbols to allocate across.
        daily_returns: {symbol: [per-bar daily returns]} — must cover the
            same date range (aligned). Series are tail-truncated to the
            shortest available length.
        risk_profile: Vault risk profile — selects the MVO objective.
        synth_budget: Total weight budget for synth assets, i.e.
            (1 - USDC_floor). Returned weights sum to this value.
        optimizer: Which optimizer to use. One of "mvo" (default, preserves
            all existing behaviour), "hrp" (Hierarchical Risk Parity,
            López de Prado 2016), "black_litterman" (Black-Litterman
            posterior without views — equivalent to a regularised max-Sharpe
            when P/Q are not supplied externally), or "robust" (robust
            mean-variance with an ellipsoidal uncertainty set on μ,
            Goldfarb & Iyengar 2003 / Tütüncü & Koenig 2004).

    Returns:
        {symbol: weight} summing to synth_budget.
        Falls back to equal-weight if optimization fails.
    """
    n = len(symbols)
    if n == 0:
        return {}

    R = _aligned_return_matrix(symbols, daily_returns)

    if R is None:
        logger.warning(
            "Insufficient return data for %s (%s) — using equal weight",
            optimizer,
            risk_profile.value,
        )
        return _equal_weight(symbols, synth_budget)

    mu = R.mean(axis=0)  # per-bar mean returns, shape (N,)
    Sigma = np.cov(R.T, ddof=1)  # covariance matrix, shape (N, N)
    if Sigma.ndim == 0:
        # Single-asset edge case: np.cov returns a scalar
        Sigma = np.array([[float(Sigma)]])
    Sigma += np.eye(n) * 1e-8  # numerical regularization

    if optimizer == "hrp":
        raw = _hrp_weights(Sigma)
    elif optimizer == "black_litterman":
        raw = _black_litterman_weights(mu, Sigma, n, cap=_CAP_DEFAULT)
    elif optimizer == "robust":
        raw = _robust_weights(mu, Sigma, n, cap=_CAP_DEFAULT)
    else:
        # Default "mvo" path — preserves all prior behaviour
        if risk_profile == RiskProfile.CONSERVATIVE:
            raw = _gmv(Sigma, n, cap=_CAP_DEFAULT)
        elif risk_profile in (RiskProfile.MODERATE, RiskProfile.AGGRESSIVE):
            raw = _max_sharpe(mu, Sigma, n, cap=_CAP_DEFAULT)
        else:
            raw = _max_expected_return(mu, n, cap=_CAP_HYPER)

    if raw is None:
        logger.warning(
            "%s optimization failed for %s — using equal weight",
            optimizer,
            risk_profile.value,
        )
        return _equal_weight(symbols, synth_budget)

    scaled = {sym: round(float(w) * synth_budget, 6) for sym, w in zip(symbols, raw, strict=False)}
    logger.info(
        "%s [%s]: %s",
        optimizer.upper(),
        risk_profile.value,
        "  ".join(f"{s}={w:.1%}" for s, w in scaled.items()),
    )
    return scaled


# ─── Objectives ──────────────────────────────────────────────────────


def compute_efficient_frontier(
    symbols: list[str],
    daily_returns: dict[str, list[float]],
    n_points: int = 30,
) -> list[dict]:
    """Compute the mean-variance efficient frontier.

    Sweeps from the minimum-variance portfolio to the maximum-return portfolio,
    returning n_points (vol, return, weights) triples.

    Returns [] if data is insufficient (< 20 bars).
    """
    n = len(symbols)
    if n == 0:
        return []

    R = _aligned_return_matrix(symbols, daily_returns)
    if R is None:
        return []

    mu = R.mean(axis=0) * _ANNUALIZATION  # annualized expected returns
    Sigma = np.cov(R.T, ddof=1) * _ANNUALIZATION  # annualized covariance
    if Sigma.ndim == 0:
        Sigma = np.array([[float(Sigma)]])
    Sigma += np.eye(n) * 1e-8

    # Bounds for min- and max-return portfolios
    mu_min = float(mu.min())
    mu_max = float(mu.max())
    if mu_min >= mu_max:
        return []

    target_returns = np.linspace(mu_min, mu_max, n_points)
    frontier: list[dict] = []

    w0 = np.ones(n) / n
    # Per-asset cap. The default 0.40 cap exists to bound concentration
    # risk when there are 3+ strategies to diversify across. With only
    # 1–2 strategies the cap makes the frontier infeasible:
    #   n=2, cap=0.4 → max sum = 0.8 < 1.0  (frontier empty)
    #   n=2, cap=0.5 → only equal-weight (0.5, 0.5) is feasible (single point)
    # When n < 3 there's no meaningful diversification cap to apply, so
    # remove it entirely; SLSQP then sweeps the full corner-to-corner
    # frontier. Live symptom: Library "Efficient Frontier" panel said
    # "Need at least 2 Tier-1 strategies" even though there were 2.
    per_asset_cap = 1.0 if n < 3 else _CAP_DEFAULT
    bounds = [(0.0, per_asset_cap)] * n

    for target_mu in target_returns:
        constraints = [
            {"type": "eq", "fun": lambda w: w.sum() - 1.0},
            {"type": "eq", "fun": lambda w, t=target_mu: float(w @ mu) - t},
        ]
        result = minimize(
            lambda w: float(w @ Sigma @ w),
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-9, "maxiter": 500},
        )
        if result.success:
            w = result.x
            port_vol = float(np.sqrt(w @ Sigma @ w))
            port_ret = float(w @ mu)
            frontier.append(
                {
                    "vol": round(port_vol, 6),
                    "return": round(port_ret, 6),
                    "weights": {sym: round(float(wi), 4) for sym, wi in zip(symbols, w, strict=False)},
                }
            )

    return frontier


# ─── Objectives ──────────────────────────────────────────────────────


def _gmv(Sigma: np.ndarray, n: int, cap: float) -> np.ndarray | None:
    """Global Minimum Variance: min w'Σw  s.t. 1'w=1, 0 ≤ w ≤ cap."""
    w0 = np.ones(n) / n
    constraints = [{"type": "eq", "fun": lambda w: float(np.sum(w)) - 1.0}]
    bounds = [(0.0, cap)] * n

    result = minimize(
        lambda w: float(w @ Sigma @ w),
        w0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"ftol": 1e-12, "maxiter": 1000},
    )
    if not result.success:
        return None
    w = np.clip(np.asarray(result.x), 0.0, 1.0)
    return w / w.sum()


def _max_sharpe(
    mu: np.ndarray,
    Sigma: np.ndarray,
    n: int,
    cap: float,
) -> np.ndarray | None:
    """Max Sharpe: maximize (μ-rf)'w / sqrt(w'Σw)  s.t. 1'w=1, 0 ≤ w ≤ cap.

    Bearish-market guard: when every asset's expected return is at or below
    the risk-free rate (``max(mu) <= _RF_DAILY``), the numerator ``w·μ - rf``
    is <= 0 for every feasible w. Minimizing ``-(excess / vol)`` with
    excess <= 0 then PUSHES vol UP (toward the least-negative ratio) — the
    solver deliberately picks the highest-volatility corner of the simplex,
    the opposite of prudent during a downturn. Short-circuit to the Global
    Minimum Variance portfolio instead, which is the textbook fallback when
    no asset offers a positive risk premium.
    """
    if float(np.max(mu)) <= _RF_DAILY:
        return _gmv(Sigma, n, cap)

    w0 = np.ones(n) / n
    constraints = [{"type": "eq", "fun": lambda w: float(np.sum(w)) - 1.0}]
    bounds = [(0.0, cap)] * n

    def neg_sharpe(w: np.ndarray) -> float:
        excess = float(w @ mu) - _RF_DAILY
        port_var = max(float(w @ Sigma @ w), 1e-14)
        return -(excess / math.sqrt(port_var))

    result = minimize(
        neg_sharpe,
        w0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"ftol": 1e-12, "maxiter": 1000},
    )
    if not result.success:
        return None
    w = np.clip(np.asarray(result.x), 0.0, 1.0)
    return w / w.sum()


def _max_expected_return(mu: np.ndarray, n: int, cap: float) -> np.ndarray | None:
    """Max Expected Return: concentrate in highest-μ assets up to cap.

    This is a linear program on the unit simplex. The unconstrained solution
    is 100% in the single best asset; the per-asset cap forces the remaining
    budget to spill into the second-best, third-best, etc.
    """
    w = np.zeros(n)
    remaining = 1.0
    for idx in np.argsort(mu)[::-1]:
        alloc = min(remaining, cap)
        w[idx] = alloc
        remaining -= alloc
        if remaining < 1e-9:
            break

    if remaining > 1e-9:
        # Numerical residual — dump into the highest-μ asset already capped
        best_idx = int(np.argmax(mu))
        w[best_idx] = min(1.0, w[best_idx] + remaining)

    w = np.clip(w, 0.0, 1.0)
    total = w.sum()
    if total <= 0:
        return None
    return w / total


# ─── HRP — Hierarchical Risk Parity (López de Prado 2016) ────────────


def _quasi_diag(link: np.ndarray) -> list[int]:
    """Extract the seriated leaf order from a scipy linkage matrix.

    Performs the same recursive unrolling as ``scipy.cluster.hierarchy.leaves_list``
    but is kept here for documentation clarity. In practice we call
    ``scipy.cluster.hierarchy.leaves_list`` directly.

    Reference: de Prado (2016), "Building Diversified Portfolios that
    Outperform Out-of-Sample", Journal of Portfolio Management 42(4), pp. 59-69.
    """
    return list(leaves_list(link))


def _hrp_recurse(cov: np.ndarray, order: list[int]) -> np.ndarray:
    """Recursive bisection HRP weight allocator.

    Splits *order* into two halves, computes the variance of each half as an
    equal-weighted sub-portfolio, then allocates weight inversely proportional
    to sub-portfolio variance. Recurses until each sub-portfolio contains a
    single asset.

    Args:
        cov: Full covariance matrix (shape N×N). Rows/cols match the original
            symbol ordering, NOT the seriated order.
        order: Current list of asset indices (in seriated order) to allocate
            weight across. Indices refer to rows/cols of *cov*.

    Returns:
        Weight vector of length ``len(order)`` in the same index ordering as
        *order*.
    """
    n = len(order)
    if n == 1:
        return np.array([1.0])

    half = n // 2
    left_idx = order[:half]
    right_idx = order[half:]

    def _cluster_var(idx: list[int]) -> float:
        """Variance of an equal-weighted sub-portfolio of the given assets."""
        k = len(idx)
        w = np.full(k, 1.0 / k)
        sub_cov = cov[np.ix_(idx, idx)]
        return float(w @ sub_cov @ w)

    var_left = _cluster_var(left_idx)
    var_right = _cluster_var(right_idx)
    # Inverse-variance allocation between the two clusters
    total_inv = 1.0 / max(var_left, 1e-14) + 1.0 / max(var_right, 1e-14)
    alpha_left = (1.0 / max(var_left, 1e-14)) / total_inv  # weight for left cluster

    w_left = _hrp_recurse(cov, left_idx) * alpha_left
    w_right = _hrp_recurse(cov, right_idx) * (1.0 - alpha_left)
    return np.concatenate([w_left, w_right])


def _hrp_weights(Sigma: np.ndarray) -> np.ndarray | None:
    """Hierarchical Risk Parity weights (López de Prado 2016).

    Steps:
      1. Cov → corr
      2. Distance matrix ``d_ij = sqrt(0.5 * (1 - corr_ij))``
      3. Single-linkage hierarchical clustering via scipy
      4. Quasi-diagonalisation (seriation) via ``leaves_list``
      5. Recursive-bisection inverse-variance allocation
      6. Weights returned in the original ``symbols`` order

    Reference: de Prado (2016), "Building Diversified Portfolios that
    Outperform Out-of-Sample", *Journal of Portfolio Management* 42(4), 59-69.

    Args:
        Sigma: (N, N) covariance matrix (daily-scale, already regularised).
        symbols: Asset labels corresponding to Sigma rows/cols (used only for
            logging; weights are returned in their order).

    Returns:
        Weight array of shape (N,) in the same order as *symbols*, or None if
        the computation fails (degenerate covariance, clustering error, etc.).
    """
    n = Sigma.shape[0]
    if n == 1:
        return np.array([1.0])

    try:
        # Cov → corr
        std = np.sqrt(np.diag(Sigma))
        std_safe = np.where(std > 1e-12, std, 1e-12)
        corr = Sigma / np.outer(std_safe, std_safe)
        np.clip(corr, -1.0, 1.0, out=corr)

        # Distance matrix (upper triangle condensed vector for scipy)
        dist_sq = 0.5 * (1.0 - corr)
        np.clip(dist_sq, 0.0, None, out=dist_sq)
        dist = np.sqrt(dist_sq)

        # Condensed upper-triangle vector (scipy format)
        condensed: list[float] = []
        for i in range(n):
            for j in range(i + 1, n):
                condensed.append(float(dist[i, j]))
        condensed_arr = np.array(condensed, dtype=float)

        # Single-linkage hierarchical clustering
        link = linkage(condensed_arr, method="single")

        # Seriated leaf order
        order = _quasi_diag(link)

        # Recursive-bisection allocation
        weights_ordered = _hrp_recurse(Sigma, order)

        # Map back to original symbol order
        # 'order' tells us: weights_ordered[k] is the weight for original asset order[k]
        raw = np.zeros(n)
        for k, orig_idx in enumerate(order):
            raw[orig_idx] = weights_ordered[k]

        # Normalise (should already sum to 1 up to float precision)
        total = raw.sum()
        if total <= 1e-9:
            return None
        return raw / total

    except Exception as exc:
        logger.warning("HRP weights failed: %s — falling back to equal weight", exc)
        return None


# ─── Black-Litterman (He & Litterman 1999) ───────────────────────────


def _black_litterman_weights(
    mu_prior: np.ndarray,
    Sigma: np.ndarray,
    n: int,
    cap: float,
    P: np.ndarray | None = None,
    Q: np.ndarray | None = None,
    tau: float = 0.025,
) -> np.ndarray | None:
    """Black-Litterman posterior weights (He & Litterman 1999).

    When no views (P, Q) are supplied the posterior mean collapses to the
    prior mean and we simply run max-Sharpe with the prior — identical to the
    standard MVO path but kept here so ``optimizer='black_litterman'`` is always
    a valid code path even without proprietary views.

    When views are supplied the posterior mean is:

        mu_BL = [ (τΣ)⁻¹ + Pᵀ(τΣP)⁻¹P ]⁻¹
                  × [ (τΣ)⁻¹ μ_prior + Pᵀ(τΣP)⁻¹ Q ]

    This is the standard Black-Litterman formula (Idzorek 2005 notation).
    The posterior covariance is not currently exposed to the optimizer —
    Sigma is used as-is, which is a common simplification in practice.

    Reference: He, G. & Litterman, R. (1999). "The intuition behind
    Black-Litterman model portfolios." Goldman Sachs Investment Management.

    Args:
        mu_prior: Per-bar (daily scale) prior expected return vector, shape (N,).
        Sigma: (N, N) covariance matrix (daily scale, regularised).
        n: Number of assets.
        cap: Per-asset weight cap passed to ``_max_sharpe``.
        P: View matrix, shape (K, N) — K views, each a long/short portfolio.
            None means no views.
        Q: View returns, shape (K,) — expected return for each view.
            None means no views.
        tau: Uncertainty scalar on the prior. Smaller τ = more weight on the
            prior; larger τ = more weight on the views. Default 0.025 (He &
            Litterman's original choice for daily data).

    Returns:
        Optimal weight array of shape (N,), or None if optimisation fails.
    """
    try:
        if P is None or Q is None:
            # No views: use prior mean directly (posterior = prior)
            return _max_sharpe(mu_prior, Sigma, n, cap=cap)

        P = np.asarray(P, dtype=float)
        Q = np.asarray(Q, dtype=float)
        K = P.shape[0]
        if P.shape[1] != n or Q.shape[0] != K:
            logger.warning("Black-Litterman: P/Q shape mismatch — falling back to max-Sharpe with prior")
            return _max_sharpe(mu_prior, Sigma, n, cap=cap)

        # Uncertainty matrix for the views (diagonal, proportional to τΣ)
        # Standard assumption: Ω = diag(P τΣ Pᵀ) as in Idzorek (2005)
        tau_Sigma = tau * Sigma
        Omega = np.diag(np.diag(P @ tau_Sigma @ P.T))

        # Posterior precision matrix
        inv_tau_Sigma = np.linalg.inv(tau_Sigma)
        inv_Omega = np.linalg.inv(Omega)
        posterior_precision = inv_tau_Sigma + P.T @ inv_Omega @ P

        # Posterior mean
        posterior_mean_unnorm = inv_tau_Sigma @ mu_prior + P.T @ inv_Omega @ Q
        mu_bl = np.linalg.solve(posterior_precision, posterior_mean_unnorm)

        return _max_sharpe(mu_bl, Sigma, n, cap=cap)

    except np.linalg.LinAlgError as exc:
        logger.warning("Black-Litterman: linear algebra failure (%s) — falling back to max-Sharpe with prior", exc)
        return _max_sharpe(mu_prior, Sigma, n, cap=cap)
    except Exception as exc:
        logger.warning("Black-Litterman: unexpected error (%s) — falling back to max-Sharpe with prior", exc)
        return _max_sharpe(mu_prior, Sigma, n, cap=cap)


# ─── Robust mean-variance (Goldfarb & Iyengar 2003) ──────────────────


def _robust_weights(
    mu: np.ndarray,
    Sigma: np.ndarray,
    n: int,
    cap: float,
    kappa: float = 1.0,
    delta: float = 1.0,
) -> np.ndarray | None:
    """Robust mean-variance with an ELLIPSOIDAL uncertainty set on μ.

    Estimation error in the expected-return vector is the dominant driver of
    out-of-sample MVO instability (Michaud's "error maximisation"). The robust
    formulation replaces the point estimate ``mu_hat`` with a worst-case mean
    drawn from the ellipsoidal confidence region

        { mu : (mu - mu_hat)' Σ⁻¹ (mu - mu_hat) ≤ κ² }

    and maximises the *worst-case* expected return inside that set. The inner
    minimisation has a closed form — the worst-case mean shifts ``mu_hat`` by
    ``-κ · Σw / sqrt(w'Σw)`` — which collapses the robust counterpart to the
    deterministic second-order-cone program

        maximise over w on the simplex:
            mu_hat'w  −  κ·sqrt(w'Σw)  −  (δ/2)·w'Σw
        s.t. 1'w = 1,  0 ≤ w ≤ cap

    The ``κ·sqrt(w'Σw)`` term penalises estimation uncertainty in proportion to
    portfolio risk, so as κ grows the solution shrinks toward minimum-variance
    (it is willing to give up estimated return to reduce its exposure to a mean
    it does not trust). ``δ`` is a small Markowitz risk-aversion on the variance
    term; δ=1.0 keeps the variance penalty present but secondary to the robust
    term, which is the dominant regulariser here.

    DESIGN CHOICE — doubly-defended estimation control: this solver runs on the
    LEDOIT-WOLF-shrunk covariance (via :func:`ledoit_wolf_shrinkage`, falling
    back to :func:`_shrink_cov` on degenerate input) rather than the raw sample
    Σ that the caller passes in. The robust term defends against error in μ; the
    shrinkage defends against error (and ill-conditioning) in Σ. Both the
    ellipsoid radius and the worst-case shift use Σ⁻¹, so a well-conditioned Σ
    is load-bearing for the geometry of the uncertainty set, not just a
    numerical nicety.

    References:
      - Goldfarb, D. & Iyengar, G. (2003). "Robust portfolio selection
        problems." Mathematics of Operations Research 28(1), 1-38.
      - Tütüncü, R. H. & Koenig, M. (2004). "Robust asset allocation."
        Annals of Operations Research 132, 157-187.

    Args:
        mu: Per-bar (daily scale) expected-return estimate ``mu_hat``, shape (N,).
        Sigma: (N, N) sample covariance (daily scale). Shrunk internally before
            use — callers should pass the same Σ they hand to ``_max_sharpe``.
        n: Number of assets.
        cap: Per-asset weight cap (0 ≤ wᵢ ≤ cap).
        kappa: Ellipsoid radius (estimation-uncertainty aversion). κ=0 recovers
            the plain mean-variance objective; larger κ ⇒ more conservative.
        delta: Markowitz risk-aversion on the explicit variance term.

    Returns:
        Weight array of shape (N,) on the long-only unit simplex (sums to 1.0),
        or None if the optimisation fails (same fallback contract as
        :func:`_max_sharpe`).
    """
    try:
        # Doubly-defended: shrink Σ before the robust solve. LW is data-driven;
        # fall back to fixed diagonal shrinkage if the analytic estimator can't
        # run on this (e.g. degenerate / singular) covariance.
        try:
            Sigma_used, _delta_lw = ledoit_wolf_shrinkage_from_cov(Sigma)
        except Exception:
            Sigma_used = _shrink_cov(Sigma, intensity=0.10)
        Sigma_used = np.asarray(Sigma_used, dtype=float)
        Sigma_used = Sigma_used + np.eye(n) * 1e-10

        w0 = np.ones(n) / n
        constraints = [{"type": "eq", "fun": lambda w: float(np.sum(w)) - 1.0}]
        bounds = [(0.0, cap)] * n

        def neg_robust_obj(w: np.ndarray) -> float:
            port_var = max(float(w @ Sigma_used @ w), 1e-14)
            value = float(w @ mu) - kappa * math.sqrt(port_var) - 0.5 * delta * port_var
            return -value

        result = minimize(
            neg_robust_obj,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-12, "maxiter": 1000},
        )
        if not result.success:
            return None
        w = np.clip(np.asarray(result.x), 0.0, 1.0)
        total = w.sum()
        if total <= 1e-9:
            return None
        return w / total
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Robust weights failed: %s — caller falls back", exc)
        return None


def ledoit_wolf_shrinkage_from_cov(Sigma: np.ndarray) -> tuple[np.ndarray, float]:
    """Diagonal-target shrinkage applied directly to a covariance matrix.

    ``ledoit_wolf_shrinkage`` derives the optimal intensity from the raw return
    *sample*; here the caller (the robust solver) only has the covariance, so we
    apply the same scaled-identity target with a conservative fixed intensity.
    Shrinks toward μ·I where μ is the average variance, exactly the LW2004
    target, and guarantees positive-definiteness. Kept separate from
    :func:`_shrink_cov` (whose target is the *diagonal*, not scaled identity) so
    the robust solver's Σ⁻¹ geometry stays well-conditioned.
    """
    Sigma = np.asarray(Sigma, dtype=float)
    if Sigma.ndim != 2 or Sigma.shape[0] != Sigma.shape[1]:
        raise ValueError("Sigma must be a square 2-D matrix")
    n = Sigma.shape[0]
    avg_var = float(np.trace(Sigma)) / n
    if not np.isfinite(avg_var) or avg_var <= 0:
        raise ValueError("non-positive average variance")
    intensity = 0.10
    target = avg_var * np.eye(n)
    return (1.0 - intensity) * Sigma + intensity * target, intensity


# ─── Optimizer comparison harness ────────────────────────────────────


def compare_optimizers(
    symbols: list[str],
    daily_returns: dict[str, list[float]],
    synth_budget: float,
    risk_profile: RiskProfile = RiskProfile.MODERATE,
    optimizers: list[str] | None = None,
) -> dict:
    """Compare optimizers on in-sample realized portfolio diagnostics.

    For each named optimizer this runs :func:`optimize_weights` to get the
    weights, then evaluates the resulting portfolio on the *same* in-sample
    aligned return matrix and reports realized diagnostics. This is an
    in-sample comparison (no walk-forward / OOS split) — useful for surfacing
    *how differently* the optimizers allocate and the risk/diversification
    trade-offs they strike, NOT for ranking out-of-sample skill. The Tier-1
    admission gate (DSR/PBO/walk-forward) is the OOS arbiter; this harness is a
    descriptive lens on the candidate weight vectors.

    Diagnostics per optimizer:
      - ``sharpe``           — annualized Sharpe over rf (``_RF_DAILY``).
      - ``annual_vol``       — annualized portfolio volatility.
      - ``annual_return``    — annualized mean portfolio return (gross).
      - ``max_drawdown``     — realized max drawdown of the cumulative-return
        path (positive decimal; 0.15 = 15% peak-to-trough).
      - ``effective_n``      — inverse Herfindahl ``1 / Σ wᵢ²`` (effective number
        of positions; ranges 1..n).
      - ``turnover_vs_eqw``  — one-shot rebalance distance from equal weight,
        ``0.5 · Σ |wᵢ − 1/n|`` (0 = equal weight, →1 = fully concentrated).
      - ``max_weight``       — concentration (largest single weight).

    Weights are normalised back to the unit simplex before diagnostics so the
    metrics are budget-independent and comparable across optimizers.

    Args:
        symbols: Ordered synth symbols.
        daily_returns: {symbol: [daily returns]}, aligned/tail-truncated.
        synth_budget: Synth weight budget passed to ``optimize_weights``.
        risk_profile: Risk profile (selects the MVO objective for the "mvo"
            branch; the other optimizers are profile-agnostic).
        optimizers: Which optimizers to compare. Defaults to
            ``["mvo", "hrp", "black_litterman", "robust"]``.

    Returns:
        ``{optimizer_name: {<diagnostics>}, ..., "best_by_sharpe": <name|None>}``.
        On insufficient/degenerate data returns ``{"best_by_sharpe": None}``
        (every requested optimizer absent) rather than raising.
    """
    if optimizers is None:
        optimizers = ["mvo", "hrp", "black_litterman", "robust"]

    out: dict = {}
    n = len(symbols)
    if n == 0:
        out["best_by_sharpe"] = None
        return out

    R = _aligned_return_matrix(symbols, daily_returns)
    if R is None:
        # Insufficient/degenerate data — clear empty-ish structure, no crash.
        out["best_by_sharpe"] = None
        return out

    eqw = np.full(n, 1.0 / n)

    best_name: str | None = None
    best_sharpe = -np.inf

    for opt in optimizers:
        weights_map = optimize_weights(symbols, daily_returns, risk_profile, synth_budget, optimizer=opt)
        # Re-vectorise in `symbols` order; normalise off `synth_budget` to the
        # unit simplex so diagnostics are budget-independent.
        w = np.array([weights_map.get(s, 0.0) for s in symbols], dtype=float)
        total = w.sum()
        w = w / total if total > 1e-12 else eqw.copy()

        port = R @ w  # in-sample daily portfolio returns, shape (T,)

        mean_daily = float(port.mean())
        vol_daily = float(port.std(ddof=1)) if port.shape[0] > 1 else 0.0
        annual_return = mean_daily * _ANNUALIZATION
        annual_vol = vol_daily * math.sqrt(_ANNUALIZATION)
        sharpe = (mean_daily - _RF_DAILY) / vol_daily * math.sqrt(_ANNUALIZATION) if annual_vol > 1e-12 else 0.0

        # Max drawdown of the cumulative-return path.
        cum = np.cumprod(1.0 + port)
        running_max = np.maximum.accumulate(cum)
        drawdowns = 1.0 - cum / running_max
        max_drawdown = float(drawdowns.max()) if drawdowns.size else 0.0

        herfindahl = float(np.sum(w**2))
        effective_n = 1.0 / herfindahl if herfindahl > 1e-12 else float(n)
        turnover = 0.5 * float(np.sum(np.abs(w - eqw)))
        max_weight = float(w.max())

        out[opt] = {
            "weights": {s: round(float(wi), 6) for s, wi in zip(symbols, w, strict=False)},
            "sharpe": round(sharpe, 6),
            "annual_vol": round(annual_vol, 6),
            "annual_return": round(annual_return, 6),
            "max_drawdown": round(max_drawdown, 6),
            "effective_n": round(effective_n, 6),
            "turnover_vs_eqw": round(turnover, 6),
            "max_weight": round(max_weight, 6),
        }

        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_name = opt

    out["best_by_sharpe"] = best_name
    return out


# ─── Helpers ─────────────────────────────────────────────────────────


def _aligned_return_matrix(
    symbols: list[str],
    daily_returns: dict[str, list[float]],
) -> np.ndarray | None:
    """Build a (T, N) return matrix. T = min series length; N = len(symbols).

    Returns None if any symbol is missing or T < _MIN_BARS.
    """
    arrays: list[np.ndarray] = []
    for sym in symbols:
        r = daily_returns.get(sym)
        if not r:
            return None
        arrays.append(np.asarray(r, dtype=float))

    T = min(len(a) for a in arrays)
    if T < _MIN_BARS:
        return None

    return np.column_stack([a[-T:] for a in arrays])


def _equal_weight(symbols: list[str], budget: float) -> dict[str, float]:
    n = len(symbols)
    if n == 0:
        return {}
    w = round(budget / n, 6)
    return dict.fromkeys(symbols, w)


# ─── Kelly mean-variance from price-history dict ──────────────────


def ledoit_wolf_shrinkage(returns: np.ndarray) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf (2004) analytic shrinkage toward a scaled-identity target.

    Reference: Ledoit & Wolf (2004), "A well-conditioned estimator for
    large-dimensional covariance matrices", J. Multivariate Analysis 88(2),
    pp. 365-411.

    The sample covariance S is the maximum-likelihood estimate but is badly
    conditioned — and often singular — when the asset count N is not small
    relative to the number of observations T.  Mean-variance optimisation has
    to invert Σ, and inverting an ill-conditioned S amplifies estimation error
    into wild, unstable corner weights.  LW shrink S toward the structured
    target F = μ·I (μ = average sample variance), picking the intensity δ* that
    minimises the expected Frobenius loss E‖Σ* − Σ‖²:

        Σ* = δ·μ·I + (1 − δ)·S,    δ* = b² / d²  ∈ [0, 1]

    where d² = ‖S − μI‖²_F / N is how far S sits from the target and b² is the
    (clamped) estimation error of S — so a short, noisy sample shrinks hard
    toward the target while a long, clean one barely shrinks at all.  Unlike a
    fixed shrinkage intensity, δ* is derived entirely from the data.

    Args:
        returns: (T, N) array of per-period returns. Demeaning is handled
            internally. Requires T ≥ 2.

    Returns:
        (shrunk_cov, delta) — the (N, N) shrunk covariance on the same period
        as the input returns, and the chosen intensity δ ∈ [0, 1].
    """
    X = np.asarray(returns, dtype=float)
    if X.ndim != 2:
        raise ValueError("returns must be a 2-D (T, N) array")
    T, N = X.shape
    if T < 2:
        raise ValueError(f"need at least 2 observations, got T={T}")

    X = X - X.mean(axis=0, keepdims=True)
    S = (X.T @ X) / T  # MLE sample covariance (1/T convention, per LW2004)

    mu = float(np.trace(S)) / N  # ⟨S, I⟩ — average sample variance
    tr_s2 = float(np.trace(S @ S))
    d2 = tr_s2 / N - mu**2  # ‖S − μI‖²_F / N — dispersion of S around target

    if d2 <= 0.0:
        # S is already isotropic (e.g. N == 1): nothing to shrink.
        return S, 0.0

    # b̄² = (1/T²)·Σ_k‖x_k x_kᵀ − S‖²_F / N, via the closed form
    #      Σ_k‖x_k‖⁴ − T·tr(S²)  (avoids the per-observation outer-product loop).
    sq_norms = np.sum(X**2, axis=1)  # ‖x_k‖² for each observation k
    b_bar2 = (float(np.sum(sq_norms**2)) - T * tr_s2) / (T**2 * N)
    b2 = max(0.0, min(b_bar2, d2))  # clamp to [0, d²] so δ ∈ [0, 1]

    delta = b2 / d2
    shrunk = delta * mu * np.eye(N) + (1.0 - delta) * S
    return shrunk, float(delta)


def _shrink_cov(cov: np.ndarray, intensity: float = 0.10) -> np.ndarray:
    """Fixed-intensity diagonal shrinkage — fallback for ``ledoit_wolf_shrinkage``.

    Used only when the data-driven LW estimator can't run (degenerate input).
    Shrinks a fixed α=0.10 toward the diagonal target (preserve per-asset
    variances, zero off-diagonals): cheap, deterministic, and keeps the matrix
    positive-definite even on short windows, at the cost of a non-optimal,
    hard-coded intensity. Prefer :func:`ledoit_wolf_shrinkage`, whose intensity
    is derived from the data.
    """
    diag = np.diag(np.diag(cov))
    return (1 - intensity) * cov + intensity * diag


def _build_mu_sigma_from_prices(
    price_histories: dict[str, pd.Series],
    symbols: list[str],
    min_overlap_days: int = 60,
) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray] | None:
    """Build (μ, Σ, corr) from a synth → price-Series dict.

    Aligns all series on a common date index (inner join), drops zero-vol
    columns, annualizes, applies identity shrinkage.  Returns None if
    fewer than 2 viable assets remain or alignment yields too few bars.
    """
    series_map = {s: price_histories[s] for s in symbols if s in price_histories and not price_histories[s].empty}
    if len(series_map) < 2:
        return None

    df = pd.DataFrame(series_map).dropna(how="any")
    if len(df) < min_overlap_days:
        df = pd.DataFrame(series_map).ffill().dropna(how="any")
    if len(df) < min_overlap_days:
        return None

    returns = df.pct_change().dropna()
    keep = [c for c in returns.columns if returns[c].std() > 0]
    if len(keep) < 2:
        return None
    returns = returns[keep]

    daily_mean = returns.mean().values
    mu_annual = daily_mean * _ANNUALIZATION
    # Data-driven Ledoit-Wolf shrinkage (preferred); fall back to fixed-intensity
    # diagonal shrinkage only if the analytic estimator can't run.
    try:
        daily_cov, lw_delta = ledoit_wolf_shrinkage(returns.values)
        logger.debug("Ledoit-Wolf shrinkage: δ=%.4f (N=%d, T=%d)", lw_delta, returns.shape[1], len(returns))
        cov_annual = daily_cov * _ANNUALIZATION
    except Exception as exc:
        logger.warning("Ledoit-Wolf shrinkage failed (%s); using fixed diagonal shrinkage", exc)
        cov_annual = _shrink_cov(np.cov(returns.values, rowvar=False) * _ANNUALIZATION, intensity=0.10)
    sigma_annual = np.sqrt(np.diag(cov_annual))
    sigma_safe = np.where(sigma_annual > 1e-9, sigma_annual, 1e-9)
    corr = cov_annual / np.outer(sigma_safe, sigma_safe)
    return keep, mu_annual, cov_annual, corr


def kelly_optimize_from_prices(
    symbols: list[str],
    price_histories: dict[str, pd.Series],
    risk_profile: str,
    synth_budget: float,
    max_weight: float = 0.20,
    mu_override: dict[str, float] | None = None,
    mu_shrinkage: float = 0.5,
    regime: str | None = None,
) -> KellyOptimizationResult | None:
    """Solve the constrained Kelly mean-variance problem.

        maximize   wᵀ(μ - rf) - ½·γ·wᵀΣw
        subject to 0 ≤ wᵢ ≤ max_weight
                   Σ wᵢ ≤ synth_budget

    γ is mapped from ``risk_profile`` via RISK_AVERSION and, when a live
    ``regime`` is provided, multiplied by ``REGIME_GAMMA_MULTIPLIER[regime]``
    so the optimizer becomes more conservative in stressed regimes
    (Ang & Bekaert 2002, *International Asset Allocation With Regime
    Shifts*).  ``mu_override`` lets the caller substitute Kelly-derived
    or backtest-stat expected returns for the sample mean (which is
    noisy on short windows).

    Kelly is defined on *excess* returns — using total returns inflates
    every allocation by rf/σ² per asset (≈1.25 units of leverage at
    rf=5%, σ=20%).  The risk-free rate is subtracted here so a treasury
    pick collapses to ~zero edge (not the 5% it has from gross return).
    """
    built = _build_mu_sigma_from_prices(price_histories, symbols)
    if built is None:
        return None

    kept, mu_sample, cov_annual, corr = built
    if mu_override:
        # ── μ-override shrinkage ────────────────────────────────────
        # The strategy-level backtest CAGR is a noisy *prior* for each
        # asset that strategy voted for; using it raw double-promises
        # paper returns across every asset (e.g. assigning a 25% CAGR
        # equity-momentum CAGR to BIL would have the optimizer load up
        # on T-bills).  Shrink toward the asset's own sample mean with
        # ``mu_shrinkage`` (0=raw override, 1=fully sample-mean).  The
        # default 0.5 splits the difference; the user-facing μ is then
        # neither pure paper-extrapolation nor pure noise.
        mu_total = np.array(
            [
                mu_shrinkage * mu_sample[i] + (1.0 - mu_shrinkage) * mu_override.get(s, mu_sample[i])
                for i, s in enumerate(kept)
            ]
        )
    else:
        mu_total = mu_sample
    # Convert to excess returns (μ - rf).  _RF_DAILY * 252 = annualized rf.
    rf_annual = _RF_DAILY * _ANNUALIZATION
    mu = mu_total - rf_annual
    sigma_annual = np.sqrt(np.diag(cov_annual))

    # Profile γ × regime multiplier (defaults to 1.0 if regime is None
    # or unrecognized — preserves prior behavior for callers that don't
    # pass a regime). The multiplier path is the only thing T-PE.7
    # changes; the underlying Kelly objective is identical.
    base_gamma = RISK_AVERSION.get(risk_profile, 3.0)
    regime_mult = REGIME_GAMMA_MULTIPLIER.get(regime, 1.0) if regime else 1.0
    gamma = base_gamma * regime_mult
    n = len(kept)

    def neg_obj(w: np.ndarray) -> float:
        return -(w @ mu - 0.5 * gamma * w @ cov_annual @ w)

    def neg_grad(w: np.ndarray) -> np.ndarray:
        return -(mu - gamma * cov_annual @ w)

    constraints = [{"type": "ineq", "fun": lambda w: synth_budget - np.sum(w)}]
    bounds = [(0.0, max_weight)] * n
    w0 = np.full(n, synth_budget / n)

    try:
        res = minimize(
            neg_obj,
            w0,
            jac=neg_grad,
            bounds=bounds,
            constraints=constraints,
            method="SLSQP",
            options={"maxiter": 200, "ftol": 1e-9},
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.warning("Kelly SLSQP failed: %s", e)
        return None

    # SLSQP can return a non-converged final iterate; that point is not a
    # solution to the QP and may even violate bounds before the clip
    # below.  Treat as failure so the caller falls back instead of
    # publishing nonsense weights as the live recommendation.
    if not res.success:
        logger.warning(
            "Kelly SLSQP did not converge: %s (returning None to trigger fallback)",
            getattr(res, "message", "unknown"),
        )
        return None

    w = np.clip(res.x, 0.0, max_weight)
    total = w.sum()
    if total > synth_budget:
        w = w * (synth_budget / total)
    w = np.clip(w, 0.0, max_weight)

    # User-facing portfolio_mu / risk_decomp.mu_annual show TOTAL annualized
    # returns (gross, what a user sees on a brokerage statement).  Sharpe
    # uses EXCESS returns over rf, the textbook definition.
    portfolio_mu_total = float(w @ mu_total)
    portfolio_mu_excess = float(w @ mu)
    portfolio_var = float(w @ cov_annual @ w)
    portfolio_vol = float(math.sqrt(max(portfolio_var, 0.0)))
    sharpe = portfolio_mu_excess / portfolio_vol if portfolio_vol > 1e-9 else 0.0
    weighted_vol = float(np.sum(w * sigma_annual))
    div_ratio = weighted_vol / portfolio_vol if portfolio_vol > 1e-9 else 1.0

    return KellyOptimizationResult(
        symbols=kept,
        weights=w,
        mu_annual=mu_total,  # display: gross return per asset
        sigma_annual=sigma_annual,
        cov_annual=cov_annual,
        corr_matrix=corr,
        expected_return=portfolio_mu_total,  # display: gross portfolio return
        expected_vol=portfolio_vol,
        expected_sharpe=sharpe,  # over excess returns
        diversification_ratio=div_ratio,
        converged=bool(res.success),
        risk_aversion=gamma,
    )


def _display_for(synth: str) -> str:
    """Resolve a synth code to its UI display symbol (sSPY -> SPY).

    Lazy import to avoid a cycle with strategy_signal_evaluator at module load.
    Falls back to the synth code if the lookup misses.
    """
    try:
        from archimedes.services.strategy_signal_evaluator import GLOBAL_ASSETS

        entry = GLOBAL_ASSETS.get(synth)
        if entry is not None:
            return entry[1]
    except Exception:
        logger.debug("optimizer symbol-cache lookup failed", exc_info=True)
    return synth


def kelly_risk_decomposition(result: KellyOptimizationResult) -> list[dict]:
    """Per-asset marginal contribution to portfolio variance.

    Euler decomposition: MCᵢ = wᵢ · (Σw)ᵢ / σ²ₚ.  Sums to 1 across assets.
    Lets the UI show "GLD contributes 22% of portfolio variance" — the
    standard risk-attribution view at any real shop.

    Returns the UI display symbol (e.g. "SPY") in ``symbol`` and keeps the
    internal synth code in ``synth`` so callers can correlate without
    showing the leading "s" to end users.
    """
    w = result.weights
    sigma_p_sq = float(w @ result.cov_annual @ w)
    if sigma_p_sq < 1e-12:
        return []
    contributions = w * (result.cov_annual @ w) / sigma_p_sq
    return [
        {
            "symbol": _display_for(result.symbols[i]),
            "synth": result.symbols[i],
            "weight": round(float(w[i]), 4),
            "mu_annual": round(float(result.mu_annual[i]), 4),
            "vol_annual": round(float(result.sigma_annual[i]), 4),
            "variance_contribution": round(float(contributions[i]), 4),
        }
        for i in range(len(result.symbols))
    ]


def correlation_pairs(result: KellyOptimizationResult, top_n: int = 8) -> list[dict]:
    """Top-N highest-magnitude correlation pairs for the picked assets.

    Uses UI display symbols (SPY, GLD) rather than synth codes (sSPY, sGOLD)
    so the rendered table lines up with the rest of the advisor view.
    """
    n = len(result.symbols)
    pairs: list[tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append((float(result.corr_matrix[i, j]), i, j))
    pairs.sort(key=lambda x: abs(x[0]), reverse=True)
    return [
        {
            "a": _display_for(result.symbols[i]),
            "b": _display_for(result.symbols[j]),
            "corr": round(rho, 3),
        }
        for rho, i, j in pairs[:top_n]
    ]

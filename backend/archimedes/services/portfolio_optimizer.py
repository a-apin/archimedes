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
    """Closed-form 1-year expected max drawdown for a GBM with drift.

    From Magdon-Ismail & Atiya (2004) "Maximum Drawdown".  For an
    arithmetic Brownian motion with annual drift μ and volatility σ
    over horizon T (here T=1), the expected max-DD has a tractable
    form in the dimensionless quantity α = μ·√T / σ.  For typical
    risk-asset Sharpes (α ∈ [0, 1.5]) we use the well-known
    approximation:

        E[max-DD over 1y] ≈ 0.63·σ - 0.30·μ        (μ ≥ 0, low Sharpe)

    This dramatically improves on the previous ``2·σ`` heuristic
    (which was the median 1y down-move for a zero-Sharpe asset, not a
    max drawdown).  Returns a POSITIVE decimal (e.g. 0.15 = 15% DD).

    Reference: Magdon-Ismail & Atiya (2004), Wilmott Magazine.
    """
    if sigma_ann <= 1e-9:
        return 0.0
    if mu_ann < 0:
        # Negative-drift assets: floor at the no-drift expected max-DD ≈ 0.79σ
        # (the η→0 limit in Magdon-Ismail's table).
        return float(0.79 * sigma_ann)
    est = 0.63 * sigma_ann - 0.30 * mu_ann
    return float(max(est, 0.05 * sigma_ann))  # never claim < 5% of σ


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
            "Insufficient return data for MVO (%s) — using equal weight",
            risk_profile.value,
        )
        return _equal_weight(symbols, synth_budget)

    mu = R.mean(axis=0)  # per-bar mean returns, shape (N,)
    Sigma = np.cov(R.T, ddof=1)  # covariance matrix, shape (N, N)
    if Sigma.ndim == 0:
        # Single-asset edge case: np.cov returns a scalar
        Sigma = np.array([[float(Sigma)]])
    Sigma += np.eye(n) * 1e-8  # numerical regularization

    if risk_profile == RiskProfile.CONSERVATIVE:
        raw = _gmv(Sigma, n, cap=_CAP_DEFAULT)
    elif risk_profile in (RiskProfile.MODERATE, RiskProfile.AGGRESSIVE):
        raw = _max_sharpe(mu, Sigma, n, cap=_CAP_DEFAULT)
    else:
        raw = _max_expected_return(mu, n, cap=_CAP_HYPER)

    if raw is None:
        logger.warning(
            "scipy optimization failed for %s — using equal weight",
            risk_profile.value,
        )
        return _equal_weight(symbols, synth_budget)

    scaled = {sym: round(float(w) * synth_budget, 6) for sym, w in zip(symbols, raw, strict=False)}
    logger.info(
        "MVO [%s]: %s",
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
    bounds = [(0.0, _CAP_DEFAULT)] * n

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
    """Max Sharpe: maximize (μ-rf)'w / sqrt(w'Σw)  s.t. 1'w=1, 0 ≤ w ≤ cap."""
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
    return {s: w for s in symbols}


# ─── Kelly mean-variance from price-history dict ──────────────────


def _shrink_cov(cov: np.ndarray, intensity: float = 0.10) -> np.ndarray:
    """Fixed-intensity diagonal shrinkage (NOT Ledoit-Wolf).

    True Ledoit-Wolf (LW 2003) solves analytically for the optimal
    intensity from the data; we use a fixed α=0.10 toward the diagonal
    target (preserve per-asset variances, zero off-diagonals).  Cheaper
    and deterministic; keeps the matrix positive-definite even on short
    windows; under-shrinks for short samples and over-shrinks for long.

    TODO: swap in `sklearn.covariance.LedoitWolf().fit(returns)` so
    intensity is data-derived; tracked separately so the trade-off is
    explicit instead of hidden in this function name.
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
    daily_cov = np.cov(returns.values, rowvar=False)
    mu_annual = daily_mean * _ANNUALIZATION
    cov_annual = _shrink_cov(daily_cov * _ANNUALIZATION, intensity=0.10)
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
) -> KellyOptimizationResult | None:
    """Solve the constrained Kelly mean-variance problem.

        maximize   wᵀ(μ - rf) - ½·γ·wᵀΣw
        subject to 0 ≤ wᵢ ≤ max_weight
                   Σ wᵢ ≤ synth_budget

    γ is mapped from ``risk_profile`` via RISK_AVERSION.  ``mu_override``
    lets the caller substitute Kelly-derived or backtest-stat expected
    returns for the sample mean (which is noisy on short windows).

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

    gamma = RISK_AVERSION.get(risk_profile, 3.0)
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
        pass
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

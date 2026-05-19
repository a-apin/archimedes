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

import numpy as np
from scipy.optimize import minimize

from archimedes.models.portfolio import RISK_PROFILE_PARAMS, RiskProfile

logger = logging.getLogger(__name__)

_ANNUALIZATION = 252
_RF_DAILY = 0.05 / _ANNUALIZATION  # 5% annual risk-free rate

# Per-asset caps — prevent degenerate corner solutions
_CAP_DEFAULT = 0.40   # Conservative / Moderate / Aggressive
_CAP_HYPER = 0.60     # Hyper-Risky: higher concentration is the intent

_MIN_BARS = 20        # Minimum history required before MVO is meaningful


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

    mu = R.mean(axis=0)        # per-bar mean returns, shape (N,)
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

    scaled = {sym: round(float(w) * synth_budget, 6) for sym, w in zip(symbols, raw)}
    logger.info(
        "MVO [%s]: %s",
        risk_profile.value,
        "  ".join(f"{s}={w:.1%}" for s, w in scaled.items()),
    )
    return scaled


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

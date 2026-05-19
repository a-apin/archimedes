"""Selection-bias corrections: DSR, PBO, and walk-forward OOS Sharpe.

Implements the four-primitive admission gate that separates Tier-1 (Archimedes
Verified) strategies from curve-fit noise:

  1. Deflated Sharpe Ratio — Bailey & López de Prado (2014)
  2. Probability of Backtest Overfitting via CSCV — Bailey et al. (2014)
  3. Walk-forward out-of-sample Sharpe
  4. Look-ahead audit (wired upstream in the backtest engine)

All functions are pure computation: no I/O, no web framework, no on-chain
dependencies. They consume daily return arrays and produce the fields that
BacktestResult.passes_rigor_gate checks.

Owner: Önder (math lane)
Spec:  docs/specs/selection-bias-corrections-spec.md
"""

from __future__ import annotations

import math
from itertools import combinations

import numpy as np
from scipy.stats import kurtosis as sp_kurtosis
from scipy.stats import norm, skew as sp_skew

_EULER_MASCHERONI = 0.5772156649
_ANNUALIZATION = 252


# ─── 1. Deflated Sharpe Ratio ────────────────────────────────────────


def compute_dsr(
    daily_returns: list[float],
    num_trials: int,
) -> tuple[float | None, float | None]:
    """Deflated Sharpe Ratio (Bailey & López de Prado 2014).

    Corrects the observed Sharpe for non-normality (skew + excess kurtosis)
    and multiple-testing inflation (selection across N candidate strategies).

    Args:
        daily_returns: Per-bar (daily) return series, un-annualized.
        num_trials: Number of strategies in the selection set (N). Pass 1
            if this is the only strategy ever evaluated — no correction
            will be applied. The orchestrator should pass
            len(strategy_library) so the correction is meaningful.

    Returns:
        (deflated_sharpe_ratio, dsr_p_value)
          - deflated_sharpe_ratio: annualized Sharpe excess over the
            expected best-of-N null. Positive means the strategy clears
            the multiple-testing bar.
          - dsr_p_value: P(true SR > 0 | observed SR, N trials, T bars).
            Gate threshold is 0.95 per passes_rigor_gate.
        Both are None if data is insufficient (T < 4) or degenerate.
    """
    arr = np.asarray(daily_returns, dtype=float)
    T = len(arr)
    if T < 4:
        return None, None

    # numpy std(ddof=1) can be a tiny non-zero float for identical values due
    # to floating-point cancellation; check range first to catch constant series.
    if float(np.ptp(arr)) == 0.0:
        return None, None

    sigma = float(arr.std(ddof=1))
    if sigma <= 0.0:
        return None, None

    SR_hat = float(arr.mean()) / sigma  # per-bar, un-annualized
    gamma_3 = float(sp_skew(arr))
    gamma_4 = float(sp_kurtosis(arr, fisher=True))  # excess kurtosis

    dsr, p_val = _dsr_from_stats(SR_hat, T, gamma_3, gamma_4, num_trials)
    return dsr, p_val


def _dsr_from_stats(
    SR_hat: float,
    T: int,
    gamma_3: float,
    gamma_4: float,
    N: int,
) -> tuple[float | None, float | None]:
    """Core DSR formula — exposed for direct unit-testing against spec cases.

    Args:
        SR_hat: Per-bar (un-annualized) Sharpe ratio.
        T: Number of bars in the return series.
        gamma_3: Skewness of the per-bar return series.
        gamma_4: Excess kurtosis (Fisher) of the per-bar return series.
        N: Number of trials in the selection set.

    Returns:
        (deflated_sharpe_annualized, dsr_p_value) or (None, None).
    """
    if T < 4:
        return None, None

    N = max(1, N)

    # E[max_N]: Bailey-LdP (2014) approximation for the expected maximum of N
    # iid standard-normal random variables (the expected best-of-N null SR).
    if N == 1:
        E_max_N = 0.0
    else:
        phi_inv_1 = float(norm.ppf(1.0 - 1.0 / N))
        phi_inv_2 = float(norm.ppf(1.0 - 1.0 / (N * math.e)))
        E_max_N = (1.0 - _EULER_MASCHERONI) * phi_inv_1 + _EULER_MASCHERONI * phi_inv_2

    # SR_zero: expected best-of-N under the null, scaled to per-bar variance
    # (under iid normal returns, per-bar SR has variance 1/(T-1))
    SR_zero = math.sqrt(1.0 / (T - 1)) * E_max_N

    # Variance-adjusted z-statistic (eq. 8 in Bailey-LdP 2014)
    denom_sq = 1.0 - gamma_3 * SR_hat + ((gamma_4 - 1.0) / 4.0) * SR_hat**2
    if denom_sq <= 0.0:
        return None, None

    z = (SR_hat - SR_zero) * math.sqrt(T - 1) / math.sqrt(denom_sq)
    dsr_p_value = float(norm.cdf(z))

    # Annualize the deflated SR for display (Sharpe units, not probability)
    deflated_sharpe_ratio = (SR_hat - SR_zero) * math.sqrt(_ANNUALIZATION)

    return round(deflated_sharpe_ratio, 6), round(dsr_p_value, 6)


# ─── 2. Probability of Backtest Overfitting (CSCV) ──────────────────


def compute_pbo(
    returns_matrix: dict[str, list[float]],
    s_partitions: int = 16,
) -> dict[str, float]:
    """Probability of Backtest Overfitting via CSCV (Bailey et al. 2014).

    Computes the fraction of combinatorial IS/OOS splits in which the
    in-sample-optimal strategy underperforms the OOS median. PBO is a
    library-level metric: a single value is computed across all N strategies
    and attached identically to each strategy's BacktestResult from this run.

    Args:
        returns_matrix: {strategy_id: [daily_returns]} — all strategies must
            cover the same date range. Series are truncated to the shortest
            length to align them.
        s_partitions: Number of equal time-partitions S. Must be even ≥ 2.
            Default 16 is the paper's recommended value.

    Returns:
        {strategy_id: pbo_score} — lower is better. PBO ≥ 0.5 means the
        library is expected to overfit (IS-best underperforms OOS median).
        Returns 0.0 for every strategy if N < 2 or data is insufficient.
    """
    if len(returns_matrix) < 2:
        return {sid: 0.0 for sid in returns_matrix}

    sorted_ids = sorted(returns_matrix.keys())
    N = len(sorted_ids)

    T = min(len(v) for v in returns_matrix.values())

    # Build (T, N) matrix, columns in sorted_ids order
    R = np.array(
        [returns_matrix[sid][:T] for sid in sorted_ids], dtype=float
    ).T  # shape (T, N)

    S = s_partitions if (s_partitions % 2 == 0 and s_partitions >= 2) else 16
    rows_per_block = T // S
    if rows_per_block < 1:
        return {sid: 0.0 for sid in sorted_ids}

    blocks = [R[i * rows_per_block : (i + 1) * rows_per_block, :] for i in range(S)]
    half = S // 2
    lambdas: list[float] = []

    for is_indices in combinations(range(S), half):
        oos_indices = [i for i in range(S) if i not in is_indices]

        IS = np.vstack([blocks[i] for i in is_indices])
        OOS = np.vstack([blocks[i] for i in oos_indices])

        is_sharpes = _sharpe_per_col(IS)
        oos_sharpes = _sharpe_per_col(OOS)

        best_is_idx = int(np.argmax(is_sharpes))

        # Ascending rank (1 = worst, N = best) of IS-best strategy in OOS
        oos_ranks = _ascending_ranks(oos_sharpes)
        rank_oos = float(oos_ranks[best_is_idx])

        omega = rank_oos / N
        omega = float(np.clip(omega, 1e-9, 1.0 - 1e-9))
        lam = math.log(omega / (1.0 - omega))
        lambdas.append(lam)

    if not lambdas:
        return {sid: 0.0 for sid in sorted_ids}

    pbo = round(sum(1 for lam in lambdas if lam <= 0.0) / len(lambdas), 6)
    return {sid: pbo for sid in sorted_ids}


# ─── 3. Walk-forward OOS Sharpe ──────────────────────────────────────


def compute_oos_sharpe(
    daily_returns: list[float],
    train_fraction: float = 0.70,
) -> float | None:
    """Annualized Sharpe on the held-out OOS slice (no shuffling).

    Splits the return series chronologically: the first train_fraction of
    bars are in-sample; the remainder are the OOS test set. The OOS Sharpe
    must be ≥ 50% of the full-sample Sharpe to pass passes_rigor_gate.

    Args:
        daily_returns: Full per-bar return series.
        train_fraction: Fraction reserved for in-sample training (0 < f < 1).

    Returns:
        Annualized OOS Sharpe, or None if insufficient OOS data (< 5 bars).
    """
    arr = np.asarray(daily_returns, dtype=float)
    T = len(arr)
    if T < 10:
        return None

    split = int(T * train_fraction)
    oos = arr[split:]

    if len(oos) < 5:
        return None

    if float(np.ptp(oos)) == 0.0:
        return None

    sigma = float(oos.std(ddof=1))
    if sigma <= 0.0:
        return None

    return round(float((oos.mean() / sigma) * math.sqrt(_ANNUALIZATION)), 6)


# ─── 4. Kelly Criterion position sizing ─────────────────────────────


def compute_kelly_fraction(
    daily_returns: list[float],
    rf_annual: float = 0.05,
    fractional: float = 0.5,
) -> float | None:
    """Fractional Kelly position size for a single-asset strategy.

    Implements the continuous-time Kelly formula:

        f* = (μ_ann - rf_ann) / σ_ann²

    where μ_ann and σ_ann² are the annualized mean return and variance.
    A fractional Kelly (default 0.5×) is applied to reduce drawdown
    volatility — the theoretical full-Kelly bet is too aggressive for
    practical use and is highly sensitive to estimation error.

    Args:
        daily_returns: Per-bar (daily) return series, un-annualized.
        rf_annual: Annual risk-free rate (default 5%).
        fractional: Kelly multiplier — 0.5 = half-Kelly (recommended),
            1.0 = full Kelly (academic reference only).

    Returns:
        Fractional Kelly weight ∈ (0, 1], clipped to [0, 1] to prevent
        leverage beyond 100%. Returns None if data is insufficient or
        degenerate (< 4 bars, zero variance, negative excess return).
    """
    arr = np.asarray(daily_returns, dtype=float)
    T = len(arr)
    if T < 4:
        return None

    if float(np.ptp(arr)) == 0.0:
        return None

    sigma_daily = float(arr.std(ddof=1))
    if sigma_daily <= 0.0:
        return None

    mu_ann = float(arr.mean()) * _ANNUALIZATION
    sigma_sq_ann = (sigma_daily**2) * _ANNUALIZATION

    excess_return = mu_ann - rf_annual
    if excess_return <= 0.0:
        return 0.0

    f_full = excess_return / sigma_sq_ann
    f_fractional = fractional * f_full

    return round(float(np.clip(f_fractional, 0.0, 1.0)), 6)


# ─── Private helpers ─────────────────────────────────────────────────


def _sharpe_per_col(R: np.ndarray) -> np.ndarray:
    """Annualized Sharpe for each column (strategy) of a return matrix."""
    if R.shape[0] < 2:
        return np.zeros(R.shape[1])
    mu = R.mean(axis=0)
    sigma = R.std(axis=0, ddof=1)
    safe_sigma = np.where(sigma > 0, sigma, np.inf)
    return (mu / safe_sigma) * math.sqrt(_ANNUALIZATION)


def _ascending_ranks(values: np.ndarray) -> np.ndarray:
    """1-based ranks, ascending order (rank N = highest value)."""
    n = len(values)
    order = np.argsort(values)
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(1, n + 1, dtype=float)
    return ranks

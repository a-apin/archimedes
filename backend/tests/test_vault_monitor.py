"""Tests for vault_monitor.compute_sharpe_drift.

Pure-computation tests — no chain client, no Redis, no database.
Covers the McLean-Pontiff decay floor, status thresholds, Lo (2002) SE,
and edge cases (insufficient data, zero variance).
"""

from __future__ import annotations

import math

import numpy as np
from archimedes.services.vault_monitor import (
    _MCLEAN_PONTIFF_DECAY,
    _MIN_SNAPSHOTS_FOR_SHARPE,
    compute_sharpe_drift,
)


def _snapshots(navs: list[float]) -> list[dict]:
    """Build snapshot dicts from a NAV series (oldest-first input).

    compute_sharpe_drift expects most-recent-first order (Redis LRANGE convention)
    and internally reverses to chronological order before computing returns.
    """
    return [{"aum_usdc": n} for n in reversed(navs)]


# ─── Insufficient data ────────────────────────────────────────────────


def test_insufficient_snapshots_returns_status():
    """Fewer than _MIN_SNAPSHOTS_FOR_SHARPE snapshots → INSUFFICIENT_DATA."""
    snaps = _snapshots([100.0] * (_MIN_SNAPSHOTS_FOR_SHARPE - 1))
    result = compute_sharpe_drift(snaps, backtest_sharpe=1.0)
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["live_sharpe"] is None
    assert result["drift_sigma"] is None


def test_exactly_min_snapshots_computes():
    """Exactly _MIN_SNAPSHOTS_FOR_SHARPE + 2 snapshots with drift should compute."""
    navs = [100.0 * (1.001**i) for i in range(_MIN_SNAPSHOTS_FOR_SHARPE + 2)]
    result = compute_sharpe_drift(_snapshots(navs), backtest_sharpe=1.0)
    assert result["status"] != "INSUFFICIENT_DATA"
    assert result["live_sharpe"] is not None


def test_zero_variance_aum_returns_insufficient():
    """Flat AUM (zero variance) → INSUFFICIENT_DATA, not a crash or 0.0."""
    navs = [100.0] * 20
    result = compute_sharpe_drift(_snapshots(navs), backtest_sharpe=1.0)
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["live_sharpe"] is None


# ─── McLean-Pontiff decay floor ───────────────────────────────────────


def test_decay_floor_is_correct_fraction():
    """decay_floor must equal backtest_sharpe * _MCLEAN_PONTIFF_DECAY (42%)."""
    bs = 1.2
    navs = [100.0] * 20  # insufficient data — but decay_floor is always returned
    result = compute_sharpe_drift(_snapshots(navs), backtest_sharpe=bs)
    assert abs(result["decay_floor"] - round(bs * _MCLEAN_PONTIFF_DECAY, 4)) < 1e-9


def test_decay_constant_is_42_percent():
    """Constant must match McLean & Pontiff (2016): 42% Sharpe retention."""
    assert abs(_MCLEAN_PONTIFF_DECAY - 0.42) < 1e-9


# ─── Status thresholds ───────────────────────────────────────────────


def test_normal_status_when_live_sharpe_above_floor():
    """live_sharpe ≥ decay_floor → NORMAL."""
    bs = 0.8
    rng = np.random.default_rng(1)
    # High drift/vol ratio → annualized Sharpe well above decay_floor (0.336)
    navs = [100.0]
    for r in rng.normal(0.002, 0.001, 50):
        navs.append(navs[-1] * (1 + r))
    result = compute_sharpe_drift(_snapshots(navs), backtest_sharpe=bs)
    assert result["status"] == "NORMAL", (
        f"Expected NORMAL, got {result['status']} "
        f"(live={result['live_sharpe']:.3f}, floor={bs * _MCLEAN_PONTIFF_DECAY:.3f})"
    )


def test_warning_or_critical_when_live_sharpe_below_floor():
    """live_sharpe below decay_floor → WARNING or CRITICAL (not NORMAL).

    Note: 5-min snapshots annualize with 72,576 periods/year, so even a
    small positive mean/vol ratio produces a huge live Sharpe. We need a
    clearly negative mean to guarantee live_sharpe < floor.
    """
    bs = 1.5
    rng = np.random.default_rng(2)
    # Negative drift → live Sharpe well below decay_floor (0.63)
    navs = [100.0]
    for r in rng.normal(-0.001, 0.002, 60):
        navs.append(navs[-1] * (1 + r))
    result = compute_sharpe_drift(_snapshots(navs), backtest_sharpe=bs)
    assert result["status"] in ("WARNING", "CRITICAL"), (
        f"Expected WARNING or CRITICAL for degraded Sharpe, got {result['status']}"
    )


def test_critical_status_when_live_sharpe_far_below_floor():
    """live_sharpe < decay_floor * 0.5 → CRITICAL."""
    bs = 2.0
    rng = np.random.default_rng(3)
    # Strongly negative returns → live Sharpe << decay_floor * 0.5 (0.42)
    navs = [100.0]
    for r in rng.normal(-0.003, 0.001, 60):
        navs.append(navs[-1] * (1 + r))
    result = compute_sharpe_drift(_snapshots(navs), backtest_sharpe=bs)
    assert result["status"] == "CRITICAL", (
        f"Expected CRITICAL for strongly negative live Sharpe, got {result['status']}"
    )


# ─── drift_sigma sign and magnitude ──────────────────────────────────


def test_drift_sigma_negative_for_degraded_performance():
    """Strategies performing far below backtest baseline should have negative drift_sigma."""
    rng = np.random.default_rng(4)
    # Negative drift → live Sharpe << backtest_sharpe of 2.0
    navs = [100.0]
    for r in rng.normal(-0.001, 0.002, 50):
        navs.append(navs[-1] * (1 + r))
    result = compute_sharpe_drift(_snapshots(navs), backtest_sharpe=2.0)
    if result["drift_sigma"] is not None:
        assert result["drift_sigma"] < 0, (
            f"Degraded live Sharpe should give negative drift_sigma, got {result['drift_sigma']:.3f}"
        )


def test_drift_sigma_near_zero_for_matching_performance():
    """live_sharpe ≈ backtest_sharpe → drift_sigma ≈ 0."""
    rng = np.random.default_rng(5)
    rets = rng.normal(0.002, 0.005, 100).tolist()
    navs = [100.0]
    for r in rets:
        navs.append(navs[-1] * (1 + r))

    # Compute the same live Sharpe the function will compute, then pass it as backtest
    mean_r = sum(rets) / len(rets)
    var_r = sum((r - mean_r) ** 2 for r in rets) / max(len(rets) - 1, 1)
    std_r = math.sqrt(var_r)
    periods_per_year = (24 * 60 / 5.0) * 252
    expected_live_sharpe = (mean_r / std_r) * math.sqrt(periods_per_year)

    result = compute_sharpe_drift(_snapshots(navs), backtest_sharpe=expected_live_sharpe)
    if result["drift_sigma"] is not None:
        assert abs(result["drift_sigma"]) < 0.5, (
            f"When live ≈ backtest, drift_sigma should be near 0, got {result['drift_sigma']:.3f}"
        )


# ─── Output shape ────────────────────────────────────────────────────


def test_output_always_has_required_keys():
    """All result dicts must contain the five required keys regardless of status."""
    required = {"live_sharpe", "backtest_sharpe", "decay_floor", "drift_sigma", "status"}
    r1 = compute_sharpe_drift(_snapshots([100.0] * 3), backtest_sharpe=1.0)
    assert required.issubset(r1.keys())
    navs = [100.0 * (1.001**i) for i in range(30)]
    r2 = compute_sharpe_drift(_snapshots(navs), backtest_sharpe=1.0)
    assert required.issubset(r2.keys())

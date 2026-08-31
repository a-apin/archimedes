"""Parity test: backend rigor_evaluator.compute_dsr ≡ analytics-engine compute_dsr.

The fixture/store generator carries its own pure-numpy mirror of the backend DSR
(``analytics-engine/scripts/regen_buy_hold_fixture.py::compute_dsr``) so the
fixture scripts can run without scipy / backend deps. Before #547 the two
implementations used *different Sharpe conventions* — the backend subtracted the
risk-free rate (excess Sharpe), the fixture side did not (raw Sharpe) — so the
same strategy could pass one path and fail the other. #547 canonicalised both on
the excess convention; this test is the guard that keeps them aligned, mirroring
``test_pbo_parity.py``.

Why a tolerance instead of exact ``==`` (as PBO uses): the two paths use
different normal-distribution implementations (backend = scipy; fixture =
Acklam/Zelen-Severo pure-numpy approximations, error < 1.2e-7). Both ``round(…,
6)`` their outputs, so in practice they agree exactly at 6 decimals; the ``1e-6``
tolerance is exactly that rounding granularity and absorbs the rare knife-edge
case without going flaky. A *convention* regression (reverting the fixture side
to raw Sharpe) shifts the p-value by 1e-2 … 7e-1 — thousands of units of this
tolerance — so it is caught decisively.

Hermetic: the fixture module's heavy data/engine imports are deferred into
main(), so importing it pulls only numpy. No scipy needed on the fixture side, no
backtrader / yfinance, no network, no DB.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from archimedes.services.rigor_evaluator import compute_dsr as backend_compute_dsr

_SCRIPTS = Path(__file__).resolve().parents[2] / "analytics-engine" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from regen_buy_hold_fixture import compute_dsr as fixture_compute_dsr  # noqa: E402

# Rounding granularity of both implementations (both round to 6 decimals).
_TOL = 1e-6


def _series(mu: float, sigma: float, t: int, seed: int) -> list[float]:
    return np.random.default_rng(seed).normal(mu, sigma, size=t).tolist()


@pytest.mark.parametrize(
    ("mu", "sigma", "t", "num_trials", "seed"),
    [
        (0.0004, 0.011, 400, 1, 1),  # N=1 → no multiple-testing correction
        (0.0004, 0.011, 1600, 5, 2),
        (0.0003, 0.010, 1000, 13, 3),
        (0.0005, 0.012, 2520, 22, 4),  # full-library N
        (0.0002, 0.009, 800, 50, 5),
        (-0.0003, 0.010, 1200, 22, 6),  # negative-mean strategy
    ],
)
def test_parity_matches_backend(mu: float, sigma: float, t: int, num_trials: int, seed: int) -> None:
    """Both compute_dsr paths agree on (deflated_sharpe, p_value) at rounding granularity.

    The backend default average_correlation=0.0 matches the fixture side, which
    has no correlation term — at ρ=0 the backend's effective-trials taper reduces
    to the same E[max_N] the fixture side computes.
    """
    returns = _series(mu, sigma, t, seed)
    f_dsr, f_p = fixture_compute_dsr(returns, num_trials)
    b_dsr, b_p = backend_compute_dsr(returns, num_trials, average_correlation=0.0)

    assert f_dsr is not None and b_dsr is not None
    assert abs(f_dsr - b_dsr) <= _TOL, f"deflated_sharpe drift: fixture={f_dsr} backend={b_dsr}"
    assert abs(f_p - b_p) <= _TOL, f"dsr_p_value drift: fixture={f_p} backend={b_p}"


def test_degenerate_inputs_match() -> None:
    """Both return (None, None) on too-short or constant series — same guard."""
    assert fixture_compute_dsr([0.01, 0.02, 0.03], 1) == backend_compute_dsr([0.01, 0.02, 0.03], 1) == (None, None)
    flat = [0.01] * 200
    assert fixture_compute_dsr(flat, 5) == backend_compute_dsr(flat, 5, average_correlation=0.0) == (None, None)


def test_convention_is_excess_not_raw() -> None:
    """Regression guard for #547: the fixture path must subtract the risk-free rate.

    On a high-positive-mean series, raw Sharpe (mean/σ) and excess Sharpe
    ((mean−rf)/σ) differ materially. We assert the fixture path agrees with the
    backend (excess) and that a hand-computed *raw* p-value is far away — so if
    anyone reverts the fixture formula to raw Sharpe, the first assertion (and the
    parametrised parity above) fail loudly.
    """
    returns = _series(0.0015, 0.008, 1500, 7)  # high mean → rf subtraction is visible
    _f_dsr, f_p = fixture_compute_dsr(returns, 1)
    _b_dsr, b_p = backend_compute_dsr(returns, 1, average_correlation=0.0)

    # Fixture path tracks the backend's excess convention.
    assert abs(f_p - b_p) <= _TOL

    # A raw-Sharpe recomputation of the same statistic lands measurably elsewhere,
    # proving the rf term is actually doing work (not a no-op at this rf level).
    arr = np.asarray(returns)
    sigma = float(arr.std(ddof=1))
    raw_sr = float(arr.mean()) / sigma
    excess_sr = (float(arr.mean()) - 0.05 / 252) / sigma
    assert abs(raw_sr - excess_sr) > 1e-3, "test series too flat to distinguish raw vs excess"


def test_parity_unaffected_by_1409s_optional_dates_parameter() -> None:
    """#1409 adds an optional `dates` kwarg to the backend's `compute_dsr` for
    the historical T-bill rf series. The fixture side (analytics-engine) does
    NOT gain this parameter — it keeps grading on the flat convention, by
    design (only the live backend gate consumes the vendored series). This
    guards that omitting `dates` (every call in this parity file) still
    produces the EXACT flat-rate numbers the fixture side parity-checks
    against — i.e. adding the parameter did not silently change the default
    path either implementation exercises here."""
    returns = _series(0.0004, 0.011, 400, 1)
    f_dsr, f_p = fixture_compute_dsr(returns, 3)
    b_dsr, b_p = backend_compute_dsr(returns, 3, average_correlation=0.0)
    b_dsr_explicit_none, b_p_explicit_none = backend_compute_dsr(returns, 3, average_correlation=0.0, dates=None)

    assert b_dsr == b_dsr_explicit_none
    assert b_p == b_p_explicit_none
    assert abs(f_dsr - b_dsr) <= _TOL
    assert abs(f_p - b_p) <= _TOL

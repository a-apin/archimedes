"""``POST /api/rigor/verify`` — the CLI's ``verify`` command backend (#1305).

Runs the rigor gate's admission checks over a BARE returns series (no
strategy code, no trial matrix) submitted directly by a caller — the CLI's
``archimedes verify RETURNS_CSV``. Reuses the exact same computational
functions the strategy-passport verdict uses
(``archimedes.services.rigor_evaluator`` / ``_rigor_helpers`` via it, and the
SAME threshold constants from ``rigor_profiles``) — no reimplementation, no
new thresholds, per the issue spec.

**Honesty contract (load-bearing).** The gate is four checks. A single
returns series can only ever support two of them:

  * **DSR** — evaluable. Deflated by the *declared* (self-attested) ``trials``
    count via ``compute_dsr_hac_and_iid`` (the identical function
    ``run_rigor_gate`` calls), gated against ``DSR_P_FLOOR`` — the always-on,
    strictness-independent correctness floor (``rigor_profiles.DSR_P_FLOOR``),
    not a strictness-adjustable per-level threshold, since this endpoint takes
    no strictness parameter.
  * **walk-forward OOS consistency** — evaluable. ``compute_oos_sharpe``
    (single chronological 70/30 holdout, the same function the gate uses)
    gated against ``OOS_ABS_FLOOR`` — the identical always-on floor
    ``RigorGateResult.blocked_by_floor`` enforces.
  * **PBO** — ALWAYS ``not_evaluable``. Probability of backtest overfitting
    (Bailey et al. 2014 CSCV) is a property of a *selection set* — it needs a
    trial matrix of multiple candidate strategies' returns over the same
    window. A bare series has no selection set to measure overfitting
    probability against, so this is reported honestly rather than silently
    passed, defaulted, or graded as a FAIL it was never evaluated for.
  * **look-ahead audit** — ALWAYS ``not_evaluable``. The audit is AST-based
    static analysis of strategy source code; a returns series carries no code.
    Archimedes never executes or uploads strategy code server-side (the
    README's hard boundary) — this endpoint accepts only numbers, on purpose.

``passes`` is ``True`` iff no EVALUABLE check failed AND at least one check
was evaluable — a request where every check is ``not_evaluable`` (e.g. a
too-short series) must not report ``passes=True`` by vacuous truth.

Account-session-gated (Better Auth) + rate-limited ``5/minute`` per the issue
spec, mirroring ``paper_routes.py`` / ``selection_bias_routes.py`` style.
"""

from __future__ import annotations

import math
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from archimedes.api.account_auth import CurrentUser, require_current_user
from archimedes.api.limiter import limiter
from archimedes.services.rigor_evaluator import (
    compute_dsr_hac_and_iid,
    compute_in_sample_sharpe,
    compute_oos_sharpe,
)
from archimedes.services.rigor_profiles import DSR_P_FLOOR, OOS_ABS_FLOOR

rigor_verify_router = APIRouter(prefix="/api/rigor", tags=["rigor"])

CheckStatus = Literal["pass", "fail", "not_evaluable"]

_PBO_NOT_EVALUABLE_REASON = (
    "PBO (probability of backtest overfitting, Bailey et al. 2014 CSCV) is a property "
    "of a SELECTION SET, not one series — it requires a trial matrix of multiple "
    "candidate strategies' returns over the same window. A single returns series has "
    "no selection set to measure overfitting probability against. "
    "See POST /api/selection-bias/pbo for the trial-matrix form."
)
_LOOK_AHEAD_NOT_EVALUABLE_REASON = (
    "The look-ahead audit is AST-based static analysis of strategy SOURCE CODE; a "
    "bare returns series carries no code to inspect. Archimedes never executes or "
    "uploads strategy code server-side, so this check can only ever run locally "
    "(see `archimedes verify --local`)."
)


# ── Schemas ──────────────────────────────────────────────────


class ReturnPoint(BaseModel):
    date: str
    daily_return: float


class RigorVerifyRequest(BaseModel):
    returns: list[ReturnPoint] = Field(min_length=1)
    trials: int = Field(default=1, ge=1, description="Self-attested trial count for the DSR deflation.")


class RigorCheck(BaseModel):
    status: CheckStatus
    reason: str | None = None


class DsrCheckResult(RigorCheck):
    deflated_sharpe: float | None = None
    dsr_p_value: float | None = None


class OosConsistencyResult(RigorCheck):
    oos_sharpe: float | None = None
    in_sample_sharpe: float | None = None


class RigorVerifyResponse(BaseModel):
    passes: bool
    trials: int
    self_attested: bool = True
    n_bars: int
    dsr: DsrCheckResult
    pbo: RigorCheck
    oos_consistency: OosConsistencyResult
    look_ahead: RigorCheck


# ── Check evaluators (thin wrappers over the SAME gate functions) ──────


def _evaluate_dsr(daily_returns: list[float], trials: int) -> DsrCheckResult:
    """DSR, deflated by the declared (self-attested) trial count.

    Calls the identical ``compute_dsr_hac_and_iid`` the passport gate calls
    (Newey-West HAC standard error, ``average_correlation=0.0`` — a bare
    series carries no cohort/variant-pool correlation context to supply, the
    same conservative default ``run_rigor_gate`` falls back to). Gated against
    ``DSR_P_FLOOR`` — the always-on floor, not a strictness-level threshold,
    since this endpoint takes no strictness parameter.
    """
    deflated_sharpe, p_value, _dsr_iid, _p_iid = compute_dsr_hac_and_iid(
        daily_returns, trials, average_correlation=0.0, hac_lags="auto"
    )
    if deflated_sharpe is None or p_value is None or not math.isfinite(deflated_sharpe) or not math.isfinite(p_value):
        return DsrCheckResult(
            status="not_evaluable",
            reason="return series too short or degenerate for DSR (need >= 4 bars with nonzero variance)",
        )
    passed = p_value >= DSR_P_FLOOR
    reason = (
        f"self-attested trials={trials}: DSR p-value {p_value:.4f} "
        f"{'>=' if passed else '<'} floor {DSR_P_FLOOR:.2f} (Newey-West HAC standard error)"
    )
    return DsrCheckResult(
        status="pass" if passed else "fail",
        reason=reason,
        deflated_sharpe=deflated_sharpe,
        dsr_p_value=p_value,
    )


def _evaluate_oos_consistency(daily_returns: list[float]) -> OosConsistencyResult:
    """Walk-forward OOS consistency — the same chronological 70/30 holdout the
    passport gate uses (``compute_oos_sharpe``), gated against
    ``OOS_ABS_FLOOR`` — the identical always-on floor
    ``RigorGateResult.blocked_by_floor`` enforces (a strategy that loses money
    out-of-sample is broken, not merely riskier)."""
    oos_sharpe = compute_oos_sharpe(daily_returns)
    if oos_sharpe is None or not math.isfinite(oos_sharpe):
        return OosConsistencyResult(
            status="not_evaluable",
            reason=(
                "insufficient data for a walk-forward OOS split "
                "(need >= 10 bars total and >= 21 OOS bars, or a degenerate slice)"
            ),
        )
    in_sample_sharpe = compute_in_sample_sharpe(daily_returns)
    passed = oos_sharpe > OOS_ABS_FLOOR
    if passed:
        reason = f"walk-forward OOS Sharpe {oos_sharpe:.4f} > floor {OOS_ABS_FLOOR:.2f} (chronological 70/30 holdout)"
    else:
        reason = (
            f"walk-forward OOS Sharpe {oos_sharpe:.4f} <= floor {OOS_ABS_FLOOR:.2f} "
            "— strategy loses money out-of-sample"
        )
    return OosConsistencyResult(
        status="pass" if passed else "fail",
        reason=reason,
        oos_sharpe=oos_sharpe,
        in_sample_sharpe=in_sample_sharpe,
    )


# ── Endpoint ─────────────────────────────────────────────────


@rigor_verify_router.post("/verify", response_model=RigorVerifyResponse)
@limiter.limit("5/minute")
async def verify_rigor(
    request: Request,  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    response: Response,  # noqa: ARG001 — slowapi injects rate-limit headers into it; omitting it 500s every SUCCESSFUL call (#1182)
    body: RigorVerifyRequest,
    user: CurrentUser = Depends(require_current_user),  # noqa: ARG001 — auth gate only; verdict is per-request
):
    daily_returns = [pt.daily_return for pt in body.returns]

    dsr_check = _evaluate_dsr(daily_returns, body.trials)
    oos_check = _evaluate_oos_consistency(daily_returns)
    pbo_check = RigorCheck(status="not_evaluable", reason=_PBO_NOT_EVALUABLE_REASON)
    look_ahead_check = RigorCheck(status="not_evaluable", reason=_LOOK_AHEAD_NOT_EVALUABLE_REASON)

    statuses = [dsr_check.status, pbo_check.status, oos_check.status, look_ahead_check.status]
    evaluable = [s for s in statuses if s != "not_evaluable"]
    passes = bool(evaluable) and all(s == "pass" for s in evaluable)

    return RigorVerifyResponse(
        passes=passes,
        trials=body.trials,
        self_attested=True,
        n_bars=len(daily_returns),
        dsr=dsr_check,
        pbo=pbo_check,
        oos_consistency=oos_check,
        look_ahead=look_ahead_check,
    )

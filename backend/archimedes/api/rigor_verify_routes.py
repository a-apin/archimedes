"""``POST /api/rigor/verify`` — the CLI's ``verify`` command backend (#1305).

Runs the rigor gate's admission checks over a BARE returns series (no
strategy code, no trial matrix) submitted directly by a caller — the CLI's
``archimedes verify RETURNS_CSV``. Reuses the exact same computational
functions the strategy-passport verdict uses
(``archimedes.services.rigor_evaluator`` / ``_rigor_helpers`` via it, and the
SAME threshold constants from ``rigor_profiles``) — no reimplementation, no
new thresholds, per the issue spec.

**rf convention (#1409 review fix, 2026-08-21).** ``ReturnPoint.date`` was
threaded to the request schema from the start, but the DSR/OOS/in-sample
Sharpe calls below discarded it and always graded on the flat 5% fallback
with no `rf_convention` disclosure at all — the exact "silent partial
substitution" issue #1409 design item 4 forbids, and on a route that (unlike
every ``run_rigor_gate`` call site) already HAS real per-bar dates on every
request, no schema change needed. Fixed the same way ``run_rigor_gate``
resolves its ONE convention up front (``_resolve_gate_rf``): the per-bar
dates are resolved ONCE here too, and the resolved (never the raw) dates are
threaded into every downstream call, so the disclosed ``rf_convention`` on
the response can never disagree with the arithmetic that produced it.

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

**The verdict is CAPPED, and ``passes`` is a quorum, not a majority (#1481).**
Two of the four legs above are structurally ``not_evaluable`` on a bare
series — always, for every request. So this endpoint can never reproduce the
full passport gate, and a caller must not read ``passes`` as "cleared the
rigor gate."

``passes`` is ``True`` iff **every RUNNABLE leg actually ran and passed** —
that is, both DSR and OOS consistency. Computing it over "whichever legs
happened to be evaluable" is the #1481 defect: OOS needs ~70 bars while DSR
needs only 4, so a 4-bar series had exactly one evaluable leg and returned
``passes=True`` on one leg of four. The zero-evaluable case was already
guarded ("vacuous truth is not honesty"); the one-evaluable case was not.

``legs_evaluated`` / ``legs_runnable`` / ``legs_total`` and
``verdict_capped`` are on the response so ``passes`` is qualifiable by a
consumer without re-deriving the leg statuses itself. ``legs_total`` is the
real gate's four; ``legs_runnable`` is the two this transport can support.

Account-session-gated (Better Auth) + rate-limited ``5/minute`` per the issue
spec, mirroring ``paper_routes.py`` / ``selection_bias_routes.py`` style.
"""

from __future__ import annotations

import math
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator

from archimedes.api.account_auth import CurrentUser, require_current_user
from archimedes.api.limiter import limiter
from archimedes.services.rigor_evaluator import (
    _resolve_gate_rf,  # the SAME once-up-front resolution run_rigor_gate uses (#1409 review fix)
    compute_dsr_hac_and_iid,
    compute_in_sample_sharpe,
    compute_oos_sharpe,
)
from archimedes.services.rigor_profiles import DSR_P_FLOOR, OOS_ABS_FLOOR

rigor_verify_router = APIRouter(prefix="/api/rigor", tags=["rigor"])

# Legs that can ever be evaluated against a bare return series. PBO needs a
# selection set and the look-ahead audit needs inspectable source; neither
# exists on this transport, so both are structurally not_evaluable on every
# request (see the module docstring's honesty contract). `passes` is a quorum
# over RUNNABLE legs — never over "whichever legs happened to be evaluable",
# which is what let a 4-bar series pass on one leg of four (#1481).
_RUNNABLE_LEGS: tuple[str, ...] = ("dsr", "oos_consistency")
_STRUCTURALLY_NOT_RUN_LEGS: tuple[str, ...] = ("pbo", "look_ahead")
_LEGS_TOTAL = len(_RUNNABLE_LEGS) + len(_STRUCTURALLY_NOT_RUN_LEGS)

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


# #1749: the ceiling on a verify payload belongs to the APPLICATION, at a number
# we chose, not to whatever byte count the edge happens to enforce.
#
# Until this cap existed, `returns` had `min_length=1` and no upper bound, so the
# only ceiling anywhere in the path was AWS WAF's `SizeRestrictions_BODY` —
# 8,192 bytes on a REGIONAL web ACL fronting an ALB. Measured (dogfood
# 2026-09-01, agent-cli): 160 rows = 8,076 B -> 200; 165 rows = 8,326 B -> 403.
# That is 50.5 bytes per serialised row including the envelope, so the limit is
# crossed at ~163 rows and one trading year (252 bars, ~12.7 KB extrapolated)
# 403'd at the edge with an `awselb/2.0` HTML body the CLI could only report as
# a generic http_error. See infra/waf.tf for the scoped edge fix.
#
# 2,600 rows is a decade of daily bars (10 x 252 = 2,520, plus headroom for
# leap-year/exchange-calendar variation). A full-cap payload measures ~122 KB of
# JSON with 6-decimal returns and extrapolates to ~131 KB at the 50.5 B/row seen
# in production — comfortably inside FastAPI/uvicorn's defaults, and small
# enough that the DSR/OOS math stays sub-second. A decade is the honest outer
# edge of what a daily-bar rigor verdict can claim anyway; beyond it the caller
# wants a different endpoint, not a bigger POST.
#
# Fail-closed: over the cap is a 422 with a message that names the limit, the
# count received and the reason — never a truncation, never a silent accept.
_MAX_RETURN_ROWS = 2600


class RigorVerifyRequest(BaseModel):
    # `max_length` is the declarative contract (it lands in the OpenAPI schema
    # and is the backstop if the validator below is ever removed); the
    # mode="before" validator runs first and is what produces the explicit
    # message, because pydantic's own max_length error ("List should have at
    # most 2600 items after validation") does not tell the caller what to do.
    returns: list[ReturnPoint] = Field(min_length=1, max_length=_MAX_RETURN_ROWS)
    trials: int = Field(default=1, ge=1, description="Self-attested trial count for the DSR deflation.")

    @field_validator("returns", mode="before")
    @classmethod
    def _cap_returns(cls, value: object) -> object:
        """Reject over-long series with a message that names the limit (#1749)."""
        if isinstance(value, list) and len(value) > _MAX_RETURN_ROWS:
            raise ValueError(
                f"returns has {len(value)} rows; the maximum is {_MAX_RETURN_ROWS} "
                f"(~10 years of daily bars). Split the series or aggregate to a "
                f"coarser frequency — a rigor verdict over a longer daily window "
                f"is not something this endpoint can honestly compute."
            )
        return value


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
    # #1481: `passes` alone overstated the evidence. These make the scalar
    # qualifiable without the caller re-deriving leg statuses.
    legs_evaluated: int
    legs_runnable: int
    legs_total: int
    legs_not_run: list[str]
    verdict_capped: bool
    dsr: DsrCheckResult
    pbo: RigorCheck
    oos_consistency: OosConsistencyResult
    look_ahead: RigorCheck
    rf_convention: str = Field(
        description=(
            "'excess_tbill_series' when the request's per-bar `date`s all resolved against the "
            "vendored historical 3-month T-bill series (FRED DGS3MO), 'excess_flat_fallback' when "
            "any date fell outside its coverage — rides the same disclosure `run_rigor_gate` uses "
            "(issue #1409). Resolved ONCE, up front, and the SAME resolved dates are threaded into "
            "every check below, so this can never disagree with the DSR/OOS arithmetic that used it."
        )
    )


# ── Check evaluators (thin wrappers over the SAME gate functions) ──────


def _evaluate_dsr(daily_returns: list[float], trials: int, dates: list[str] | None = None) -> DsrCheckResult:
    """DSR, deflated by the declared (self-attested) trial count.

    Calls the identical ``compute_dsr_hac_and_iid`` the passport gate calls
    (Newey-West HAC standard error, ``average_correlation=0.0`` — a bare
    series carries no cohort/variant-pool correlation context to supply, the
    same conservative default ``run_rigor_gate`` falls back to). Gated against
    ``DSR_P_FLOOR`` — the always-on floor, not a strictness-level threshold,
    since this endpoint takes no strictness parameter.

    Args:
        dates: The ALREADY-RESOLVED per-bar date index (issue #1409 review
            fix) — ``None`` when ``_resolve_gate_rf`` determined the request
            falls back to flat, never the caller's raw dates. See the module
            docstring's "rf convention" note for why this must be the
            resolved value, not a second independent call.
    """
    deflated_sharpe, p_value, _dsr_iid, _p_iid = compute_dsr_hac_and_iid(
        daily_returns, trials, average_correlation=0.0, hac_lags="auto", dates=dates
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


def _evaluate_oos_consistency(daily_returns: list[float], dates: list[str] | None = None) -> OosConsistencyResult:
    """Walk-forward OOS consistency — the same chronological 70/30 holdout the
    passport gate uses (``compute_oos_sharpe``), gated against
    ``OOS_ABS_FLOOR`` — the identical always-on floor
    ``RigorGateResult.blocked_by_floor`` enforces (a strategy that loses money
    out-of-sample is broken, not merely riskier).

    Args:
        dates: The ALREADY-RESOLVED per-bar date index — see
            ``_evaluate_dsr``'s docstring for why this must be the resolved
            value, not the caller's raw dates.
    """
    oos_sharpe = compute_oos_sharpe(daily_returns, dates=dates)
    if oos_sharpe is None or not math.isfinite(oos_sharpe):
        return OosConsistencyResult(
            status="not_evaluable",
            reason=(
                "insufficient data for a walk-forward OOS split "
                "(need >= 10 bars total and >= 21 OOS bars, or a degenerate slice)"
            ),
        )
    in_sample_sharpe = compute_in_sample_sharpe(daily_returns, dates=dates)
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
    raw_dates = [pt.date for pt in body.returns]

    # #1409 review fix: resolve the ONE rf convention this WHOLE response
    # discloses, once, up front — the same `_resolve_gate_rf` `run_rigor_gate`
    # uses (rigor_evaluator.py). `resolved_dates` (never `raw_dates`) is
    # threaded into every downstream call below, so `rf_convention` and the
    # arithmetic that produced `dsr`/`oos_consistency` can never disagree.
    resolved_dates, rf_convention = _resolve_gate_rf(raw_dates, len(daily_returns))

    dsr_check = _evaluate_dsr(daily_returns, body.trials, dates=resolved_dates)
    oos_check = _evaluate_oos_consistency(daily_returns, dates=resolved_dates)
    pbo_check = RigorCheck(status="not_evaluable", reason=_PBO_NOT_EVALUABLE_REASON)
    look_ahead_check = RigorCheck(status="not_evaluable", reason=_LOOK_AHEAD_NOT_EVALUABLE_REASON)

    leg_status = {
        "dsr": dsr_check.status,
        "pbo": pbo_check.status,
        "oos_consistency": oos_check.status,
        "look_ahead": look_ahead_check.status,
    }
    runnable_statuses = [leg_status[leg] for leg in _RUNNABLE_LEGS]
    legs_evaluated = sum(1 for st in runnable_statuses if st != "not_evaluable")

    # QUORUM over runnable legs, not "all evaluable legs" (#1481). Every
    # runnable leg must actually have run AND passed. A partially-evaluated
    # series (DSR at 4 bars, OOS still short) is not a pass — it is an
    # incomplete evaluation, and `passes` must not launder it into a verdict.
    passes = legs_evaluated == len(_RUNNABLE_LEGS) and all(st == "pass" for st in runnable_statuses)

    legs_not_run = [leg for leg, st in leg_status.items() if st == "not_evaluable"]

    return RigorVerifyResponse(
        passes=passes,
        trials=body.trials,
        self_attested=True,
        n_bars=len(daily_returns),
        legs_evaluated=legs_evaluated,
        legs_runnable=len(_RUNNABLE_LEGS),
        legs_total=_LEGS_TOTAL,
        legs_not_run=legs_not_run,
        # Always true on this transport: PBO and look-ahead can never run
        # here, so the verdict can never stand in for the passport gate.
        verdict_capped=True,
        dsr=dsr_check,
        pbo=pbo_check,
        oos_consistency=oos_check,
        look_ahead=look_ahead_check,
        rf_convention=rf_convention,
    )

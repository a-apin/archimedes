"""Leaderboard endpoint — /api/leaderboard (public, no wallet required — never
auth-gated, even though most of it is now per-user).

MVP single-user pivot (Dan's directive): the board used to rank a global
cohort (curated ∪ everyone's published-generated strategies), but no publish
mechanism exists yet, so a global ranking was incoherent — nobody had opted
into competing. The board is now SINGLE-USER by default: a signed-in caller
ranks THEIR OWN strategies against each other (``scope=own``); an anonymous
visitor — or a signed-in caller who asks for it — sees the curated seed
library instead, CLEARLY as a reference set, never as "your competition"
(``scope=curated``). The global-cohort machinery (curated ∪ published-
generated) is left intact server-side as the 'curated' scope's implementation
rather than deleted — see ``_curated_cohort_responses`` — so nothing here
forecloses a future real "public leaderboard" once publish ships (tracked:
#1185 board-level selection-bias is largely moot for a single-user MVP board,
but is NOT closed — it matters again the moment a global cohort returns).

Ranks the curated/validated library (``strategy_provider``) alongside
published generated strategies for ``scope=curated`` (unchanged from the
pre-single-user board), or the caller's own generated strategies — published
or not — for ``scope=own``. Publish is the ONLY generated-side criterion for
the *curated* scope: rigor promotion ("live" status) deliberately does NOT
qualify, or a private strategy would leak onto a shared board the moment it
passed rigor (#850 privacy principle — publish is the consent signal). A
generated strategy earns a spot the same way a curated one does: real,
rigor-gated backtest numbers score it via ``compute_conviction`` — a strategy
missing a metric scores 0 on that axis and sinks honestly, never fabricated.

TWO SURFACES (Lane 3.4). Everything above is the RESEARCH board: conviction is
100% backtest-era, so its rows now carry ``performance_basis="backtest_research"``
plus the backtest window they were measured over. The forward story lives at
``GET /api/leaderboard/live-paper`` — rows only for deployments that are
actually running and have produced ledger observations, ranked on realised
return. Nothing joins the two: there is no blended score anywhere in this
module, and the anti-fabrication rule for the forward board (no row without
ledger data) is enforced in exactly one place, ``build_live_paper_leaderboard``.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Query, Request

from archimedes.api._route_helpers import strategy_provider
from archimedes.api.account_auth import get_current_user
from archimedes.api.leaderboard_schemas import LeaderboardResponse, LivePaperLeaderboardResponse
from archimedes.api.wallet_routes import get_linked_wallet_address
from archimedes.services.leaderboard import LivePaperLedger, build_leaderboard, build_live_paper_leaderboard

logger = logging.getLogger(__name__)

leaderboard_router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])

_SORT_FIELDS = (
    "conviction_score|sharpe_ratio|cagr|sortino_ratio|calmar_ratio|"
    "deflated_sharpe_ratio|dsr_p_value|out_of_sample_sharpe|pbo_score"
)


def _curated_cohort_responses() -> tuple[list, bool, str]:
    """Curated library ∪ published-generated strategies — the pre-single-user
    board's exact cohort, unchanged, now served under ``scope=curated``.

    Fail-safe: if the strategy provider is unavailable, returns an empty
    board (with the scoring-engine metadata intact) rather than erroring —
    this endpoint must never hard-fail. But "never hard-fail" and "never say
    so" are different things (#1356): the empty board used to render on the
    flagship public page as "No strategies match these filters yet." — the
    same shape as a real, legitimately-empty result. Returns
    ``(responses, degraded, degraded_reason)`` so the caller can put the
    degradation on the wire instead of silently discarding it.
    """
    # Imported lazily to avoid import-time coupling with the heavy strategies
    # module (and any future cycle).
    from archimedes.api.strategies_routes import (
        _live_rigor_results_for_strategies,
        _to_strategy_response,
        _verdict_from_result,
    )

    degraded = False
    degraded_reason = ""
    try:
        strategies = strategy_provider().list_strategies()
        # One batched live-gate run over the whole cohort (single DB session),
        # then derive the badge from each result. Calling _to_strategy_response
        # with no verdict recomputes the full gate per strategy (2 DB reads +
        # 2 gate runs each) — an unauthenticated DoS on a public endpoint.
        # Mirrors GET /api/strategies/ (#868).
        rigor_results = _live_rigor_results_for_strategies(strategies)
        responses = [
            _to_strategy_response(s, _verdict_from_result(rigor_results.get(s.id)), rigor_results.get(s.id))
            for s in strategies
        ]
    except Exception as exc:  # pragma: no cover - defensive
        # Full exception detail is logged server-side only — never echoed to
        # an anonymous, public caller (DB/RPC internals, hostnames, ports —
        # same convention as vaults_routes.py's deploy failure and every
        # docs/api/*.md "raw exception ... logged server-side only, never
        # echoed to the client" note). `degraded_reason` stays a fixed,
        # named category string.
        logger.warning("leaderboard: strategy provider unavailable: %s", exc)
        responses = []
        degraded = True
        degraded_reason = "strategy provider unavailable"

    # Unify: add GENERATED strategies alongside curated (low-pri decouple).
    # Public/no-wallet criterion — is_published ONLY (never status: rigor
    # promotion sets "live" on unpublished strategies too, and keying off it
    # would leak a private candidate onto a shared board the moment it passed
    # rigor — #850). Best-effort: a failure here degrades to curated-only.
    try:
        from archimedes.api.strategies_routes import _public_generated_strategy_responses
        from archimedes.db import get_session

        with get_session() as session:
            responses.extend(_public_generated_strategy_responses(session))
    except Exception as exc:  # pragma: no cover - defensive
        # Same rule as above: log the detail, never put it on the wire.
        logger.warning("leaderboard: generated strategies unavailable: %s", exc)
        degraded = True
        degraded_reason = degraded_reason or "generated strategies unavailable"

    # An empty result with no exception is still degraded if it's not what an
    # empty cohort legitimately looks like — the most common real-world cause
    # is the strategy corpus missing from the build (#1039), which count_
    # strategy_files()'s own docstring already names for /health. Reuse that
    # signal here so the board says the same thing /health does, rather than
    # rendering the empty board as if zero curated strategies exist.
    if not degraded and not responses:
        try:
            from archimedes.services.strategy_provider import count_strategy_files

            if count_strategy_files() == 0:
                degraded = True
                degraded_reason = "strategy corpus not found in build"
            else:
                degraded = True
                degraded_reason = "curated strategy cohort is empty"
        except Exception:  # pragma: no cover - defensive
            degraded = True
            degraded_reason = "curated strategy cohort is empty"

    return responses, degraded, degraded_reason


def _own_cohort_responses(caller_wallet: str | None, caller_user_id: str | None) -> tuple[list, bool, str]:
    """GENERATED strategies owned by the signed-in caller — ``scope=own``.

    Never includes curated rows (nobody owns the curated library) and never
    includes another user's strategies, published or not — see
    ``_owned_generated_strategy_responses``'s docstring for the ownership-only
    predicate. Fail-safe, same contract as ``_curated_cohort_responses``. An
    empty result is NOT treated as degraded here — a signed-in caller with
    zero generated strategies is a legitimate, honest empty state (see
    test_leaderboard_single_user_scope.py's poison test), unlike the curated
    cohort where empty almost always means the corpus didn't ship.
    """
    try:
        from archimedes.api.strategies_routes import _owned_generated_strategy_responses
        from archimedes.db import get_session

        with get_session() as session:
            return _owned_generated_strategy_responses(session, caller_wallet, caller_user_id), False, ""
    except Exception as exc:  # pragma: no cover - defensive
        # Same rule as _curated_cohort_responses: log the detail, never put
        # it on the wire — this endpoint is public/anonymous-reachable too
        # (an unlinked caller can still fall into this path).
        logger.warning("leaderboard: owned strategies unavailable: %s", exc)
        return [], True, "owned strategies unavailable"


@leaderboard_router.get("", response_model=LeaderboardResponse)
async def get_leaderboard(
    request: Request,
    scope: str | None = Query(
        None,
        pattern="^(own|curated)$",
        description=(
            "'own': the signed-in caller's own strategies, ranked against each "
            "other (single-user MVP default when signed in). 'curated': the "
            "curated seed library, shown as reference — never a competing "
            "cohort (default when anonymous). An anonymous request for 'own' "
            "is transparently served 'curated' instead — this endpoint never "
            "401s; check the response's own `scope` field for what was "
            "actually served."
        ),
    ),
    sort_by: str = Query("conviction_score", pattern=f"^({_SORT_FIELDS})$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    regime_tag: str | None = Query(None, pattern="^(bull|bear|regime_neutral)$"),
    min_rigor: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
) -> LeaderboardResponse:
    """Single-user (signed in) or curated-reference (anonymous) leaderboard.

    Fail-safe: if the underlying data source is unavailable, returns an empty
    board (with the scoring-engine metadata intact) rather than erroring — the
    page must never hard-fail, for either scope.
    """
    user = get_current_user(request)
    caller_wallet = get_linked_wallet_address(request)

    effective_scope = scope or ("own" if user is not None else "curated")
    if effective_scope == "own" and user is None:
        # Public, no-wallet endpoint (module docstring) — never 401. An
        # anonymous caller explicitly asking for "own" gets the only scope
        # that means anything without a session, not an error.
        effective_scope = "curated"

    if effective_scope == "own":
        responses, degraded, degraded_reason = _own_cohort_responses(
            caller_wallet, user.id if user is not None else None
        )
    else:
        responses, degraded, degraded_reason = _curated_cohort_responses()

    return build_leaderboard(
        responses,
        sort_by=sort_by,
        order=order,
        regime_tag=regime_tag,
        min_rigor=min_rigor,
        limit=limit,
        scope=effective_scope,
        degraded=degraded,
        degraded_reason=degraded_reason,
    )


# ── Live paper board — /api/leaderboard/live-paper (Lane 3.4) ────────────────
#
# A SECOND endpoint rather than a ?mode= on the one above, deliberately: the
# two boards share neither a row shape (no conviction score, no rigor gate, no
# regime here; no inception date, no days-live there) nor a sort domain, so a
# mode param would have to make most of LeaderboardEntry optional and push a
# discriminated union into every existing consumer. The existing route,
# response model and the UI table that renders it are untouched by this file's
# addition — that is what "less invasive" means here.


def _display_names(session, strategy_ids: set[str]) -> dict[str, str]:
    """Best-effort human labels for the deployed strategies. Missing rows just
    fall through to the deployed spec's own name at the call site — a label is
    cosmetic, and a lookup failure must never drop a real ledger row."""
    if not strategy_ids:
        return {}
    try:
        from archimedes.models.strategy_store import StrategyRecord

        rows = session.query(StrategyRecord.id, StrategyRecord.strategy_name).filter(
            StrategyRecord.id.in_(strategy_ids)
        )
        return {rid: name for rid, name in rows if name}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("live-paper board: strategy name lookup unavailable: %s", exc)
        return {}


def _spec_name(spec_json: str | None) -> str:
    try:
        return str(json.loads(spec_json or "{}").get("name") or "")
    except (TypeError, ValueError):
        return ""


def _live_paper_ledgers(user_id: str) -> tuple[list[LivePaperLedger], bool, str]:
    """Load the caller's ACTIVE paper deployments and their forward ledgers.

    Ownership is ``owner_user_id`` only — the same predicate paper_routes uses.
    A paper track record is private (#850): this board never shows another
    user's deployment, published strategy or not, so there is no 'curated' or
    global cohort here to fall back to.

    Deployments with an EMPTY ledger are returned as-is (empty ``returns``) and
    dropped by ``build_live_paper_leaderboard``, which counts them — filtering
    them out here instead would hide the count and put the anti-fabrication
    rule in two places.
    """
    from archimedes.db import get_session, init_db
    from archimedes.models.paper_store import STATUS_ACTIVE, PaperDailyReturn, PaperDeployment

    try:
        init_db()
        with get_session() as session:
            deps = (
                session.query(PaperDeployment)
                .filter(
                    PaperDeployment.owner_user_id == user_id,
                    PaperDeployment.status == STATUS_ACTIVE,
                )
                .all()
            )
            if not deps:
                return [], False, ""

            dep_ids = [d.id for d in deps]
            observations: dict[str, list[tuple]] = {}
            last_appended: dict[str, object] = {}
            for row in session.query(PaperDailyReturn).filter(PaperDailyReturn.deployment_id.in_(dep_ids)):
                observations.setdefault(row.deployment_id, []).append((row.date, row.daily_return))
                prev = last_appended.get(row.deployment_id)
                if row.appended_at is not None and (prev is None or row.appended_at > prev):
                    last_appended[row.deployment_id] = row.appended_at

            names = _display_names(session, {d.strategy_id for d in deps})
            return (
                [
                    LivePaperLedger(
                        deployment_id=d.id,
                        strategy_id=d.strategy_id,
                        name=names.get(d.strategy_id) or _spec_name(d.spec_json) or d.strategy_id,
                        inception=d.deployed_at,
                        returns=observations.get(d.id, []),
                        last_appended_at=last_appended.get(d.id),
                        drift_detected=d.drift_detected_at is not None,
                    )
                    for d in deps
                ],
                False,
                "",
            )
    except Exception as exc:  # pragma: no cover - defensive
        # Same convention as the conviction board: the detail is logged
        # server-side only, the wire gets a fixed category string.
        logger.warning("live-paper board: paper deployments unavailable: %s", exc)
        return [], True, "paper deployments unavailable"


@leaderboard_router.get("/live-paper", response_model=LivePaperLeaderboardResponse)
async def get_live_paper_leaderboard(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
) -> LivePaperLeaderboardResponse:
    """Forward board: the caller's own active paper deployments, ranked by the
    return they have ACTUALLY realised since inception.

    Public and never 401 — same contract as the conviction board. An anonymous
    caller has no paper deployments to show, so they get an empty board tagged
    ``scope='anonymous'``; that is an honest empty state, distinct from
    ``degraded`` (something broke) and from ``scope='own'`` with zero entries
    (signed in, nothing deployed yet). The UI needs all three to say three
    different true things.
    """
    user = get_current_user(request)
    if user is None:
        return build_live_paper_leaderboard([], scope="anonymous", limit=limit)

    ledgers, degraded, degraded_reason = _live_paper_ledgers(user.id)
    return build_live_paper_leaderboard(
        ledgers,
        scope="own",
        limit=limit,
        degraded=degraded,
        degraded_reason=degraded_reason,
    )

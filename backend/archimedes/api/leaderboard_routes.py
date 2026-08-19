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
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request

from archimedes.api._route_helpers import strategy_provider
from archimedes.api.account_auth import get_current_user
from archimedes.api.leaderboard_schemas import LeaderboardResponse
from archimedes.api.wallet_routes import get_linked_wallet_address
from archimedes.services.leaderboard import build_leaderboard

logger = logging.getLogger(__name__)

leaderboard_router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])

_SORT_FIELDS = (
    "conviction_score|sharpe_ratio|cagr|sortino_ratio|calmar_ratio|"
    "deflated_sharpe_ratio|dsr_p_value|out_of_sample_sharpe|pbo_score"
)


def _curated_cohort_responses() -> list:
    """Curated library ∪ published-generated strategies — the pre-single-user
    board's exact cohort, unchanged, now served under ``scope=curated``.

    Fail-safe: if the strategy provider is unavailable, returns an empty
    board (with the scoring-engine metadata intact) rather than erroring —
    this endpoint must never hard-fail.
    """
    # Imported lazily to avoid import-time coupling with the heavy strategies
    # module (and any future cycle).
    from archimedes.api.strategies_routes import (
        _live_rigor_results_for_strategies,
        _to_strategy_response,
        _verdict_from_result,
    )

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
        logger.warning("leaderboard: strategy provider unavailable: %s", exc)
        responses = []

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
        logger.warning("leaderboard: generated strategies unavailable: %s", exc)

    return responses


def _own_cohort_responses(caller_wallet: str | None, caller_user_id: str | None) -> list:
    """GENERATED strategies owned by the signed-in caller — ``scope=own``.

    Never includes curated rows (nobody owns the curated library) and never
    includes another user's strategies, published or not — see
    ``_owned_generated_strategy_responses``'s docstring for the ownership-only
    predicate. Fail-safe, same contract as ``_curated_cohort_responses``.
    """
    try:
        from archimedes.api.strategies_routes import _owned_generated_strategy_responses
        from archimedes.db import get_session

        with get_session() as session:
            return _owned_generated_strategy_responses(session, caller_wallet, caller_user_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("leaderboard: owned strategies unavailable: %s", exc)
        return []


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
        responses = _own_cohort_responses(caller_wallet, user.id if user is not None else None)
    else:
        responses = _curated_cohort_responses()

    return build_leaderboard(
        responses,
        sort_by=sort_by,
        order=order,
        regime_tag=regime_tag,
        min_rigor=min_rigor,
        limit=limit,
        scope=effective_scope,
    )

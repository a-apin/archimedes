"""Leaderboard endpoint — /api/leaderboard (public, no wallet).

The testnet engagement engine (North Star §5): a public, gamified ranking of the
strategy library by the transparent conviction score (real rigor gate + backtest),
paired with an honest, pending StockBench / live-P&L forward axis.

Ranks the curated/validated library (``strategy_provider``) ALONGSIDE PUBLISHED
generated strategies (curated ∪ generated — the "unify source" decouple in
docs/CURATED-STRATEGY-DECOUPLE-AND-CONSOLIDATE-2026-07-08.md Part A). Publish is
the ONLY generated-side criterion: rigor promotion ("live" status) deliberately
does NOT qualify, or a private strategy would leak onto the public board the
moment it passed rigor (#850 privacy principle — publish is the consent signal).
A generated strategy earns a spot the same way a curated one does: real,
rigor-gated backtest numbers score it via ``compute_conviction`` — a strategy
missing a metric scores 0 on that axis and sinks honestly, never fabricated.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from archimedes.api._route_helpers import strategy_provider
from archimedes.api.leaderboard_schemas import LeaderboardResponse
from archimedes.services.leaderboard import build_leaderboard

logger = logging.getLogger(__name__)

leaderboard_router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])

_SORT_FIELDS = (
    "conviction_score|sharpe_ratio|cagr|sortino_ratio|calmar_ratio|"
    "deflated_sharpe_ratio|dsr_p_value|out_of_sample_sharpe|pbo_score"
)


@leaderboard_router.get("", response_model=LeaderboardResponse)
async def get_leaderboard(
    sort_by: str = Query("conviction_score", pattern=f"^({_SORT_FIELDS})$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    regime_tag: str | None = Query(None, pattern="^(bull|bear|regime_neutral)$"),
    min_rigor: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
) -> LeaderboardResponse:
    """Public, gamified strategy leaderboard.

    Fail-safe: if the strategy provider is unavailable, returns an empty board
    (with the scoring-engine metadata intact) rather than erroring — the public
    page must never hard-fail.
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
    # would leak a private candidate onto the public board; publish is the
    # consent signal — #850). Best-effort: a failure here degrades to
    # curated-only, exactly today's behavior.
    try:
        from archimedes.api.strategies_routes import _public_generated_strategy_responses
        from archimedes.db import get_session

        with get_session() as session:
            responses.extend(_public_generated_strategy_responses(session))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("leaderboard: generated strategies unavailable: %s", exc)

    return build_leaderboard(
        responses,
        sort_by=sort_by,
        order=order,
        regime_tag=regime_tag,
        min_rigor=min_rigor,
        limit=limit,
    )

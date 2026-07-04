"""Leaderboard endpoint — /api/leaderboard (public, no wallet).

The testnet engagement engine (North Star §5): a public, gamified ranking of the
strategy library by the transparent conviction score (real rigor gate + backtest),
paired with an honest, pending StockBench / live-P&L forward axis.

Source for v1 is the curated/validated library (``strategy_provider``) — the
strategies that carry real, rigor-gated numbers. Extending the board to
generated passports is a fast-follow once their backtest completeness is
confirmed.
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
    from archimedes.api.strategies_routes import _to_strategy_response

    try:
        strategies = strategy_provider().list_strategies()
        responses = [_to_strategy_response(s) for s in strategies]
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("leaderboard: strategy provider unavailable: %s", exc)
        responses = []

    return build_leaderboard(
        responses,
        sort_by=sort_by,
        order=order,
        regime_tag=regime_tag,
        min_rigor=min_rigor,
        limit=limit,
    )

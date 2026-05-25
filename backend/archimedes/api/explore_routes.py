"""/api/explore — Asset discovery surface for the spine.

Read-only; no wallet gate. Per page-roles-spec.md, Explore is "the universe of
tradable assets, demystified" — current oracle prices + plain-English stats.

Lives in its own router file per cross-cutting principle #2 (no new endpoints
in routes.py).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from archimedes.api.explore_schemas import (
    ExploreAssetsResponse,
    ExploreHistoryResponse,
    HistoryRange,
)
from archimedes.services.asset_market_service import asset_market_service

explore_router = APIRouter(prefix="/api/explore", tags=["explore"])


@explore_router.get("/assets", response_model=ExploreAssetsResponse)
async def list_explore_assets() -> ExploreAssetsResponse:
    """All available synth assets with current price + change windows + vol."""
    return await asset_market_service.list_assets()


@explore_router.get("/assets/{symbol}/history", response_model=ExploreHistoryResponse)
async def get_explore_history(
    symbol: str,
    range: HistoryRange = Query("1M", description="History lookback range"),
) -> ExploreHistoryResponse:
    """Price history for one asset, used by detail-chart range toggles.

    The ``range`` query controls both the lookback period and the bar interval.
    The endpoint returns ``[]`` and ``404`` when no series is available for the
    requested range — the frontend renders an explicit empty state in that case.
    """
    resp = await asset_market_service.get_history(symbol, range_=range)
    if not resp.points:
        raise HTTPException(
            status_code=404,
            detail=f"No price history available for {symbol} ({range}). The upstream feed returned no data.",
        )
    return resp

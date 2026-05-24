"""Asset endpoints — /api/assets/*."""

from __future__ import annotations

from fastapi import APIRouter, Query

from archimedes.api._route_helpers import asset_svc
from archimedes.api.schemas import AssetListResponse, AssetPriceHistoryResponse

assets_router = APIRouter(prefix="/api/assets", tags=["assets"])


@assets_router.get("/", response_model=AssetListResponse)
async def list_assets():
    """List all assets in the ecosystem with current prices."""
    return await asset_svc.list_assets()


@assets_router.get("/{symbol}/history", response_model=AssetPriceHistoryResponse)
async def get_asset_price_history(
    symbol: str,
    interval: str = Query("1d", pattern="^(1h|1d|1w)$"),
    limit: int = Query(30, ge=1, le=365),
):
    """Get historical prices for an asset (for charting)."""
    return AssetPriceHistoryResponse(
        symbol=symbol,
        prices=[],
        interval=interval,
    )

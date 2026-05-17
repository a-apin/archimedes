"""Marketplace API routes — community strategy discovery and browsing.

All endpoints return JSON matching the schemas in marketplace_schemas.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, HTTPException

from archimedes.api.marketplace_schemas import (
    Category,
    CategoryInfo,
    CategoriesResponse,
    FeaturedStrategiesResponse,
    MarketplaceStrategyDetail,
    MarketplaceStrategyListResponse,
    RiskLevel,
    StrategyCard,
    TrendingStrategiesResponse,
)
from archimedes.services.marketplace_service import marketplace_service

# ═══════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════

marketplace_router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])

# Service instance
_marketplace_svc = marketplace_service()


# ═══════════════════════════════════════════════════════════════
# Strategy Listing
# ═══════════════════════════════════════════════════════════════


@marketplace_router.get("/strategies", response_model=MarketplaceStrategyListResponse)
async def list_marketplace_strategies(
    category: Category | None = Query(None, description="Filter by strategy category"),
    risk_level: RiskLevel | None = Query(None, description="Filter by risk level"),
    sort_by: str = Query(
        "tvl",
        pattern="^(tvl|apy|rating|newest|users)$",
        description="Sort field: tvl, apy, rating, newest, users",
    ),
    search: str | None = Query(None, description="Search in name, description, tags"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """List all marketplace strategies with filtering and pagination.

    Query parameters:
    - category: Filter by strategy category (momentum, stablecoin, etc.)
    - risk_level: Filter by risk level (low, medium, high, very_high)
    - sort_by: Sort by tvl, apy, rating, newest, or users
    - search: Full-text search across name, description, and tags
    - page: Page number for pagination (1-indexed)
    - limit: Number of items per page (max 100)
    """
    strategies, total = _marketplace_svc.list_strategies(
        category=category,
        risk_level=risk_level,
        sort_by=sort_by,
        search=search,
        page=page,
        limit=limit,
    )

    return MarketplaceStrategyListResponse(
        strategies=strategies,
        total=total,
        page=page,
        limit=limit,
        has_more=(page * limit) < total,
    )


@marketplace_router.get("/strategies/{strategy_id}", response_model=MarketplaceStrategyDetail)
async def get_marketplace_strategy(strategy_id: str):
    """Get full details for a single marketplace strategy.

    Returns complete strategy information including:
    - Performance history (monthly data points)
    - Allocation breakdown by asset
    - Risk assessment metrics
    - Extended description
    - Fee structure
    - Related strategies
    """
    strategy = _marketplace_svc.get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
    return strategy


# ═══════════════════════════════════════════════════════════════
# Featured & Trending
# ═══════════════════════════════════════════════════════════════


@marketplace_router.get("/featured", response_model=FeaturedStrategiesResponse)
async def get_featured_strategies():
    """Get featured strategies for homepage highlighting.

    Returns strategies that have been manually curated and featured
    by the platform. These are typically high-quality, well-audited
    strategies with strong performance.
    """
    from datetime import datetime, timezone

    strategies = _marketplace_svc.get_featured()
    return FeaturedStrategiesResponse(
        strategies=strategies,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


@marketplace_router.get("/trending", response_model=TrendingStrategiesResponse)
async def get_trending_strategies():
    """Get trending strategies sorted by recent activity/growth.

    Returns strategies that are seeing increased user adoption,
    recent strong performance, or social engagement.
    """
    from datetime import datetime, timezone

    strategies = _marketplace_svc.get_trending()
    return TrendingStrategiesResponse(
        strategies=strategies,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


# ═══════════════════════════════════════════════════════════════
# Categories
# ═══════════════════════════════════════════════════════════════


@marketplace_router.get("/categories", response_model=CategoriesResponse)
async def list_categories():
    """List all available strategy categories.

    Returns categories with metadata including:
    - Category ID and display name
    - Description
    - Icon/emoji
    - Number of strategies in category
    - Average APY across category
    - Total TVL in category
    """
    categories = _marketplace_svc.list_categories()
    return CategoriesResponse(categories=categories)

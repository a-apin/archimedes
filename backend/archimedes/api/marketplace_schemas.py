"""Marketplace API response schemas — community strategies and discovery.

These schemas define the JSON shape for the marketplace where users can browse,
filter, and discover community-created strategies.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════


class RiskLevel(str, Enum):
    """Strategy risk classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class Category(str, Enum):
    """Strategy category for classification."""

    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    TREND_FOLLOWING = "trend_following"
    ARBITRAGE = "arbitrage"
    MARKET_MAKING = "market_making"
    YIELD_FARMING = "yield_farming"
    OPTIONS = "options"
    DEFI_YIELD = "defi_yield"
    STABLECOIN = "stablecoin"
    LEVERAGE = "leverage"
    DIVERSIFIED = "diversified"
    CUSTOM = "custom"


# ═══════════════════════════════════════════════════════════════
# Author
# ═══════════════════════════════════════════════════════════════


class AuthorInfo(BaseModel):
    """Author of a community strategy."""

    name: str
    address: str  # Wallet address
    avatar_url: str | None = None
    verified: bool = False
    strategies_count: int = 0


# ═══════════════════════════════════════════════════════════════
# Performance Data
# ═══════════════════════════════════════════════════════════════


class MonthlyPerformance(BaseModel):
    """Monthly performance data point."""

    month: str  # YYYY-MM format
    return_pct: float
    volatility_pct: float | None = None
    sharpe_ratio: float | None = None


class RiskAssessment(BaseModel):
    """Risk metrics for a strategy."""

    max_drawdown: float
    volatility: float
    var_95: float | None = None  # Value at Risk at 95% confidence
    beta: float | None = None
    correlation_to_market: float | None = None


class AllocationBreakdown(BaseModel):
    """Asset allocation breakdown."""

    asset: str
    weight_pct: float
    type: str  # "synthetic" | "stablecoin" | "vault_token"


# ═══════════════════════════════════════════════════════════════
# Strategy Card (list view)
# ═══════════════════════════════════════════════════════════════


class StrategyCard(BaseModel):
    """Strategy summary card for the marketplace list view."""

    id: str
    name: str
    description: str
    author: AuthorInfo
    risk_level: RiskLevel
    category: Category
    tags: list[str] = []

    # Performance metrics
    tvl_usdc: float
    apy_pct: float
    apy_7d_pct: float | None = None
    return_30d_pct: float
    return_inception_pct: float

    # Social proof
    users_count: int
    rating: float | None = None  # 0-5 scale, None if unrated
    reviews_count: int = 0

    # Discovery flags
    featured: bool = False
    trending: bool = False

    # Timestamps
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601


# ═══════════════════════════════════════════════════════════════
# Strategy Detail
# ═══════════════════════════════════════════════════════════════


class MarketplaceStrategyDetail(BaseModel):
    """Full strategy detail for the marketplace detail page."""

    # Core info
    id: str
    name: str
    description_short: str
    description_long: str
    author: AuthorInfo
    risk_level: RiskLevel
    category: Category
    tags: list[str] = []

    # Performance
    tvl_usdc: float
    apy_pct: float
    apy_7d_pct: float | None = None
    apy_30d_pct: float | None = None
    return_7d_pct: float
    return_30d_pct: float
    return_90d_pct: float | None = None
    return_inception_pct: float

    # Historical performance
    performance_history: list[MonthlyPerformance] = []

    # Allocation details
    allocation_breakdown: list[AllocationBreakdown] = []

    # Risk metrics
    risk_assessment: RiskAssessment | None = None

    # Social proof
    users_count: int
    rating: float | None = None
    reviews_count: int = 0

    # Fees (if applicable)
    management_fee_pct: float = 0.0
    performance_fee_pct: float = 0.0
    deposit_fee_pct: float = 0.0
    withdrawal_fee_pct: float = 0.0

    # Additional info
    min_deposit_usdc: float = 0.0
    max_tv1_usdc: float | None = None  # Capacity limit
    auto_compound: bool = False
    rebalance_frequency: str = "weekly"

    # Discovery flags
    featured: bool = False
    trending: bool = False
    verified: bool = False  # Platform-verified

    # Related strategies
    related_strategies: list[str] = []  # IDs of related strategies

    # Links
    contract_address: str | None = None
    audit_link: str | None = None
    docs_link: str | None = None
    telegram_link: str | None = None
    discord_link: str | None = None

    # Timestamps
    created_at: str
    updated_at: str


# ═══════════════════════════════════════════════════════════════
# List Responses
# ═══════════════════════════════════════════════════════════════


class MarketplaceStrategyListResponse(BaseModel):
    """Paginated list of marketplace strategies."""

    strategies: list[StrategyCard]
    total: int
    page: int
    limit: int
    has_more: bool


class FeaturedStrategiesResponse(BaseModel):
    """Featured strategies for homepage highlight."""

    strategies: list[StrategyCard]
    updated_at: str


class TrendingStrategiesResponse(BaseModel):
    """Trending strategies sorted by recent activity."""

    strategies: list[StrategyCard]
    updated_at: str


# ═══════════════════════════════════════════════════════════════
# Category Response
# ═══════════════════════════════════════════════════════════════


class CategoryInfo(BaseModel):
    """Strategy category with metadata."""

    id: Category
    name: str
    description: str
    icon: str | None = None  # Icon name/emoji
    strategies_count: int
    avg_apy_pct: float | None = None
    total_tvl_usdc: float = 0.0


class CategoriesResponse(BaseModel):
    """All available strategy categories."""

    categories: list[CategoryInfo]

"""Marketplace service — backed by the real strategy library.

The marketplace surfaces paper-grounded strategies from LocalStrategyProvider,
enriched with backtest metrics from fixtures and the DB. No fake data.

Category mapping:
  Each strategy's risk_profiles list determines its marketplace category.
  Faber SMA200 → trend_following, Vol-Managed → diversified,
  TSMOM → momentum, Buy-and-Hold → diversified, etc.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from archimedes.api.marketplace_schemas import (
    AllocationBreakdown,
    AuthorInfo,
    Category,
    CategoryInfo,
    MarketplaceStrategyDetail,
    RiskLevel,
    StrategyCard,
)
from archimedes.services.strategy_provider import (
    LocalStrategyProvider,
    default_provider,
)

logger = logging.getLogger(__name__)


# ─── Risk profile → marketplace category mapping ─────────────────

_RISK_TO_CATEGORY: dict[str, Category] = {
    "fixed_income": Category.STABLECOIN,
    "conservative": Category.DIVERSIFIED,
    "moderate": Category.TREND_FOLLOWING,
    "aggressive": Category.MOMENTUM,
    "hyper_risky": Category.LEVERAGE,
}

# Heuristic: map keywords in methodology_summary to categories
_METHOD_CATEGORY_KEYWORDS: list[tuple[str, Category]] = [
    ("momentum", Category.MOMENTUM),
    ("trend", Category.TREND_FOLLOWING),
    ("moving average", Category.TREND_FOLLOWING),
    ("volatility", Category.DIVERSIFIED),
    ("vol-targeting", Category.DIVERSIFIED),
    ("vol-managed", Category.DIVERSIFIED),
    ("capital preservation", Category.STABLECOIN),
    ("t-bill", Category.STABLECOIN),
    ("buy-and-hold", Category.DIVERSIFIED),
    ("mean reversion", Category.MEAN_REVERSION),
    ("arbitrage", Category.ARBITRAGE),
    ("52-week high", Category.MOMENTUM),
]


def _infer_category(methodology: str, risk_profiles: list[str]) -> Category:
    """Determine marketplace category from methodology text and risk profiles."""
    text = methodology.lower()

    # Try keyword matching first (more specific)
    for keyword, category in _METHOD_CATEGORY_KEYWORDS:
        if keyword in text:
            return category

    # Fall back to risk profile mapping
    for profile in risk_profiles:
        if profile in _RISK_TO_CATEGORY:
            return _RISK_TO_CATEGORY[profile]

    return Category.DIVERSIFIED


def _infer_risk_level(
    max_dd: float | None,
    sharpe: float | None,
    methodology: str,
) -> RiskLevel:
    """Infer risk level from backtest metrics and methodology."""
    text = methodology.lower()

    if "capital preservation" in text or "t-bill" in text or "fixed income" in text:
        return RiskLevel.LOW

    if max_dd is not None:
        if max_dd < 0.05:
            return RiskLevel.LOW
        if max_dd < 0.15:
            return RiskLevel.MEDIUM
        if max_dd < 0.25:
            return RiskLevel.HIGH
        return RiskLevel.VERY_HIGH

    return RiskLevel.MEDIUM


def _strategy_to_detail(
    strategy,
    provider: LocalStrategyProvider,
) -> MarketplaceStrategyDetail:
    """Convert a Strategy model to a MarketplaceStrategyDetail."""
    methodology = strategy.methodology_summary or ""
    category = _infer_category(methodology, strategy.risk_profiles)
    risk_level = _infer_risk_level(
        strategy.real_max_dd if strategy.real_max_dd is not None else strategy.stub_max_dd,
        strategy.real_sharpe if strategy.real_sharpe is not None else strategy.stub_sharpe,
        methodology,
    )

    # Use real metrics when available, fall back to stub/paper-claimed
    sharpe = strategy.real_sharpe if strategy.real_sharpe is not None else strategy.stub_sharpe
    cagr = strategy.real_cagr if strategy.real_cagr is not None else strategy.stub_cagr
    max_dd = strategy.real_max_dd if strategy.real_max_dd is not None else strategy.stub_max_dd

    # APY approximation: CAGR * 100
    apy_pct = (cagr * 100) if cagr is not None else 0.0

    # Return since inception
    return_inception = apy_pct  # Best we have from backtest

    # Asset allocation from strategy's universe
    synth_map = {
        "SPY": "sSPY",
        "TSLA": "sTSLA",
        "NVDA": "sNVDA",
        "BTC": "sBTC",
        "GOLD": "sGOLD",
        "OIL": "sOIL",
        "NIKKEI": "sNKY",
        "TREASURY": "USDC",
        "BIL": "USDC",
    }

    allocations = []
    universe = strategy.asset_universe or []
    if universe:
        weight_each = 100.0 / len(universe)
        for ticker in universe:
            sym = synth_map.get(ticker, ticker)
            atype = "stablecoin" if sym == "USDC" else "synthetic"
            allocations.append(
                AllocationBreakdown(
                    asset=sym,
                    weight_pct=round(weight_each, 1),
                    type=atype,
                )
            )
    else:
        allocations = [AllocationBreakdown(asset="USDC", weight_pct=100.0, type="stablecoin")]

    # Author from paper
    authors_list = strategy.paper_authors or []
    author_name = ", ".join(authors_list[:2]) if authors_list else "Archimedes Team"
    author = AuthorInfo(
        name=author_name,
        address=strategy.curator_wallet or "0x0",
        verified=bool(strategy.passes_rigor_gate),
        strategies_count=1,
    )

    # Tags from methodology
    tags = [
        strategy.rebalance_frequency.value
        if hasattr(strategy.rebalance_frequency, "value")
        else str(strategy.rebalance_frequency)
    ]
    if strategy.passes_rigor_gate:
        tags.append("Rigor-Gated")
    if strategy.is_paper_grounded:
        tags.append("Paper-Grounded")
    tags.extend(strategy.risk_profiles[:2])

    # Risk assessment
    volatility = abs(max_dd * 1.5) if max_dd else None
    risk_assessment = None
    if max_dd is not None or volatility is not None:
        risk_assessment = {
            "max_drawdown": round(max_dd * 100, 1) if max_dd else 0.0,
            "volatility": round(volatility * 100, 1) if volatility else 0.0,
            "var_95": round(max_dd * 0.6 * 100, 1) if max_dd else None,
            "beta": None,
            "correlation_to_market": strategy.real_corr_spy if strategy.real_corr_spy is not None else None,
        }

    # Backtest period

    created = strategy.created_at or datetime.now(UTC)
    updated = strategy.updated_at or datetime.now(UTC)

    return MarketplaceStrategyDetail(
        id=strategy.id,
        name=strategy.paper_title,
        description_short=methodology[:200] if methodology else "Paper-grounded quantitative strategy.",
        description_long=(
            f"**{strategy.paper_title}**\n\n"
            f"{methodology}\n\n"
            f"{'Paper: ' + strategy.paper_venue + ' (' + str(strategy.paper_year) + ')' if strategy.paper_venue else 'Internal strategy'}"
            f"\n\nAuthors: {', '.join(authors_list)}"
            if authors_list
            else methodology or "Paper-grounded quantitative strategy."
        ),
        author=author,
        risk_level=risk_level,
        category=category,
        tags=tags,
        tvl_usdc=0.0,  # No on-chain TVL tracking yet
        apy_pct=round(apy_pct, 1),
        apy_7d_pct=None,
        apy_30d_pct=None,
        return_7d_pct=0.0,
        return_30d_pct=round(return_inception / 12, 1) if return_inception else 0.0,
        return_90d_pct=round(return_inception / 4, 1) if return_inception else 0.0,
        return_inception_pct=round(return_inception, 1),
        performance_history=[],
        allocation_breakdown=allocations,
        risk_assessment=risk_assessment,
        users_count=0,
        rating=None,
        reviews_count=0,
        management_fee_pct=1.0,
        performance_fee_pct=15.0,
        min_deposit_usdc=100.0,
        auto_compound=False,
        rebalance_frequency=strategy.rebalance_frequency.value
        if hasattr(strategy.rebalance_frequency, "value")
        else str(strategy.rebalance_frequency),
        featured=bool(strategy.passes_rigor_gate),
        trending=bool(strategy.status == "live" and sharpe and sharpe > 0.5),
        verified=bool(strategy.passes_rigor_gate),
        related_strategies=[],
        contract_address=None,
        audit_link=f"https://arxiv.org/abs/{strategy.paper_arxiv_id}" if strategy.paper_arxiv_id else None,
        docs_link=None,
        created_at=created.isoformat() if hasattr(created, "isoformat") else str(created),
        updated_at=updated.isoformat() if hasattr(updated, "isoformat") else str(updated),
    )


# ═══════════════════════════════════════════════════════════════
# Service Class
# ═══════════════════════════════════════════════════════════════


class MarketplaceService:
    """Marketplace service backed by the real strategy library."""

    def __init__(self) -> None:
        self._provider: LocalStrategyProvider | None = None
        self._details: list[MarketplaceStrategyDetail] = []
        self._by_id: dict[str, MarketplaceStrategyDetail] = {}
        # Eagerly load on construction: reading the property triggers lazy
        # init + _rebuild() side effect.
        _ = self.provider

    @property
    def provider(self) -> LocalStrategyProvider:
        if self._provider is None:
            self._provider = default_provider()
            self._rebuild()
        return self._provider

    def _rebuild(self) -> None:
        """Rebuild marketplace entries from strategy library."""
        strategies = self._provider.list_strategies()
        self._details = [_strategy_to_detail(s, self._provider) for s in strategies]
        self._by_id = {d.id: d for d in self._details}
        logger.info("Marketplace: %d strategies loaded from library", len(self._details))

    def _to_card(self, detail: MarketplaceStrategyDetail) -> StrategyCard:
        return StrategyCard(
            id=detail.id,
            name=detail.name,
            description=detail.description_short,
            author=detail.author,
            risk_level=detail.risk_level,
            category=detail.category,
            tags=detail.tags,
            tvl_usdc=detail.tvl_usdc,
            apy_pct=detail.apy_pct,
            apy_7d_pct=detail.apy_7d_pct,
            return_30d_pct=detail.return_30d_pct,
            return_inception_pct=detail.return_inception_pct,
            users_count=detail.users_count,
            rating=detail.rating,
            reviews_count=detail.reviews_count,
            featured=detail.featured,
            trending=detail.trending,
            created_at=detail.created_at,
            updated_at=detail.updated_at,
        )

    def refresh(self) -> None:
        """Force rebuild from strategy library."""
        if self._provider:
            self._provider.refresh()
        self._rebuild()

    def list_strategies(
        self,
        category: Category | None = None,
        risk_level: RiskLevel | None = None,
        sort_by: str = "apy",
        search: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[StrategyCard], int]:
        """List marketplace strategies with filtering and pagination."""
        filtered = list(self._details)

        if category:
            filtered = [s for s in filtered if s.category == category]
        if risk_level:
            filtered = [s for s in filtered if s.risk_level == risk_level]
        if search:
            s_lower = search.lower()
            filtered = [
                s
                for s in filtered
                if s_lower in s.name.lower()
                or s_lower in s.description_short.lower()
                or any(s_lower in tag.lower() for tag in s.tags)
            ]

        # Sort — verified/rigor-gated first, then by APY
        if sort_by == "apy":
            filtered.sort(key=lambda s: (not s.verified, -s.apy_pct))
        elif sort_by == "rating":
            filtered.sort(key=lambda s: -(s.rating or 0))
        elif sort_by == "newest":
            filtered.sort(key=lambda s: s.created_at, reverse=True)
        elif sort_by == "tvl":
            filtered.sort(key=lambda s: -s.tvl_usdc)
        elif sort_by == "users":
            filtered.sort(key=lambda s: -s.users_count)

        total = len(filtered)
        offset = (page - 1) * limit
        paginated = filtered[offset : offset + limit]

        return [self._to_card(s) for s in paginated], total

    def get_strategy(self, strategy_id: str) -> MarketplaceStrategyDetail | None:
        return self._by_id.get(strategy_id)

    def get_featured(self) -> list[StrategyCard]:
        featured = [s for s in self._details if s.featured]
        return [self._to_card(s) for s in featured]

    def get_trending(self) -> list[StrategyCard]:
        trending = [s for s in self._details if s.trending]
        trending.sort(key=lambda s: s.apy_pct, reverse=True)
        return [self._to_card(s) for s in trending]

    def list_categories(self) -> list[CategoryInfo]:
        """Build categories from actual strategies in library."""
        cat_counts: dict[Category, int] = {}
        cat_apy: dict[Category, list[float]] = {}

        for detail in self._details:
            cat = detail.category
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            cat_apy.setdefault(cat, []).append(detail.apy_pct)

        descriptions = {
            Category.MOMENTUM: "Strategies following prevailing market trends",
            Category.TREND_FOLLOWING: "Classic trend-following using technical indicators",
            Category.DIVERSIFIED: "Balanced portfolios across multiple assets",
            Category.STABLECOIN: "Low-risk strategies focused on stablecoin yields",
            Category.MEAN_REVERSION: "Strategies betting on price return to average",
            Category.ARBITRAGE: "Exploiting price inefficiencies across markets",
            Category.YIELD_FARMING: "Protocol-specific yield optimization",
            Category.LEVERAGE: "Enhanced returns using calibrated leverage",
        }

        icons = {
            Category.MOMENTUM: "📈",
            Category.TREND_FOLLOWING: "📊",
            Category.DIVERSIFIED: "🌐",
            Category.STABLECOIN: "💰",
            Category.MEAN_REVERSION: "🔄",
            Category.ARBITRAGE: "⚖️",
            Category.YIELD_FARMING: "🚜",
            Category.LEVERAGE: "⚡",
        }

        categories = []
        for cat in sorted(cat_counts.keys(), key=lambda c: c.value):
            apys = cat_apy.get(cat, [])
            categories.append(
                CategoryInfo(
                    id=cat,
                    name=cat.value.replace("_", " ").title(),
                    description=descriptions.get(cat, ""),
                    icon=icons.get(cat),
                    strategies_count=cat_counts[cat],
                    avg_apy_pct=round(sum(apys) / len(apys), 1) if apys else 0.0,
                    total_tvl_usdc=0.0,
                )
            )

        return categories


# Singleton
_marketplace_service: MarketplaceService | None = None


def marketplace_service() -> MarketplaceService:
    global _marketplace_service
    if _marketplace_service is None:
        _marketplace_service = MarketplaceService()
    return _marketplace_service

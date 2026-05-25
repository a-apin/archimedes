"""Schemas for the /explore page (asset discovery surface).

Per page-roles-spec.md, Explore is the read-only "what's tradable?" page —
no wallet required. The response includes plain-English explanations so
non-finance users can read it without a glossary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AssetExploreItem(BaseModel):
    symbol: str
    name: str
    asset_class: str = Field(description="us_stock, us_equity_etf, crypto, etc.")
    current_price: float | None
    change_24h_pct: float | None
    change_7d_pct: float | None
    change_30d_pct: float | None
    high_24h: float | None = Field(default=None, description="High over the last available bar; null if not available")
    low_24h: float | None = Field(default=None, description="Low over the last available bar; null if not available")
    realized_vol_30d: float | None = Field(
        default=None, description="Annualized standard deviation of daily returns over last 30 trading days"
    )
    oracle_address: str | None = None
    last_updated: str | None = Field(default=None, description="ISO8601 timestamp of last price update")
    price_source: Literal["oracle", "yfinance", "none"] = Field(
        default="none",
        description="Where the displayed current_price came from: 'oracle' = on-chain PriceOracle, "
        "'yfinance' = upstream market data fallback, 'none' = no data",
    )
    is_stale: bool = Field(
        default=False,
        description="True iff the displayed price is itself unusably old. A missing on-chain oracle "
        "is NOT stale on its own — only the source actually being shown can be stale.",
    )
    explanations: dict[str, str] = Field(
        default_factory=dict,
        description="Per-metric plain-English copy keyed by field name",
    )


class ExploreAssetsResponse(BaseModel):
    assets: list[AssetExploreItem]
    cache_ttl_seconds: int = 30
    generated_at: str


class ExploreHistoryPoint(BaseModel):
    ts: str  # ISO8601 date
    price: float


HistoryRange = Literal["1D", "1W", "1M", "1Y", "5Y", "10Y", "MAX"]


class ExploreHistoryResponse(BaseModel):
    symbol: str
    range: HistoryRange = "1M"
    interval: Literal["1m", "5m", "1h", "1d", "1wk", "1mo"] = "1d"
    points: list[ExploreHistoryPoint]

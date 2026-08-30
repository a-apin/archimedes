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
    change_window_hours: float | None = Field(
        default=None,
        description="Hours actually spanned by change_24h_pct — the elapsed time between the "
        "last two bars. Null when it cannot be determined (fewer than two bars, or an "
        "unparseable index).",
    )
    change_window_label: str | None = Field(
        default=None,
        description="Short honest label for that window: '24h' when the last two bars are a "
        "day apart, otherwise '3d' / '4d' etc. change_24h_pct is a one-bar change, and one bar "
        "is 24 hours only on a 24/7 feed — a Friday-to-Monday equity pair spans 72 (#1378). "
        "Null means the window is unknown; render it as an unspecific 'prev close' rather than "
        "falling back to '24h'.",
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
    rejected_fields: list[str] = Field(
        default_factory=list,
        description="Field names (e.g. 'change_24h_pct', 'realized_vol_30d') whose computed "
        "value was actively suppressed as arithmetically implausible (#1322 — a bad tick / "
        "decimal-placement error in the upstream feed), distinct from a field that is null "
        "because there isn't enough history yet. The honest-absence mechanism this item's "
        "is_stale/price_source already carry is about the displayed *price*; this discloses "
        "suppression of a *derived* stat on an otherwise fresh, correctly-sourced price.",
    )


class ExploreAssetsResponse(BaseModel):
    assets: list[AssetExploreItem]
    cache_ttl_seconds: int = 30
    generated_at: str
    universe_size: int = Field(
        default=0,
        description="Total size of the deploy-eligible SSOT universe this listing covers "
        "(archimedes.universe.ON_CHAIN_SYNTHS — same set as the Generate picker)",
    )
    priced_count: int = Field(
        default=0,
        description="How many assets carry a non-null current_price in this response. "
        "priced_count < universe_size means an honest partial result (cold caches / "
        "fetch budget exhausted); coverage converges on subsequent requests.",
    )


class ExploreHistoryPoint(BaseModel):
    ts: str  # ISO8601 date
    price: float


HistoryRange = Literal["1D", "1W", "1M", "1Y", "5Y", "10Y", "MAX"]


class ExploreHistoryResponse(BaseModel):
    symbol: str
    range: HistoryRange = "1M"
    interval: Literal["1m", "5m", "1h", "1d", "1wk", "1mo"] = "1d"
    points: list[ExploreHistoryPoint]

"""Postgres cache of vendor daily close bars (#775 / #1218 vendor seam).

One row per (symbol, trade_date). ``symbol`` is the VENDOR ticker (e.g.
``"SPY"``, ``"^GSPC"``) rather than our internal synth symbol (e.g. ``"sSPY"``)
— the cache is keyed on the thing that's actually fetched from the market-data
provider, so multiple synths that happen to share an underlying ticker share
one cached row instead of duplicating it.

Populated read-through by ``archimedes.services.market_data_provider``'s
``CachingMarketDataProvider``: a request for daily bars checks this table
first; a coverage/freshness miss fetches the full requested window from the
active ``MarketDataProvider`` (default yfinance) and writes it here. Shape
modeled on ``daily_returns_store.py``'s ``StrategyDailyReturn`` (add/update by
natural key, unique + indexed).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from archimedes.models.chat import Base


class AssetDailyBar(Base):
    """One daily OHLC(V) bar for one vendor ticker, cached from the active
    market-data provider (``archimedes.services.market_data_provider``).

    Re-fetching the same (symbol, trade_date) updates the row in place
    (source + fetched_at move forward) rather than accumulating duplicates —
    the cache reflects the vendor's latest-known value for that trading day,
    not a history of revisions.
    """

    __tablename__ = "asset_daily_bars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)

    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Which MARKET_DATA_PROVIDER wrote this row (e.g. "yfinance") — provenance
    # for the vendor seam; lets a future multi-vendor cache tell rows apart.
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # When this row was (last) written — the cache-freshness clock. Distinct
    # from trade_date: a bar for a date weeks ago can be freshly re-verified
    # today, and a bar fetched today is trade_date=today but stamped "just now".
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", name="uq_asset_daily_bars_symbol_trade_date"),
        Index("ix_asset_daily_bars_symbol", "symbol"),
    )

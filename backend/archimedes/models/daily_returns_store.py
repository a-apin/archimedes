"""DB-backed store for per-strategy daily-return series (#774).

Replaces the committed ``analytics-engine/strategies/daily_returns/*.json``
files as the data source for the library-level PBO (#546,
``rigor_evaluator.compute_library_pbo``). One row per (stem, date)
observation; ``data_vintage`` carries the same per-run provenance string each
JSON file used to carry once for its whole series.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from archimedes.models.chat import Base


class StrategyDailyReturn(Base):
    """One daily-return observation for one strategy, feeding the library-PBO
    cross-section (``rigor_evaluator.load_daily_returns_store``).

    Add-only per stem, same law as the JSON store it replaces: a fresh
    measurement for a stem writes a full new set of (date, daily_return) rows
    for that stem rather than mutating existing ones in place. Re-measuring a
    stem (a new ``data_vintage``) replaces that stem's rows wholesale — see
    the ingestion path this table is populated from.
    """

    __tablename__ = "strategy_daily_returns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stem: Mapped[str] = mapped_column(String(128), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    daily_return: Mapped[float] = mapped_column(Float, nullable=False)
    # Same semantics as the JSON records' data_vintage: the run date of the
    # backtest that produced this stem's series. All rows for a given stem
    # share the same value (one measurement run per stem).
    data_vintage: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("stem", "date", name="uq_strategy_daily_returns_stem_date"),
        Index("ix_strategy_daily_returns_stem", "stem"),
    )

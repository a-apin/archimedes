"""DB-backed store for per-strategy backtest summary metrics.

Replaces the committed ``analytics-engine/strategies/backtest_fixtures.json``
file as the data source ``strategy_provider._load_fixtures`` layers onto each
curated strategy's passport (Sharpe, DSR, PBO, Kelly, etc.). One row per
strategy stem — unlike ``daily_returns_store.StrategyDailyReturn`` (one row
per (stem, date) observation), this table is a wide summary snapshot, so
``stem`` is the primary key directly rather than an autoincrement id plus a
unique constraint.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from archimedes.models.chat import Base


class StrategyBacktestFixture(Base):
    """One strategy's summary backtest metrics, feeding the passport display
    fields ``strategy_provider._to_strategy`` reads via ``fx.get(...)``.

    Replaced wholesale per stem on import (see
    ``backend/scripts/import_backtest_fixtures.py``) — the same "add-only,
    explicit re-measurement replaces" law the JSON file was curated under
    (see ``analytics-engine/scripts/regen_fixtures.py``).
    """

    __tablename__ = "strategy_backtest_fixtures"

    stem: Mapped[str] = mapped_column(String(128), primary_key=True)
    n_obs_daily: Mapped[int] = mapped_column(Integer, nullable=False)
    sharpe_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    sortino_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    max_drawdown: Mapped[float] = mapped_column(Float, nullable=False)
    cagr: Mapped[float] = mapped_column(Float, nullable=False)
    calmar_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_holding_period_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    correlation_to_spy: Mapped[float] = mapped_column(Float, nullable=False)
    correlation_to_btc: Mapped[float | None] = mapped_column(Float, nullable=True)
    out_of_sample_sharpe: Mapped[float] = mapped_column(Float, nullable=False)
    look_ahead_audit_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    backtest_engine: Mapped[str] = mapped_column(String(64), nullable=False)
    transaction_cost_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    backtest_start: Mapped[str] = mapped_column(String(32), nullable=False)
    backtest_end: Mapped[str] = mapped_column(String(32), nullable=False)
    paper_claimed_sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    paper_claimed_cagr: Mapped[float | None] = mapped_column(Float, nullable=True)
    paper_claimed_max_dd: Mapped[float | None] = mapped_column(Float, nullable=True)
    deflated_sharpe_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    dsr_p_value: Mapped[float] = mapped_column(Float, nullable=False)
    dsr_convention: Mapped[str] = mapped_column(String(16), nullable=False)
    num_trials_in_selection: Mapped[int] = mapped_column(Integer, nullable=False)
    pbo_score: Mapped[float] = mapped_column(Float, nullable=False)
    passes_rigor_gate: Mapped[bool] = mapped_column(Boolean, nullable=False)
    kelly_fraction: Mapped[float] = mapped_column(Float, nullable=False)


# The full set of JSON-record keys this table mirrors, in the exact shape
# ``strategy_provider._load_fixtures_from_db`` returns per stem. Single source
# for both the DB→dict projection and the JSON→DB import so the two can never
# silently drift apart.
FIXTURE_FIELDS: tuple[str, ...] = (
    "n_obs_daily",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown",
    "cagr",
    "calmar_ratio",
    "win_rate",
    "profit_factor",
    "total_trades",
    "avg_holding_period_days",
    "correlation_to_spy",
    "correlation_to_btc",
    "out_of_sample_sharpe",
    "look_ahead_audit_passed",
    "backtest_engine",
    "transaction_cost_bps",
    "backtest_start",
    "backtest_end",
    "paper_claimed_sharpe",
    "paper_claimed_cagr",
    "paper_claimed_max_dd",
    "deflated_sharpe_ratio",
    "dsr_p_value",
    "dsr_convention",
    "num_trials_in_selection",
    "pbo_score",
    "passes_rigor_gate",
    "kelly_fraction",
)

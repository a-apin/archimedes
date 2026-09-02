from __future__ import annotations

import copy
from datetime import date

import pytest
from archimedes.models.backtest import BacktestResult
from archimedes.models.backtest_store import BacktestResultRecord
from archimedes.models.chat import Base
from archimedes.services.backtest_mapper import canonical_artifact_hash
from archimedes.services.backtest_repository import (
    get_all_daily_returns,
    insert_backtest_if_missing,
    latest_backtests_by_strategy,
)
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def _sample_result(strategy_id: str, sharpe: float) -> BacktestResult:
    return BacktestResult(
        strategy_id=strategy_id,
        sharpe_ratio=sharpe,
        sortino_ratio=0.5,
        max_drawdown=0.2,
        cagr=0.1,
        calmar_ratio=0.5,
        win_rate=0.5,
        profit_factor=1.2,
        total_trades=10,
        avg_holding_period_days=5.0,
        correlation_to_spy=0.3,
        correlation_to_btc=0.1,
        equity_curve=[100000, 101000],
        monthly_returns=[0.01],
        backtest_start=date(2020, 1, 1),
        backtest_end=date(2020, 12, 31),
        backtest_engine="backtrader",
    )

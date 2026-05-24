from __future__ import annotations

from datetime import date

from archimedes.models.backtest import BacktestResult
from archimedes.models.backtest_store import BacktestResultRecord
from archimedes.models.chat import Base
from archimedes.services.backtest_repository import (
    insert_backtest_if_missing,
    latest_backtests_by_strategy,
)
from sqlalchemy import create_engine
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
    )


def test_insert_backtest_is_idempotent_on_content_hash() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        row1, inserted1 = insert_backtest_if_missing(
            session,
            strategy_id="s1",
            content_hash="abc123",
            result=_sample_result("s1", sharpe=0.7),
            run_id="run1",
        )
        row1_id = row1.id
        session.commit()

    with SessionLocal() as session:
        row2, inserted2 = insert_backtest_if_missing(
            session,
            strategy_id="s1",
            content_hash="abc123",
            result=_sample_result("s1", sharpe=0.9),
            run_id="run2",
        )
        session.commit()

        rows = session.query(BacktestResultRecord).all()
        assert inserted1 is True
        assert inserted2 is False
        assert row1_id == row2.id
        assert len(rows) == 1


def test_latest_backtests_by_strategy_picks_newest_row() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        insert_backtest_if_missing(
            session,
            strategy_id="s1",
            content_hash="h1",
            result=_sample_result("s1", sharpe=0.5),
            run_id="run1",
        )
        insert_backtest_if_missing(
            session,
            strategy_id="s1",
            content_hash="h2",
            result=_sample_result("s1", sharpe=0.8),
            run_id="run2",
        )
        insert_backtest_if_missing(
            session,
            strategy_id="s2",
            content_hash="h3",
            result=_sample_result("s2", sharpe=1.1),
            run_id="run3",
        )
        session.commit()

        latest = latest_backtests_by_strategy(session, ["s1", "s2"])

    assert latest["s1"].sharpe_ratio == 0.8
    assert latest["s2"].sharpe_ratio == 1.1

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
        # Required: insert_backtest_if_missing refuses an unattributed row, so
        # a row can always be traced to the engine that produced it.
        backtest_engine="backtrader",
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
            source_pipeline="test",
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
            source_pipeline="test",
        )
        session.commit()

        rows = session.query(BacktestResultRecord).all()
        assert inserted1 is True
        assert inserted2 is False
        assert row1_id == row2.id
        assert len(rows) == 1


def test_insert_backtest_if_missing_skips_duplicate_run_with_only_run_id_and_timestamp_diff() -> None:
    """End-to-end reproduction of issue #1347's production symptom: two
    "refreshes" of the SAME strategy content (identical metrics, differing
    only in run_id/timestamp_utc — exactly what a scheduled re-run of
    run_backtests.py produces on a container restart) must collapse to ONE
    row via content_hash, not two.

    Mutation-proven: with canonical_artifact_hash reverted to hash the whole
    payload (no volatile-key exclusion), `hash_a != hash_b` below and this
    test fails with `inserted2 is True` / `len(rows) == 2`. See the PR body
    for the revert/re-apply transcript.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    payload_a = {
        "run_id": "20260518T223743Z",
        "timestamp_utc": "2026-05-18T22:37:43.964677+00:00",
        "strategy": {"backtest_code_hash": "sha256:deadbeef"},
        "assumptions": {"transaction_cost_bps": 10},
        "results": [{"operation": "SPY", "metrics": {"sharpe_ratio": 0.71, "total_trades": 12}}],
    }
    payload_b = copy.deepcopy(payload_a)
    payload_b["run_id"] = "20260519T010101Z"
    payload_b["timestamp_utc"] = "2026-05-19T01:01:01.000000+00:00"

    hash_a = canonical_artifact_hash(payload_a)
    hash_b = canonical_artifact_hash(payload_b)
    assert hash_a == hash_b, "two runs over identical content must produce equal canonical hashes"

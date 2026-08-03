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
            source_pipeline="test",
        )
        insert_backtest_if_missing(
            session,
            strategy_id="s1",
            content_hash="h2",
            result=_sample_result("s1", sharpe=0.8),
            run_id="run2",
            source_pipeline="test",
        )
        insert_backtest_if_missing(
            session,
            strategy_id="s2",
            content_hash="h3",
            result=_sample_result("s2", sharpe=1.1),
            run_id="run3",
            source_pipeline="test",
        )
        session.commit()

        latest = latest_backtests_by_strategy(session, ["s1", "s2"])

    assert latest["s1"].sharpe_ratio == 0.8
    assert latest["s2"].sharpe_ratio == 1.1


def test_omitted_source_git_sha_stamps_the_running_build() -> None:
    """Omitting the argument means "stamp the running build"."""
    import os
    from unittest.mock import patch

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    with patch.dict(os.environ, {"ARCHIMEDES_GIT_SHA": "deadbeefcafe"}), SessionLocal() as session:
        row, _ = insert_backtest_if_missing(
            session,
            strategy_id="s1",
            content_hash="omitted",
            result=_sample_result("s1", sharpe=0.7),
            run_id="run1",
            source_pipeline="test",
        )
        assert row.source_git_sha == "deadbeefcafe"


def test_explicit_none_source_git_sha_persists_null_even_with_env_set() -> None:
    """An EXPLICIT None means "the producing commit is not knowable" and must
    persist NULL — even when ARCHIMEDES_GIT_SHA is set.

    This is the case that matters: seed_backtests_from_artifacts.py replays
    artifacts produced by some earlier, unrecorded commit. If explicit-unknown
    collapsed into omission, every replayed row would claim the CURRENT deploy
    SHA — a confident, false provenance claim in the very column added to make
    provenance trustworthy. A plausible wrong SHA is worse than no SHA.

    Note the env var is deliberately SET here. With it unset the test would
    pass whether or not the sentinel exists, and would prove nothing.
    """
    import os
    from unittest.mock import patch

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    with patch.dict(os.environ, {"ARCHIMEDES_GIT_SHA": "deadbeefcafe"}), SessionLocal() as session:
        row, _ = insert_backtest_if_missing(
            session,
            strategy_id="s2",
            content_hash="explicit-none",
            result=_sample_result("s2", sharpe=0.7),
            run_id="run2",
            source_pipeline="seed_from_artifacts",
            source_git_sha=None,
        )
        assert row.source_git_sha is None


def test_replay_script_passes_explicit_none() -> None:
    """Wiring guard: the replay script must pass ``source_git_sha=None``.

    Asserted against the parsed AST, not a substring of the source: a raw
    ``"source_git_sha=None" in inspect.getsource(...)`` check would still pass
    if the real keyword argument were deleted and the text survived in a
    comment or docstring. That is the same vacuous-guard shape this repo keeps
    producing, so the guard itself is written to be unfoolable.
    """
    import ast
    import inspect

    from archimedes.scripts import seed_backtests_from_artifacts as mod

    tree = ast.parse(inspect.getsource(mod))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "insert_backtest_if_missing"
    ]
    assert calls, "seed_backtests_from_artifacts no longer calls insert_backtest_if_missing"

    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "source_git_sha" in kw, "replay must pass source_git_sha explicitly, not omit it"
        assert isinstance(kw["source_git_sha"], ast.Constant) and kw["source_git_sha"].value is None, (
            "replay must pass source_git_sha=None (explicit unknown), not the running build's SHA"
        )

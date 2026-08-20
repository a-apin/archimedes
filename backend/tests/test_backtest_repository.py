from __future__ import annotations

from datetime import date

from archimedes.models.backtest import BacktestResult
from archimedes.models.backtest_store import BacktestResultRecord
from archimedes.models.chat import Base
from archimedes.services.backtest_repository import (
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


# ── Bounded reader (2026-08-19 OOM regression guard) ─────────────────────────
#
# `latest_backtests_by_strategy` used to `.all()` every row for every strategy
# and reduce in Python. Because `canonical_artifact_hash` includes `run_id` (a
# timestamp), `insert_backtest_if_missing` never dedupes, so the table grows by
# ~30 rows on every refresh cycle forever. Each row carries a ~489 KB
# artifact_json, and the old read cost a measured 0.58 MB of peak RSS per
# persisted row — the staircase that pushed the backend past its 3072 MB task
# budget on 2026-08-19. These tests pin the "resolve in SQL, hydrate only the
# winners" contract.


def _seeded_session(n_strategies: int, n_cycles: int):
    """A session over a DB holding n_strategies x n_cycles rows."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    ids = [f"strat_{i:03d}" for i in range(n_strategies)]
    for cycle in range(n_cycles):
        for sid in ids:
            # sharpe encodes the cycle so we can assert WHICH row came back.
            insert_backtest_if_missing(
                session,
                strategy_id=sid,
                content_hash=f"{sid}-cycle-{cycle}",
                result=_sample_result(sid, float(cycle)),
                source_pipeline="run_backtests",
                run_id=f"run-{cycle}",
            )
    session.commit()
    return session, ids


def test_latest_backtests_returns_the_newest_row_per_strategy() -> None:
    session, ids = _seeded_session(n_strategies=5, n_cycles=8)
    try:
        latest = latest_backtests_by_strategy(session, ids)
        assert set(latest) == set(ids)
        # Cycle 7 was inserted last, so it must be the one returned for each id.
        for sid in ids:
            assert latest[sid].sharpe_ratio == 7.0, sid
    finally:
        session.close()


def test_latest_backtests_hydrates_only_one_row_per_strategy() -> None:
    """THE GUARD: 34 strategies x 20 cycles = 680 rows on disk must still
    hydrate exactly 34 ORM objects. The old `.all()` implementation loaded all
    680 — this assertion is what makes that regression impossible to reintroduce.
    """
    n_strategies, n_cycles = 34, 20
    session, ids = _seeded_session(n_strategies=n_strategies, n_cycles=n_cycles)
    try:
        total_rows = session.query(BacktestResultRecord).count()
        assert total_rows == n_strategies * n_cycles, "seed did not produce duplicates"

        session.expunge_all()

        # Count hydration via the mapper "load" event, which fires once per row
        # the ORM materialises from the DB. Counting session.identity_map instead
        # does NOT work: the identity map holds WEAK references, so rows the old
        # implementation loaded and then discarded are garbage-collected before
        # the assertion runs and the test passes against the unfixed code.
        loaded: list[str] = []

        def _count(target, _context) -> None:
            loaded.append(target.strategy_id)

        event.listen(BacktestResultRecord, "load", _count)
        try:
            latest = latest_backtests_by_strategy(session, ids)
        finally:
            event.remove(BacktestResultRecord, "load", _count)

        assert len(latest) == n_strategies
        assert len(loaded) == n_strategies, (
            f"hydrated {len(loaded)} BacktestResultRecord rows to return {n_strategies}; "
            f"the reader is loading the whole table ({total_rows} rows on disk)"
        )
    finally:
        session.close()


def test_latest_backtests_ignores_unrequested_strategies() -> None:
    session, ids = _seeded_session(n_strategies=6, n_cycles=3)
    try:
        subset = ids[:2]
        latest = latest_backtests_by_strategy(session, subset)
        assert set(latest) == set(subset)
    finally:
        session.close()


def test_latest_backtests_empty_ids_short_circuits() -> None:
    session, _ = _seeded_session(n_strategies=2, n_cycles=2)
    try:
        assert latest_backtests_by_strategy(session, []) == {}
    finally:
        session.close()

from __future__ import annotations

import copy
from datetime import date

from archimedes.models.backtest import BacktestResult
from archimedes.models.backtest_store import BacktestResultRecord
from archimedes.models.chat import Base
from archimedes.services.backtest_mapper import canonical_artifact_hash
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

    with SessionLocal() as session:
        row1, inserted1 = insert_backtest_if_missing(
            session,
            strategy_id="s1",
            content_hash=hash_a,
            result=_sample_result("s1", sharpe=0.71),
            run_id=payload_a["run_id"],
            source_pipeline="run_backtests",
        )
        session.commit()

        row2, inserted2 = insert_backtest_if_missing(
            session,
            strategy_id="s1",
            content_hash=hash_b,
            result=_sample_result("s1", sharpe=0.71),
            run_id=payload_b["run_id"],
            source_pipeline="run_backtests",
        )
        session.commit()

        rows = session.query(BacktestResultRecord).filter(BacktestResultRecord.strategy_id == "s1").all()

    assert inserted1 is True
    assert inserted2 is False, "the second (content-identical) refresh must be SKIPPED, not re-inserted"
    assert row1.id == row2.id
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


# ── Query SHAPE: the hot read must not select artifact_json (issue #1543) ──
#
# The row-count tests above pin how MANY rows this reader hydrates. They say
# nothing about how WIDE each row is, and that is the other half of the same
# defect: the outer query selected every column of the winners, so each
# returned row dragged its `artifact_json` blob (~349 KB/row by this repo's own
# verified 2026-08-19 averages, quoted in scripts/archive_backtest_results.py)
# across the wire on a path that never reads it. These tests pin the projection.


def _capture_sql(engine) -> tuple[list[str], object]:
    """Attach a before_cursor_execute listener; return (statements, detach)."""
    statements: list[str] = []

    def _on_exec(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _on_exec)

    def _detach() -> None:
        event.remove(engine, "before_cursor_execute", _on_exec)

    return statements, _detach


def _selects_from_backtest_results(statements: list[str]) -> list[str]:
    """The captured statements that are SELECTs against backtest_results."""
    return [s for s in statements if s.lstrip().upper().startswith("SELECT") and "backtest_results" in s]


def _seeded_session_with_artifacts(n_strategies: int, n_cycles: int, artifact_bytes: int = 4096):
    """Like _seeded_session, but every row carries a non-empty artifact_json.

    A NULL blob would make "the reader does not transfer it" trivially true, so
    the fixture gives the deferred column real content to omit.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    ids = [f"strat_{i:03d}" for i in range(n_strategies)]
    blob = '{"results": [{"metrics": {"pad": "' + ("x" * artifact_bytes) + '"}}]}'
    for cycle in range(n_cycles):
        for sid in ids:
            insert_backtest_if_missing(
                session,
                strategy_id=sid,
                content_hash=f"{sid}-cycle-{cycle}",
                result=_sample_result(sid, float(cycle)),
                source_pipeline="run_backtests",
                run_id=f"run-{cycle}",
                artifact_json=blob,
            )
    session.commit()
    return session, SessionLocal, ids


def test_latest_backtests_does_not_select_artifact_json() -> None:
    """THE GUARD (#1543): the hot read must not put artifact_json in its SELECT.

    Reverting the `.options(defer(...))` in latest_backtests_by_strategy makes
    this fail — the ORM renders `backtest_results.artifact_json` in the column
    list again.
    """
    session, _SessionLocal, ids = _seeded_session_with_artifacts(n_strategies=6, n_cycles=3)
    try:
        session.expunge_all()
        statements, detach = _capture_sql(session.get_bind())
        try:
            latest = latest_backtests_by_strategy(session, ids)
        finally:
            detach()

        assert set(latest) == set(ids)

        selects = _selects_from_backtest_results(statements)
        # Non-vacuity: if the listener never fired (wrong engine, wrong event)
        # the "artifact_json absent" assertion below would pass over an empty
        # list and guard nothing. Pin that a real, recognisable ORM projection
        # was observed before trusting its contents.
        assert selects, "captured no SELECT against backtest_results — the listener did not fire"
        assert any("backtest_results.sharpe_ratio" in s for s in selects), (
            "captured no hydrating SELECT with a column list; "
            f"the assertion below would be vacuous. statements={selects!r}"
        )

        offenders = [s for s in selects if "artifact_json" in s]
        assert not offenders, (
            "latest_backtests_by_strategy selected artifact_json "
            f"(~349 KB/row) on a path that never reads it: {offenders!r}"
        )
    finally:
        session.close()


def test_sql_capture_detects_artifact_json_when_it_is_present() -> None:
    """ADVERSARIAL CONTROL: feed the guard a query that SHOULD fail it.

    The un-projected query below is exactly what latest_backtests_by_strategy
    emitted before the fix. If `_selects_from_backtest_results` + the substring
    check could not see artifact_json here, the test above would be a
    tautology that passes against the unfixed code too.
    """
    session, _SessionLocal, ids = _seeded_session_with_artifacts(n_strategies=3, n_cycles=2)
    try:
        session.expunge_all()
        statements, detach = _capture_sql(session.get_bind())
        try:
            # No .options(defer(...)) — the pre-fix shape.
            session.query(BacktestResultRecord).filter(BacktestResultRecord.strategy_id.in_(ids)).all()
        finally:
            detach()

        selects = _selects_from_backtest_results(statements)
        assert selects, "captured no SELECT against backtest_results"
        assert any("artifact_json" in s for s in selects), (
            "the detector failed to see artifact_json in an un-projected SELECT, "
            "so it cannot prove the projected one omits it"
        )
    finally:
        session.close()


def test_deferred_artifact_json_does_not_change_what_callers_read() -> None:
    """Projection is a transfer-size change, not a data change.

    Every field to_backtest_result() exposes must survive, equity_curve
    (deliberately NOT deferred — api/risk_routes.py consumes it off the
    provider cache) included, and reading them must emit no follow-up SQL.
    """
    session, _SessionLocal, ids = _seeded_session_with_artifacts(n_strategies=4, n_cycles=3)
    try:
        session.expunge_all()
        latest = latest_backtests_by_strategy(session, ids)

        statements, detach = _capture_sql(session.get_bind())
        try:
            results = {sid: row.to_backtest_result() for sid, row in latest.items()}
        finally:
            detach()

        assert not _selects_from_backtest_results(statements), (
            "to_backtest_result() triggered a lazy load — the deferred column "
            "is being touched on the hot path after all"
        )
        for sid in ids:
            res = results[sid]
            assert res.strategy_id == sid
            assert res.sharpe_ratio == 2.0  # newest cycle
            assert res.equity_curve == [100000, 101000]
            assert res.monthly_returns == [0.01]
            assert res.backtest_engine == "backtrader"
            assert res.backtest_start == date(2020, 1, 1)
    finally:
        session.close()


def test_deferred_artifact_json_is_still_reachable_in_session() -> None:
    """Deferral must not make the column unreadable — only unfetched by default.

    `defer` (not `defer(..., raiseload=True)`) is the deliberate choice: a
    caller that does touch the attribute inside the session gets one extra
    SELECT rather than an exception that strategy_provider._load_backtests
    would swallow into "no backtests at all".
    """
    session, _SessionLocal, ids = _seeded_session_with_artifacts(n_strategies=2, n_cycles=2)
    try:
        session.expunge_all()
        latest = latest_backtests_by_strategy(session, ids)
        row = latest[ids[0]]
        assert row.artifact_json is not None
        assert "results" in row.artifact_json
    finally:
        session.close()


def test_provider_backtest_load_does_not_select_artifact_json(tmp_path, monkeypatch) -> None:
    """The production hot path, not just the repository helper.

    LocalStrategyProvider._load_backtests is what runs on every
    default_provider() construction (issue #1543's amplifier). Mocked at the
    DB boundary only — the session factory — so the real provider method,
    the real repository query and the real ORM mapping all execute.
    """
    from archimedes.services import strategy_provider as sp

    session, SessionLocal, ids = _seeded_session_with_artifacts(n_strategies=5, n_cycles=3)
    engine = session.get_bind()
    session.close()

    monkeypatch.setattr(sp, "get_session", SessionLocal)

    # A non-existent strategies dir makes refresh() return early without any DB
    # access, so construction cannot pollute the capture below.
    provider = sp.LocalStrategyProvider(tmp_path / "no-such-strategies-dir")

    statements, detach = _capture_sql(engine)
    try:
        loaded = provider._load_backtests(ids)
    finally:
        detach()

    assert set(loaded) == set(ids)
    selects = _selects_from_backtest_results(statements)
    assert selects, "provider hot path emitted no SELECT against backtest_results"
    assert any("backtest_results.sharpe_ratio" in s for s in selects), f"no hydrating SELECT captured: {selects!r}"
    assert not [s for s in selects if "artifact_json" in s], (
        f"provider hot path still transfers artifact_json: {[s for s in selects if 'artifact_json' in s]!r}"
    )

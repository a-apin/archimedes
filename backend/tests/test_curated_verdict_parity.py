"""Every endpoint that reports a verdict for one strategy id reports the SAME one.

Issue #1746, reproduced 3x in the 2026-09-01 agent-mcp dogfood:

    GET /api/strategies/1f9cfe96…            → pass  / passes_rigor_gate: true / Sharpe 0.406
    GET /api/strategies/passports/1f9cfe96…  → candidate / false               / Sharpe null

``1f9cfe96d5048fec9cd22cd888a7d797`` is a CURATED strategy —
``analytics-engine/strategies/harvey_2018_volatility_targeting.py``. The two
endpoints read two different things:

* the detail route took the curated branch, ran a LIVE cohort rigor gate per
  request, promoted the file's ``STATUS = "candidate"`` to ``validated`` off that
  live pass, and resolved its Sharpe through ``real_* → persisted backtest →
  stub`` (harvey has no fixture row, so link 2: ``0.406``);
* ``/passports/{id}`` was a pure read of the ``strategy_passports`` row, whose
  ``passes_rigor_gate`` was a hardcoded ``False`` placeholder and whose
  ``sharpe_ratio`` had been filled from link 1 alone — ``NULL``.

A constant-``False`` column agrees with every failing and pending strategy and
MUST disagree with any the live gate passes, which is why only the flagship pass
case showed it.

PR-A (#1792) made the passport row the verdict of record for GENERATED
strategies. This is PR-B: the curated grade is produced by an operator-run job
(``services.curated_grading``, called from ``scripts/run_backtests.py`` and
``scripts/grade_curated.py``), stored on the same row, and every surface reads
it. ``docs/adr/rigor-verdict-of-record.md``.

Every test here names the mutation that reddens it. Hermetic: tmp sqlite, real
curated files on disk, no network.

Run:
  /opt/homebrew/Caskroom/mambaforge/base/envs/archimedes/bin/pytest -q \\
      -p no:cacheprovider backend/tests/test_curated_verdict_parity.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

# The id from the issue, in full. Pinned as a literal AND re-derived from the
# strategy file below, so the constant cannot rot into a meaningless hex string.
REPRO_ID = "1f9cfe96d5048fec9cd22cd888a7d797"
REPRO_STEM = "harvey_2018_volatility_targeting"

# The Sharpe the issue reported on the detail route, from the persisted backtest.
REPRO_SHARPE = 0.406

# A clean strategy snippet that passes the AST look-ahead audit.
_CLEAN_CODE = "def init(self):\n    self.sma = 0\n"


def _passing_series(seed: int = 0, n: int = 500) -> list[float]:
    """A return series engineered to clear the real gate."""
    return np.random.default_rng(seed).normal(0.0015, 0.004, n).tolist()


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    """A per-test SQLite file, with the engine and sessionmaker rebound.

    Setting ``DATABASE_URL`` alone would not isolate anything: ``archimedes.db``
    resolves it once, at import time.
    """
    import archimedes.db as db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = f"sqlite:///{tmp_path / 'curated_parity.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    eng = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, autocommit=False, autoflush=False))
    db.init_db()
    yield


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _insert_backtest(session, strategy_id: str, returns: list[float], sharpe: float) -> None:
    """Persist a real ``backtest_results`` row with a daily-returns artifact."""
    from archimedes.models.backtest import BacktestResult
    from archimedes.services.backtest_repository import insert_backtest_if_missing

    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1.0 + r))
    result = BacktestResult(
        strategy_id=strategy_id,
        sharpe_ratio=sharpe,
        sortino_ratio=0.51,
        max_drawdown=0.23,
        cagr=0.08,
        calmar_ratio=0.35,
        win_rate=0.54,
        profit_factor=1.1,
        total_trades=41,
        avg_holding_period_days=7.0,
        correlation_to_spy=0.62,
        correlation_to_btc=0.0,
        equity_curve=equity,
        backtest_engine="backtrader",
    )
    artifact_json = json.dumps({"results": [{"metrics": {"daily_returns": returns}}]})
    insert_backtest_if_missing(
        session,
        strategy_id=strategy_id,
        content_hash=hashlib.sha256((strategy_id + artifact_json).encode()).hexdigest(),
        result=result,
        artifact_json=artifact_json,
        source_pipeline="test",
    )


def _seed_the_reproduction(monkeypatch, *, grade: bool):
    """Put the issue's exact state in the DB, optionally graded.

    A persisted backtest for the reproduction id (Sharpe 0.406, a return series
    the real gate passes) plus one more graded strategy so the cohort has two
    members. Returns the provider.
    """
    from archimedes.api._route_helpers import strategy_provider
    from archimedes.db import get_session
    from archimedes.services import curated_grading

    # Resolve ids from a throwaway provider BEFORE the cached one is built, so
    # the singleton's boot-time backtest memo sees the rows we insert.
    from archimedes.services.strategy_provider import default_provider

    monkeypatch.setattr(curated_grading, "_load_strategy_code_safe", lambda s: _CLEAN_CODE)

    strategy_provider.cache_clear()
    scratch = default_provider()
    library = scratch.list_strategies()
    cohort_partner = next(s for s in library if s.id != REPRO_ID)

    with get_session() as session:
        _insert_backtest(session, REPRO_ID, _passing_series(0), REPRO_SHARPE)
        _insert_backtest(session, cohort_partner.id, _passing_series(1), 0.9)
        session.commit()

    # Rebuild the cached provider so its backtest map carries the rows above,
    # and so its passport sync writes the resolved display metrics.
    strategy_provider.cache_clear()
    provider = strategy_provider()

    if grade:
        with get_session() as session:
            curated_grading.grade_curated_library(session, provider=provider)
            session.commit()
    return provider


# ═══════════════════════════════════════════════════════════════════════════
# The id in the issue
# ═══════════════════════════════════════════════════════════════════════════


def test_the_reproduction_id_is_the_harvey_strategy():
    """``1f9cfe96…`` is ``harvey_2018_volatility_targeting.py``, re-derived.

    The issue truncates the id; this pins the whole thing to the file it names,
    by running the real id function over the real file. If the strategy's paper
    metadata or methodology text changes, its id changes and this test says so
    rather than letting the parity tests below quietly start asserting nothing.
    """
    from archimedes.services.strategy_provider import _hash_file, _read_module_constants, _strategy_id

    path = _repo_root() / "analytics-engine" / "strategies" / f"{REPRO_STEM}.py"
    assert path.exists(), f"the reproduction's strategy file is gone: {path}"
    metadata = _read_module_constants(path)
    assert _strategy_id(metadata, _hash_file(path)) == REPRO_ID
    assert str(metadata.get("STATUS")).lower() == "candidate", (
        "the reproduction is a CANDIDATE whose live gate passed — that mismatch "
        "between the file's status and the served one is half of what #1746 reported"
    )


# ═══════════════════════════════════════════════════════════════════════════
# The parity guard
# ═══════════════════════════════════════════════════════════════════════════

#: Keys both payloads publish that are NOT compared field-for-field, each with a
#: reason. Anything else the two share must be equal — so a new shared field is
#: covered automatically rather than needing this list extended.
_PARITY_EXEMPT = {
    # `status` is the PERSISTED lifecycle column on the passport (the `?status=`
    # filter queries it) and the SERVED card status on the detail route. Both are
    # published side by side and both are asserted below, under names that say
    # which is which — see `served_status`.
    "status",
}


@pytest.mark.asyncio
async def test_the_two_gate_endpoints_agree_on_the_reproduction_id(monkeypatch):
    """THE #1746 GUARD. ``GET /api/strategies/{id}`` vs ``/passports/{id}``.

    Compares EVERY key the two payloads share — id, methodology, universe, the
    four-state verdict, the boolean, and the metrics both publish
    (``sharpe_ratio``, ``sortino_ratio``, ``max_drawdown``) — not a hand-listed
    subset, so a field added to one payload cannot quietly escape the check.

    RED on the pre-fix behaviour, on this exact id: the detail route computed a
    live ``pass`` with Sharpe 0.406 while the passport row read ``false`` /
    ``null``.

    MUTATION 1: restore the live computation in ``_to_strategy_response``
    (``verdict, rigor_result = _live_verdict_and_result_for_one(s)``) — the
    booleans and the four-state come apart again.
    MUTATION 2: revert ``_sync_to_unified_table`` to ingesting the bare
    ``strategy`` instead of ``with_display_metrics(strategy, bt)`` — the passport
    row's Sharpe goes back to ``null`` while the detail route serves 0.406.
    """
    from archimedes.main import app

    _seed_the_reproduction(monkeypatch, grade=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        detail_resp = await client.get(f"/api/strategies/{REPRO_ID}")
        passport_resp = await client.get(f"/api/strategies/passports/{REPRO_ID}")

    assert detail_resp.status_code == 200, detail_resp.text
    assert passport_resp.status_code == 200, passport_resp.text
    detail = detail_resp.json()
    passport = passport_resp.json()

    # The reproduction only bites on a PASS — a constant-False column agrees with
    # every fail and every pending. If this is not a pass, the guard is vacuous.
    assert detail["rigor_gate_status"] == "pass", (
        f"fixture guard: the reproduction must reproduce the PASS case; got {detail['rigor_gate_status']}"
    )
    assert detail["sharpe_ratio"] == pytest.approx(REPRO_SHARPE)

    shared = (set(detail) & set(passport)) - _PARITY_EXEMPT
    assert {"rigor_gate_status", "passes_rigor_gate", "sharpe_ratio"} <= shared, (
        f"the parity guard lost one of the three fields the issue names; shared keys were {sorted(shared)}"
    )
    disagreements = {k: (detail[k], passport[k]) for k in sorted(shared) if detail[k] != passport[k]}
    assert not disagreements, (
        f"GET /api/strategies/{REPRO_ID} and GET /api/strategies/passports/{REPRO_ID} "
        f"disagree on {list(disagreements)}: {disagreements}"
    )

    # `status`: two names, one derivation, published together.
    assert passport["status"] == "candidate", "the passport publishes the PERSISTED lifecycle column"
    assert passport["served_status"] == "validated", "…and the card status the same stored verdict derives"
    assert detail["status"] == passport["served_status"]

    # Provenance proves the verdict came from a real gate run, not a placeholder.
    assert passport["graded_at"] is not None
    assert passport["gate_version"], "a stored verdict must name the gate that produced it"
    assert passport["cohort_n"] == 2, "graded against the two strategies with persisted returns"


@pytest.mark.asyncio
async def test_every_surface_that_reports_the_verdict_reports_the_same_one(monkeypatch):
    """Library list, detail, leaderboard and the passport, for one id.

    MUTATION: put the live cohort gate back into ``leaderboard_routes.
    _curated_cohort_responses``. Its numbers drift from the stored ones — the
    same two-answers-for-one-id shape, one surface over.
    """
    from archimedes.main import app

    _seed_the_reproduction(monkeypatch, grade=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/strategies/?limit=100")
        detail = await client.get(f"/api/strategies/{REPRO_ID}")
        board = await client.get("/api/leaderboard?limit=100")
        passport = await client.get(f"/api/strategies/passports/{REPRO_ID}")

    for resp in (listed, detail, board, passport):
        assert resp.status_code == 200, resp.text

    row = next(s for s in listed.json()["strategies"] if s["id"] == REPRO_ID)
    entry = next(e for e in board.json()["entries"] if e["id"] == REPRO_ID)
    body = detail.json()
    stored = passport.json()

    assert row["rigor_gate_status"] == body["rigor_gate_status"] == stored["rigor_gate_status"] == "pass"
    assert row["passes_rigor_gate"] == body["passes_rigor_gate"] == stored["passes_rigor_gate"] is True
    assert (
        row["sharpe_ratio"]
        == body["sharpe_ratio"]
        == stored["sharpe_ratio"]
        == entry["sharpe_ratio"]
        == pytest.approx(REPRO_SHARPE)
    )
    for key in ("deflated_sharpe_ratio", "dsr_p_value", "pbo_score", "out_of_sample_sharpe"):
        assert row[key] == body[key] == entry[key], f"{key} differs between the list, the detail route and the board"


@pytest.mark.asyncio
async def test_two_reads_of_the_same_id_cannot_drift(monkeypatch):
    """The issue's secondary finding: "Sharpe drifted between reads 37s apart".

    The cause was a per-process provider memo with no TTL against two ECS tasks —
    a live recompute presented as a persisted number. A stored answer cannot
    drift between two readers of one row, and the detail route now reads that
    row.

    MUTATION: serve ``metrics = resolve_display_metrics(s, bt)`` unconditionally
    in ``_to_strategy_response`` (drop the ``stored is not None`` branch). The
    number goes back to being resolved per process, and the guard below stops
    being a guarantee — this test still passes in one process, which is why the
    assertion that carries the weight is the one on the SOURCE, not the two
    reads.
    """
    from archimedes.api.strategies_routes import _to_strategy_response
    from archimedes.db import get_session
    from archimedes.main import app
    from archimedes.services.passport_loader import get_passport

    provider = _seed_the_reproduction(monkeypatch, grade=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get(f"/api/strategies/{REPRO_ID}")
        second = await client.get(f"/api/strategies/{REPRO_ID}")
    assert first.json()["sharpe_ratio"] == second.json()["sharpe_ratio"]

    # The claim under the guard: the served number IS the stored one. A reader in
    # another process, with another boot vintage of the provider memo, reads the
    # same row and therefore serves the same number.
    with get_session() as session:
        record = get_passport(session, REPRO_ID)
        served = _to_strategy_response(provider.get_strategy(REPRO_ID), record)
    assert served.sharpe_ratio == record.sharpe_ratio == pytest.approx(REPRO_SHARPE)
    assert served.rigor_gate_status == record.rigor_gate_status
    assert served.passes_rigor_gate == record.passes_rigor_gate


# ═══════════════════════════════════════════════════════════════════════════
# No recompute on read
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_detail_route_reads_pending_until_the_grading_job_runs(monkeypatch):
    """Persisted returns the gate WOULD pass, and no grade: the answer is
    ``pending`` on both endpoints.

    This is the direct assertion that the read path does not grade. It is also
    the honest state: nothing has graded this strategy, and ``pending`` says so.

    MUTATION: restore ``_live_verdict_and_result_for_one`` in
    ``_to_strategy_response``. The detail route answers ``pass`` while the
    passport still answers ``pending`` — #1746, exactly.
    """
    from archimedes.main import app

    _seed_the_reproduction(monkeypatch, grade=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        detail = await client.get(f"/api/strategies/{REPRO_ID}")
        passport = await client.get(f"/api/strategies/passports/{REPRO_ID}")

    assert detail.status_code == 200, detail.text
    assert detail.json()["rigor_gate_status"] == "pending"
    assert detail.json()["passes_rigor_gate"] is False
    assert detail.json()["status"] == "candidate", "no pass, so no promotion"
    assert passport.json()["rigor_gate_status"] == "pending"

    # …and the display metrics are still served, from the row the sync wrote.
    # An ungraded strategy has no verdict; it does have a backtest.
    assert detail.json()["sharpe_ratio"] == pytest.approx(REPRO_SHARPE)
    assert passport.json()["sharpe_ratio"] == pytest.approx(REPRO_SHARPE)


@pytest.mark.asyncio
async def test_a_legacy_row_with_fixture_gate_numbers_serves_none(monkeypatch):
    """A curated row carrying the #1187 fixture's DSR with no ``graded_at`` must
    serve no numbers at all.

    Every curated passport in production is such a row until the grading job
    runs: the boot-time sync used to copy the fixture snapshot's
    ``deflated_sharpe_ratio`` / ``dsr_p_value`` / ``pbo_score`` /
    ``out_of_sample_sharpe`` into columns that name a gate. Serving those beside
    a ``pending`` badge would re-commit #1187 in the act of fixing #1746.

    MUTATION: drop the ``graded`` guard in ``_to_strategy_response``
    (``stored.deflated_sharpe_ratio if graded else None`` → the bare read). The
    fixture value reaches the wire and this reddens.
    """
    from archimedes.db import get_session
    from archimedes.main import app
    from sqlalchemy import text

    _seed_the_reproduction(monkeypatch, grade=False)

    # Exactly what the pre-PR-B sync left behind: fixture numbers, no grade.
    with get_session() as session:
        session.execute(
            text(
                "UPDATE strategy_passports SET deflated_sharpe_ratio = 0.283312, dsr_p_value = 0.611531, "
                "pbo_score = 0.373116, out_of_sample_sharpe = 0.930283 WHERE id = :i"
            ),
            {"i": REPRO_ID},
        )
        session.commit()
        assert (
            session.execute(text("SELECT graded_at FROM strategy_passports WHERE id = :i"), {"i": REPRO_ID}).scalar()
            is None
        ), "fixture guard: this row must be ungraded"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        detail = await client.get(f"/api/strategies/{REPRO_ID}")

    body = detail.json()
    assert body["rigor_gate_status"] == "pending"
    for key in ("deflated_sharpe_ratio", "dsr_p_value", "pbo_score", "out_of_sample_sharpe"):
        assert body[key] is None, f"{key} leaked a fixture number from an ungraded row: {body[key]}"
    assert body["metrics_source"] == "unavailable"


# ═══════════════════════════════════════════════════════════════════════════
# The grading job itself
# ═══════════════════════════════════════════════════════════════════════════


def test_the_grading_job_stores_what_the_real_gate_computed(monkeypatch):
    """The stored verdict and the four numbers come from one real gate run.

    MUTATION: drop the four numeric arguments from the ``RigorVerdictWrite`` in
    ``grade_curated_library``. The badge stays but the numbers become NULL — a
    verdict with nothing to show for it.
    """
    from archimedes.db import get_session
    from archimedes.services.curated_grading import grade_cohort
    from archimedes.services.passport_loader import get_passport

    provider = _seed_the_reproduction(monkeypatch, grade=True)

    expected = grade_cohort(list(provider.list_strategies())).results[REPRO_ID]
    with get_session() as session:
        row = get_passport(session, REPRO_ID)

    assert row.rigor_gate_status == "pass"
    assert row.passes_rigor_gate is True
    assert row.deflated_sharpe_ratio == pytest.approx(expected.deflated_sharpe)
    assert row.dsr_p_value == pytest.approx(expected.dsr_p_value)
    assert row.out_of_sample_sharpe == pytest.approx(expected.oos_sharpe)
    assert row.gate_version and row.graded_at is not None


def test_a_strategy_with_no_persisted_returns_is_graded_pending(monkeypatch):
    """The pairs family's real state: no persisted row, so no verdict.

    ``run_backtests`` refuses to persist their artifact (realized-vol
    plausibility), so they have no returns, so the gate cannot run. ``pending``
    is the honest answer and re-running the job does not change it.

    MUTATION: make ``verdict_from_result(None)`` return ``failed()``. Thirty-odd
    curated strategies start claiming they were graded and lost.
    """
    from archimedes.db import get_session
    from archimedes.services.passport_loader import get_passport

    provider = _seed_the_reproduction(monkeypatch, grade=True)

    ungraded = [s for s in provider.list_strategies() if s.id != REPRO_ID]
    with get_session() as session:
        rows = {s.id: get_passport(session, s.id) for s in ungraded}

    pending = [r for r in rows.values() if r is not None and r.rigor_gate_status == "pending"]
    assert pending, "expected curated strategies with no persisted returns"
    for row in pending:
        assert row.passes_rigor_gate is False
        assert row.graded_at is None, "an ungraded row must not carry a grading timestamp"
        assert row.gate_version is None
        assert row.cohort_n is None, "graded against nothing — never a guessed cohort of 1"
        assert row.deflated_sharpe_ratio is None

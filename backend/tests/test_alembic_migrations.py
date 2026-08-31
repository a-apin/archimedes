"""Alembic migration tests (issue #1028, "tests" chunk — 5/5).

Covers acceptance criterion 5 ("``alembic upgrade head`` from an empty DB
reproduces the schema deterministically") plus the FK/CHECK retrofit the
issue's Target schema locks in, exercised against the ACTUAL migration
path (not the ``create_all()`` ORM path — that's covered separately by
``test_identity_schema.py``).

Hermetic: every ``alembic`` invocation runs as a subprocess against a fresh
``tmp_path`` SQLite file, with a whitelist-only environment (no ``.env``
leakage — see ``test_security_hardening.py``'s ``_clean_subprocess_env``
docstring for why: an inherited ``DATABASE_URL=postgresql://...@postgres:...``
from a developer's ``.env`` would make ``alembic upgrade head`` try to reach a
docker-compose-only hostname on bare metal). No network, no Postgres, no live
services.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _clean_subprocess_env(database_url: str) -> dict[str, str]:
    """Whitelist-only env for the alembic subprocess — see module docstring."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DATABASE_URL": database_url,
    }


def _run_alembic(*args: str, database_url: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["alembic", *args],
        cwd=str(_BACKEND_DIR),
        env=_clean_subprocess_env(database_url),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _expected_head_revision() -> str:
    """The head revision id, read from the version scripts themselves (not
    hardcoded) so this test doesn't need editing every time a new revision
    is added on top."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1, f"expected exactly one alembic head, found {heads}"
    return heads[0]


def _table_names(db_path: Path) -> set[str]:
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in cur.fetchall()}
    finally:
        con.close()


def test_alembic_upgrade_head_from_empty_db(tmp_path):
    """AC5: ``alembic upgrade head`` from an empty DB reproduces the schema
    deterministically — the exact clean-clone / CI scenario (revision zero,
    no prior ``create_all()``, no stamp)."""
    db_path = tmp_path / "fresh.db"
    assert not db_path.exists()

    result = _run_alembic("upgrade", "head", database_url=f"sqlite:///{db_path}")
    assert result.returncode == 0, f"alembic upgrade head failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert db_path.exists()

    tables = _table_names(db_path)
    # The three issue #1028 ledger tables, plus a sample of long-standing
    # tables the baseline revision must also reproduce (not just the newest
    # revision's tables — a real "from zero" replay of the whole chain).
    for expected in (
        "alembic_version",
        "wallet_identities",
        "controlled_wallets",
        "identity_events",
        "strategy_store",
        "chat_messages",
        "marketplace_agents",
        "user_profiles",
        "request_count_snapshots",
        "generation_costs",
    ):
        assert expected in tables, f"table {expected!r} missing after upgrade head; got {sorted(tables)}"


def test_alembic_current_reports_head_after_upgrade(tmp_path):
    """AC5: ``alembic current`` reports a revision — and it must be the actual
    head, not merely "some revision"."""
    db_path = tmp_path / "current.db"
    database_url = f"sqlite:///{db_path}"

    upgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr

    current = _run_alembic("current", database_url=database_url)
    assert current.returncode == 0, current.stderr
    assert _expected_head_revision() in current.stdout


def test_alembic_upgrade_head_is_idempotent(tmp_path):
    """Running ``upgrade head`` a second time against an already-migrated DB
    is a no-op, not an error (the redeploy runbook re-runs this on every
    boot)."""
    db_path = tmp_path / "idempotent.db"
    database_url = f"sqlite:///{db_path}"

    first = _run_alembic("upgrade", "head", database_url=database_url)
    assert first.returncode == 0, first.stderr
    tables_after_first = _table_names(db_path)

    second = _run_alembic("upgrade", "head", database_url=database_url)
    assert second.returncode == 0, f"second upgrade head failed:\n{second.stderr}"
    assert _table_names(db_path) == tables_after_first


def test_alembic_strategy_spec_column_added_and_removed(tmp_path):
    """Rebalancer decouple (Part A #1): ``strategy_store.strategy_spec`` lands
    on upgrade, is gone on downgrade, and comes back on re-upgrade — the
    per-migration up/down/idempotent contract, exercised directly (not just
    implied by the whole-chain tests above).

    Downgrades to the strategy_spec revision's OWN ``down_revision`` (looked
    up from the script directory, not hardcoded, and not a relative ``-1``)
    so this test keeps targeting that specific migration's up/down contract
    regardless of how many further revisions have since landed on top of it —
    a relative ``-1`` from head silently started downgrading a *different*
    migration the moment this one stopped being the head."""
    db_path = tmp_path / "spec_column.db"
    database_url = f"sqlite:///{db_path}"

    def _has_strategy_spec_column() -> bool:
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            cur.execute("PRAGMA table_info(strategy_store)")
            return "strategy_spec" in {row[1] for row in cur.fetchall()}
        finally:
            con.close()

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(_BACKEND_DIR / "alembic.ini")))
    strategy_spec_revision = script.get_revision("3f643d292e04")
    target = strategy_spec_revision.down_revision

    upgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr
    assert _has_strategy_spec_column()

    # ``target`` is derived (strategy_spec_revision.down_revision), not a
    # hardcoded hash: this branch pinned "7b6e8d812331" literally, which silently
    # stops testing the intended migration the moment another revision is
    # inserted into the chain -- and this branch inserts four.
    downgrade = _run_alembic("downgrade", target, database_url=database_url)
    assert downgrade.returncode == 0, downgrade.stderr
    assert not _has_strategy_spec_column()

    # Idempotent re-upgrade: column comes back, and running head→head again
    # afterwards is a no-op (not an error).
    reupgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert reupgrade.returncode == 0, reupgrade.stderr
    assert _has_strategy_spec_column()

    reupgrade_again = _run_alembic("upgrade", "head", database_url=database_url)
    assert reupgrade_again.returncode == 0, reupgrade_again.stderr
    assert _has_strategy_spec_column()


def test_alembic_generation_costs_table_added_and_removed(tmp_path):
    """Durable generation cost (#1326): ``generation_costs`` lands on upgrade, is
    gone on downgrade, and comes back on re-upgrade — the per-migration
    up/down/idempotent contract exercised directly.

    Same derived-target discipline as the ``strategy_spec`` test above: the
    downgrade target is looked up as this revision's OWN ``down_revision``, never
    a hardcoded hash or a relative ``-1``, so it keeps testing this migration
    however many revisions later land on top of it.
    """
    db_path = tmp_path / "generation_costs.db"
    database_url = f"sqlite:///{db_path}"

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(_BACKEND_DIR / "alembic.ini")))
    target = script.get_revision("e2b7f4c81d93").down_revision

    upgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr
    assert "generation_costs" in _table_names(db_path)

    downgrade = _run_alembic("downgrade", target, database_url=database_url)
    assert downgrade.returncode == 0, downgrade.stderr
    assert "generation_costs" not in _table_names(db_path)

    reupgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert reupgrade.returncode == 0, reupgrade.stderr
    assert "generation_costs" in _table_names(db_path)

    again = _run_alembic("upgrade", "head", database_url=database_url)
    assert again.returncode == 0, again.stderr
    assert "generation_costs" in _table_names(db_path)


def test_alembic_generation_costs_matches_a_fresh_create_all_schema(tmp_path):
    """Column parity for ``generation_costs`` between the two schema-management
    paths — the ORM's ``GenerationCostRecord`` (create_all, every hermetic test
    and local dev) and this migration's ``create_table`` (Alembic, CI/prod).

    Divergence here is not cosmetic: the cost card reads
    ``measurement_json`` / ``quote_json`` by name, so a column that exists on one
    path and not the other means the passport renders "not measured" in exactly
    one environment — the hardest kind of gap to notice.
    """
    create_all_db = tmp_path / "create_all_generation_costs.db"
    script = (
        "import sqlalchemy as sa\n"
        "from archimedes.models.account import AuthUser\n"
        "from archimedes.models.chat import Base\n"
        # vault_metadata.creator_address FKs wallet_identities; register it or
        # create_all() cannot resolve the whole metadata graph.
        "from archimedes.models.identity import WalletIdentity\n"
        "from archimedes.models.generation_cost import GenerationCostRecord\n"
        f"engine = sa.create_engine('sqlite:///{create_all_db}')\n"
        "Base.metadata.create_all(bind=engine)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_BACKEND_DIR),
        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr

    alembic_db = tmp_path / "alembic_built_generation_costs.db"
    upgrade = _run_alembic("upgrade", "head", database_url=f"sqlite:///{alembic_db}")
    assert upgrade.returncode == 0, upgrade.stderr

    def _columns(db_path: Path, table: str) -> set[str]:
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cur.fetchall()}
        finally:
            con.close()

    create_all_cols = _columns(create_all_db, "generation_costs")
    alembic_cols = _columns(alembic_db, "generation_costs")
    assert {"job_id", "strategy_id", "schema_version", "measurement_json", "quote_json", "recorded_at"} <= (
        create_all_cols
    )
    assert create_all_cols == alembic_cols


def test_alembic_upgrade_head_matches_a_fresh_create_all_schema(tmp_path):
    """The two schema-management paths (Alembic for retrofitting an
    already-populated Postgres DB; ``create_all()`` for fresh SQLite —
    ``archimedes/db.py``'s two-path design) must produce column-for-column
    identical tables, or a fresh dev clone (create_all) and a fresh CI/prod
    replay (alembic) would silently diverge.

    Compares one representative table from each of the three build phases
    (baseline / identity-ledger / request-snapshot revisions) rather than
    every table, to keep this a smoke test, not a schema-diff tool.
    """
    # Build via create_all() in a SEPARATE subprocess (not this pytest
    # process's already-bound shared engine) against a throwaway sqlite file,
    # independent of whichever DATABASE_URL the rest of the suite is using.
    # `python -c` puts cwd (backend/, via `cwd=`) on sys.path[0], so the
    # `archimedes` package resolves without any path manipulation here.
    create_all_db = tmp_path / "create_all.db"
    script = (
        "import sqlalchemy as sa\n"
        "from archimedes.models.account import AuthUser\n"
        "from archimedes.models.chat import Base\n"
        "from archimedes.models.identity import ControlledWallet, IdentityEvent, WalletIdentity\n"
        "from archimedes.models.request_snapshot import RequestCountSnapshot\n"
        f"engine = sa.create_engine('sqlite:///{create_all_db}')\n"
        "Base.metadata.create_all(bind=engine)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_BACKEND_DIR),
        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr

    alembic_db = tmp_path / "alembic_built.db"
    upgrade = _run_alembic("upgrade", "head", database_url=f"sqlite:///{alembic_db}")
    assert upgrade.returncode == 0, upgrade.stderr

    def _columns(db_path: Path, table: str) -> set[str]:
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cur.fetchall()}
        finally:
            con.close()

    for table in ("wallet_identities", "controlled_wallets", "identity_events"):
        assert _columns(create_all_db, table) == _columns(alembic_db, table), (
            f"{table} columns diverge between create_all() and alembic upgrade head"
        )


def test_alembic_strategy_store_matches_a_fresh_create_all_schema(tmp_path):
    """Same column-parity contract as the smoke test above, scoped to
    ``strategy_store`` specifically (Part A #1's ``strategy_spec`` column) —
    the ORM's ``StrategyRecord.strategy_spec`` (create_all path) and this
    migration's ``ADD COLUMN strategy_spec`` (alembic path) must agree."""
    create_all_db = tmp_path / "create_all_strategy_store.db"
    script = (
        "import sqlalchemy as sa\n"
        "from archimedes.models.account import AuthUser\n"
        "from archimedes.models.chat import Base\n"
        # Ownership FKs target auth_users and wallet_identities; register both.
        "from archimedes.models.identity import WalletIdentity\n"
        "from archimedes.models.strategy_store import StrategyRecord\n"
        f"engine = sa.create_engine('sqlite:///{create_all_db}')\n"
        "Base.metadata.create_all(bind=engine)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_BACKEND_DIR),
        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr

    alembic_db = tmp_path / "alembic_built_strategy_store.db"
    upgrade = _run_alembic("upgrade", "head", database_url=f"sqlite:///{alembic_db}")
    assert upgrade.returncode == 0, upgrade.stderr

    def _columns(db_path: Path, table: str) -> set[str]:
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cur.fetchall()}
        finally:
            con.close()

    create_all_cols = _columns(create_all_db, "strategy_store")
    alembic_cols = _columns(alembic_db, "strategy_store")
    assert "strategy_spec" in create_all_cols
    assert "strategy_spec" in alembic_cols
    assert create_all_cols == alembic_cols


# ── backtest_results dedupe data migration (issue #1347) ────────────────────
#
# canonical_artifact_hash used to hash run_id/timestamp_utc along with the
# rest of the artifact payload, so every run's content_hash was unique by
# construction and insert_backtest_if_missing's dedupe never fired — see
# backend/archimedes/services/backtest_mapper.py's module-level docstring.
# This migration (f0ab58339d55) is the one-time cleanup: it recomputes every
# row's canonical hash from its OWN stored artifact_json and collapses rows
# that recompute to the same (strategy_id, hash), keeping the earliest.

_DEDUPE_MIGRATION_REVISION = (
    "f0ab58339d55"  # migrations/versions/f0ab58339d55_dedupe_backtest_results_canonical_hash.py
)


def _dedupe_migration_down_revision() -> str:
    """The dedupe migration's OWN down_revision, looked up from the script
    directory (not hardcoded) — same rationale as the strategy_spec test
    above: stays correct if another migration lands on top later."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(_BACKEND_DIR / "alembic.ini")))
    target = script.get_revision(_DEDUPE_MIGRATION_REVISION).down_revision
    assert isinstance(target, str), f"expected a single down_revision, got {target!r}"
    return target


def _artifact_payload(run_id: str, timestamp_utc: str, sharpe: float) -> dict:
    """Minimal artifact payload shaped like a real run_backtests.py artifact:
    run_id/timestamp_utc are the two volatile fields the code fix excludes;
    everything else is content."""
    return {
        "run_id": run_id,
        "timestamp_utc": timestamp_utc,
        "strategy": {"backtest_code_hash": "sha256:dedupe-fixture"},
        "assumptions": {"transaction_cost_bps": 10},
        "results": [{"operation": "SPY", "metrics": {"sharpe_ratio": sharpe, "total_trades": 12}}],
    }


def _seed_pre_dedupe_rows(db_path: Path) -> None:
    """Seed rows AS THEY WOULD HAVE BEEN WRITTEN under the unfixed hash:
    genuinely identical content (strat_A) but a distinct content_hash per
    row, exactly like every real run_backtests.py refresh cycle produced
    before the code fix. Uses the real ORM factory
    (`BacktestResultRecord.from_backtest_result`) against the
    already-alembic-built schema, so seeding tracks real column
    nullability/defaults instead of hand-enumerating them.
    """
    import json as _json
    from datetime import UTC, datetime

    import sqlalchemy as sa
    from archimedes.models.backtest import BacktestResult
    from archimedes.models.backtest_store import BacktestResultRecord
    from sqlalchemy.orm import sessionmaker

    engine = sa.create_engine(f"sqlite:///{db_path}")
    SessionLocal = sessionmaker(bind=engine)

    def _result(sharpe: float) -> BacktestResult:
        return BacktestResult(
            strategy_id="unused",  # overwritten by from_backtest_result's own kwarg
            sharpe_ratio=sharpe,
            sortino_ratio=0.0,
            max_drawdown=0.0,
            cagr=0.0,
            calmar_ratio=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            total_trades=12,
            avg_holding_period_days=0.0,
            correlation_to_spy=None,
            correlation_to_btc=None,
            equity_curve=[],
            monthly_returns=[],
            backtest_start=None,
            backtest_end=None,
            backtest_engine="backtrader",
        )

    with SessionLocal() as session:
        # strat_A: three refreshes of IDENTICAL content (sharpe=0.7 every
        # time), each stamped with the OLD volatile-inclusive hash — a
        # different stale value per row even though the content never
        # changed. Must collapse to ONE row: the earliest (r1, 2026-08-01).
        for run_id, ts, created in (
            ("r1", "2026-08-01T00:00:00+00:00", datetime(2026, 8, 1, tzinfo=UTC)),
            ("r2", "2026-08-02T00:00:00+00:00", datetime(2026, 8, 2, tzinfo=UTC)),
            ("r3", "2026-08-03T00:00:00+00:00", datetime(2026, 8, 3, tzinfo=UTC)),
        ):
            row = BacktestResultRecord.from_backtest_result(
                strategy_id="strat_A",
                content_hash=f"stale-hash-{run_id}",
                result=_result(0.7),
                source_pipeline="run_backtests",
                run_id=run_id,
                artifact_json=_json.dumps(_artifact_payload(run_id, ts, 0.7)),
            )
            row.created_at = created
            session.add(row)

        # strat_B: two rows with GENUINELY DIFFERENT content (sharpe 0.5 vs
        # 0.9) — must NOT collapse; both survive, each gets its content_hash
        # normalized to its own recomputed value.
        for run_id, ts, sharpe, created in (
            ("r4", "2026-08-01T00:00:00+00:00", 0.5, datetime(2026, 8, 1, tzinfo=UTC)),
            ("r5", "2026-08-02T00:00:00+00:00", 0.9, datetime(2026, 8, 2, tzinfo=UTC)),
        ):
            row = BacktestResultRecord.from_backtest_result(
                strategy_id="strat_B",
                content_hash=f"stale-hash-{run_id}",
                result=_result(sharpe),
                source_pipeline="run_backtests",
                run_id=run_id,
                artifact_json=_json.dumps(_artifact_payload(run_id, ts, sharpe)),
            )
            row.created_at = created
            session.add(row)

        # strat_C: a row with NO resolvable artifact_json — must be left
        # untouched (no payload to recompute a canonical hash from; the
        # migration never guesses).
        row_c = BacktestResultRecord.from_backtest_result(
            strategy_id="strat_C",
            content_hash="stale-hash-r6",
            result=_result(0.3),
            source_pipeline="run_backtests",
            run_id="r6",
            artifact_json=None,
        )
        row_c.created_at = datetime(2026, 8, 1, tzinfo=UTC)
        session.add(row_c)

        session.commit()

    engine.dispose()


def _fetch_backtest_rows(db_path: Path) -> list[sqlite3.Row]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        cur.execute("SELECT id, strategy_id, content_hash, run_id, artifact_json FROM backtest_results ORDER BY id")
        return cur.fetchall()
    finally:
        con.close()


def test_dedupe_backtest_results_migration_collapses_duplicates_and_keeps_earliest(tmp_path):
    """Acceptance criterion: post-migration row count == number of distinct
    recomputed canonical hashes, and the EARLIEST row per group survives.

    strat_A: 3 content-identical rows -> 1 survivor (the earliest, r1).
    strat_B: 2 content-DIFFERENT rows -> both survive, untouched-in-count.
    strat_C: 1 unresolvable-payload row -> survives unchanged (own group).
    Total before: 6 rows over 3 (strategy_id, recomputed_hash) groups (A has
    1 distinct group despite 3 rows; B has 2; C has 1) -> 4 distinct groups,
    4 rows after.
    """
    from archimedes.services.backtest_mapper import canonical_artifact_hash

    db_path = tmp_path / "dedupe_collapse.db"
    database_url = f"sqlite:///{db_path}"

    pre = _run_alembic("upgrade", _dedupe_migration_down_revision(), database_url=database_url)
    assert pre.returncode == 0, pre.stderr

    _seed_pre_dedupe_rows(db_path)
    assert len(_fetch_backtest_rows(db_path)) == 6, "sanity: 6 rows seeded before the dedupe migration runs"

    post = _run_alembic("upgrade", "head", database_url=database_url)
    assert post.returncode == 0, f"dedupe migration failed:\nSTDOUT:\n{post.stdout}\nSTDERR:\n{post.stderr}"

    rows = _fetch_backtest_rows(db_path)
    by_strategy: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_strategy.setdefault(row["strategy_id"], []).append(row)

    # Row count == number of distinct recomputed canonical hashes (AC).
    assert len(rows) == 4, f"expected 4 surviving rows, got {len(rows)}: {[dict(r) for r in rows]}"
    recomputed_hashes = {row["content_hash"] for row in rows}
    assert len(recomputed_hashes) == len(rows), "every surviving row must carry a distinct content_hash"

    # strat_A collapsed to exactly the EARLIEST row (r1), and its content_hash
    # is the fixed canonical_artifact_hash of its OWN payload — proving the
    # migration used the real function, not a placeholder.
    assert len(by_strategy["strat_A"]) == 1
    survivor_a = by_strategy["strat_A"][0]
    assert survivor_a["run_id"] == "r1", "the EARLIEST duplicate must survive, not an arbitrary one"
    expected_hash_a = canonical_artifact_hash(_artifact_payload("r1", "2026-08-01T00:00:00+00:00", 0.7))
    assert survivor_a["content_hash"] == expected_hash_a
    # And that recomputed hash is identical to what r2/r3's payloads would
    # produce too (proving run_id/timestamp really are excluded) even though
    # r2/r3 themselves are gone.
    assert expected_hash_a == canonical_artifact_hash(_artifact_payload("r2", "2026-08-02T00:00:00+00:00", 0.7))

    # strat_B: both distinct-content rows survive, each normalized to its own
    # recomputed hash (never merged with each other).
    assert len(by_strategy["strat_B"]) == 2
    hashes_b = {row["content_hash"] for row in by_strategy["strat_B"]}
    assert hashes_b == {
        canonical_artifact_hash(_artifact_payload("r4", "2026-08-01T00:00:00+00:00", 0.5)),
        canonical_artifact_hash(_artifact_payload("r5", "2026-08-02T00:00:00+00:00", 0.9)),
    }

    # strat_C: unresolvable payload -> left exactly as it was.
    assert len(by_strategy["strat_C"]) == 1
    assert by_strategy["strat_C"][0]["content_hash"] == "stale-hash-r6"


def test_dedupe_backtest_results_migration_upgrade_is_idempotent(tmp_path):
    """Running the dedupe migration's upgrade a second time (downgrade to its
    own down_revision — a documented structural no-op — then upgrade to head
    again, which re-executes the SAME upgrade() body against
    already-deduped data) must be a no-op: identical row set, no error."""
    db_path = tmp_path / "dedupe_idempotent.db"
    database_url = f"sqlite:///{db_path}"
    down_revision = _dedupe_migration_down_revision()

    pre = _run_alembic("upgrade", down_revision, database_url=database_url)
    assert pre.returncode == 0, pre.stderr
    _seed_pre_dedupe_rows(db_path)

    first = _run_alembic("upgrade", "head", database_url=database_url)
    assert first.returncode == 0, first.stderr
    rows_after_first = [dict(r) for r in _fetch_backtest_rows(db_path)]

    second_down = _run_alembic("downgrade", down_revision, database_url=database_url)
    assert second_down.returncode == 0, second_down.stderr
    second_up = _run_alembic("upgrade", "head", database_url=database_url)
    assert second_up.returncode == 0, f"second upgrade head failed:\n{second_up.stderr}"

    rows_after_second = [dict(r) for r in _fetch_backtest_rows(db_path)]
    assert rows_after_second == rows_after_first, "re-running the dedupe migration changed already-deduped data"


# ── schema-relations Phase 1 (fb8d0bae8112 + 9c2e7b5a1f4d): indices + gated
# FKs ─────────────────────────────────────────────────────────────────────
#
# The FK-adding step (fb8d0bae8112: NOT VALID only) and the validating step
# (9c2e7b5a1f4d: the live orphan gate + VALIDATE CONSTRAINT) are two separate
# revisions — see 9c2e7b5a1f4d's module docstring for why. Things this
# section proves, in order:
#  1. The raw orphan-count SQL each gated FK uses is CORRECT against a
#     realistic mixed fixture (some rows satisfy the invariant, some don't) —
#     tested directly against the queries the revision modules themselves
#     define, independent of which dialect branch runs them, AND that both
#     revisions' copies of that SQL are byte-identical (they're deliberately
#     duplicated, not imported — see the drift-guard test below).
#  2. `alembic upgrade head` (both revisions) SUCCEEDS against that same
#     fixture with the orphans still in place — the whole point of NOT
#     VALID: a migration that can't proceed past unmeasured historical data
#     is not "minimal blast radius", it's a fresh outage.
#  3. On SQLite specifically, the Postgres-only VALIDATE CONSTRAINT branch
#     never fires (there's nothing to validate) — proven from subprocess
#     stdout, not by reaching into the migration's internals.
#  4. `alembic upgrade --sql` (offline mode, no live connection) renders both
#     revisions' DDL without crashing — the exact regression this section
#     guards (a live-bind orphan query used to run unconditionally and blow
#     up `context.is_offline_mode()`).
#  5. The two model↔migration parity tests at the bottom of this section
#     compare INDEX NAMES/COLUMNS and FK TARGETS between the `create_all()`
#     path and the alembic path, not just column names — the blind spot a
#     column-only parity gate has no way to see (a dropped index or a FK
#     pointed at the wrong table would pass a column-name-only comparison).

_PHASE1_REVISION = "fb8d0bae8112"  # migrations/versions/fb8d0bae8112_schema_relations_phase1.py
_PHASE1_VALIDATE_REVISION = "9c2e7b5a1f4d"  # .../9c2e7b5a1f4d_schema_relations_phase1_validate.py

_ORPHAN_WALLET = "0x" + "c" * 40  # never anchored in wallet_identities
_ORPHAN_STRATEGY = "strat-does-not-exist"  # never a row in strategy_store
_REAL_WALLET = "0x" + "a" * 40
_REAL_STRATEGY = "strat-real"


def _revision_module(revision_id: str):
    """A revision module by id — gives direct access to a migration's own
    module-level data (`_GATED_FKS` / `_INDICES`) so tests exercise the real
    thing instead of a duplicated copy."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(_BACKEND_DIR / "alembic.ini")))
    return script.get_revision(revision_id).module


def _phase1_module():
    return _revision_module(_PHASE1_REVISION)


def _phase1_validate_module():
    return _revision_module(_PHASE1_VALIDATE_REVISION)


def _phase1_down_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(_BACKEND_DIR / "alembic.ini")))
    target = script.get_revision(_PHASE1_REVISION).down_revision
    assert isinstance(target, str), f"expected a single down_revision, got {target!r}"
    return target


def _seed_phase1_fixture(db_path: Path) -> None:
    """Seed the schema AS IT STANDS immediately before fb8d0bae8112 runs
    (i.e. at its own down_revision) with a REALISTIC mix for every gated FK:

      - one wallet_identities anchor + a linked_wallets row that correctly
        points at it (fk_linked_wallets_address_wallet_identity: 0 orphans —
        this is the invariant `_link_verified_wallet` already enforces in
        app code, per the migration's own docstring).
      - one auth_users + one strategy_store row.
      - one paper_deployments row that points at REAL strategy_store /
        wallet_identities / auth_users rows (0 orphans on all three of its
        gated FKs).
      - one paper_deployments row whose strategy_id and owner_wallet point
        at NOTHING (a genuine pre-migration orphan on
        fk_paper_deployments_strategy_id and fk_paper_deployments_owner_wallet)
        but whose owner_user_id is real (0 orphans on
        fk_paper_deployments_owner_user_id) — exactly the mixed shape the
        audit's own pre-flight queries anticipate: some FKs clean, one not.

    Uses the real ORM model classes against the pre-migration schema (same
    technique as `_seed_pre_dedupe_rows` above) rather than hand-rolled SQL,
    so seeding tracks real column nullability/defaults instead of
    hand-enumerating them.
    """
    from datetime import UTC, date, datetime

    import sqlalchemy as sa
    from archimedes.models.account import AuthUser, LinkedWallet
    from archimedes.models.identity import WalletIdentity
    from archimedes.models.paper_store import PaperDeployment
    from archimedes.models.strategy_store import StrategyRecord
    from sqlalchemy.orm import sessionmaker

    engine = sa.create_engine(f"sqlite:///{db_path}")
    SessionLocal = sessionmaker(bind=engine)
    now = datetime.now(UTC)

    with SessionLocal() as session:
        session.add(
            AuthUser(
                id="user-1", name="Ada", email="ada@example.com", email_verified=True, created_at=now, updated_at=now
            )
        )
        session.add(WalletIdentity(wallet_address=_REAL_WALLET, actor_class="human", first_seen_at=now))
        session.add(
            StrategyRecord(
                id=_REAL_STRATEGY,
                content_hash="0x" + "b" * 64,
                generation_method="curated",
                strategy_name="real strategy",
                thesis="",
                source_papers="[]",
                asset_universe="[]",
                is_example=True,
            )
        )
        session.flush()

        session.add(
            LinkedWallet(
                id="lw-healthy",
                user_id="user-1",
                normalized_identity=f"1:{_REAL_WALLET}",
                address=_REAL_WALLET,
                display_address=_REAL_WALLET,
                chain_id=1,
                provider="siwe",
                is_primary=True,
                verified_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            PaperDeployment(
                id="pd-healthy",
                strategy_id=_REAL_STRATEGY,
                owner_wallet=_REAL_WALLET,
                owner_user_id="user-1",
                spec_json="{}",
                deployed_at=date(2026, 1, 1),
                status="active",
                created_at=now,
            )
        )
        session.add(
            PaperDeployment(
                id="pd-orphan",
                strategy_id=_ORPHAN_STRATEGY,
                owner_wallet=_ORPHAN_WALLET,
                owner_user_id="user-1",
                spec_json="{}",
                deployed_at=date(2026, 1, 1),
                status="active",
                created_at=now,
            )
        )
        session.commit()
    engine.dispose()


def test_phase1_orphan_queries_match_expected_counts(tmp_path):
    """The exact `orphan_sql` strings the migration defines for each gated
    FK, run directly against the realistic mixed fixture — proving the SQL
    itself is correct independent of which dialect branch would run it (the
    Postgres VALIDATE branch can't be exercised in this hermetic SQLite-only
    suite; this is the SQL-correctness half of the same guarantee)."""
    import sqlalchemy as sa

    db_path = tmp_path / "phase1_orphan_queries.db"
    database_url = f"sqlite:///{db_path}"

    pre = _run_alembic("upgrade", _phase1_down_revision(), database_url=database_url)
    assert pre.returncode == 0, pre.stderr
    _seed_phase1_fixture(db_path)

    module = _phase1_module()
    expected_orphans = {
        "fk_linked_wallets_address_wallet_identity": 0,
        "fk_paper_deployments_owner_user_id": 0,
        "fk_paper_deployments_strategy_id": 1,
        "fk_paper_deployments_owner_wallet": 1,
    }
    assert {name for name, *_ in module._GATED_FKS} == set(expected_orphans), (
        "test fixture is out of sync with the migration's own _GATED_FKS list"
    )

    engine = sa.create_engine(database_url)
    with engine.connect() as conn:
        for constraint_name, _table, _local, _ref_table, _remote, _ondelete, orphan_sql in module._GATED_FKS:
            count = conn.execute(sa.text(orphan_sql)).scalar_one()
            assert count == expected_orphans[constraint_name], (
                f"{constraint_name}: expected {expected_orphans[constraint_name]} orphans, got {count}"
            )
    engine.dispose()


def test_phase1_validate_orphan_sql_matches_source_revision():
    """9c2e7b5a1f4d (the validate-only follow-up) carries its OWN copy of
    each orphan_sql string rather than importing fb8d0bae8112's module (see
    both files' docstrings: migrations shouldn't import each other, so each
    stays a standalone, replayable artifact). That duplication is only safe
    if the two copies can never silently drift apart — this is the guard.
    Mutation-check: change one character in either copy and this test must
    fail; confirmed and reverted (see PR description)."""
    fk_module = _phase1_module()
    validate_module = _phase1_validate_module()

    fk_orphan_sql = {name: orphan_sql for name, _table, *_rest, orphan_sql in fk_module._GATED_FKS}
    validate_orphan_sql = {name: orphan_sql for name, _table, orphan_sql in validate_module._GATED_FKS}

    assert set(fk_orphan_sql) == set(validate_orphan_sql), (
        "fb8d0bae8112 and 9c2e7b5a1f4d disagree on WHICH constraints are gated"
    )
    for name, sql in fk_orphan_sql.items():
        assert sql == validate_orphan_sql[name], f"{name}: orphan_sql differs between the two revisions"


def test_phase1_migration_upgrade_head_survives_realistic_orphans(tmp_path):
    """`alembic upgrade head` must SUCCEED against the mixed fixture above,
    leave the orphan row's data untouched (NOT VALID enforces future writes
    only — it never mutates or rejects existing rows), and still create every
    constraint. This is acceptance criterion for the whole NOT VALID design:
    a migration that can't proceed past unmeasured historical orphans is not
    the "minimal blast radius" change the audit asked for."""
    db_path = tmp_path / "phase1_orphans.db"
    database_url = f"sqlite:///{db_path}"

    pre = _run_alembic("upgrade", _phase1_down_revision(), database_url=database_url)
    assert pre.returncode == 0, pre.stderr
    _seed_phase1_fixture(db_path)

    result = _run_alembic("upgrade", "head", database_url=database_url)
    assert result.returncode == 0, (
        f"upgrade head failed against orphaned data:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()

        # The orphan row survived, byte-for-byte — NOT VALID never touches
        # existing data, only future writes.
        cur.execute("SELECT strategy_id, owner_wallet, owner_user_id FROM paper_deployments WHERE id = 'pd-orphan'")
        assert cur.fetchone() == (_ORPHAN_STRATEGY, _ORPHAN_WALLET, "user-1")

        # The healthy row survived too.
        cur.execute("SELECT strategy_id, owner_wallet, owner_user_id FROM paper_deployments WHERE id = 'pd-healthy'")
        assert cur.fetchone() == (_REAL_STRATEGY, _REAL_WALLET, "user-1")

        # Every constraint exists on the table regardless of the orphan —
        # this is the schema-level guarantee for all FUTURE writes.
        cur.execute("PRAGMA foreign_key_list(paper_deployments)")
        pd_fk_columns = {row[3] for row in cur.fetchall()}
        assert pd_fk_columns == {"strategy_id", "owner_wallet", "owner_user_id"}

        cur.execute("PRAGMA foreign_key_list(linked_wallets)")
        lw_fk_columns = {row[3] for row in cur.fetchall()}
        assert "address" in lw_fk_columns
    finally:
        con.close()

    # Idempotent: running head->head again on the now-migrated, still-orphaned
    # database is a no-op, not an error (the redeploy runbook re-runs this).
    again = _run_alembic("upgrade", "head", database_url=database_url)
    assert again.returncode == 0, again.stderr


def test_phase1_sqlite_never_reaches_the_postgres_only_validate_branch(tmp_path):
    """Black-box proof of the dialect gate: on SQLite, 9c2e7b5a1f4d's
    `if not is_postgres: return` fires before either of the two
    Postgres-only print statements (`... validated` / `... orphan row(s)`),
    so NEITHER string reaches stdout — regardless of whether the fixture's
    data would count as zero orphans or several. Checked via the real
    subprocess's captured output, not by importing and calling the
    migration's internals directly."""
    db_path = tmp_path / "phase1_dialect_gate.db"
    database_url = f"sqlite:///{db_path}"

    pre = _run_alembic("upgrade", _phase1_down_revision(), database_url=database_url)
    assert pre.returncode == 0, pre.stderr
    _seed_phase1_fixture(db_path)  # mixed: some FKs clean, one with real orphans

    result = _run_alembic("upgrade", "head", database_url=database_url)
    assert result.returncode == 0, result.stderr
    assert "orphan row(s)" not in result.stdout
    assert "validated" not in result.stdout


#: The 10 indices the audit's §1.2 table specifies, hardcoded here (NOT
#: derived from the migration module's own `_INDICES`, unlike the SQL-level
#: tests above) — a regression that silently drops an entry from `_INDICES`
#: must still fail this test, not evade it by shrinking the thing being
#: compared against right along with the bug.
_PHASE1_REQUIRED_INDICES = frozenset(
    {
        "ix_linked_wallets_address",
        "ix_paper_deployments_owner_user_id",
        "ix_identity_events_wallet_time",
        "ix_subscriber_liabilities_sub",
        "ix_subscriber_liabilities_strategy_created",
        "ix_settlement_intents_sub",
        "ix_settlement_intents_status_created",
        "ix_marketplace_agents_subscriber_wallet",
        "ix_marketplace_agents_creator_wallet",
        "ix_generation_costs_recorded_at",
    }
)


def test_phase1_indices_fks_and_column_width_added_and_removed(tmp_path):
    """Per-migration up/down/idempotent contract, exercised directly (same
    pattern as `test_alembic_strategy_spec_column_added_and_removed` /
    `test_alembic_generation_costs_table_added_and_removed` above): every
    index and FK lands on upgrade, is gone on downgrade, and comes back on
    re-upgrade — including the `paper_deployments.strategy_id` width step."""
    db_path = tmp_path / "phase1_updown.db"
    database_url = f"sqlite:///{db_path}"

    def _state(db_path: Path) -> dict:
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            cur.execute("PRAGMA table_info(paper_deployments)")
            strategy_col = next(row for row in cur.fetchall() if row[1] == "strategy_id")
            cur.execute("PRAGMA foreign_key_list(paper_deployments)")
            pd_fks = {row[3] for row in cur.fetchall()}
            cur.execute("PRAGMA foreign_key_list(linked_wallets)")
            lw_fks = {row[3] for row in cur.fetchall()}
            cur.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
            index_names = {row[0] for row in cur.fetchall()}
            return {
                "strategy_id_type": strategy_col[2],
                "pd_fk_columns": pd_fks,
                "lw_fk_columns": lw_fks,
                "index_names": index_names,
            }
        finally:
            con.close()

    down_revision = _phase1_down_revision()

    upgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr
    up_state = _state(db_path)
    assert up_state["strategy_id_type"] == "VARCHAR(128)"
    assert up_state["pd_fk_columns"] == {"strategy_id", "owner_wallet", "owner_user_id"}
    assert "address" in up_state["lw_fk_columns"]
    assert up_state["index_names"] >= _PHASE1_REQUIRED_INDICES, (
        f"missing after upgrade: {_PHASE1_REQUIRED_INDICES - up_state['index_names']}"
    )

    downgrade = _run_alembic("downgrade", down_revision, database_url=database_url)
    assert downgrade.returncode == 0, downgrade.stderr
    down_state = _state(db_path)
    assert down_state["strategy_id_type"] == "VARCHAR(64)"
    assert down_state["pd_fk_columns"] == set()
    assert "address" not in down_state["lw_fk_columns"]
    assert not (_PHASE1_REQUIRED_INDICES & down_state["index_names"]), (
        f"survived downgrade: {_PHASE1_REQUIRED_INDICES & down_state['index_names']}"
    )

    reupgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert reupgrade.returncode == 0, reupgrade.stderr
    assert _state(db_path) == up_state

    reupgrade_again = _run_alembic("upgrade", "head", database_url=database_url)
    assert reupgrade_again.returncode == 0, reupgrade_again.stderr
    assert _state(db_path) == up_state


def test_phase1_offline_sql_renders_without_crashing():
    """Regression test for the offline-mode crash: both revisions used to
    call `bind.execute(...).scalar_one()` unconditionally, which raises
    `AttributeError: 'NoneType' object has no attribute 'scalar_one'` under
    `alembic upgrade --sql` (offline mode never has a live connection — see
    fb8d0bae8112's `_add_gated_fk_not_valid`, which now makes no live query
    at all, and 9c2e7b5a1f4d's `context.is_offline_mode()` guard).

    No live Postgres needed: `--sql` never opens a real connection, it only
    uses the configured URL to pick the rendering dialect."""
    result = _run_alembic(
        "upgrade",
        f"{_phase1_down_revision()}:{_PHASE1_VALIDATE_REVISION}",
        "--sql",
        database_url="postgresql://user:pass@localhost:5432/dbname",
    )
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "SET lock_timeout = '5s';" in result.stdout
    assert "NOT VALID" in result.stdout
    for constraint_name, *_rest in _phase1_module()._GATED_FKS:
        assert f"ADD CONSTRAINT {constraint_name}" in result.stdout
        assert f"VALIDATE CONSTRAINT {constraint_name}" in result.stdout


def test_phase1_offline_sql_downgrade_renders_without_crashing():
    """Same regression guard, downgrade direction: the strategy_id
    narrow-safety check (`_fail_if_narrow_would_truncate`) is
    `is_offline_mode()`-guarded too, so `alembic downgrade --sql` must also
    render cleanly rather than crash trying to count real rows that don't
    exist in offline mode."""
    result = _run_alembic(
        "downgrade",
        f"{_PHASE1_VALIDATE_REVISION}:{_phase1_down_revision()}",
        "--sql",
        database_url="postgresql://user:pass@localhost:5432/dbname",
    )
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "DROP CONSTRAINT" in result.stdout
    assert "ALTER COLUMN strategy_id TYPE VARCHAR(64)" in result.stdout


class _FakeScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _FakeBind:
    """Stands in for a live Postgres connection just far enough to exercise
    `_fail_if_narrow_would_truncate`'s SQL-count branch without needing a
    real Postgres server (this repo's hermetic test suite has none)."""

    def __init__(self, count: int) -> None:
        self._count = count

    def execute(self, _stmt):
        return _FakeScalarResult(self._count)


def test_phase1_downgrade_narrow_guard_rejects_rows_that_would_truncate():
    """The strategy_id downgrade narrow (VARCHAR(128) -> VARCHAR(64)) is the
    one honestly-irreversible piece of this migration (see fb8d0bae8112's
    docstring § 2 and `_fail_if_narrow_would_truncate`): if any row's
    strategy_id is longer than 64 characters, narrowing back would
    truncate-fail. This proves the guard actually rejects that input rather
    than silently letting Postgres's own generic error through — built the
    input that SHOULD fail (a fake bind reporting a nonzero count) per
    CLAUDE.md's guard-adversarial-pass rule."""
    module = _phase1_module()

    with pytest.raises(RuntimeError, match="would truncate-fail"):
        module._fail_if_narrow_would_truncate(_FakeBind(count=3), is_postgres=True, is_offline=False)

    # Zero offending rows -> no exception.
    module._fail_if_narrow_would_truncate(_FakeBind(count=0), is_postgres=True, is_offline=False)

    # Non-Postgres (SQLite never enforces VARCHAR(N) length) -> never even
    # looks at the count; a bind that would raise if queried proves this.
    class _ExplodingBind:
        def execute(self, _stmt):
            raise AssertionError("must not query a non-Postgres bind for the truncate guard")

    module._fail_if_narrow_would_truncate(_ExplodingBind(), is_postgres=False, is_offline=False)

    # Offline -> never queries either, regardless of dialect (no live
    # connection exists to query in `alembic downgrade --sql`).
    module._fail_if_narrow_would_truncate(_ExplodingBind(), is_postgres=True, is_offline=True)


def _index_signature(db_path: Path, table: str) -> frozenset[tuple[str, tuple[str, ...]]]:
    """(index_name, ordered_columns) for every EXPLICITLY-named index on
    `table` — NOT just column names. A dropped or misdirected index changes
    this signature even though it changes zero column names, which is
    exactly the blind spot a column-only parity test cannot see.

    SQLite's own `sqlite_autoindex_<table>_<n>` indices (one per UNIQUE
    constraint, auto-created regardless of which code path built the table)
    are normalized to a constant name before comparing: their trailing
    sequence number depends on column-declaration ORDER in the `CREATE
    TABLE` statement, which legitimately differs between `create_all()`'s
    declarative column order and `batch_alter_table`'s rebuild order even
    when the actual set of unique constraints is identical. Normalizing
    still preserves discriminating power because the (name, columns) tuple
    stays keyed on columns too — two different unique-constraint column sets
    still produce two different tuples.
    """
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?", (table,))
        names = [row[0] for row in cur.fetchall()]
        signature = set()
        for name in names:
            cur.execute(f"PRAGMA index_info({name})")
            columns = tuple(row[2] for row in sorted(cur.fetchall(), key=lambda row: row[0]))
            normalized_name = "sqlite_autoindex" if name.startswith("sqlite_autoindex_") else name
            signature.add((normalized_name, columns))
        return frozenset(signature)
    finally:
        con.close()


def _fk_signature(db_path: Path, table: str) -> frozenset[tuple[str, str, str, str]]:
    """(from_column, referenced_table, referenced_column, on_delete) for
    every FK on `table`. SQLite's `PRAGMA foreign_key_list` doesn't surface
    constraint NAMES at all (create_all()'s unnamed FKs vs the migration's
    explicitly-named ones couldn't be compared by name anyway) — comparing
    the actual referential shape is both what's available and what actually
    matters: a FK silently pointed at the wrong table/column, or missing
    entirely, changes zero column names but changes this signature."""
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute(f"PRAGMA foreign_key_list({table})")
        # columns: id, seq, table, from, to, on_update, on_delete, match
        return frozenset((row[3], row[2], row[4], row[6]) for row in cur.fetchall())
    finally:
        con.close()


def test_phase1_linked_wallets_matches_a_fresh_create_all_schema(tmp_path):
    """Column, INDEX, and FK parity between the two schema-management paths
    for the one Phase-1-touched table on the `account.py` side: `create_all()`
    (`LinkedWallet.address`'s new FK/index) vs this migration's
    `batch_alter_table`. Same column-parity rationale as the existing
    generation_costs / strategy_store parity tests above, extended to index
    names/columns and FK targets (see `_index_signature` / `_fk_signature`
    docstrings) — a column-only comparison would pass even if the FK or
    index were silently dropped from one side. Mutation-check: comment out
    `LinkedWallet.address`'s `ForeignKey(...)` in `models/account.py` and
    this test's FK-signature assertion fails while the column-name assertion
    still passes; confirmed and reverted (see PR description)."""
    create_all_db = tmp_path / "create_all_linked_wallets.db"
    script = (
        "import sqlalchemy as sa\n"
        "from archimedes.models.account import AuthUser, LinkedWallet\n"
        "from archimedes.models.chat import Base\n"
        "from archimedes.models.identity import WalletIdentity\n"
        f"engine = sa.create_engine('sqlite:///{create_all_db}')\n"
        "Base.metadata.create_all(bind=engine)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_BACKEND_DIR),
        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr

    alembic_db = tmp_path / "alembic_built_linked_wallets.db"
    upgrade = _run_alembic("upgrade", "head", database_url=f"sqlite:///{alembic_db}")
    assert upgrade.returncode == 0, upgrade.stderr

    def _columns(db_path: Path, table: str) -> set[str]:
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cur.fetchall()}
        finally:
            con.close()

    create_all_cols = _columns(create_all_db, "linked_wallets")
    alembic_cols = _columns(alembic_db, "linked_wallets")
    assert create_all_cols == alembic_cols

    assert _index_signature(create_all_db, "linked_wallets") == _index_signature(alembic_db, "linked_wallets"), (
        "linked_wallets indices diverge between create_all() and alembic upgrade head"
    )
    assert _fk_signature(create_all_db, "linked_wallets") == _fk_signature(alembic_db, "linked_wallets"), (
        "linked_wallets foreign keys diverge between create_all() and alembic upgrade head"
    )


def test_phase1_paper_deployments_matches_a_fresh_create_all_schema(tmp_path):
    """Same column/index/FK-parity contract, for `paper_deployments` — the
    table this revision widens `strategy_id` on and adds all THREE of its
    foreign keys to: `owner_user_id -> auth_users.id` (ON DELETE SET NULL),
    `strategy_id -> strategy_store.id` and
    `owner_wallet -> wallet_identities.wallet_address`. None of the three
    pre-dates this revision — `_GATED_FKS` in
    `fb8d0bae8112_schema_relations_phase1.py` is the full list, and it names
    all three. Mutation-check: comment out
    `PaperDeployment.strategy_id`'s `ForeignKey(...)` in
    `models/paper_store.py` and this test's FK-signature assertion fails
    while the column-name assertion still passes; confirmed and reverted
    (see PR description)."""
    create_all_db = tmp_path / "create_all_paper_deployments.db"
    script = (
        "import sqlalchemy as sa\n"
        "from archimedes.models.account import AuthUser\n"
        "from archimedes.models.chat import Base\n"
        "from archimedes.models.identity import WalletIdentity\n"
        "from archimedes.models.paper_store import PaperDeployment\n"
        "from archimedes.models.strategy_store import StrategyRecord\n"
        f"engine = sa.create_engine('sqlite:///{create_all_db}')\n"
        "Base.metadata.create_all(bind=engine)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_BACKEND_DIR),
        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr

    alembic_db = tmp_path / "alembic_built_paper_deployments.db"
    upgrade = _run_alembic("upgrade", "head", database_url=f"sqlite:///{alembic_db}")
    assert upgrade.returncode == 0, upgrade.stderr

    def _columns(db_path: Path, table: str) -> set[str]:
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cur.fetchall()}
        finally:
            con.close()

    create_all_cols = _columns(create_all_db, "paper_deployments")
    alembic_cols = _columns(alembic_db, "paper_deployments")
    assert "strategy_id" in create_all_cols
    assert create_all_cols == alembic_cols

    assert _index_signature(create_all_db, "paper_deployments") == _index_signature(alembic_db, "paper_deployments"), (
        "paper_deployments indices diverge between create_all() and alembic upgrade head"
    )
    assert _fk_signature(create_all_db, "paper_deployments") == _fk_signature(alembic_db, "paper_deployments"), (
        "paper_deployments foreign keys diverge between create_all() and alembic upgrade head"
    )


# ── strategy_store.brief_intent + its backfill (v8 Lane 3.3, dbrowneup/brief-on-passport) ──
#
# The revision (5cb798feef58) adds the column, then joins strategy_store to
# strategy_proposals on the (strategy_name, thesis) pair the pipeline writes
# identically to both tables, backfilling only when every matching proposal
# agrees on ONE intent string — see that migration's own docstring for the
# full "genuinely ambiguous" rationale this test suite exercises directly.

_BRIEF_INTENT_MIGRATION_REVISION = "5cb798feef58"


def _brief_intent_migration_down_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(_BACKEND_DIR / "alembic.ini")))
    target = script.get_revision(_BRIEF_INTENT_MIGRATION_REVISION).down_revision
    assert isinstance(target, str), f"expected a single down_revision, got {target!r}"
    return target


def test_alembic_brief_intent_column_added_and_removed(tmp_path):
    """Per-migration up/down/idempotent contract, exercised directly — same
    derived-target discipline as the strategy_spec/generation_costs tests
    above (never a hardcoded down_revision or a relative ``-1``)."""
    db_path = tmp_path / "brief_intent_column.db"
    database_url = f"sqlite:///{db_path}"

    def _has_brief_intent_column() -> bool:
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            cur.execute("PRAGMA table_info(strategy_store)")
            return "brief_intent" in {row[1] for row in cur.fetchall()}
        finally:
            con.close()

    target = _brief_intent_migration_down_revision()

    upgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr
    assert _has_brief_intent_column()

    downgrade = _run_alembic("downgrade", target, database_url=database_url)
    assert downgrade.returncode == 0, downgrade.stderr
    assert not _has_brief_intent_column()

    reupgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert reupgrade.returncode == 0, reupgrade.stderr
    assert _has_brief_intent_column()

    reupgrade_again = _run_alembic("upgrade", "head", database_url=database_url)
    assert reupgrade_again.returncode == 0, reupgrade_again.stderr
    assert _has_brief_intent_column()


def _seed_pre_brief_intent_rows(db_path: Path) -> None:
    """Seed strategy_store + strategy_proposals rows AS THEY WOULD HAVE BEEN
    WRITTEN by the pipeline, at the schema state immediately BEFORE the
    brief_intent migration runs (i.e. after upgrading only to its own
    down_revision — strategy_store has no brief_intent column yet).

    Six cases, one per strategy:
      * unambiguous  — one proposal, one intent -> backfilled.
      * ambiguous    — two proposals sharing the (name, thesis) key but
                        DIFFERENT intents (the fixture-path collision this
                        migration's docstring calls out) -> left NULL.
      * no_match     — a strategy with no matching proposal at all
                        (pre-persist_proposal legacy row) -> left NULL.
      * curated       — generation_method='curated' -> never even considered,
                        left NULL, even though its (name, thesis) happens to
                        coincide with a real proposal.
      * blank         — its one matching proposal logged a WHITESPACE-ONLY
                        intent -> left NULL, not backfilled with blanks (this
                        backfill is the second writer to the column and must
                        normalize the same way ``upsert_strategy`` does; note
                        ``"   "`` is truthy, so only the ``.strip()`` makes it
                        fall out).
      * padded        — a real intent with incidental surrounding whitespace
                        -> backfilled TRIMMED.
    """
    import json as _json
    from datetime import UTC, datetime

    import sqlalchemy as sa

    engine = sa.create_engine(f"sqlite:///{db_path}")

    metadata = sa.MetaData()
    metadata.reflect(bind=engine, only=["strategy_store", "strategy_proposals"])
    strategy_store = metadata.tables["strategy_store"]
    strategy_proposals = metadata.tables["strategy_proposals"]

    # A real datetime object, not an ISO string — Core insert against a
    # DateTime column (unlike the ORM path elsewhere in this file) requires
    # one, and SQLite's DBAPI adapter rejects a string outright.
    now = datetime(2026, 8, 1, tzinfo=UTC)

    def _strategy_row(sid: str, name: str, thesis: str, method: str = "debate") -> dict:
        return {
            "id": sid,
            "content_hash": ("0x" + sid).ljust(66, "0"),
            "generation_method": method,
            "source_papers": "[]",
            "strategy_name": name,
            "thesis": thesis,
            "asset_universe": "[]",
            "risk_profile": "moderate",
            "status": "candidate",
            "is_example": False,
            "is_published": False,
            "created_at": now,
            "updated_at": now,
        }

    def _proposal_row(pid: str, gen_id: str, name: str, thesis: str, intent: str) -> dict:
        payload = _json.dumps(
            {
                "intent": intent,
                "strategy_spec": {"strategy_name": name, "thesis": thesis, "weights": {}, "asset_universe": []},
                "papers": [],
                "rigor_verdict": None,
                "agent": "debate",
            }
        )
        return {
            "id": pid,
            "generation_id": gen_id,
            "proposal_id": pid,
            "verdict": "selected",
            "trust_level": "CANDIDATE",
            "content_hash": ("0z" + pid).ljust(66, "0"),
            "agent": "debate",
            "payload": payload,
            "created_at": now,
            "updated_at": now,
        }

    with engine.begin() as conn:
        conn.execute(
            strategy_store.insert(),
            [
                _strategy_row("unambig01", "Unambiguous Strategy", "Its thesis"),
                _strategy_row("ambig0001", "Ambiguous Strategy", "Its thesis"),
                _strategy_row("nomatch01", "No Match Strategy", "Its thesis"),
                _strategy_row("curated01", "Ambiguous Strategy", "Its thesis", method="curated"),
                _strategy_row("blank0001", "Blank Brief Strategy", "Its thesis"),
                _strategy_row("padded001", "Padded Brief Strategy", "Its thesis"),
            ],
        )
        conn.execute(
            strategy_proposals.insert(),
            [
                _proposal_row("p-unambig", "gen-1", "Unambiguous Strategy", "Its thesis", "Beat SPY in a downturn"),
                # Two DIFFERENT jobs coincidentally produced the identical
                # (name, thesis) pair under two DIFFERENT briefs — the
                # collision case. Every real proposal write shares an intent
                # with every OTHER proposal from the SAME job, but nothing
                # stops two SEPARATE jobs from colliding on name/thesis text
                # (most plausible with the deterministic fixture path).
                _proposal_row("p-ambig-a", "gen-2", "Ambiguous Strategy", "Its thesis", "Brief A"),
                _proposal_row("p-ambig-b", "gen-3", "Ambiguous Strategy", "Its thesis", "Brief B"),
                # Whitespace-only and padded intents — the migration must
                # normalize both the way upsert_strategy does.
                _proposal_row("p-blank00", "gen-4", "Blank Brief Strategy", "Its thesis", "   \n\t "),
                _proposal_row(
                    "p-padded0", "gen-5", "Padded Brief Strategy", "Its thesis", "  Beat SPY in a drawdown \n"
                ),
            ],
        )

    engine.dispose()


def test_brief_intent_backfill_resolves_only_unambiguous_rows(tmp_path):
    """Acceptance: unambiguous match backfilled; ambiguous, no-match, and
    curated rows all left NULL — never guessed."""
    db_path = tmp_path / "brief_intent_backfill.db"
    database_url = f"sqlite:///{db_path}"

    pre = _run_alembic("upgrade", _brief_intent_migration_down_revision(), database_url=database_url)
    assert pre.returncode == 0, pre.stderr

    _seed_pre_brief_intent_rows(db_path)

    post = _run_alembic("upgrade", "head", database_url=database_url)
    assert post.returncode == 0, f"brief_intent migration failed:\nSTDOUT:\n{post.stdout}\nSTDERR:\n{post.stderr}"

    con = sqlite3.connect(str(db_path))
    try:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT id, brief_intent FROM strategy_store ORDER BY id")
        by_id = {row["id"]: row["brief_intent"] for row in cur.fetchall()}
    finally:
        con.close()

    assert by_id["unambig01"] == "Beat SPY in a downturn"
    assert by_id["ambig0001"] is None, "colliding (name, thesis) with two distinct intents must never be guessed"
    assert by_id["nomatch01"] is None, "no matching proposal at all must stay NULL"
    assert by_id["curated01"] is None, "curated rows are never considered, even on a coincidental name/thesis match"
    assert by_id["blank0001"] is None, "a whitespace-only logged intent must stay NULL, not land as a row of blanks"
    assert by_id["padded001"] == "Beat SPY in a drawdown", "a padded intent must be backfilled trimmed"


def test_brief_intent_backfill_migration_upgrade_is_idempotent(tmp_path):
    """Running the backfill migration's upgrade a second time (downgrade to
    its own down_revision, then upgrade to head again) must be a no-op:
    identical resolved values, no error."""
    db_path = tmp_path / "brief_intent_idempotent.db"
    database_url = f"sqlite:///{db_path}"
    down_revision = _brief_intent_migration_down_revision()

    pre = _run_alembic("upgrade", down_revision, database_url=database_url)
    assert pre.returncode == 0, pre.stderr
    _seed_pre_brief_intent_rows(db_path)

    first = _run_alembic("upgrade", "head", database_url=database_url)
    assert first.returncode == 0, first.stderr

    def _snapshot() -> dict[str, str | None]:
        con = sqlite3.connect(str(db_path))
        try:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            cur.execute("SELECT id, brief_intent FROM strategy_store ORDER BY id")
            return {row["id"]: row["brief_intent"] for row in cur.fetchall()}
        finally:
            con.close()

    after_first = _snapshot()

    second_down = _run_alembic("downgrade", down_revision, database_url=database_url)
    assert second_down.returncode == 0, second_down.stderr
    second_up = _run_alembic("upgrade", "head", database_url=database_url)
    assert second_up.returncode == 0, f"second upgrade head failed:\n{second_up.stderr}"

    assert _snapshot() == after_first, "re-running the brief_intent backfill changed already-resolved data"


def test_alembic_paper_marks_table_added_and_removed(tmp_path):
    """Intraday marks v1: ``paper_marks`` and the two ``paper_deployments``
    position-cache columns land on upgrade, are gone on downgrade, and come
    back on re-upgrade — the per-migration up/down/idempotent contract
    exercised directly.

    Same derived-target discipline as the tests above: the downgrade target is
    this revision's OWN ``down_revision``, never a hardcoded hash or a
    relative ``-1``, so it keeps testing this migration however many revisions
    later land on top of it.
    """
    db_path = tmp_path / "paper_marks.db"
    database_url = f"sqlite:///{db_path}"

    def _paper_deployment_columns() -> set[str]:
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            cur.execute("PRAGMA table_info(paper_deployments)")
            return {row[1] for row in cur.fetchall()}
        finally:
            con.close()

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(_BACKEND_DIR / "alembic.ini")))
    target = script.get_revision("e41c7a9b2d63").down_revision

    upgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr
    assert "paper_marks" in _table_names(db_path)
    assert {"position_cache_json", "position_cache_at"} <= _paper_deployment_columns()

    downgrade = _run_alembic("downgrade", target, database_url=database_url)
    assert downgrade.returncode == 0, downgrade.stderr
    assert "paper_marks" not in _table_names(db_path)
    assert not ({"position_cache_json", "position_cache_at"} & _paper_deployment_columns())
    # The ledger the marks decorate is NOT collateral damage of a rollback:
    # paper_daily_returns predates this revision and must survive it.
    assert "paper_daily_returns" in _table_names(db_path)

    reupgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert reupgrade.returncode == 0, reupgrade.stderr
    assert "paper_marks" in _table_names(db_path)
    assert {"position_cache_json", "position_cache_at"} <= _paper_deployment_columns()

    reupgrade_again = _run_alembic("upgrade", "head", database_url=database_url)
    assert reupgrade_again.returncode == 0, reupgrade_again.stderr


def test_paper_marks_unique_constraint_actually_rejects_a_duplicate(tmp_path):
    """The constraint that makes a re-run of the daily rollup a no-op instead
    of a duplicate. A constraint nobody has seen reject anything is a comment,
    not a guard — so this inserts the conflicting row and asserts the DB
    refuses it.

    Demonstrated to reject: dropping ``uq_paper_marks_dep_ts_gran`` from the
    migration makes the second INSERT succeed and this test fail.
    """
    db_path = tmp_path / "paper_marks_uq.db"
    upgrade = _run_alembic("upgrade", "head", database_url=f"sqlite:///{db_path}")
    assert upgrade.returncode == 0, upgrade.stderr

    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO paper_deployments (id, strategy_id, spec_json, deployed_at, status, created_at) "
            "VALUES ('dep1', 's1', '{}', '2026-08-30', 'active', '2026-08-30T00:00:00')"
        )
        row = (
            "INSERT INTO paper_marks "
            "(deployment_id, ts, prices_json, portfolio_value, source, is_delayed, granularity) "
            "VALUES ('dep1', '2026-08-30 14:45:00', '{}', 1.0, 'yfinance', 1, 'raw')"
        )
        cur.execute(row)
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(row)
            con.commit()
    finally:
        con.close()


def test_alembic_paper_marks_matches_a_fresh_create_all_schema(tmp_path):
    """Column parity for ``paper_marks`` and ``paper_deployments`` between the
    two schema-management paths — the ORM's ``PaperMark`` (create_all: every
    hermetic test and local dev) and this migration's ``create_table``
    (Alembic: CI/prod).

    Divergence here is a live-in-one-environment defect of exactly the kind
    this repo has already paid for: the marks loop writes ``is_delayed`` and
    ``granularity`` by name and the retention job filters on ``granularity``,
    so a column present on one path and absent on the other means the live
    value silently stops rendering — or the prune job silently stops pruning —
    in precisely one environment.
    """
    create_all_db = tmp_path / "create_all_paper_marks.db"
    script = (
        "import sqlalchemy as sa\n"
        "from archimedes.models.account import AuthUser\n"
        "from archimedes.models.chat import Base\n"
        # Same metadata-graph completion the generation_costs parity test needs:
        # unrelated tables in Base.metadata carry FKs that create_all() must be
        # able to resolve before it will emit ANY DDL.
        "from archimedes.models.identity import WalletIdentity\n"
        "from archimedes.models.paper_store import PaperDeployment, PaperMark\n"
        f"engine = sa.create_engine('sqlite:///{create_all_db}')\n"
        "Base.metadata.create_all(bind=engine)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_BACKEND_DIR),
        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr

    alembic_db = tmp_path / "alembic_built_paper_marks.db"
    upgrade = _run_alembic("upgrade", "head", database_url=f"sqlite:///{alembic_db}")
    assert upgrade.returncode == 0, upgrade.stderr

    def _columns(db_path: Path, table: str) -> set[str]:
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cur.fetchall()}
        finally:
            con.close()

    for table in ("paper_marks", "paper_deployments"):
        assert _columns(create_all_db, table) == _columns(alembic_db, table), (
            f"{table} columns differ between create_all() and alembic upgrade head"
        )

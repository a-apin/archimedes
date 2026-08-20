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


# ── schema-relations Phase 1 (fb8d0bae8112): indices + gated FKs ────────────
#
# Three things this section proves, in order:
#  1. The raw orphan-count SQL each gated FK uses is CORRECT against a
#     realistic mixed fixture (some rows satisfy the invariant, some don't) —
#     tested directly against the queries the revision module itself defines,
#     independent of which dialect branch runs them.
#  2. `alembic upgrade head` SUCCEEDS against that same fixture with the
#     orphans still in place — the whole point of NOT VALID: a migration
#     that can't proceed past unmeasured historical data is not "minimal
#     blast radius", it's a fresh outage.
#  3. On SQLite specifically, the Postgres-only VALIDATE CONSTRAINT branch
#     never fires (there's nothing to validate) — proven from subprocess
#     stdout, not by reaching into the migration's internals.

_PHASE1_REVISION = "fb8d0bae8112"  # migrations/versions/fb8d0bae8112_schema_relations_phase1.py

_ORPHAN_WALLET = "0x" + "c" * 40  # never anchored in wallet_identities
_ORPHAN_STRATEGY = "strat-does-not-exist"  # never a row in strategy_store
_REAL_WALLET = "0x" + "a" * 40
_REAL_STRATEGY = "strat-real"


def _phase1_module():
    """The revision module itself — gives direct access to `_GATED_FKS` /
    `_INDICES` so the orphan-count SQL can be exercised without duplicating
    it here (duplicating it would test a copy, not the real thing)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(_BACKEND_DIR / "alembic.ini")))
    return script.get_revision(_PHASE1_REVISION).module


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
    """Black-box proof of the dialect gate: on SQLite, `_add_gated_fk`'s
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


def test_phase1_linked_wallets_matches_a_fresh_create_all_schema(tmp_path):
    """Column parity between the two schema-management paths for the one
    Phase-1-touched table on the `account.py` side: `create_all()`
    (`LinkedWallet.address`'s new FK/index) vs this migration's
    `batch_alter_table`. Same rationale as the existing generation_costs /
    strategy_store parity tests above."""
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


def test_phase1_paper_deployments_matches_a_fresh_create_all_schema(tmp_path):
    """Same column-parity contract, for `paper_deployments` — the table this
    revision widens `strategy_id` on and adds two FKs to."""
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

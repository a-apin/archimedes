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

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

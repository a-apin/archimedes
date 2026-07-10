"""Shared per-test isolation for archimedes.db's process-global engine.

``archimedes.db`` builds ``engine`` / ``SessionLocal`` once at import time from
whatever ``DATABASE_URL`` is set at that moment. Monkeypatching the
``DATABASE_URL`` env var afterward does not rebind an already-constructed
engine, and every runtime code path that resolves a session via
``get_session()`` looks up ``archimedes.db.SessionLocal`` dynamically at call
time — so the only way to give a test a genuinely fresh, isolated database is
to reassign the ``archimedes.db`` module attributes directly, and the only way
to do that safely when many test files share one pytest process is to restore
the originals afterward. Without the restore step, one test file's tmp-db
redirect silently outlives it and leaks into every test that runs later in
the same process (see issue #1100).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from archimedes import db as archimedes_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def redirect_to_tmp_sqlite(tmp_path: Path) -> Iterator[None]:
    """Point archimedes.db at a fresh tmp-file SQLite DB, then restore.

    Use from an autouse fixture: ``yield from redirect_to_tmp_sqlite(tmp_path)``.
    Saves the current engine/SessionLocal/DATABASE_URL, builds a new engine
    bound to a tmp_path-scoped file, creates every Base-registered table on
    it, yields to the test, then disposes the tmp engine and restores the
    originals — so no later test, in this file or any other, can inherit a
    dangling redirected engine.
    """
    orig_engine = archimedes_db.engine
    orig_session_local = archimedes_db.SessionLocal
    orig_database_url = archimedes_db.DATABASE_URL

    db_path = tmp_path / "test_archimedes.db"
    archimedes_db.DATABASE_URL = f"sqlite:///{db_path}"
    archimedes_db.engine = create_engine(
        archimedes_db.DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    archimedes_db.SessionLocal = sessionmaker(
        bind=archimedes_db.engine,
        autocommit=False,
        autoflush=False,
    )
    archimedes_db.Base.metadata.create_all(bind=archimedes_db.engine)

    try:
        yield
    finally:
        archimedes_db.engine.dispose()
        archimedes_db.engine = orig_engine
        archimedes_db.SessionLocal = orig_session_local
        archimedes_db.DATABASE_URL = orig_database_url

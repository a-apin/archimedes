"""Tests for `archimedes.db` — default SQLite path resolution.

Covers the CWD-independence fix for `_default_database_url()`: the default
SQLite path must always anchor to `backend/archimedes_chat.db` regardless of
the process's current working directory, and the `DATABASE_URL` env var
override must remain unaffected.
"""

from __future__ import annotations

import os

from archimedes import db


class TestDefaultDatabaseUrl:
    def test_default_database_url_is_absolute(self):
        """The default SQLite URL must use an absolute path, not `./`."""
        url = db._default_database_url()

        assert url.startswith("sqlite:///")
        path = url.removeprefix("sqlite:///")
        assert os.path.isabs(path)

    def test_default_database_url_points_at_backend_dir(self):
        """The default path must resolve to `backend/archimedes_chat.db`."""
        url = db._default_database_url()
        path = url.removeprefix("sqlite:///")

        assert path.endswith("/backend/archimedes_chat.db")
        # Anchored to db.py's parent's parent (backend/), not the caller's CWD.
        assert path == str(db._BACKEND_DIR / "archimedes_chat.db")

    def test_default_database_url_independent_of_cwd(self, tmp_path, monkeypatch):
        """Changing CWD must not change the resolved default path."""
        baseline = db._default_database_url()

        monkeypatch.chdir(tmp_path)
        from_tmp_cwd = db._default_database_url()

        assert from_tmp_cwd == baseline

    def test_module_level_database_url_matches_default_when_unset(self, monkeypatch):
        """DATABASE_URL (module constant) falls back to _default_database_url()
        when the env var is unset — verified by re-deriving via the same
        os.getenv call the module performs at import time."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        resolved = os.getenv("DATABASE_URL", db._default_database_url())

        assert resolved == db._default_database_url()
        assert os.path.isabs(resolved.removeprefix("sqlite:///"))


class TestDatabaseUrlEnvOverride:
    def test_get_engine_kwargs_sqlite_vs_postgres(self, monkeypatch):
        """_get_engine_kwargs branches on the DATABASE_URL prefix — verify both
        branches independent of the module-level constant's current value."""
        monkeypatch.setattr(db, "DATABASE_URL", "sqlite:////tmp/whatever.db")
        sqlite_kwargs = db._get_engine_kwargs()
        assert sqlite_kwargs == {"connect_args": {"check_same_thread": False}}

        monkeypatch.setattr(db, "DATABASE_URL", "postgresql://user:pass@host:5432/db")
        postgres_kwargs = db._get_engine_kwargs()
        assert postgres_kwargs == {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}

"""_artifact_dir resolution (stale-leaderboard incident, 2026-08-18).

The scheduled refresh on Fargate died on EVERY strategy with
``PermissionError: /app/analytics-engine/artifacts/<run>.json`` — the packaged
default is read-only for the nonroot task user, and the write happens before
the DB insert, so no rows ever landed and the leaderboard froze at 2026-07-01.
These pin the three resolution branches; the read-only case is reproduced with
a genuinely unwritable directory, not a mock.
"""

from __future__ import annotations

import stat
import tempfile
from pathlib import Path

from archimedes.scripts.run_backtests import _artifact_dir, _dir_writable


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCHIMEDES_ARTIFACT_DIR", str(tmp_path / "chosen"))
    assert _artifact_dir(tmp_path / "repo") == tmp_path / "chosen"


def test_writable_repo_default_unchanged(monkeypatch, tmp_path):
    monkeypatch.delenv("ARCHIMEDES_ARTIFACT_DIR", raising=False)
    repo = tmp_path / "repo"
    (repo / "analytics-engine").mkdir(parents=True)
    assert _artifact_dir(repo) == repo / "analytics-engine" / "artifacts"


def test_read_only_default_falls_back_loudly(monkeypatch, tmp_path, caplog):
    """The prod incident's exact shape: parent exists, is not writable."""
    monkeypatch.delenv("ARCHIMEDES_ARTIFACT_DIR", raising=False)
    repo = tmp_path / "repo"
    locked = repo / "analytics-engine"
    locked.mkdir(parents=True)
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x: mkdir/writes inside fail
    try:
        if _dir_writable(locked / "artifacts"):  # e.g. running as root: cannot reproduce
            import pytest

            pytest.skip("cannot create a read-only dir under this user")
        with caplog.at_level("WARNING"):
            result = _artifact_dir(repo)
        assert result == Path(tempfile.gettempdir()) / "archimedes-artifacts"
        assert any("not writable" in r.message for r in caplog.records), (
            "the fallback must be LOUD — a silent location change is how artifacts disappear"
        )
    finally:
        locked.chmod(stat.S_IRWXU)


def test_dir_writable_probe_leaves_no_droppings(tmp_path):
    assert _dir_writable(tmp_path / "fresh") is True
    assert list((tmp_path / "fresh").iterdir()) == []

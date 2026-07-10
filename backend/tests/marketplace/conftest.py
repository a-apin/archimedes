"""Autouse DB isolation for every test under backend/tests/marketplace/.

See ``tests/db_isolation.py`` for why this can't be a plain
``Base.metadata.create_all`` / ``drop_all`` on the module-level engine —
``archimedes.db.engine`` is a process-global singleton, and a test elsewhere
in this directory reassigning it without restoring breaks that pattern for
every other test in the same pytest process (issue #1100).
"""

from __future__ import annotations

import pytest

from tests.db_isolation import redirect_to_tmp_sqlite


@pytest.fixture(autouse=True)
def _isolated_marketplace_db(tmp_path):
    yield from redirect_to_tmp_sqlite(tmp_path)

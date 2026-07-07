"""Idempotent stamp-then-upgrade pre-flight for the ECS migrate task (issue #1039 B3).

Prod Aurora has NO ``alembic_version`` table today — its schema was built by
``archimedes.db.init_db()``'s ``Base.metadata.create_all()`` plus hand-rolled
``ADD COLUMN IF NOT EXISTS`` patches (see ``archimedes/db.py`` and the
timestamped ``.sql`` files under ``backend/migrations/`` that predate
Alembic), never by Alembic itself. The FIRST bare ``alembic upgrade head``
against that already-populated database tries to CREATE every baseline
table again and fails with "relation ... already exists" — it has no way to
know the schema is already there.

This script closes that gap exactly once, idempotently, as the ECS migrate
task's command (see ``infra/ecs_migrate.tf`` /
``.github/workflows/deploy.yml``'s ``migrate`` job): if the
``alembic_version`` table is absent AND a known baseline table already
exists, it stamps the baseline revision as already-applied BEFORE running
``alembic upgrade head`` for real. On every subsequent run (this task runs on
every deploy), ``alembic_version`` already exists, so the stamp step is
skipped and only ``upgrade head`` runs — the normal, permanent path. A truly
fresh database (CI, a new dev clone) has neither ``alembic_version`` nor the
baseline sentinel table, so the stamp is skipped there too and ``upgrade
head`` runs every revision for real, including the baseline.

Deferred ``alembic`` import: this module (and the whole ``archimedes``
package) must stay importable before ``backend/alembic.ini`` /
``backend/migrations/`` land (issue #1028) and before ``alembic`` is a
declared dependency again (it was dropped as unused in PR #354, 2026-05-25).
A module-level ``import alembic`` would break that. This mirrors the exact
self-activation pattern already used by ``.github/workflows/deploy.yml``'s
``migrate`` job (its own ``check`` step no-ops the whole job until
``backend/alembic.ini`` exists) and ``infra/ecs_migrate.tf``'s header
comment — nothing here runs for real until that PR lands; once it does, this
activates with zero further changes.

Usage (the ECS migrate task's overridden container command):

    python -m archimedes.scripts.alembic_migrate_preflight
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# backend/alembic.ini (repo) → /app/alembic.ini (container — backend/Dockerfile's
# `COPY . /app` + `WORKDIR /app`). Derived from this file's own location
# (backend/archimedes/scripts/<this file> → parents[2] == backend/, the app
# root) rather than assumed cwd, so this works whether or not the ECS task's
# command inherits WORKDIR /app.
_APP_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _APP_ROOT / "alembic.ini"

# The baseline revision id — backend/migrations/versions/af9c6a9376e4_baseline_schema.py
# (issue #1028). This is a literal, not re-derived from the script directory at
# runtime: resolving "the baseline revision" by walking the version history
# would need Alembic's own script directory, which is exactly what's untrustworthy
# on a DB that has never been stamped. If the baseline revision is ever
# regenerated, update this literal to match — the same "literal + a place to
# grep it against" pattern used for ECS_CLUSTER / ECS_MIGRATE_TASK_FAMILY in
# .github/workflows/deploy.yml.
BASELINE_REVISION = "af9c6a9376e4"

# A table that only exists once the pre-Alembic, create_all()-built schema is
# live — the oldest table in the schema (added 2026-05-18, see
# backend/migrations/20260518_add_backtest_results.sql), so its presence is a
# reliable "this DB predates Alembic" signal that a genuinely fresh/empty DB
# (CI, a new dev clone) will never trip.
BASELINE_SENTINEL_TABLE = "backtest_results"


def _needs_baseline_stamp(engine) -> bool:
    """True iff this DB has the pre-Alembic schema but no Alembic version tracking."""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    return not inspector.has_table("alembic_version") and inspector.has_table(BASELINE_SENTINEL_TABLE)


def stamp_and_upgrade() -> None:
    """Stamp the baseline revision on an already-populated, unversioned DB
    (idempotent — a no-op once ``alembic_version`` exists), then run
    ``alembic upgrade head`` unconditionally."""
    from alembic import command
    from alembic.config import Config

    from archimedes.db import engine

    if _needs_baseline_stamp(engine):
        logger.warning(
            "MIGRATE_PREFLIGHT_STAMP: alembic_version absent but %r already exists — "
            "stamping baseline revision %s before upgrade head (issue #1039 B3).",
            BASELINE_SENTINEL_TABLE,
            BASELINE_REVISION,
        )
        stamp_cfg = Config(str(_ALEMBIC_INI))
        command.stamp(stamp_cfg, BASELINE_REVISION)
    else:
        logger.info("MIGRATE_PREFLIGHT_SKIP_STAMP: alembic_version already tracked (or DB is genuinely fresh).")

    upgrade_cfg = Config(str(_ALEMBIC_INI))
    command.upgrade(upgrade_cfg, "head")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        stamp_and_upgrade()
    except Exception:
        logger.exception("MIGRATE_PREFLIGHT_FAILED")
        sys.exit(1)
    logger.info("MIGRATE_PREFLIGHT_OK: schema is current.")

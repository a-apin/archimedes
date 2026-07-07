"""request count snapshot

Issue #1028 (chunk 4/5, metrics-views), D8. Adds
``request_count_snapshots`` — the singleton, Postgres-durable floor under the
Redis human/agent request counters (``services/telemetry_store.py``). Redis
stays the fast live counter; this table is what ``GET /api/metrics``
reconciles every read against (``services/request_snapshot_store.py``), so a
Redis restart on the box does not zero the "lifetime requests" number the
Insights page shows (AC6).

No data migration: the row is created lazily on first read
(``request_snapshot_store._get_or_create_row``), not backfilled here — there
is no historical Redis value worth seeding it with (the whole point of D8 is
that the pre-existing Redis-only counters could not survive a restart, so
"the count as of migration time" is not a number this migration can
recover).

Revision ID: 4c320621f047
Revises: 07e9c1489199
Create Date: 2026-07-06 23:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c320621f047"
down_revision: str | Sequence[str] | None = "07e9c1489199"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Portable counter width: BigInteger on Postgres, plain Integer on SQLite —
# matches models/request_snapshot.py's _CounterVariant.
_COUNTER_VARIANT = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "request_count_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("human_baseline", _COUNTER_VARIANT, nullable=False, server_default="0"),
        sa.Column("agent_baseline", _COUNTER_VARIANT, nullable=False, server_default="0"),
        sa.Column("last_redis_human", _COUNTER_VARIANT, nullable=False, server_default="0"),
        sa.Column("last_redis_agent", _COUNTER_VARIANT, nullable=False, server_default="0"),
        sa.Column("epoch_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("epoch_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshotted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("request_count_snapshots")

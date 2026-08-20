"""add generation_costs — durable per-generation measurement + the quote beside it

Issue #1326, following the #1314 cost meter. The ``cost_v1`` snapshot lived only
on the Redis job record (``JOB_TTL`` 3600s), so an hour after a generation ended
the only measurement of what it consumed was gone. This table is where it
survives, and what the passport card and the library column read.

Two payload columns, deliberately two:

  - ``measurement_json`` — the literal ``cost_v1`` snapshot. Counts and seconds.
    Screened by ``cost_meter.assert_measurement_only`` on the way in, so a
    pricing-shaped key at any depth raises rather than landing here.
  - ``quote_json`` — the literal ``generation_payment.quote()`` payload in force
    when the job started. A recorded fact about what we charged; NULL when the
    quote seam could not be read, which means "not recorded", never "$0.00".

Separate columns are the design, not an accident of normalization:
quote-vs-measured must be a pairing of two independently recorded facts, never a
server-side conversion of tokens into dollars (that is #1217's remaining pricing
work and it happens off-server).

``strategy_id`` carries no foreign key, matching ``paper_deployments`` — the
sibling table that also references ``strategy_store.id``. Purging a strategy row
must not delete the record of what its run consumed: the run still happened.

BACKFILL (migrations-ship-with-their-data rule): **none, and none is possible.**
The table is new and every pre-#1314 generation was never measured — there is no
recoverable figure for those runs, and fabricating one is precisely the failure
this instrumentation exists to end. Historical strategies therefore render as
"not measured" on the passport and as an em-dash in the library, which is the
honest state. The issue's own anti-goal says the same: don't backfill.

Revision ID: e2b7f4c81d93
Revises: c9396e0d95d4
Create Date: 2026-08-20 00:00:00.000000

SEQUENCING: chained onto ``c9396e0d95d4`` (asset_daily_bars), the chain head at
authoring time. If another migration lands on main first, re-point
``down_revision`` at the new head — the chain stays serial (the two-heads fork
has bitten this repo three times; ``.github/scripts/migration_chain_guard.py``
is the check that catches it).

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2b7f4c81d93"
down_revision: str | Sequence[str] | None = "c9396e0d95d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "generation_costs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("measurement_json", sa.Text(), nullable=False),
        sa.Column("quote_json", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "strategy_id", name="uq_generation_costs_job_strategy"),
    )
    op.create_index("ix_generation_costs_strategy", "generation_costs", ["strategy_id"])


def downgrade() -> None:
    """Downgrade schema.

    Dropping the table discards the measurements recorded while this revision
    was live. There is nowhere else to put them — the Redis job record they used
    to live on has an hour's TTL, which is the gap this revision closes — so a
    downgrade is a deliberate loss of measurement, not a reversible move.
    """
    op.drop_index("ix_generation_costs_strategy", table_name="generation_costs")
    op.drop_table("generation_costs")

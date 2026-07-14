"""add strategy_spec to strategy_store

Rebalancer decouple (Part A #1, `docs/CURATED-STRATEGY-DECOUPLE-AND-CONSOLIDATE-
2026-07-08.md`): the live agent runner (`chain/agent_runner.py::tick()`) only
ever evaluated CURATED strategies (`default_provider().list_strategies()`) —
a vault deployed from a GENERATED strategy is scoped (Issue #307) to a
strategy_id that never appears in that curated signal set, so it silently
never rebalances. ``strategy_signal_evaluator.evaluate_strategies`` already
has a DSL-spec dispatch branch (``getattr(strategy, "strategy_spec", None)``)
— it was simply never fed a generated strategy, because ``strategy_store``
had nowhere to persist one.

This revision adds a nullable ``strategy_spec`` (JSON-encoded DSL spec dict,
mirroring ``StrategyPassport.strategy_spec``) so newly-generated strategies
can persist their validated spec and the agent runner can load + evaluate it
for any vault bound to a generated strategy_id.

Backfill: existing generated ``StrategyRecord`` rows (persisted before this
column existed, from ``strategy_proposals`` payloads) are left with
``strategy_spec IS NULL`` — the agent runner already skips a generated
strategy_id with no spec (same behavior as today, just now correctly scoped
to only the ids that lack one). Backfilling old rows is an explicit
follow-up, out of scope here.

Revision ID: 3f643d292e04
Revises: 7b6e8d812331
Create Date: 2026-07-09 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f643d292e04"
down_revision: str | Sequence[str] | None = "7b6e8d812331"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("strategy_store", schema=None) as batch_op:
        # Text, not a typed JSON column — matches this table's existing JSON
        # columns (source_papers, rigor_verdict), which are also hand-encoded
        # Text for SQLite portability. Nullable: curated/example rows and
        # legacy generated rows never had a spec to persist.
        batch_op.add_column(sa.Column("strategy_spec", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("strategy_store", schema=None) as batch_op:
        batch_op.drop_column("strategy_spec")

"""add owner_wallet to strategy_proposals

Privacy-leak fix (`dbrowneup/proposals-owner-scope`): ``GET /api/proposals/``
and ``GET /api/proposals/{generation_id}/siblings`` were completely
unauthenticated and returned EVERY user's ``StrategyProposal`` rows —
including each user's private ``intent``, full ``strategy_spec``, and
``rigor_verdict``, for rejected candidates too. ``strategy_proposals`` had no
owner column, so there was nothing to scope reads on. This revision adds
``owner_wallet`` (SIWE-derived, lowercased, server-bound — mirrors
``strategy_store.owner_wallet``'s shape) so ``proposals_routes.py`` can filter
in the query rather than fetch-all-then-filter in Python.

Backfill: existing rows are left with ``owner_wallet IS NULL``. They predate
per-user ownership, so there is no wallet to backfill them with; the
owner-scoped read path treats a NULL-owner row as private to NO ONE (an exact
``owner_wallet = :caller`` match never satisfies NULL), not as visible to
everyone the way the old unauthenticated endpoint made every row.

Revision ID: 7b6e8d812331
Revises: 4c320621f047
Create Date: 2026-07-08 19:33:56.219407

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7b6e8d812331"
down_revision: str | Sequence[str] | None = "4c320621f047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("strategy_proposals", schema=None) as batch_op:
        # String(42) matches strategy_store.owner_wallet's width (0x + 40 hex
        # chars); nullable — legacy rows stay NULL (see module docstring).
        batch_op.add_column(sa.Column("owner_wallet", sa.String(length=42), nullable=True))
        batch_op.create_index(batch_op.f("ix_strategy_proposals_owner_wallet"), ["owner_wallet"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("strategy_proposals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_strategy_proposals_owner_wallet"))
        batch_op.drop_column("owner_wallet")

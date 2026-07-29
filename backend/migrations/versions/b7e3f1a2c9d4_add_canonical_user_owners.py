"""add canonical Better Auth user owners

Add nullable user ownership beside legacy wallet provenance. Existing rows
remain unclaimed until that wallet is cryptographically linked by its owner.

Revision ID: b7e3f1a2c9d4
Revises: 9ad1c4e2b7f0
Create Date: 2026-07-28 15:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e3f1a2c9d4"
down_revision: str | Sequence[str] | None = "9ad1c4e2b7f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic reads these module globals reflectively.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLES = ("strategy_store", "strategy_passports", "strategy_proposals", "user_profiles", "vault_metadata")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("owner_user_id", sa.String(length=64), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table}_owner_user_id",
                "auth_users",
                ["owner_user_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_index(f"ix_{table}_owner_user_id", ["owner_user_id"], unique=table == "user_profiles")


def downgrade() -> None:
    for table in reversed(_TABLES):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f"ix_{table}_owner_user_id")
            batch_op.drop_constraint(f"fk_{table}_owner_user_id", type_="foreignkey")
            batch_op.drop_column("owner_user_id")

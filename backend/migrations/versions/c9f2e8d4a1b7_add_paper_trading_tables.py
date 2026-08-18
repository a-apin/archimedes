"""add paper-trading deployments and append-only daily-return ledgers

MVP paper trading (Dan's design, 2026-08-18): a deployment snapshots the spec
at deploy time and forward-runs the GRADED backtest engine one bar per day.
The ledger table is append-only by law — deliberately a sibling of
strategy_daily_returns, whose own law (wholesale replace per re-measurement)
is the opposite of what a user-facing track record may do.

Revision ID: c9f2e8d4a1b7
Revises: b41c7d9e2a05
Create Date: 2026-08-18 12:00:00.000000

SEQUENCING NOTE: authored against main's head b41c7d9e2a05 while PR #1194
(Better Auth) carries 9ad1c4e2b7f0 -> b7e3f1a2c9d4 against the same parent.
Whichever lands second must re-point onto the other's head — the two-heads
fork has now bitten this repo twice (2026-08-03 deploy outage; #1194's CI on
2026-08-18), so this note exists to make the third time a conscious step
instead of a surprise. The tables are disjoint from #1194's (new paper_*
tables only).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9f2e8d4a1b7"
down_revision: str | Sequence[str] | None = "b41c7d9e2a05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "paper_deployments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("owner_wallet", sa.String(length=42), nullable=True),
        sa.Column("owner_user_id", sa.String(length=64), nullable=True),
        sa.Column("spec_json", sa.Text(), nullable=False),
        sa.Column("deployed_at", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("drift_detected_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_deployments_owner_wallet", "paper_deployments", ["owner_wallet"])
    op.create_index("ix_paper_deployments_strategy", "paper_deployments", ["strategy_id"])
    op.create_table(
        "paper_daily_returns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("deployment_id", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("daily_return", sa.Float(), nullable=False),
        sa.Column("appended_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["paper_deployments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deployment_id", "date", name="uq_paper_daily_returns_dep_date"),
    )
    op.create_index("ix_paper_daily_returns_dep", "paper_daily_returns", ["deployment_id"])


def downgrade() -> None:
    op.drop_index("ix_paper_daily_returns_dep", table_name="paper_daily_returns")
    op.drop_table("paper_daily_returns")
    op.drop_index("ix_paper_deployments_strategy", table_name="paper_deployments")
    op.drop_index("ix_paper_deployments_owner_wallet", table_name="paper_deployments")
    op.drop_table("paper_deployments")

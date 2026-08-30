"""add generation_credits — a settled payment survives a failed job (#1441)

The x402 generation paywall settles the charge and then enqueues the job.
Between those points the entitlement gate can raise 402 and the enqueue can
error; after them the worker can crash, the LLM can fail, or a container roll
can take the run down. In every one of those cases the money was taken and
nothing released it, and there was no record that the payer was owed anything.

This table is that record. A settled payment buys a credit; a generation spends
the credit. If the run does not deliver, the credit goes back to ``available``
and the payer's next attempt spends it instead of paying again.

A refund was the alternative and was rejected: settling runs one way through
Circle's facilitator, so refunding means a fresh outbound transfer out of the
recipient DCW — the money path, gated on #975's custody migration. See
``docs/adr/generation-payment-credit-not-refund.md``.

The ``pending`` state is claimed BEFORE the settle call, mirroring
``marketplace/service.py``'s ``_claim_settlement_intent``. x402 is not
crash-retry-idempotent — a retry signs a fresh EIP-3009 nonce and settles as a
new payment — so a logical key claimed ahead of the charge is the only thing
that can recognise a retry. ``uq_generation_credits_user_key`` is what makes
that claim atomic.

BACKFILL (migrations-ship-with-their-data rule): **none, and none is needed.**
``PAYMENTS_DRY_RUN`` has held in production for the whole life of the paywall,
so no generation payment has ever settled real value — there is no historical
charge that is owed a credit. Every row in ``payment_receipts`` predating this
revision was written on a path where no value moved.

Revision ID: a3f19c7d2e84
Revises: 1752121b8d7c
Create Date: 2026-08-29 00:00:00.000000

SEQUENCING: chained onto ``1752121b8d7c``, the chain head at authoring time.
If another migration lands on main first, re-point ``down_revision`` at the
new head — ``.github/scripts/migration_chain_guard.py`` catches a fork.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f19c7d2e84"
down_revision: str | Sequence[str] | None = "1752121b8d7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "generation_credits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payer_wallet", sa.String(length=64), nullable=True),
        sa.Column("amount_base_units", sa.Integer(), nullable=True),
        sa.Column("price_usd", sa.String(length=32), nullable=True),
        sa.Column("network", sa.String(length=64), nullable=True),
        sa.Column("settlement_ref", sa.String(length=128), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # The atomic half of the idempotency claim. NULL keys do not collide
        # under UNIQUE in either Postgres or SQLite, so a caller who sends no
        # Idempotency-Key can still hold several credits at once.
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_generation_credits_user_key"),
    )
    op.create_index("ix_generation_credits_user_status", "generation_credits", ["user_id", "status"])


def downgrade() -> None:
    """Downgrade schema.

    Dropping the table discards every unspent credit. Those represent payments
    that settled and were never delivered against — money owed to real payers,
    kept nowhere else. A downgrade is a deliberate write-off, not a reversible
    move.
    """
    op.drop_index("ix_generation_credits_user_status", table_name="generation_credits")
    op.drop_table("generation_credits")

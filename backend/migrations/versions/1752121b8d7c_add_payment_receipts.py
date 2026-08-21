"""add payment_receipts — durable per-generation payment receipts

Dan's directive (2026-08-21): "we must provide people with their receipts."
The $2/generation x402 paywall is live on testnet and settles through
Circle's facilitator, but nothing survived the request — a payer had no way
to look back at what they were charged. This table is where the settled
``PaymentInfo`` (``verified``/``payer``/``amount``/``network``/``transaction``/
``response_headers``) survives, written at the settle site
(``api/generate_routes.py``'s ``start_generation``, fail-safe: a write
failure there never fails or delays the paid generation) and read back by
``GET /api/payments/receipts``.

``settlement_ref`` stores ``PaymentInfo.transaction`` — a Circle facilitator
reference id, NOT an on-chain transaction hash (Circle batches and settles
on-chain later). The read surface must never render it as an arcscan link.

BACKFILL (migrations-ship-with-their-data rule): **none, and none is
possible.** The table is new; every generation payment settled before this
revision has no recoverable receipt record — there is nothing to backfill
from, since the settled ``PaymentInfo`` was never persisted anywhere. Those
earlier payments simply predate the receipts list.

Revision ID: 1752121b8d7c
Revises: f0ab58339d55
Create Date: 2026-08-21 00:00:00.000000

SEQUENCING: chained onto ``f0ab58339d55``, the chain head at authoring time.
If another migration lands on main first, re-point ``down_revision`` at the
new head — ``.github/scripts/migration_chain_guard.py`` catches a fork.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1752121b8d7c"
down_revision: str | Sequence[str] | None = "f0ab58339d55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "payment_receipts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("payer_wallet", sa.String(length=64), nullable=False),
        sa.Column("amount_base_units", sa.Integer(), nullable=False),
        sa.Column("price_usd", sa.String(length=32), nullable=False),
        sa.Column("network", sa.String(length=64), nullable=False),
        sa.Column("settlement_ref", sa.String(length=128), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_receipts_user_id", "payment_receipts", ["user_id"])


def downgrade() -> None:
    """Downgrade schema.

    Dropping the table discards every receipt recorded while this revision
    was live. There is nowhere else they are kept — this table is the sole
    durable record of a settled generation payment — so a downgrade is a
    deliberate loss of receipts, not a reversible move.
    """
    op.drop_index("ix_payment_receipts_user_id", table_name="payment_receipts")
    op.drop_table("payment_receipts")

"""add asset_daily_bars vendor cache table

Market-data vendor seam (#775 / #1218): a Postgres read-through cache of
daily close bars, fronting the swappable ``MarketDataProvider`` (default
yfinance, see ``archimedes.services.market_data_provider``). Populated on
miss by the provider's ``CachingMarketDataProvider`` — this migration only
creates the empty table; no backfill (there is nothing to backfill: the
table did not exist before, and the cache primes itself from the first live
read).

Chained onto ``c9f2e8d4a1b7`` (paper trading) — re-serialized 2026-08-19
after the merge train landed #1194 (``b7e3f1a2c9d4``) and #1268
(``c9f2e8d4a1b7``); originally authored against ``b41c7d9e2a05``, with the
re-point predicted in this docstring from day one. Chain stays serial.

Revision ID: c9396e0d95d4
Revises: c9f2e8d4a1b7
Create Date: 2026-08-19 09:16:04.228106

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9396e0d95d4"
down_revision: str | Sequence[str] | None = "c9f2e8d4a1b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "asset_daily_bars",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "trade_date", name="uq_asset_daily_bars_symbol_trade_date"),
    )
    with op.batch_alter_table("asset_daily_bars", schema=None) as batch_op:
        batch_op.create_index("ix_asset_daily_bars_symbol", ["symbol"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("asset_daily_bars", schema=None) as batch_op:
        batch_op.drop_index("ix_asset_daily_bars_symbol")
    op.drop_table("asset_daily_bars")

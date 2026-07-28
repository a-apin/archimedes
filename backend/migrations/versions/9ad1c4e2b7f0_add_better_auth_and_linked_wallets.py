"""add Better Auth accounts and linked wallets

Better Auth users become canonical application identities. Wallets remain
verified external accounts and existing wallet-keyed tables are unchanged by
this revision; user ownership adapters land separately.

Revision ID: 9ad1c4e2b7f0
Revises: 3f643d292e04
Create Date: 2026-07-28 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9ad1c4e2b7f0"
down_revision: str | Sequence[str] | None = "3f643d292e04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("emailVerified", sa.Boolean(), nullable=False),
        sa.Column("image", sa.Text(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("expiresAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ipAddress", sa.Text(), nullable=True),
        sa.Column("userAgent", sa.Text(), nullable=True),
        sa.Column("userId", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["userId"], ["auth_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )
    op.create_index("auth_sessions_userId_idx", "auth_sessions", ["userId"])
    op.create_table(
        "auth_accounts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("accountId", sa.Text(), nullable=False),
        sa.Column("providerId", sa.String(length=64), nullable=False),
        sa.Column("userId", sa.String(length=64), nullable=False),
        sa.Column("accessToken", sa.Text(), nullable=True),
        sa.Column("refreshToken", sa.Text(), nullable=True),
        sa.Column("idToken", sa.Text(), nullable=True),
        sa.Column("accessTokenExpiresAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refreshTokenExpiresAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("password", sa.Text(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["userId"], ["auth_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("auth_accounts_userId_idx", "auth_accounts", ["userId"])
    op.create_table(
        "auth_verifications",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("expiresAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("auth_verifications_identifier_idx", "auth_verifications", ["identifier"])
    op.create_table(
        "linked_wallets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("normalized_identity", sa.String(length=128), nullable=False),
        sa.Column("address", sa.String(length=42), nullable=False),
        sa.Column("display_address", sa.String(length=42), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("circle_wallet_id", sa.String(length=128), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("address = lower(address)", name="ck_linked_wallets_address_lower"),
        sa.CheckConstraint("chain_id > 0", name="ck_linked_wallets_chain_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["auth_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("circle_wallet_id"),
        sa.UniqueConstraint("normalized_identity"),
    )
    op.create_index("ix_linked_wallets_user_id", "linked_wallets", ["user_id"])
    op.create_index(
        "uq_linked_wallets_one_primary_per_user",
        "linked_wallets",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
        sqlite_where=sa.text("is_primary = 1"),
    )
    op.create_table(
        "wallet_link_challenges",
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("address", sa.String(length=42), nullable=False),
        sa.Column("display_address", sa.String(length=42), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("circle_wallet_id", sa.String(length=128), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("uri", sa.String(length=512), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("address = lower(address)", name="ck_wallet_link_challenges_address_lower"),
        sa.CheckConstraint("chain_id > 0", name="ck_wallet_link_challenges_chain_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["auth_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("nonce_hash"),
    )
    op.create_index("ix_wallet_link_challenges_user_id", "wallet_link_challenges", ["user_id"])
    op.create_index("ix_wallet_link_challenges_expires_at", "wallet_link_challenges", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_wallet_link_challenges_expires_at", table_name="wallet_link_challenges")
    op.drop_index("ix_wallet_link_challenges_user_id", table_name="wallet_link_challenges")
    op.drop_table("wallet_link_challenges")
    op.drop_index("uq_linked_wallets_one_primary_per_user", table_name="linked_wallets")
    op.drop_index("ix_linked_wallets_user_id", table_name="linked_wallets")
    op.drop_table("linked_wallets")
    op.drop_index("auth_verifications_identifier_idx", table_name="auth_verifications")
    op.drop_table("auth_verifications")
    op.drop_index("auth_accounts_userId_idx", table_name="auth_accounts")
    op.drop_table("auth_accounts")
    op.drop_index("auth_sessions_userId_idx", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("auth_users")

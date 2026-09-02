"""Better Auth accounts and verified wallet links.

Better Auth owns writes to the five ``auth_*`` tables. SQLAlchemy maps the
same schema so application rows can reference the canonical user ID without
parsing Better Auth cookies or duplicating account state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from archimedes.models.chat import Base


def _now() -> datetime:
    return datetime.now(UTC)


# ── The platform legacy-adoption account (#1283) ───────────────────────────
#
# A real ``auth_users`` row, created idempotently by the alembic revision that
# adopts orphaned pre-account rows, so the ownership FK those rows carry
# (``owner_user_id -> auth_users.id``) points at something that exists. It is
# an OWNER, never a LOGIN:
#
#   * no ``auth_accounts`` credential row and no ``auth_sessions`` row is ever
#     written for it, so nothing can authenticate as it — adopted rows are
#     therefore readable by nobody, which is the fail-closed direction;
#   * the address is in the RFC 6761 ``.invalid`` TLD, which by definition
#     resolves nowhere, so no email flow can ever reach it — and because
#     ``auth_users.email`` is UNIQUE, a signup attempting this address is
#     rejected by the database rather than taking the account over;
#   * every account COUNT in this codebase excludes it by id (see
#     ``services/user_stats.py`` and ``services/engagement_metrics.py``) —
#     a bookkeeping row must never inflate a user-count the product publishes.
#
# The id is fixed and documented rather than generated: the migration, its
# downgrade, the release path, and the metrics filters all have to name the
# same account, and a generated id would make the migration non-idempotent.
PLATFORM_LEGACY_USER_ID = "platform-legacy"
PLATFORM_LEGACY_EMAIL = "platform-legacy@archimedes.invalid"
PLATFORM_LEGACY_NAME = "Archimedes Platform (legacy rows)"


class AuthUser(Base):
    __tablename__ = "auth_users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    email_verified: Mapped[bool] = mapped_column("emailVerified", Boolean, nullable=False, default=False)
    image: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        "updatedAt", DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column("expiresAt", DateTime(timezone=True), nullable=False)
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        "updatedAt", DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
    ip_address: Mapped[str | None] = mapped_column("ipAddress", Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column("userAgent", Text, nullable=True)
    user_id: Mapped[str] = mapped_column(
        "userId", String(64), ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False, index=True
    )


class AuthAccount(Base):
    __tablename__ = "auth_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column("accountId", Text, nullable=False)
    provider_id: Mapped[str] = mapped_column("providerId", String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(
        "userId", String(64), ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    access_token: Mapped[str | None] = mapped_column("accessToken", Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column("refreshToken", Text, nullable=True)
    id_token: Mapped[str | None] = mapped_column("idToken", Text, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        "accessTokenExpiresAt", DateTime(timezone=True), nullable=True
    )
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(
        "refreshTokenExpiresAt", DateTime(timezone=True), nullable=True
    )
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        "updatedAt", DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class AuthVerification(Base):
    __tablename__ = "auth_verifications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    identifier: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column("expiresAt", DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        "updatedAt", DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_request: Mapped[int] = mapped_column("lastRequest", BigInteger, nullable=False)


class LinkedWallet(Base):
    """Wallet ownership proven by a Better Auth user; never an app session."""

    __tablename__ = "linked_wallets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    normalized_identity: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    # FK retrofit (schema-relations Phase 1): the sole bridge between Better
    # Auth and the SIWE identity ledger — every row here is written only
    # AFTER `_link_verified_wallet` (wallet_routes.py) has flushed the
    # matching WalletIdentity, so this converts an existing app-code
    # invariant into a schema guarantee. Indexed: this is the column every
    # "which account owns wallet W" query filters on.
    address: Mapped[str] = mapped_column(
        String(42), ForeignKey("wallet_identities.wallet_address"), nullable=False, index=True
    )
    display_address: Mapped[str] = mapped_column(String(42), nullable=False)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    circle_wallet_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        CheckConstraint("address = lower(address)", name="ck_linked_wallets_address_lower"),
        CheckConstraint("chain_id > 0", name="ck_linked_wallets_chain_positive"),
        Index(
            "uq_linked_wallets_one_primary_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_primary"),
            sqlite_where=text("is_primary = 1"),
        ),
    )


class WalletLinkChallenge(Base):
    """Hashed, user-bound, short-lived, single-use wallet proof challenge."""

    __tablename__ = "wallet_link_challenges"

    nonce_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    address: Mapped[str] = mapped_column(String(42), nullable=False)
    display_address: Mapped[str] = mapped_column(String(42), nullable=False)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    circle_wallet_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    uri: Mapped[str] = mapped_column(String(512), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        CheckConstraint("address = lower(address)", name="ck_wallet_link_challenges_address_lower"),
        CheckConstraint("chain_id > 0", name="ck_wallet_link_challenges_chain_positive"),
    )

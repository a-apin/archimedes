"""Wallet-anchored identity ledger — issue #1028 (D1/D1a/D2/D3/D7).

Three tables, per the issue's locked Target schema:

  - ``wallet_identities`` — the identity anchor. SIWE-capable wallets ONLY
    (D1/D1a). ``wallet_address`` is the primary key everything else in the
    system either FKs to or is deliberately kept out of (``controlled_wallets``).
  - ``controlled_wallets`` — every address the SYSTEM operates: agent wallets,
    Circle DCWs, treasury (D1a/D3). Never an identity; ``controller_wallet``
    FKs back to ``wallet_identities`` (NULL = platform-owned). Population of
    this table (agent_runner, the marketplace engine, Circle DCWs) is Phase B
    work — this chunk only creates the table.
  - ``identity_events`` — the durable ledger (D2). Every lifecycle event
    lands here; every metric becomes a SQL view over this table. Emitting
    events at write points (incl. the missed SIWE-verify write) is Phase B
    work — this chunk only creates the table.

D7: NULL-owner rows (curated passports, pre-SIWE strategies) backfill to a
synthetic ``system`` identity rather than carrying a NULL owner. ``'system'``
is not a real 0x address — it satisfies the anchor's own lowercase CHECK
(``'system' == lower('system')``) and gives every retrofitted owner column a
real FK target instead of a nullable escape hatch.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from archimedes.models.chat import Base

# D7 sentinel identity — never a real wallet, always lowercase by construction.
SYSTEM_WALLET = "system"

# D3: the actor_class taxonomy shared by wallet_identities + identity_events.
ACTOR_CLASSES = ("human", "agent", "operator_dogfood", "system")

# controlled_wallets.wallet_class taxonomy per the Target schema comment.
CONTROLLED_WALLET_CLASSES = (
    "trading_agent",
    "subscriber_payment",
    "publisher_settlement",
    "treasury",
)

# Portable JSON column: JSONB on Postgres (matches the Target schema exactly),
# plain JSON on SQLite (no native JSONB there — this is the same
# with_variant pattern used for other dialect-sensitive types in this repo).
_JSONVariant = JSONB().with_variant(JSON(), "sqlite")

# Portable autoincrement PK: BigInteger on Postgres (BIGSERIAL — matches the
# Target schema's BIGSERIAL exactly), plain Integer on SQLite. SQLite only
# aliases a primary-key column to its ROWID (the thing that makes
# autoincrement work with no application-supplied value) when the column's
# type affinity is exactly INTEGER; BIGINT does NOT get that aliasing, so an
# INSERT with no explicit id raises "NOT NULL constraint failed: ...id" on
# every SQLite target (all tests + local dev) even though the identical
# schema is correct on Postgres.
_IdVariant = BigInteger().with_variant(Integer(), "sqlite")


class WalletIdentity(Base):
    """Identity anchor — SIWE-capable wallets ONLY (D1/D1a)."""

    __tablename__ = "wallet_identities"

    wallet_address: Mapped[str] = mapped_column(String(42), primary_key=True)
    # human | agent | operator_dogfood | system (D3)
    actor_class: Mapped[str] = mapped_column(String(20), nullable=False, default="human")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    last_auth_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("wallet_address = lower(wallet_address)", name="ck_wallet_identities_lower"),
        CheckConstraint(
            "actor_class IN ('human','agent','operator_dogfood','system')",
            name="ck_wallet_identities_actor_class",
        ),
    )


class ControlledWallet(Base):
    """Every address the SYSTEM operates: agent wallets, Circle DCWs, treasury (D1a/D3).

    Never an identity — ``controller_wallet`` links back to the anchor but
    this table's own primary key is never itself an identity FK target.
    """

    __tablename__ = "controlled_wallets"

    address: Mapped[str] = mapped_column(String(42), primary_key=True)
    controller_wallet: Mapped[str | None] = mapped_column(
        String(42), ForeignKey("wallet_identities.wallet_address"), nullable=True
    )
    # trading_agent | subscriber_payment | publisher_settlement | treasury
    wallet_class: Mapped[str] = mapped_column(String(32), nullable=False)
    key_binding: Mapped[str | None] = mapped_column(String(128), nullable=True)  # e.g. Circle wallet UUID
    provisioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        CheckConstraint("address = lower(address)", name="ck_controlled_wallets_lower"),
        CheckConstraint(
            "wallet_class IN ('trading_agent','subscriber_payment','publisher_settlement','treasury')",
            name="ck_controlled_wallets_class",
        ),
    )


class IdentityEvent(Base):
    """The ledger (D2) — every metric is a SQL view over this table."""

    __tablename__ = "identity_events"

    id: Mapped[int] = mapped_column(_IdVariant, primary_key=True, autoincrement=True)
    # NULL only for actor_class='system' (per the Target schema comment).
    wallet: Mapped[str | None] = mapped_column(
        String(42), ForeignKey("wallet_identities.wallet_address"), nullable=True
    )
    vid: Mapped[str | None] = mapped_column(String(32), nullable=True)  # browser id; post-auth only (D2a)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_class: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    meta: Mapped[dict] = mapped_column(_JSONVariant, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_identity_events_wallet_type", "wallet", "event_type"),
        Index("ix_identity_events_type_time", "event_type", "occurred_at"),
        CheckConstraint(
            "actor_class IN ('human','agent','operator_dogfood','system')",
            name="ck_identity_events_actor_class",
        ),
    )

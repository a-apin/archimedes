"""Marketplace registry ORM — logical publishers/subscribers (no containers)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from archimedes.models.chat import Base


class MarketplaceAgent(Base):
    __tablename__ = "marketplace_agents"

    __table_args__ = (
        # One active publisher per strategy. Partial unique index — MUST carry
        # the same WHERE on BOTH backends (sqlite_where + postgresql_where):
        # without sqlite_where the index degrades to an unconditional unique on
        # (role, strategy_id) in SQLite, which would reject a SECOND subscriber
        # to the same strategy (all subscriber rows share role='subscriber') and
        # forbid re-publishing after a stop. The WHERE scopes it to running
        # publishers so neither of those is constrained.
        Index(
            "uq_marketplace_agents_running_publisher",
            "role",
            "strategy_id",
            unique=True,
            postgresql_where=text("role = 'publisher' AND status = 'running'"),
            sqlite_where=text("role = 'publisher' AND status = 'running'"),
        ),
        # One RUNNING subscription per (wallet, strategy) — closes the
        # check-then-insert TOCTOU race. Partial on both backends (see above):
        # scoping to running lets a wallet re-subscribe after a stop/retire.
        Index(
            "uq_marketplace_agents_running_subscriber",
            "role",
            "subscriber_wallet",
            "strategy_id",
            unique=True,
            postgresql_where=text("role = 'subscriber' AND status = 'running'"),
            sqlite_where=text("role = 'subscriber' AND status = 'running'"),
        ),
        # sub_id is client-supplied and keys the in-process engine
        # (pub.subscribers[sub_id]) — a reused sub_id would silently overwrite
        # another subscriber's engine entry (hijack). Globally unique among
        # subscriptions regardless of status (a retired sub_id must not be
        # reusable). This MUST be a PARTIAL index excluding publishers on BOTH
        # backends: publishers all carry the default sub_id="" (unlike the
        # running-subscriber index above, strategy_id does NOT disambiguate
        # them here), so a plain (role, sub_id) unique would reject the 2nd
        # publisher on SQLite. sqlite_where + postgresql_where scope it to
        # subscribers on each backend.
        Index(
            "uq_marketplace_agents_sub_id",
            "sub_id",
            unique=True,
            postgresql_where=text("role = 'subscriber'"),
            sqlite_where=text("role = 'subscriber'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "publisher" | "subscriber"
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    creator_wallet: Mapped[str] = mapped_column(String(42), nullable=False, default="")
    subscriber_wallet: Mapped[str] = mapped_column(String(42), nullable=False, default="")
    sub_id: Mapped[str] = mapped_column(String(66), nullable=False, default="")  # 0x + 64 hex
    pool_id: Mapped[str] = mapped_column(
        String(66), nullable=False, default=""
    )  # 0x + 64 hex — REAL column, always set
    vault_address: Mapped[str] = mapped_column(String(42), nullable=False, default="")
    ephemeral_wallet: Mapped[str] = mapped_column(String(42), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")  # running | stopped
    halted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )  # subscriber halted for non-payment (C-5)
    # Per-creator Gateway seller address (publisher role). The creator's Circle
    # agent wallet 0x address that receives x402 Gateway settlement.
    gateway_seller_address: Mapped[str | None] = mapped_column(String(42), nullable=True, default=None)
    # Circle wallet UUID controlling the gateway_seller_address.
    agent_wallet_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    # Circle Developer-Controlled Wallet UUID for subscriber x402 signing.
    circle_wallet_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        d = {
            "role": self.role,
            "strategy_id": self.strategy_id,
            "creator_wallet": self.creator_wallet,
            "subscriber_wallet": self.subscriber_wallet,
            "sub_id": self.sub_id,
            "pool_id": self.pool_id,
            "vault_address": self.vault_address,
            "ephemeral_wallet": self.ephemeral_wallet,
            "status": self.status,
            "gateway_seller_address": self.gateway_seller_address,
            "agent_wallet_id": self.agent_wallet_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "stopped_at": self.stopped_at.isoformat() if self.stopped_at else None,
        }
        if self.role == "subscriber" and self.status == "retired":
            d["notice"] = (
                "This strategy has been retired by its creator. Your subscription "
                "is no longer active on the marketplace. Any unused balance remains "
                "reserved on-chain — call unsubscribe() from your wallet to reclaim it."
            )
        return d

    def public_dict(self) -> dict:
        """Attribution-only projection for UNAUTHENTICATED surfaces.

        Redaction by construction: never expose payment plumbing
        (gateway_seller_address, agent_wallet_id, circle_wallet_id,
        ephemeral_wallet) or the client-supplied engine key (sub_id). Every
        public GET (browse list + strategy detail) MUST render from this, not
        ``to_dict``. Subscriber identity is deliberately absent — the public
        surface gets a ``subscriber_count`` only (see marketplace_routes).
        """
        return {
            "role": self.role,
            "strategy_id": self.strategy_id,
            "creator_wallet": self.creator_wallet,
            "pool_id": self.pool_id,
            "vault_address": self.vault_address,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SubscriberLiability(Base):
    __tablename__ = "subscriber_liabilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sub_id: Mapped[str] = mapped_column(String(66), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tick_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_usdc: Mapped[float] = mapped_column(Numeric, nullable=True)
    amount_owed_usdc: Mapped[float] = mapped_column(Numeric, nullable=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=False, default="mirror_execution_failed")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="owed")  # owed | settled | waived
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(512), nullable=True)

    def to_dict(self) -> dict:
        return {
            "sub_id": self.sub_id,
            "strategy_id": self.strategy_id,
            "tick_id": self.tick_id,
            "action_count": self.action_count,
            "unit_price_usdc": float(self.unit_price_usdc) if self.unit_price_usdc is not None else None,
            "amount_owed_usdc": float(self.amount_owed_usdc) if self.amount_owed_usdc is not None else None,
            "reason": self.reason,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolution_note": self.resolution_note,
        }


class SubscriberTickLog(Base):
    __tablename__ = "subscriber_tick_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sub_id: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tick_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    step_reached: Mapped[str] = mapped_column(String(32), nullable=False)
    halted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    halt_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    halt_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    charged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    action_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

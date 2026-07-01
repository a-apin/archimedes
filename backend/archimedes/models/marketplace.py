"""Marketplace registry ORM — logical publishers/subscribers (no containers)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from archimedes.models.chat import Base


class MarketplaceAgent(Base):
    __tablename__ = "marketplace_agents"

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

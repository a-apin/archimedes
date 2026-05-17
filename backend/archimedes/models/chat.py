"""Chat message model — per-vault chat for the Archimedes marketplace.

Design decisions (hackathon MVP per ecosystem-design-spec.md § 16–17):
  - Fully open: any connected wallet can read/write in any vault chat
  - Wallet address = identity (no profiles, DMs, reactions)
  - AI messages: is_ai=True, triggered by @archimedes mentions or rebalance events
  - Persistence: SQLite for local dev, Postgres in Docker — both via SQLAlchemy
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, Text, Boolean, Integer, DateTime, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all Archimedes models."""
    pass


class VaultMetadata(Base):
    """Off-chain vault metadata — strategy associations, display name, etc.

    Created when a user deploys a vault via the UI. The on-chain vault
    contract holds the financial state; this table holds the metadata
    the frontend needs (strategy_ids, display name, symbol).
    """

    __tablename__ = "vault_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vault_address: Mapped[str] = mapped_column(String(42), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    creator_address: Mapped[str] = mapped_column(String(42), nullable=False, default="")
    strategy_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON array
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def get_strategy_ids(self) -> list[str]:
        import json
        try:
            return json.loads(self.strategy_ids)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_strategy_ids(self, ids: list[str]) -> None:
        import json
        self.strategy_ids = json.dumps(ids)

    def to_dict(self) -> dict:
        return {
            "vault_address": self.vault_address,
            "name": self.name,
            "symbol": self.symbol,
            "creator_address": self.creator_address,
            "strategy_ids": self.get_strategy_ids(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ChatMessage(Base):
    """A single chat message in a vault's chat room."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vault_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_ai: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Composite index for efficient vault + time-ordered queries
    __table_args__ = (
        Index("ix_chat_vault_created", "vault_address", "created_at"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "vault_address": self.vault_address,
            "wallet_address": self.wallet_address,
            "message": self.message,
            "is_ai": self.is_ai,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

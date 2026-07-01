"""StrategyGenerator — records which wallet(s) generated a given strategy.

Many-to-many: content-hash dedup in strategy_store.py means two different
wallets can independently generate byte-identical content and land on the
same StrategyRecord.id. Both are legitimate generators and both may publish
it (D5 / review M2). This table is the source of truth for "is this wallet
allowed to publish this strategy_id."
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import Session

from archimedes.models.chat import Base


class StrategyGenerator(Base):
    __tablename__ = "strategy_generators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("strategy_id", "wallet_address", name="uq_strategy_generator"),
        Index("ix_strategy_generators_strategy_wallet", "strategy_id", "wallet_address"),
    )


def record_generator(session: Session, *, strategy_id: str, wallet_address: str) -> None:
    """Insert-if-not-exists. Idempotent — safe to call on every persist."""
    wallet_address = wallet_address.lower()
    existing = (
        session.query(StrategyGenerator)
        .filter_by(strategy_id=strategy_id, wallet_address=wallet_address)
        .first()
    )
    if existing is None:
        session.add(StrategyGenerator(strategy_id=strategy_id, wallet_address=wallet_address))
        session.flush()


def wallet_can_publish(session: Session, *, strategy_id: str, wallet_address: str, is_example: bool) -> bool:
    """D5 enforcement rule. See spec §2 for the decision record."""
    wallet_address = wallet_address.lower()
    if is_example:
        import os

        admin_wallets = {
            w.strip().lower() for w in os.getenv("PLATFORM_ADMIN_WALLETS", "").replace(",", " ").split() if w.strip()
        }
        if wallet_address in admin_wallets:
            return True
    row = (
        session.query(StrategyGenerator)
        .filter_by(strategy_id=strategy_id, wallet_address=wallet_address)
        .first()
    )
    return row is not None

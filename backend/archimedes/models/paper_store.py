"""Paper-trading deployments and their forward return ledgers (MVP: verdict → paper-trade).

Design (Dan, 2026-08-18, validated with one amendment): a paper deployment is
an incremental forward run of the BACKTEST engine's semantics — the engine
that graded the verdict the user paid for — one bar per day from the deploy
date. NOT the live signal evaluator: the divergence audit (F2/F3) established
it grades a different strategy.

The amendment: this is deliberately a SIBLING of the daily-returns machinery,
not a tenant of it. ``strategy_daily_returns``' own documented law is
"re-measuring a stem replaces that stem's rows wholesale" — a measurement
store. A paper ledger obeys the OPPOSITE law: append-only per deployment,
never rewritten, because it is a user-facing track record. Same row shape, so
every metric function that eats (date, daily_return) series works unchanged.

Provenance: ``spec_json`` snapshots the strategy spec AT DEPLOY TIME. The
strategy row's spec can be regenerated later; the ledger must keep grading
the spec the user actually deployed — the same lesson as backtest provenance
(#1220): a row must not silently change what it asserts about the past.
"""

from __future__ import annotations

import secrets
from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from archimedes.models.chat import Base

STATUS_ACTIVE = "active"
STATUS_STOPPED = "stopped"


def _new_id() -> str:
    return secrets.token_hex(16)


class PaperDeployment(Base):
    """One user's paper-trade of one strategy, from one start date."""

    __tablename__ = "paper_deployments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Ownership carries BOTH identity columns during the canonical-identity
    # transition (#1194): wallet for the SIWE model live today, user id for
    # Better Auth once it lands. Same pattern as strategy_store.
    owner_wallet: Mapped[str | None] = mapped_column(String(42), nullable=True)
    # CASCADE (issue #1367, D3): a paper deployment is a private per-user
    # ledger — no other account reads or depends on someone else's paper
    # trades (unlike strategy_store/strategy_passports, which can be public
    # marketplace/audit artifacts other users rely on). Deleting the owning
    # account should remove it outright, and PaperDailyReturn already
    # cascades off `paper_deployments.id` (see below), so the whole ledger
    # goes with it — no orphaned rows either direction.
    owner_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # The deploy-time snapshot of the validated spec — the thing being graded.
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    deployed_at: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    # Set when a replay disagrees with already-written ledger rows (upstream
    # data restatement). The ledger is never rewritten; this flags that a
    # fresh replay would tell a different story — surfaced, not hidden.
    drift_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_paper_deployments_owner_wallet", "owner_wallet"),
        Index("ix_paper_deployments_strategy", "strategy_id"),
    )


class PaperDailyReturn(Base):
    """One appended daily observation of one deployment. Append-only by law."""

    __tablename__ = "paper_daily_returns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deployment_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("paper_deployments.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    daily_return: Mapped[float] = mapped_column(Float, nullable=False)
    appended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("deployment_id", "date", name="uq_paper_daily_returns_dep_date"),
        Index("ix_paper_daily_returns_dep", "deployment_id"),
    )

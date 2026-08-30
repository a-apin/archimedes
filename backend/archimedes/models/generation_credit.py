"""Durable generation credits — the accounting record behind a settled payment (#1441).

The x402 generation paywall settles the charge and *then* enqueues the job
(``api/generate_routes.py``'s ``start_generation``). Everything between those
two points, and everything the job does afterwards, could fail with the money
already taken: the entitlement gate raising 402, the enqueue erroring, the
worker crashing, the LLM failing, a container roll mid-run. Nothing released
the charge and nothing recorded that the payer was owed anything.

**The decision, written down** (the issue asked for one): a settled payment
buys a *credit*, and the credit — not the payment — is what a generation
spends. A refund was the alternative and was rejected: settling runs one way
through Circle's facilitator, so refunding means a fresh outbound transfer out
of the recipient DCW. That needs the Circle signer on the money path and rides
on #975's unfinished custody migration. A refund path we cannot execute today
would be a promise the code does not keep, and this repo's first rule is that
claims must be true. A credit is local, durable, and needs no value to move.

Lifecycle, one row per logical charge::

    pending ──settle ok──> available ──enqueue ok──> consumed
       │                        ▲                        │
       │                        └────job did not finish──┘
       └──settle failed/refused──> void

``pending`` is claimed BEFORE the settle call, mirroring
``marketplace/service.py``'s ``_claim_settlement_intent``: x402 is not
crash-retry-idempotent (a retry signs a fresh EIP-3009 nonce, which settles as
a brand-new payment), so the only way a retry can be recognised is a logical
key claimed ahead of the charge. That key is the caller's ``Idempotency-Key``.

``void`` rather than ``failed``: nothing went wrong with the money — the
settle was refused or never attempted, so no value moved. Keeping it distinct
from a *failed job* keeps the ledger readable.

A credit carries no expiry. One is owed until it is spent.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column

from archimedes.models.chat import Base

logger = logging.getLogger(__name__)

CREDIT_PENDING = "pending"
CREDIT_AVAILABLE = "available"
CREDIT_CONSUMED = "consumed"
CREDIT_VOID = "void"

#: Bound on one payer's ledger read. Matches ``payment_receipt.MAX_RECEIPTS``.
MAX_CREDITS = 200


class GenerationCreditRecord(Base):
    """One logical generation charge, from claim through to the job it funded."""

    __tablename__ = "generation_credits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # The caller's Idempotency-Key. NULL when the client sent none — and NULLs
    # do not collide under a UNIQUE constraint in either Postgres or SQLite, so
    # a caller who never sends one can still hold several credits at once.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CREDIT_PENDING)

    # Everything below is unknown until the settle returns, so all of it is
    # nullable: a `pending` row exists precisely because we do not yet know
    # whether there will be a payment to describe.
    payer_wallet: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_base_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_usd: Mapped[str | None] = mapped_column(String(32), nullable=True)
    network: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settlement_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: The generation this credit was spent on. Set when the credit is
    #: consumed, and deliberately KEPT when it is restored, so the ledger shows
    #: which run failed rather than quietly reverting to a blank row.
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_generation_credits_user_key"),
        Index("ix_generation_credits_user_status", "user_id", "status"),
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "settled_at": self.settled_at.isoformat() if self.settled_at else None,
            "consumed_at": self.consumed_at.isoformat() if self.consumed_at else None,
            "price_usd": self.price_usd,
            "amount_base_units": self.amount_base_units,
            "payer_wallet": self.payer_wallet,
            "settlement_ref": self.settlement_ref,
            "job_id": self.job_id,
            "network": self.network,
        }


def claim_credit(session: Session, *, user_id: str, idempotency_key: str | None) -> tuple[str, GenerationCreditRecord]:
    """Claim the right to charge, BEFORE the money moves.

    Returns ``(outcome, row)`` where outcome is one of:

    ``claimed``
        A fresh ``pending`` row. The caller may settle.
    ``in_flight``
        Another request holds this key and has not finished settling. The
        caller must NOT settle — that is the double-charge this exists to stop.
    ``already_settled``
        This key already bought a credit that is still unspent.
    ``already_consumed``
        This key already bought a credit and a generation already spent it.

    Ordering matters and is the whole point: claiming after the settle would
    leave the window (settle returns, process dies before the write) wide open,
    which is exactly the window a client retry lands in.
    """
    if not user_id:
        raise ValueError("claim_credit requires a user_id")

    if idempotency_key:
        existing = (
            session.query(GenerationCreditRecord)
            .filter_by(user_id=user_id, idempotency_key=idempotency_key)
            .one_or_none()
        )
        if existing is not None:
            if existing.status == CREDIT_PENDING:
                return "in_flight", existing
            if existing.status == CREDIT_AVAILABLE:
                return "already_settled", existing
            if existing.status == CREDIT_CONSUMED:
                return "already_consumed", existing
            # A voided key is a charge that never happened; let it be retried.
            existing.status = CREDIT_PENDING
            existing.created_at = datetime.now(UTC)
            session.flush()
            return "claimed", existing

    record = GenerationCreditRecord(
        user_id=user_id,
        idempotency_key=idempotency_key or None,
        status=CREDIT_PENDING,
        created_at=datetime.now(UTC),
    )
    session.add(record)
    session.flush()
    return "claimed", record


def mark_credit_settled(
    session: Session,
    credit_id: int,
    *,
    payer_wallet: str,
    amount_base_units: int,
    price_usd: str,
    network: str,
    settlement_ref: str | None,
) -> GenerationCreditRecord | None:
    """Money moved: the claim becomes a spendable credit."""
    record = session.get(GenerationCreditRecord, credit_id)
    if record is None:
        return None
    record.status = CREDIT_AVAILABLE
    record.payer_wallet = payer_wallet
    record.amount_base_units = int(amount_base_units)
    record.price_usd = str(price_usd)
    record.network = str(network)
    record.settlement_ref = settlement_ref
    record.settled_at = datetime.now(UTC)
    session.flush()
    return record


def void_credit(session: Session, credit_id: int) -> None:
    """No value moved — release the claim so the key is usable again.

    Called when the settle was refused, or when the paywall returned without
    charging at all (flag off, dry-run). Leaving the row ``pending`` would
    poison the idempotency key permanently: every later retry would read
    ``in_flight`` and refuse to charge for a payment that never happened.
    """
    record = session.get(GenerationCreditRecord, credit_id)
    if record is None:
        return
    if record.status == CREDIT_PENDING:
        record.status = CREDIT_VOID
        session.flush()


def take_available_credit(session: Session, user_id: str) -> GenerationCreditRecord | None:
    """The oldest unspent credit for *user_id*, or None.

    Oldest-first so a payer's ledger drains in the order they paid.
    """
    if not user_id:
        return None
    return (
        session.query(GenerationCreditRecord)
        .filter_by(user_id=user_id, status=CREDIT_AVAILABLE)
        .order_by(GenerationCreditRecord.created_at.asc(), GenerationCreditRecord.id.asc())
        .first()
    )


def consume_credit(session: Session, credit_id: int, *, job_id: str) -> GenerationCreditRecord | None:
    """Spend a credit on *job_id*. Only an ``available`` credit can be spent."""
    record = session.get(GenerationCreditRecord, credit_id)
    if record is None or record.status != CREDIT_AVAILABLE:
        return None
    record.status = CREDIT_CONSUMED
    record.job_id = job_id
    record.consumed_at = datetime.now(UTC)
    session.flush()
    return record


def restore_credit_for_job(session: Session, job_id: str) -> GenerationCreditRecord | None:
    """Give the credit back because *job_id* did not deliver.

    ``job_id`` is left in place: the ledger should show which run burned and
    gave the credit back, not silently rewind to a blank row. ``consumed_at``
    is cleared, since the credit is once again unspent.

    Idempotent — restoring an already-restored credit is a no-op, so a retried
    or duplicated terminal-state callback cannot mint a second credit out of
    one payment.
    """
    if not job_id:
        return None
    record = session.query(GenerationCreditRecord).filter_by(job_id=job_id, status=CREDIT_CONSUMED).one_or_none()
    if record is None:
        return None
    record.status = CREDIT_AVAILABLE
    record.consumed_at = None
    session.flush()
    return record


def list_credits(session: Session, user_id: str, *, limit: int = MAX_CREDITS) -> list[dict[str, Any]]:
    """Newest-first credits owned by *user_id*, capped at *limit*."""
    if not user_id:
        return []
    records = (
        session.query(GenerationCreditRecord)
        .filter_by(user_id=user_id)
        .order_by(GenerationCreditRecord.created_at.desc(), GenerationCreditRecord.id.desc())
        .limit(limit)
        .all()
    )
    return [r.to_payload() for r in records]

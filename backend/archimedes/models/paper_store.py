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

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from archimedes.models.chat import Base

STATUS_ACTIVE = "active"
STATUS_STOPPED = "stopped"

#: Publication outcome for one paper decision (#1575 §7). Every decision the
#: replay detects lands in exactly one of these, and the settle asserts the
#: accounting identity over them — a decision that falls out of the pipeline
#: uncounted is the failure mode that produces a silent zero.
TRACE_PUBLISHED = "published"
TRACE_FAILED = "failed"
TRACE_UNOWNED = "unowned"
TRACE_DISABLED = "disabled"
TRACE_STATUSES = (TRACE_PUBLISHED, TRACE_FAILED, TRACE_UNOWNED, TRACE_DISABLED)
#: The states a later settle re-attempts. ``unowned`` is deliberately absent:
#: it is a data problem, not a transient one, and retrying it forever would
#: turn a loud ERROR into a recurring one that operators learn to ignore.
TRACE_RETRYABLE = (TRACE_FAILED, TRACE_DISABLED)


def _new_id() -> str:
    return secrets.token_hex(16)


class PaperDeployment(Base):
    """One user's paper-trade of one strategy, from one start date."""

    __tablename__ = "paper_deployments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    # Width normalized to VARCHAR(128) (schema-relations Phase 1) to match
    # strategy_store.id's column width ahead of the FK below — same reasoning
    # strategy_store.id itself documents (issue #1028): actual values are
    # content_hash[:16] (16 chars), so this is headroom, not a live-data
    # change. FK retrofit: closes the gap this module's own docstring already
    # named ("Same pattern as strategy_store" — strategy_store carries this
    # FK, this table didn't). Added NOT VALID in fb8d0bae8112; historical
    # orphans (if any) are left un-enforced rather than blocking the deploy —
    # VALIDATE CONSTRAINT runs, gated on a live orphan count, in the separate
    # follow-up revision 9c2e7b5a1f4d.
    strategy_id: Mapped[str] = mapped_column(String(128), ForeignKey("strategy_store.id"), nullable=False)
    # Ownership carries BOTH identity columns during the canonical-identity
    # transition (#1194): wallet for the SIWE model live today, user id for
    # Better Auth once it lands. Same pattern as strategy_store. Both FKs
    # retrofitted in schema-relations Phase 1 (#1438) — added NOT VALID in
    # fb8d0bae8112, validated (gated on a live orphan count) in 9c2e7b5a1f4d.
    owner_wallet: Mapped[str | None] = mapped_column(
        String(42), ForeignKey("wallet_identities.wallet_address"), nullable=True
    )
    # CASCADE (issue #1367, D3): a paper deployment is a private per-user
    # ledger — no other account reads or depends on someone else's paper
    # trades (unlike strategy_store/strategy_passports, which can be public
    # marketplace/audit artifacts other users rely on). Deleting the owning
    # account should remove it outright, and PaperDailyReturn already
    # cascades off `paper_deployments.id` (see below), so the whole ledger
    # goes with it — no orphaned rows either direction.
    #
    # #1438 created this FK (`fk_paper_deployments_owner_user_id`) with
    # ondelete="SET NULL", matching the five sibling ownership columns
    # `b7e3f1a2c9d4` established. `85ca5310b7a1` ALTERS that constraint to
    # CASCADE — it does not create a second one. The FK's existence is #1438's;
    # only its ON DELETE action is this PR's.
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
    # Opt-in on-chain anchoring for this deployment's reasoning traces (#1575
    # §6). Default false, and PER DEPLOYMENT rather than a global switch,
    # because the commit-reveal `reveal()` puts `portfolio_before/after` —
    # the user's holdings — on-chain permanently. Anchoring a private
    # simulation by default would defeat #1556's ownership gate on purpose,
    # irreversibly. Both this and PAPER_TRACE_ANCHOR must be true to anchor.
    anchor_traces: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Set when a decision this deployment made did NOT get a published trace.
    # Distinct from drift_detected_at: that is the ledger disagreeing with
    # itself, this is the provenance claim having a hole in it.
    trace_gap_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set when a re-replay produces a decision for a date whose trace is
    # already published. The trace is NOT rewritten — the hash is the point —
    # so the disagreement is stamped and surfaced, exactly as the ledger's own
    # drift is.
    trace_drift_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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


class PaperDecisionTrace(Base):
    """One paper DECISION and what happened when we tried to publish its trace.

    The idempotency key AND the loud-failure record, in one row — deliberately
    the same table, because a design where the "published" bookkeeping is
    durable and the "failed" bookkeeping is a log line degrades into a silent
    zero the first time Redis blips. ``advance_deployment`` re-derives every
    historical decision on every settle (the engine is a position FSM with no
    serialisable state), so without a durable key each deployment would
    republish its whole decision history daily.

    ``(deployment_id, decision_date)`` is the key, not the leg: the universe
    runs as independent dollar sleeves, so one decision date can carry several
    symbol legs, and the user-visible unit of "a move" is the date. The legs
    live inside the trace body's ``trades_executed``.
    """

    __tablename__ = "paper_decision_traces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    deployment_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("paper_deployments.id", ondelete="CASCADE"), nullable=False
    )
    decision_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Null for every non-published status: a trace id we never wrote is not a
    # trace id, and a placeholder here would make `trace_coverage` countable
    # but wrong.
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # "settle" or "backfill" — the value that was HASHED INTO the published
    # trace. Stored so a later settle can re-derive that trace's hash exactly
    # and tell "the replay now decides differently" from "this row was written
    # on a different settle". Without it every re-replay would look like drift.
    provenance: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint("deployment_id", "decision_date", name="uq_paper_decision_traces_dep_date"),
        Index("ix_paper_decision_traces_dep", "deployment_id"),
    )

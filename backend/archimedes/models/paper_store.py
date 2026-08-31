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

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from archimedes.models.chat import Base

STATUS_ACTIVE = "active"
STATUS_STOPPED = "stopped"

# ─── paper_marks granularity + retention (intraday design §3) ──────────

GRANULARITY_RAW = "raw"
GRANULARITY_HOURLY = "hourly"

# Retention defaults. Every one is an env knob because the cadence is a
# product call and the arithmetic in §3.2 moves with it — but the DEFAULTS
# are the policy, and they exist before the first row is written rather than
# after the first bill. See PaperMark's docstring for why that ordering is
# the whole point.
DEFAULT_RAW_RETENTION_DAYS = 7
DEFAULT_HOURLY_RETENTION_DAYS = 90
# ~7x the crypto-24/7 steady-state ceiling (2,664 rows/deployment). Not a
# capacity plan — a runaway-loop tripwire that fires in minutes.
DEFAULT_MAX_ROWS_PER_DEPLOYMENT = 20_000
DEFAULT_MARKS_INTERVAL_MINUTES = 15
DEFAULT_MAX_STALENESS_MINUTES = 60


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
    # ── The position-set cache (intraday design §4.1) ──────────────────
    # WRITTEN by the daily advance, READ by the 15-minute marks loop, and by
    # nothing else. That one-way arrow is the entire safety argument for
    # intraday marks: the marks loop cannot change what the strategy does
    # because it has no path to do so (§4.0). It re-decides nothing — it
    # applies prices to a position set the DAILY replay established.
    #
    # A column rather than Redis or an in-process dict because the marks loop
    # runs in a DIFFERENT PROCESS (the runner box) from the advance loop (the
    # web tier), and must survive a restart of either. Nullable: every
    # deployment that existed before this shipped has no cache until its next
    # advance, and "no cache" must read as "no marks yet", never as a
    # fabricated flat line. Shape is documented on
    # ``paper_trading.PositionSet``.
    position_cache_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    position_cache_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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


class PaperMark(Base):
    """One intraday mark-to-market of one deployment's open position set.

    **Marks are NOT the track record.** ``paper_daily_returns`` stays
    append-only-by-law and stays the thing that carries to mainnet. This table
    is a DECORATION WITH A TTL and is safe to delete wholesale — that single
    sentence is what makes the aggressive retention policy below safe, and it
    is why the third tier of that policy is ``DELETE`` rather than a rollup.
    Beyond 90 days there is nothing worth aggregating to: the daily close is
    already stored, authoritatively and permanently, one table over. Rolling
    marks up to daily would be a second, less-trustworthy source of truth for
    a fact the ledger already owns.

    **Why the retention policy ships in the same migration as the table.**
    ``backtest_results`` reached 6.3 GB because it stores full curve blobs per
    row with no retention policy and no size alarm — nobody chose that, it
    accumulated. A marks table is a higher-volume version of the same shape
    (a crypto-24/7 deployment writes 96 rows/day), so the policy exists before
    the first row rather than after the first bill. Under the three tiers the
    steady state is BOUNDED, not linear in time: ~2,664 rows/deployment for a
    24/7 universe, ~543 for an equity-session one.

    **Every honesty-bearing fact is a stored column, not a render-time
    inference** (§2.4):

      - ``ts`` is the UPSTREAM OBSERVATION time, never the write time. A mark
        written at 14:47 from a 14:32 bar is a 14:32 mark. For a multi-leg
        universe it is the OLDEST contributing leg's bar time — a mark is only
        as current as the stalest price inside it.
      - ``prices_json`` records what was ACTUALLY OBSERVED, per symbol. For a
        mixed equity+crypto universe outside market hours some legs are fresh
        and some are not; storing the per-symbol map keeps that recoverable
        instead of collapsing it into one opaque number. A leg too stale to
        use is ABSENT here rather than carried at a stale price.
      - ``source`` is ``provider_name()`` at fetch time, so a future vendor
        swap cannot retroactively relabel history (same reasoning as
        ``asset_daily_bars.source``).
      - ``is_delayed`` is set by the FETCH PATH from what the provider
        declares about its own feed, so the UI's "delayed" badge reads a fact
        rather than guessing from a timestamp.

    ``portfolio_value`` is an INDEX (1.0 == deploy-time capital), not dollars.
    ``PaperDeployment`` has no notional/capital column — there is no deployed
    capital amount anywhere in this system — so rendering "$10,347" would
    require inventing the $10,000, and an invented number on a track-record
    page is the exact class of claim this product exists to oppose. The index
    matches how ``deployment_summary`` already computes ``equity_index``.

    ``granularity`` marks rolled-up rows IN PLACE rather than in a second
    table: the daily rollup rewrites the surviving row as ``'hourly'`` and
    deletes the ``'raw'`` rows it covers. One table, one query shape, and the
    unique constraint makes a re-run of the rollup a no-op instead of a
    duplicate.
    """

    __tablename__ = "paper_marks"

    # BIGSERIAL on Postgres (a 15-min cadence across many deployments makes a
    # 32-bit key a real, if distant, ceiling), plain INTEGER on SQLite — a
    # SQLite BIGINT primary key is NOT a rowid alias and therefore does not
    # autoincrement, which would break every hermetic test on this table.
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    deployment_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("paper_deployments.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prices_json: Mapped[str] = mapped_column(Text, nullable=False)
    portfolio_value: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    is_delayed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    granularity: Mapped[str] = mapped_column(String(8), nullable=False, default=GRANULARITY_RAW)

    __table_args__ = (
        UniqueConstraint("deployment_id", "ts", "granularity", name="uq_paper_marks_dep_ts_gran"),
        Index("ix_paper_marks_dep_ts", "deployment_id", "ts"),
    )

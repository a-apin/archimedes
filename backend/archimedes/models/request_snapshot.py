"""Request-count snapshot — D8 (issue #1028): lifetime REQUESTS, Postgres-durable.

The "lifetime requests" instrument (human/agent request tallies, issue #428) is
real traffic worth keeping — but until now it lived ONLY in Redis (``INCR``),
so a Redis restart silently zeroed the number the Insights page shows. D8
keeps the instrument, relabels it precisely (**requests**, not visitors —
these are per-request tallies, not distinct users; ``real_users`` /
``wallet_identities`` is the honest distinct-identity count), and makes it
durable: Redis stays the fast live counter, this single-row table is the
Postgres floor under it.

Design — a singleton row (``id=1``), reconciled on every read
(``services/request_snapshot_store.py``):

  - ``*_baseline`` — the total contributed by all PRIOR counting epochs (i.e.
    before the most recently observed Redis reset).
  - ``last_redis_*`` — the most recent raw Redis counter value this process
    has observed. Comparing a freshly-read Redis value against it is how a
    reset is DETECTED: Redis's own ``INCR`` counters are monotonic within an
    epoch, so a reading LOWER than the last-observed one means Redis was
    flushed/restarted since the last read.
  - The honest lifetime total is always ``baseline + last_redis`` for each of
    human/agent — it never regresses even though the underlying Redis counter
    did, and a total Redis outage still has a floor to read (AC6: "Restart
    Redis on the box -> no Insights number changes beyond sub-30s cache
    staleness").
  - ``epoch_started_at`` is set once, the first time this row is created — the
    earliest point this durable count can vouch for (the "epoch noted" half of
    D8: numbers from before this table existed are the fossil Redis-only era
    and are not reconcilable with it).
  - ``epoch_count`` / ``last_reset_at`` track how many Redis resets have been
    absorbed since then, so the Insights page can honestly say "counting since
    <epoch_started_at>, N reset(s) survived" rather than implying one
    unbroken Redis counter.

This table is a plain durable counter, not the identity ledger — it has no
FK to ``wallet_identities`` because a request tally is not identity-scoped
(most requests are pre-auth). It sits alongside ``identity_events`` as the
other half of the honest-metrics substrate: identity_events answers "how many
wallets", this table answers "how much traffic".
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from archimedes.models.chat import Base

# Portable counter width: BigInteger on Postgres, plain Integer on SQLite —
# same with_variant pattern used by models/identity.py, for the same reason
# (SQLite only ROWID-aliases an INTEGER primary key; this column isn't the PK
# here but keeping the type consistent with the rest of the codebase avoids a
# surprise BIGINT-vs-INTEGER mismatch between dialects).
_CounterVariant = BigInteger().with_variant(Integer(), "sqlite")

# The one row this table ever holds. A singleton keyed on a fixed id (rather
# than, say, a free-standing key-value table) keeps the reconcile-on-read
# logic a simple get-or-create + update, with no risk of a second row ever
# existing to disagree with the first.
SNAPSHOT_ROW_ID = 1


class RequestCountSnapshot(Base):
    """Singleton durable floor under the Redis human/agent request counters."""

    __tablename__ = "request_count_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    human_baseline: Mapped[int] = mapped_column(_CounterVariant, nullable=False, default=0)
    agent_baseline: Mapped[int] = mapped_column(_CounterVariant, nullable=False, default=0)
    last_redis_human: Mapped[int] = mapped_column(_CounterVariant, nullable=False, default=0)
    last_redis_agent: Mapped[int] = mapped_column(_CounterVariant, nullable=False, default=0)
    epoch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    epoch_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    last_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snapshotted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

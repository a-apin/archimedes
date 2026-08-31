"""Durable per-generation cost record — the measurement, and the quote beside it.

Issue #1326, following the #1314 cost meter. The meter's ``cost_v1`` snapshot
lived only on the Redis job record, whose ``JOB_TTL`` is 3600s: an hour after a
generation finished, the only measurement anyone had of what it consumed was
gone, and no surface ever showed it. This table is where it survives.

Two columns, deliberately two:

* ``measurement_json`` — the literal ``cost_v1`` snapshot
  (:meth:`archimedes.services.cost_meter.CostMeter.snapshot`). Counts and
  seconds. No money, and :func:`archimedes.services.cost_meter.assert_measurement_only`
  is run over it on the way in, so a future edit that merges a price into the
  measurement raises instead of shipping a priced ``cost_v1`` record.
* ``quote_json`` — the literal ``generation_payment.quote()`` payload that was
  in force when the job started. A *recorded fact* about what we charged, which
  is why it is displayable; it is not derived from the measurement and nothing
  here converts one into the other. Turning measured tokens into dollars is
  #1217's remaining pricing work and happens off-server.

Keeping them in separate columns is the whole design: quote-vs-measured is an
honest pairing of two independently recorded facts, never a conversion.

**The row is keyed to a (job, strategy) pair, and the measurement is the JOB's.**
Generation is K=1 today (one ``strategy_store`` row per job — see
``generation_pipeline`` § "K=1 persistence"), so the pairing is one-to-one in
practice. If a job ever persists more than one strategy again, each gets a row
carrying the *same* job-level measurement: that is the honest record (the run
cost what it cost), and summing across rows would double-count. Read a row as
"the generation run that produced this strategy consumed this", not as "this
strategy's private share".

No backfill: strategies generated before #1314 have no measurement, and
inventing one would be the exact failure this instrumentation exists to end.
They render as *not measured*.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column

from archimedes.models.chat import Base

logger = logging.getLogger(__name__)

#: Used when a stored snapshot carries no ``schema`` of its own. Never guessed
#: to be ``cost_v1`` — a record whose shape we cannot name is not a record whose
#: shape we may assert.
UNKNOWN_SCHEMA = "unknown"


class GenerationCostRecord(Base):
    """One generation run's measurement, durably keyed to the strategy it produced.

    ``strategy_id`` carries no foreign key, mirroring ``paper_deployments``
    (the sibling table that also references ``strategy_store.id``): a purge of
    the strategy row must not delete the record of what the run consumed —
    the run still happened and still cost what it cost.
    """

    __tablename__ = "generation_costs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # The ``schema`` field of the stored snapshot, lifted out so a reader can
    # tell ``cost_v1`` rows from a future ``cost_v2`` without parsing the blob.
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)

    measurement_json: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL when the quote seam could not be read at job start. An honest
    # absence: it means "we did not record what was quoted", not "$0.00".
    quote_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("job_id", "strategy_id", name="uq_generation_costs_job_strategy"),
        Index("ix_generation_costs_strategy", "strategy_id"),
        # Schema-relations Phase 1: cost-over-time is a core owner metric;
        # only strategy_id and (job_id, strategy_id) were indexed before this.
        Index("ix_generation_costs_recorded_at", "recorded_at"),
    )

    # ── Readout ──────────────────────────────────────────────────────────

    def to_payload(self) -> dict[str, Any] | None:
        """The API shape, or ``None`` when the measurement cannot be read.

        A corrupt ``measurement_json`` yields ``None`` rather than an empty
        measurement: the row exists to carry a measurement, so one we cannot
        decode is an absence, and an absence renders as *not measured*. The
        warning is the loud half of that (``CLAUDE.md`` § fail-soft).

        A corrupt ``quote_json`` degrades on its own — the measurement is still
        a measurement without the price beside it.
        """
        measurement = _decode(self.measurement_json, what="measurement", strategy_id=self.strategy_id)
        if not isinstance(measurement, Mapping):
            return None
        quote = _decode(self.quote_json, what="quote", strategy_id=self.strategy_id)
        return {
            "schema": self.schema_version,
            "job_id": self.job_id,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
            "measurement": dict(measurement),
            "quote": dict(quote) if isinstance(quote, Mapping) else None,
        }


def _decode(raw: str | None, *, what: str, strategy_id: str) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("generation_costs: corrupt %s JSON for strategy %s — treating as absent", what, strategy_id)
        return None


def record_generation_cost(
    session: Session,
    *,
    job_id: str,
    strategy_id: str,
    measurement: Mapping[str, Any],
    quote: Mapping[str, Any] | None = None,
) -> GenerationCostRecord:
    """Upsert one (job, strategy) measurement. Idempotent on re-run.

    ``measurement`` is screened by
    :func:`archimedes.services.cost_meter.assert_measurement_only` before it is
    serialized, so a pricing-shaped key at any depth raises
    :class:`~archimedes.services.cost_meter.PricingLeakError` at this boundary
    instead of landing in the column. ``quote`` is NOT screened — it is a price
    by definition and that is why it lives in its own column.

    Raises ``ValueError`` on a missing key or a non-mapping measurement: writing
    a measurement with no job or no strategy would produce a row nothing can
    ever read back.
    """
    from archimedes.services.cost_meter import assert_measurement_only

    if not job_id:
        raise ValueError("record_generation_cost requires a job_id")
    if not strategy_id:
        raise ValueError("record_generation_cost requires a strategy_id")
    if not isinstance(measurement, Mapping):
        raise ValueError(f"record_generation_cost requires a measurement mapping, got {type(measurement).__name__}")

    assert_measurement_only(measurement, where="generation_costs.measurement_json")

    schema_version = str(measurement.get("schema") or UNKNOWN_SCHEMA)[:32]
    # ``default=str`` mirrors ``JobStore.update_status``/``merge_result``: the
    # same snapshot is written to both stores, so a value one of them can encode
    # and the other cannot would silently give the two copies different fates —
    # the durable row failing while the (expiring) job record succeeds.
    measurement_json = json.dumps(measurement, sort_keys=True, ensure_ascii=False, default=str)
    quote_json = (
        json.dumps(dict(quote), sort_keys=True, ensure_ascii=False, default=str) if isinstance(quote, Mapping) else None
    )
    now = datetime.now(UTC)

    existing = session.query(GenerationCostRecord).filter_by(job_id=job_id, strategy_id=strategy_id).first()
    if existing is not None:
        existing.schema_version = schema_version
        existing.measurement_json = measurement_json
        existing.quote_json = quote_json
        existing.recorded_at = now
        session.flush()
        return existing

    record = GenerationCostRecord(
        job_id=job_id,
        strategy_id=strategy_id,
        schema_version=schema_version,
        measurement_json=measurement_json,
        quote_json=quote_json,
        recorded_at=now,
    )
    session.add(record)
    session.flush()
    return record


def generation_cost_for_strategy(session: Session, strategy_id: str) -> dict[str, Any] | None:
    """The most recently recorded measurement for one strategy, or ``None``.

    ``None`` is the honest answer for every strategy generated before #1326 (and
    for every curated one): nothing measured it, so nothing is claimed about it.
    """
    if not strategy_id:
        return None
    record = (
        session.query(GenerationCostRecord)
        .filter_by(strategy_id=strategy_id)
        .order_by(GenerationCostRecord.recorded_at.desc(), GenerationCostRecord.id.desc())
        .first()
    )
    return record.to_payload() if record is not None else None


def generation_costs_for_strategies(session: Session, strategy_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Batch form of :func:`generation_cost_for_strategy` — one query for a page.

    Strategies with no record are simply absent from the mapping; the caller
    renders that absence, it does not get a zeroed placeholder. A strategy whose
    NEWEST record will not decode is absent too — the same answer the
    single-strategy reader gives, which is the point.

    That equivalence is why this orders DESC and keeps a ``seen`` set rather
    than ordering ASC and letting later rows overwrite earlier ones. The
    overwrite form silently fell back to an older, stale row when only the
    newest was corrupt, so the batch and single readers disagreed about the same
    strategy: one served a superseded measurement, the other honestly served
    nothing. Skipping every row after the first per strategy makes "newest wins,
    and if the newest is unreadable we have nothing" true in both readers.
    """
    ids = [s for s in dict.fromkeys(strategy_ids) if s]
    if not ids:
        return {}
    records = (
        session.query(GenerationCostRecord)
        .filter(GenerationCostRecord.strategy_id.in_(ids))
        .order_by(GenerationCostRecord.recorded_at.desc(), GenerationCostRecord.id.desc())
        .all()
    )
    out: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for record in records:
        # First row per strategy IS the newest (DESC). Once seen, older rows are
        # skipped whether or not the newest decoded — an older row is never a
        # stand-in for a newer one we could not read.
        if record.strategy_id in seen:
            continue
        seen.add(record.strategy_id)
        payload = record.to_payload()
        if payload is not None:
            out[record.strategy_id] = payload
    return out

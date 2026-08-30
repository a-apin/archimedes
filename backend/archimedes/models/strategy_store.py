"""StrategyStore — persistent, content-hashed, provenance-anchored strategy substrate.

Every strategy generated (fusion, architect, curated) is persisted here with
a keccak256 content hash for dedup and on-chain anchoring.  Status transitions
(candidate → live, demotions) are tracked.  Source paper provenance links
strategies to their origin arXiv documents for full traceability.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from archimedes.models.chat import Base

if TYPE_CHECKING:
    # Import only for the forward-reference type annotation on
    # to_strategy_passport(). Avoids a circular import at runtime (mirrors
    # models/strategy_passport_record.py's identical guard).
    from archimedes.models.strategy import StrategyPassport

logger = logging.getLogger(__name__)


class StrategyRecord(Base):
    """Persistent strategy with content-hash dedup and provenance."""

    __tablename__ = "strategy_store"

    # Width normalized to VARCHAR(128) (issue #1028) to match the
    # marketplace/billing tables' strategy_id columns (already VARCHAR(128))
    # ahead of a future cross-table FK. Actual id values are content_hash[:16]
    # (16 chars) so this is headroom, not a live-data change.
    id = Column(String(128), primary_key=True)
    content_hash = Column(String(66), nullable=False)  # keccak256, 0x-prefixed

    # Generation provenance
    generation_method = Column(String(32), nullable=False)  # fusion|architect|curated
    source_papers = Column(Text, nullable=False, default="[]")  # JSON: [{arxiv_id, sha256}]
    provenance_hash = Column(String(66), nullable=True)

    # Strategy definition
    strategy_name = Column(String(256), nullable=False, default="")
    thesis = Column(Text, nullable=False, default="")
    # The user's own free-text ask that produced this strategy (v8 Lane 3.3) —
    # distinct from `thesis`, which is the DERIVED methodology. Sourced from
    # `GenerateBrief.intent` at generation time (`_persist_candidate`'s
    # `_do_persist`). NULL for curated/example rows (which have no brief) and
    # for legacy generated rows this column's migration could not resolve
    # (see that migration's docstring for the backfill's honest boundary).
    brief_intent = Column(Text, nullable=True)
    asset_universe = Column(Text, nullable=False, default="[]")  # JSON list
    risk_profile = Column(String(32), nullable=False, default="moderate")
    # Raw validated DSL spec JSON (rebalancer decouple, Part A #1 of
    # docs/CURATED-STRATEGY-DECOUPLE-AND-CONSOLIDATE-2026-07-08.md). Mirrors
    # ``StrategyPassport.strategy_spec`` — when present, the live signal
    # evaluator (``strategy_signal_evaluator.evaluate_strategies``)
    # interprets it directly via the shared DSL condition tree instead of
    # buy-and-hold/keyword-matching. NULL for curated/example rows (which
    # carry a ``strategy_code_path`` instead) and for generated rows
    # persisted before this column existed — the agent runner simply skips
    # any bound generated strategy_id that has no spec, same as before.
    strategy_spec = Column(Text, nullable=True)

    # Status lifecycle
    status = Column(String(16), nullable=False, default="candidate")  # candidate|live|retired|rejected
    rigor_verdict = Column(Text, nullable=True)  # JSON: DSR/PBO/walk-forward results
    is_example = Column(Boolean, nullable=False, default=False)  # hand-curated static strategies

    # Ownership + visibility (per-user strategies, private-until-published).
    # owner_wallet is optional proof-linked wallet provenance bound server-side (mirrors
    # VaultMetadata.creator_address) — never a client-supplied value. Stored
    # lowercase. NULL = legacy/anonymous row (backfilled to the D7 'system'
    # identity in the issue #1028 migration; the column itself stays nullable
    # so this ORM's own anonymous-row semantics — see upsert_strategy below —
    # are unchanged). is_published is a dormant flag (nothing flips it yet —
    # the publish flow is a future marketplace hop); unpublished non-example
    # rows are visible only to their owner.
    # FK retrofit (issue #1028, D1): every non-NULL value must be a known
    # identity.
    owner_wallet: Mapped[str | None] = mapped_column(
        String(42), ForeignKey("wallet_identities.wallet_address"), nullable=True, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("auth_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_published = Column(Boolean, nullable=False, default=False)

    # On-chain registration (populated when strategy passes rigor gate)
    on_chain_registration_tx = Column(String(66), nullable=True)  # 0x-prefixed tx hash
    on_chain_registration_block = Column(String(32), nullable=True)  # block number as string

    # Lineage
    parent_id = Column(String(64), nullable=True)

    # Timestamps
    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_strategy_content_hash"),
        Index("ix_strategy_status", "status"),
        Index("ix_strategy_generation", "generation_method"),
    )

    def _decode_strategy_spec(self) -> dict | None:
        """Defensively decode ``strategy_spec`` for ``to_dict()``.

        Mirrors ``VaultMetadata.get_strategy_ids()`` (models/chat.py): a
        corrupt/non-JSON column value must not raise out of a dict-shaping
        method that's reachable straight from API routes (e.g.
        ``strategies_routes.py`` calling ``record.to_dict()``) — a single bad
        row shouldn't 500 the whole response. Falls back to ``None``,
        matching the ``dict | None`` contract every other reader of this
        field (``upsert_strategy``, ``to_strategy_passport``) already uses.
        """
        if not self.strategy_spec:
            return None
        try:
            return json.loads(self.strategy_spec)
        except (json.JSONDecodeError, TypeError):
            logger.warning("strategy %s: corrupt strategy_spec JSON — returning None", self.id)
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content_hash": self.content_hash,
            "generation_method": self.generation_method,
            "source_papers": json.loads(self.source_papers),
            "provenance_hash": self.provenance_hash,
            "strategy_name": self.strategy_name,
            "thesis": self.thesis,
            "asset_universe": json.loads(self.asset_universe),
            "risk_profile": self.risk_profile,
            "strategy_spec": self._decode_strategy_spec(),
            "status": self.status,
            "rigor_verdict": json.loads(self.rigor_verdict) if self.rigor_verdict else None,
            "is_example": self.is_example,
            "owner_wallet": self.owner_wallet,
            "is_published": bool(self.is_published),
            "parent_id": self.parent_id,
            "on_chain_registration_tx": self.on_chain_registration_tx,
            "on_chain_registration_block": self.on_chain_registration_block,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_strategy_passport(self) -> StrategyPassport:
        """Adapt to the ``StrategyPassport`` dataclass (mirrors
        ``StrategyPassportRecord.to_strategy_passport`` in
        ``models/strategy_passport_record.py``).

        This is the shape ``strategy_signal_evaluator.evaluate_strategies``
        and ``PortfolioConstructor`` already accept — curated strategies flow
        through the SAME dataclass, so a generated ``StrategyRecord`` needs no
        bespoke duck-typed carrier to be evaluated by the live agent runner
        (rebalancer decouple, Part A #1). Only the fields the signal/
        portfolio-construction path actually reads are populated; this is
        NOT a full passport reconstruction (rigor/backtest columns live on
        ``strategy_passports`` instead — see ``StrategyPassportRecord``).
        """
        from archimedes.models.paper_ref import PaperRef
        from archimedes.models.strategy import StrategyPassport

        source_papers = json.loads(self.source_papers) if self.source_papers else []
        papers = [
            PaperRef(arxiv_id=p.get("arxiv_id"), title=p.get("title") or self.strategy_name or "")
            for p in source_papers
        ] or [PaperRef(title=self.strategy_name or "")]

        return StrategyPassport(
            id=self.id,
            papers=papers,
            methodology_summary=self.thesis or "",
            asset_universe=json.loads(self.asset_universe) if self.asset_universe else [],
            # Same defensive decode as to_dict(): a corrupt spec column must
            # degrade to None (spec-less strategy) rather than raise out of a
            # dataclass adapter that future call sites may not wrap (review).
            strategy_spec=self._decode_strategy_spec(),
        )


def _compute_content_hash(
    generation_method: str,
    strategy_name: str,
    thesis: str,
    source_papers: list[dict],
    asset_universe: list[str],
) -> str:
    """Deterministic keccak256 content hash for dedup."""
    from web3 import Web3

    canonical = json.dumps(
        {
            "generation_method": generation_method,
            "strategy_name": strategy_name,
            "thesis": thesis,
            "source_papers": sorted(source_papers, key=lambda p: p.get("arxiv_id", "")),
            "asset_universe": sorted(asset_universe),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return Web3.keccak(text=canonical).hex()


def upsert_strategy(
    session: Session,
    *,
    generation_method: str,
    strategy_name: str,
    thesis: str,
    source_papers: list[dict],
    asset_universe: list[str],
    risk_profile: str = "moderate",
    rigor_verdict: dict | None = None,
    parent_id: str | None = None,
    provenance_hash: str | None = None,
    is_example: bool = False,
    owner_wallet: str | None = None,
    owner_user_id: str | None = None,
    strategy_spec: dict | None = None,
    brief_intent: str | None = None,
) -> StrategyRecord:
    """Idempotent upsert: same content → same row, no duplicates.

    ``owner_user_id`` is the canonical Better Auth owner. ``owner_wallet`` is
    optional verified-wallet provenance. On a content-hash match, ownership is
    only backfilled onto ownerless rows — an existing owner is never overwritten.

    ``strategy_spec`` is the validated DSL spec dict (rebalancer decouple,
    Part A #1) — JSON-encoded when present, left NULL otherwise. On a
    content-hash match it is backfilled onto a row that lacks one, the same
    never-overwrite rule as ownership; an existing non-null spec is never
    replaced.

    ``brief_intent`` is the user's own free-text ask (v8 Lane 3.3) — plain
    text, left NULL otherwise. Same never-overwrite backfill rule: a
    content-hash match only fills a NULL, never replaces an existing value.
    """
    owner_wallet = owner_wallet.lower() if owner_wallet else None
    content_hash = _compute_content_hash(
        generation_method,
        strategy_name,
        thesis,
        source_papers,
        asset_universe,
    )

    existing = session.query(StrategyRecord).filter_by(content_hash=content_hash).first()
    if existing:
        # Backfill ownership on a legacy/anonymous row; never reassign an owner.
        if owner_user_id and not existing.owner_user_id:
            existing.owner_user_id = owner_user_id
            session.flush()
        if owner_wallet and not existing.owner_wallet:
            existing.owner_wallet = owner_wallet
            existing.updated_at = datetime.now(UTC)
            session.flush()
        # Backfill a missing spec onto an existing row; never overwrite one
        # that's already there.
        if strategy_spec is not None and not existing.strategy_spec:
            existing.strategy_spec = json.dumps(strategy_spec)
            existing.updated_at = datetime.now(UTC)
            session.flush()
        # Same never-overwrite backfill rule for the user's brief.
        if brief_intent and not existing.brief_intent:
            existing.brief_intent = brief_intent
            existing.updated_at = datetime.now(UTC)
            session.flush()
        # Update status/verdict if provided, but don't duplicate
        if rigor_verdict is not None:
            existing.rigor_verdict = json.dumps(rigor_verdict)
            existing.updated_at = datetime.now(UTC)
            # Status transition per docs/specs strategy-lifecycle:
            #   passing=True  → "live"     (in-portfolio-eligible, preserves
            #                                marketplace_service.trending logic)
            #   passing=False → "rejected" (visible failure — honesty wedge)
            #   no verdict    → unchanged
            if rigor_verdict.get("passing"):
                existing.status = "live"
            else:
                existing.status = "rejected"
            session.flush()
        return existing

    record = StrategyRecord(
        id=content_hash[:16],
        content_hash=content_hash,
        generation_method=generation_method,
        source_papers=json.dumps(source_papers),
        strategy_name=strategy_name,
        thesis=thesis,
        asset_universe=json.dumps(asset_universe),
        risk_profile=risk_profile,
        status="candidate",
        rigor_verdict=json.dumps(rigor_verdict) if rigor_verdict else None,
        parent_id=parent_id,
        provenance_hash=provenance_hash,
        is_example=is_example,
        owner_wallet=owner_wallet,
        owner_user_id=owner_user_id,
        # `is not None` (not truthiness) — consistent with the backfill branch
        # above, which also treats "present vs None" as the distinction. A
        # bare truthiness check would silently drop an explicitly-provided
        # empty dict ({}) by storing NULL instead of the serialized "{}".
        strategy_spec=json.dumps(strategy_spec) if strategy_spec is not None else None,
        # Truthiness here (unlike strategy_spec above) is correct: brief_intent
        # is plain text, not a dict where {} is a meaningful distinct value
        # from "absent" — an empty/whitespace brief has nothing to show either
        # way, so it collapses to NULL like any other "nothing here".
        brief_intent=brief_intent or None,
    )
    if rigor_verdict:
        # Same transition rule as the upsert-existing branch above
        record.status = "live" if rigor_verdict.get("passing") else "rejected"
    session.add(record)
    session.flush()
    logger.info(
        "store: persisted strategy %s (%s, %d papers)",
        record.id,
        generation_method,
        len(source_papers),
    )
    return record


def resolve_source_papers(session: Session, strategy_id: str) -> list[dict]:
    """Given a strategy/trace → its source_papers (arxiv_id + sha256)."""
    record = session.query(StrategyRecord).filter_by(id=strategy_id).first()
    if not record:
        return []
    return json.loads(record.source_papers)


def strategies_by_paper(session: Session, arxiv_id: str) -> list[StrategyRecord]:
    """Find all strategies citing a given arXiv paper (bidirectional link).

    Pushes a substring prefilter into the DB so we don't load the entire
    StrategyRecord table and JSON-parse every row on each call. The LIKE
    matches any row whose serialized ``source_papers`` text contains the
    arxiv_id; the exact JSON check below then removes substring false
    positives (e.g. ``2401.001`` matching ``2401.0012``). Portable across
    SQLite and Postgres.
    """
    if not arxiv_id:
        return []
    # Escape LIKE wildcards in the id so they're matched literally.
    needle = arxiv_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    candidates = (
        session.query(StrategyRecord).filter(StrategyRecord.source_papers.like(f"%{needle}%", escape="\\")).all()
    )
    return [r for r in candidates if arxiv_id in {p.get("arxiv_id", "") for p in json.loads(r.source_papers)}]

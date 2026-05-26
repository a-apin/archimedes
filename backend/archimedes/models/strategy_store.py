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
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Session

from archimedes.models.chat import Base

logger = logging.getLogger(__name__)


class StrategyRecord(Base):
    """Persistent strategy with content-hash dedup and provenance."""

    __tablename__ = "strategy_store"

    id = Column(String(64), primary_key=True)
    content_hash = Column(String(66), nullable=False)  # keccak256, 0x-prefixed

    # Generation provenance
    generation_method = Column(String(32), nullable=False)  # fusion|architect|curated
    source_papers = Column(Text, nullable=False, default="[]")  # JSON: [{arxiv_id, sha256}]
    provenance_hash = Column(String(66), nullable=True)

    # Strategy definition
    strategy_name = Column(String(256), nullable=False, default="")
    thesis = Column(Text, nullable=False, default="")
    asset_universe = Column(Text, nullable=False, default="[]")  # JSON list
    risk_profile = Column(String(32), nullable=False, default="moderate")

    # Status lifecycle
    status = Column(String(16), nullable=False, default="candidate")  # candidate|live|retired|rejected
    rigor_verdict = Column(Text, nullable=True)  # JSON: DSR/PBO/walk-forward results
    is_example = Column(Boolean, nullable=False, default=False)  # hand-curated static strategies

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
            "status": self.status,
            "rigor_verdict": json.loads(self.rigor_verdict) if self.rigor_verdict else None,
            "is_example": self.is_example,
            "parent_id": self.parent_id,
            "on_chain_registration_tx": self.on_chain_registration_tx,
            "on_chain_registration_block": self.on_chain_registration_block,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


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
) -> StrategyRecord:
    """Idempotent upsert: same content → same row, no duplicates."""
    content_hash = _compute_content_hash(
        generation_method,
        strategy_name,
        thesis,
        source_papers,
        asset_universe,
    )

    existing = session.query(StrategyRecord).filter_by(content_hash=content_hash).first()
    if existing:
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

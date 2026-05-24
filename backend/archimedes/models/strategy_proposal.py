"""StrategyProposal — episodic memory of every generation attempt.

Every fusion / architect / agent proposal — including rigor-fails and
user-rejects — is persisted here.  This is the "compounding substrate" from
T-PE.8: the strategy library is not static; every generation contributes
content-hashed, retrievable rows.  Separated from ``strategy_store`` because
proposals are ephemeral candidates while strategies are admitted artifacts.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    String,
    Text,
)

from archimedes.models.chat import Base

logger = logging.getLogger(__name__)


class StrategyProposal(Base):
    """Episodic record of every strategy generation attempt."""

    __tablename__ = "strategy_proposals"

    id = Column(String(64), primary_key=True)  # content_hash[:16]
    generation_id = Column(String(64), nullable=False, index=True)
    proposal_id = Column(String(64), nullable=False, index=True)
    parent_proposal_id = Column(String(64), nullable=True)

    # Verdict: rigor_pass | rigor_fail | user_rejected | pending
    verdict = Column(String(32), nullable=False, default="pending")
    # Trust: CANDIDATE | VALIDATED | RETIRED
    trust_level = Column(String(16), nullable=False, default="CANDIDATE")

    # Content hash (keccak256 of canonical payload, matching on-chain convention)
    content_hash = Column(String(66), nullable=False, unique=True)

    # Which agent produced this
    agent = Column(String(32), nullable=False, default="unknown")

    # Regime tag (nullable — only populated when regime is known)
    regime_tag = Column(String(16), nullable=True)

    # Full proposal payload as JSONB
    payload = Column(Text, nullable=False, default="{}")

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
        Index("ix_proposal_verdict", "verdict"),
        Index("ix_proposal_agent", "agent"),
        Index("ix_proposal_generation_id", "generation_id"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "generation_id": self.generation_id,
            "proposal_id": self.proposal_id,
            "parent_proposal_id": self.parent_proposal_id,
            "verdict": self.verdict,
            "trust_level": self.trust_level,
            "content_hash": self.content_hash,
            "agent": self.agent,
            "regime_tag": self.regime_tag,
            "payload": json.loads(self.payload) if self.payload else {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

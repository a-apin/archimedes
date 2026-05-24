"""Knowledge Graph ORM models.

Backing store for the SPECTER2 + REBEL/SciSpacy pipeline output that
`scripts/run_kb_pipeline.py` writes once the KB integration is live.

Per docs/specs/kb-integration-spec.md: heavy binaries (embeddings,
KG_triples.jsonl) live on the named volume; these tables hold the
DB-only fast paths.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    REAL,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from archimedes.models.chat import Base


class KGEntity(Base):
    __tablename__ = "kg_entities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_name = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False)
    paper_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (UniqueConstraint("canonical_name", "entity_type", name="uq_kg_entity"),)


class KGRelation(Base):
    __tablename__ = "kg_relations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_id = Column(Integer, ForeignKey("kg_entities.id"), index=True)
    relation = Column(String, nullable=False)
    object_id = Column(Integer, ForeignKey("kg_entities.id"))
    paper_arxiv_id = Column(String, ForeignKey("papers.arxiv_id", ondelete="CASCADE"), index=True)
    confidence = Column(REAL)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint(
            "subject_id",
            "relation",
            "object_id",
            "paper_arxiv_id",
            name="uq_kg_relation",
        ),
    )

"""Persistent storage for the q-fin paper corpus.

Two tables:
  - ``papers``: one row per arxiv paper (dedup key = arxiv_id).
  - ``corpus_meta``: singleton row tracking intake state (last run, hashes, cap).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from archimedes.models.chat import Base


class PaperRecord(Base):
    """DB row for one arXiv paper in the corpus."""

    __tablename__ = "papers"

    arxiv_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    authors: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    abstract: Mapped[str] = mapped_column(Text, nullable=False, default="")
    primary_category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    categories: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    published: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    updated: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    pdf_url: Mapped[str] = mapped_column(Text, nullable=True)
    pdf_sha256: Mapped[str] = mapped_column(String(64), nullable=True)
    full_text_path: Mapped[str] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="seed")
    cluster_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    topic_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        Index("ix_papers_primary_category", "primary_category"),
        Index("ix_papers_published", "published"),
    )

    def to_dict(self) -> dict:
        import json

        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "authors": json.loads(self.authors) if self.authors else [],
            "abstract": self.abstract,
            "primary_category": self.primary_category,
            "categories": json.loads(self.categories) if self.categories else [],
            "published": self.published,
            "updated": self.updated,
            "pdf_url": self.pdf_url,
            "pdf_sha256": self.pdf_sha256,
            "source": self.source,
            "cluster_id": self.cluster_id,
            "topic_label": self.topic_label,
            "ingested_at": self.ingested_at.isoformat() if self.ingested_at else None,
        }


class CorpusMetaRecord(Base):
    """Singleton tracking corpus intake state."""

    __tablename__ = "corpus_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    last_intake_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    corpus_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    artifact_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    artifact_built_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    paper_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")

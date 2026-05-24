"""Database setup — SQLAlchemy async engine + session factory.

Uses DATABASE_URL env var (set by docker-compose to Postgres).
Falls back to local SQLite for development.
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from archimedes.models.backtest_store import BacktestResultRecord  # noqa: F401
from archimedes.models.chat import Base
from archimedes.models.corpus_store import CorpusMetaRecord, PaperRecord  # noqa: F401
from archimedes.models.strategy_proposal import StrategyProposal  # noqa: F401
from archimedes.models.strategy_store import StrategyRecord  # noqa: F401
from archimedes.models.user_profile import UserProfile  # noqa: F401

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./archimedes_chat.db")


def _get_engine_kwargs() -> dict:
    """Return engine kwargs appropriate for the database type."""
    if DATABASE_URL.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    # Postgres
    return {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}


engine = create_engine(DATABASE_URL, **_get_engine_kwargs())
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db() -> None:
    """Create all tables (idempotent for hackathon — no Alembic needed).

    Also runs hand-rolled ADD COLUMN IF NOT EXISTS migrations for model fields
    that were added after the original `papers` table was created. Postgres
    only; on SQLite (local dev) the create_all on a fresh DB takes the new
    columns directly. Without this, /api/papers/ returns a 500 in any env
    where the papers table predates these model additions (i.e. our running
    docker volume).
    """
    # Side-effect imports: ensure all ORM models register their tables with
    # Base.metadata before create_all runs. Otherwise the kg_* tables only
    # appear if some other code path imports archimedes.models.kg first.
    from archimedes.models import kg  # noqa: F401

    Base.metadata.create_all(bind=engine)
    logger.info(f"Database tables created at {DATABASE_URL}")

    if DATABASE_URL.startswith("postgresql"):
        from sqlalchemy import text

        added_columns_sql = [
            "ALTER TABLE papers ADD COLUMN IF NOT EXISTS cluster_id TEXT",
            "ALTER TABLE papers ADD COLUMN IF NOT EXISTS topic_label TEXT",
            "ALTER TABLE papers ADD COLUMN IF NOT EXISTS content_hash TEXT",
        ]
        try:
            with engine.begin() as conn:
                for stmt in added_columns_sql:
                    conn.execute(text(stmt))
            logger.info("init_db: papers schema patches applied (idempotent)")
        except Exception as exc:
            logger.warning("init_db: papers schema patch failed (non-fatal): %s", exc)


def get_session() -> Session:
    """Get a new DB session. Use as context manager."""
    return SessionLocal()

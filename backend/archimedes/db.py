"""Database setup — SQLAlchemy async engine + session factory.

Uses DATABASE_URL env var (set by docker-compose to Postgres).
Falls back to local SQLite for development.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from archimedes.models.backtest_fixtures_store import StrategyBacktestFixture  # noqa: F401
from archimedes.models.backtest_store import BacktestResultRecord  # noqa: F401
from archimedes.models.chat import Base
from archimedes.models.corpus_store import CorpusMetaRecord, PaperRecord  # noqa: F401
from archimedes.models.daily_returns_store import StrategyDailyReturn  # noqa: F401
from archimedes.models.marketplace import MarketplaceAgent, SubscriberLiability, SubscriberTickLog  # noqa: F401
from archimedes.models.strategy_generators import StrategyGenerator  # noqa: F401
from archimedes.models.strategy_proposal import StrategyProposal  # noqa: F401
from archimedes.models.strategy_store import StrategyRecord  # noqa: F401
from archimedes.models.user_profile import UserProfile  # noqa: F401

logger = logging.getLogger(__name__)

# backend/ — the directory containing the top-level `archimedes` package.
_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _default_database_url() -> str:
    """Return the default SQLite URL, anchored to `backend/` regardless of CWD.

    `sqlite:///./archimedes_chat.db` (the old default) resolves `./` against
    the process's current working directory, so launching from the repo root
    vs. `backend/` produced two disjoint `archimedes_chat.db` files with split
    session/chat history. Anchoring to this file's parent directory makes the
    default deterministic across launch contexts.
    """
    return f"sqlite:///{_BACKEND_DIR / 'archimedes_chat.db'}"


DATABASE_URL = os.getenv("DATABASE_URL", _default_database_url())


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
    from archimedes.models import (
        kg,  # noqa: F401
        strategy_passport_record,  # noqa: F401
    )

    Base.metadata.create_all(bind=engine)
    logger.info(f"Database tables created at {DATABASE_URL}")

    if DATABASE_URL.startswith("postgresql"):
        from sqlalchemy import text

        added_columns_sql = [
            "ALTER TABLE papers ADD COLUMN IF NOT EXISTS cluster_id TEXT",
            "ALTER TABLE papers ADD COLUMN IF NOT EXISTS topic_label TEXT",
            "ALTER TABLE papers ADD COLUMN IF NOT EXISTS content_hash TEXT",
            # strategy_store columns added after the table was first created.
            # Without these, Generate persistence dies with UndefinedColumn
            # (observed live 2026-05-25 on every Generate attempt — the agent
            # completes but the post-evaluation upsert crashes).
            "ALTER TABLE strategy_store ADD COLUMN IF NOT EXISTS is_example BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE strategy_store ADD COLUMN IF NOT EXISTS on_chain_registration_tx VARCHAR(66)",
            "ALTER TABLE strategy_store ADD COLUMN IF NOT EXISTS on_chain_registration_block VARCHAR(32)",
            "ALTER TABLE strategy_store ADD COLUMN IF NOT EXISTS parent_id VARCHAR(64)",
            # chat_messages.verified — SIWE-bound chat identity (issue #524).
            # Pre-existing rows default to FALSE: they were body-supplied, never verified.
            "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT FALSE",
            # Marketplace race/hijack guards (PR #958): sub_id is client-supplied
            # and keys the in-process engine, so it must be globally unique among
            # subscriptions; one RUNNING subscription per (strategy, wallet)
            # closes the check-then-insert race in POST /subscribe.
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_marketplace_sub_id "
            "ON marketplace_agents (sub_id) WHERE role = 'subscriber'",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_marketplace_running_subscription "
            "ON marketplace_agents (strategy_id, subscriber_wallet) "
            "WHERE role = 'subscriber' AND status = 'running'",
        ]
        try:
            with engine.begin() as conn:
                for stmt in added_columns_sql:
                    conn.execute(text(stmt))
            logger.info("init_db: papers schema patches applied (idempotent)")
        except Exception as exc:
            logger.warning("init_db: papers schema patch failed (non-fatal): %s", exc)

    _ensure_ownership_columns()


def _ensure_ownership_columns() -> None:
    """Idempotent ADD COLUMN for per-user strategy ownership.

    Unlike the Postgres-only ``ADD COLUMN IF NOT EXISTS`` patches above, these
    columns must also land on a pre-existing SQLite file (local dev DBs that
    predate the model change — create_all only covers FRESH databases), and
    SQLite doesn't support ``IF NOT EXISTS`` in ``ADD COLUMN``. So this helper
    uses dialect-safe introspection (``sqlalchemy.inspect``) and only ALTERs
    when a column is actually missing — works identically on SQLite and
    Postgres. Non-fatal on failure, matching the patch block above.
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    wanted: dict[str, list[tuple[str, str]]] = {
        "strategy_store": [
            ("owner_wallet", "VARCHAR(42)"),
            # TRUE/FALSE literals are valid DDL defaults on both dialects
            # (SQLite ≥3.23 parses them as 1/0).
            ("is_published", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ],
        "strategy_passports": [
            ("owner_wallet", "VARCHAR(42)"),
            # universe_source (#857): "user" | "model" | "full" provenance of
            # the asset_universe pick. NULL on rows that predate this column.
            ("universe_source", "VARCHAR(16)"),
        ],
    }
    try:
        inspector = sa_inspect(engine)
        with engine.begin() as conn:
            for table, columns in wanted.items():
                if not inspector.has_table(table):
                    continue
                existing = {col["name"] for col in inspector.get_columns(table)}
                for name, ddl in columns:
                    if name not in existing:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                        logger.info("init_db: added %s.%s", table, name)
            # The model declares index=True on strategy_store.owner_wallet;
            # create_all only builds it on fresh DBs, so mirror it here for
            # ALTERed ones. IF NOT EXISTS is valid on both SQLite and Postgres.
            if inspector.has_table("strategy_store"):
                conn.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_strategy_store_owner_wallet ON strategy_store (owner_wallet)")
                )
    except Exception as exc:
        logger.warning("init_db: ownership column patch failed (non-fatal): %s", exc)


def get_session() -> Session:
    """Get a new DB session. Use as context manager."""
    return SessionLocal()

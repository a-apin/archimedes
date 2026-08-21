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

from archimedes.models.account import (
    AuthAccount,
    AuthSession,
    AuthUser,
    AuthVerification,
    LinkedWallet,
    WalletLinkChallenge,
)
from archimedes.models.asset_daily_bars import AssetDailyBar
from archimedes.models.backtest_fixtures_store import StrategyBacktestFixture
from archimedes.models.backtest_store import BacktestResultRecord
from archimedes.models.chat import Base
from archimedes.models.corpus_store import CorpusMetaRecord, PaperRecord
from archimedes.models.daily_returns_store import StrategyDailyReturn
from archimedes.models.generation_cost import GenerationCostRecord
from archimedes.models.identity import ControlledWallet, IdentityEvent, WalletIdentity
from archimedes.models.marketplace import (
    MarketplaceAgent,
    SettlementIntent,
    SubscriberLiability,
    SubscriberTickLog,
)
from archimedes.models.payment_receipt import PaymentReceiptRecord
from archimedes.models.request_snapshot import RequestCountSnapshot
from archimedes.models.strategy_generators import StrategyGenerator
from archimedes.models.strategy_proposal import StrategyProposal
from archimedes.models.strategy_store import StrategyRecord
from archimedes.models.user_profile import UserProfile

# The model imports above exist to register their tables on ``Base.metadata``
# (a side effect of import) so ``init_db()``'s ``create_all`` sees every table.
# ruff ignores them via ``noqa: F401``; listing them in ``__all__`` marks them as
# intentional re-exports so CodeQL's unused-import query also treats them as used.
# (``db.py`` is never ``import *``-ed, so ``__all__`` has no other effect.)
__all__ = [
    "DATABASE_URL",
    "AuthAccount",
    "AuthSession",
    "AuthUser",
    "AuthVerification",
    "AssetDailyBar",
    "BacktestResultRecord",
    "Base",
    "ControlledWallet",
    "CorpusMetaRecord",
    "GenerationCostRecord",
    "IdentityEvent",
    "LinkedWallet",
    "MarketplaceAgent",
    "PaperRecord",
    "PaymentReceiptRecord",
    "RequestCountSnapshot",
    "SettlementIntent",
    "StrategyBacktestFixture",
    "StrategyDailyReturn",
    "StrategyGenerator",
    "StrategyProposal",
    "StrategyRecord",
    "SubscriberLiability",
    "SubscriberTickLog",
    "UserProfile",
    "WalletIdentity",
    "WalletLinkChallenge",
    "engine",
    "get_session",
    "init_db",
]

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
    """Build the schema on SQLite (local dev + hermetic tests); on Postgres,
    Alembic owns schema creation (`alembic upgrade head` — see
    `migrations/README.md`) and this function only applies transitional
    idempotent patches.

    Also runs hand-rolled ADD COLUMN IF NOT EXISTS migrations for model fields
    that were added after the original `papers` table was created. Postgres
    only; on SQLite (local dev) the create_all on a fresh DB takes the new
    columns directly. Without this, /api/papers/ returns a 500 in any env
    where the papers table predates these model additions (i.e. our running
    docker volume). This block, and `_ensure_ownership_columns()` below, are
    transitional: Alembic is now the source of truth for new Postgres schema
    changes going forward (issue #1028), and these idempotent ALTERs remain
    only until every column they cover has landed as a proper Alembic
    revision — a follow-up cleanup, not done here.
    """
    # Side-effect imports: ensure all ORM models register their tables with
    # Base.metadata before create_all runs. Otherwise the kg_* tables only
    # appear if some other code path imports archimedes.models.kg first.
    from archimedes.models import (
        kg,  # noqa: F401
        strategy_passport_record,  # noqa: F401
    )

    # SQLite-only. On Postgres, schema is Alembic's job exclusively — running
    # create_all() unconditionally here raced Alembic's own DDL under
    # multiple concurrently-booting Fargate tasks: two tasks starting at once
    # could each try to create (or one create while another's migration
    # ALTERs/constrains) the same tables — e.g. the issue #1028
    # identity-ledger tables — producing duplicate-table / duplicate-
    # constraint errors on a cold multi-task deploy instead of one clean
    # rollout. Gating to SQLite (local dev + every hermetic test's fresh
    # file/in-memory DB) removes Postgres from that race entirely.
    if DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
        logger.info(f"Database tables created at {DATABASE_URL}")

    if DATABASE_URL.startswith("postgresql"):
        from sqlalchemy import text

        # Transitional: these predate Alembic (issue #1028) and still run on
        # every Postgres boot. New Postgres schema changes go through an
        # Alembic revision instead (see `migrations/README.md`) — this block
        # is not the place to add new columns.
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
            # chat_messages.verified — cryptographically linked-wallet attribution.
            # Pre-existing rows default to FALSE: they were body-supplied, never verified.
            "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT FALSE",
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

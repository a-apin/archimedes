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
from archimedes.models.api_key import ApiKeyRecord
from archimedes.models.asset_daily_bars import AssetDailyBar
from archimedes.models.backtest_fixtures_store import StrategyBacktestFixture
from archimedes.models.backtest_store import BacktestResultRecord
from archimedes.models.chat import Base
from archimedes.models.corpus_store import CorpusMetaRecord, PaperRecord
from archimedes.models.daily_returns_store import StrategyDailyReturn
from archimedes.models.debate_transcript import DebateTranscriptRecord
from archimedes.models.free_generation_grant import FreeGenerationGrantRecord
from archimedes.models.generation_cost import GenerationCostRecord
from archimedes.models.generation_credit import GenerationCreditRecord
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
    "ApiKeyRecord",
    "AssetDailyBar",
    "BacktestResultRecord",
    "Base",
    "ControlledWallet",
    "CorpusMetaRecord",
    "DebateTranscriptRecord",
    "FreeGenerationGrantRecord",
    "GenerationCostRecord",
    "GenerationCreditRecord",
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


# How long a boot schema patch may wait for its lock before it gives up.
#
# #1818: a no-op ``ADD COLUMN IF NOT EXISTS`` still takes AccessExclusiveLock on
# the table for the rest of its transaction, and a *waiting* AccessExclusiveLock
# request queues every later reader of that table behind it. On 2026-09-03 that
# turned a boot into a 91-minute hang: the replacement tasks' ``init_db()`` (run
# at import from ``main.py``) sat in the lock queue behind another process's open
# transaction, logged nothing, and served no requests until an OOM kill broke the
# chain. Five seconds is longer than any healthy DDL patch here (all of them are
# no-ops on a current schema) and short enough that a boot is never held hostage.
PATCH_LOCK_TIMEOUT = os.getenv("DB_PATCH_LOCK_TIMEOUT", "5s")


def _bind_is_postgres(bind) -> bool:
    """True when this engine speaks Postgres.

    Asked of the DIALECT rather than of the ``DATABASE_URL`` string, because
    that is the thing ``SET LOCAL lock_timeout`` is a property of — and because
    it is what makes the SQLite path provably untouched in a test that hands
    this module a real SQLite engine.
    """
    try:
        return bool(bind.dialect.name == "postgresql")
    except AttributeError:  # pragma: no cover - a bind with no dialect is not ours
        return False


def _apply_patch_statement(stmt: str, *, context: str) -> bool:
    """Run ONE boot schema patch in its OWN short transaction. Never raises.

    Two properties, both from #1818, and both load-bearing:

    1. **One statement, one transaction.** The patches used to share a single
       ``engine.begin()``. The three ``ALTER TABLE papers`` ran first and held
       AccessExclusiveLock on ``papers`` for the rest of the block, so when the
       fourth statement (``ALTER TABLE strategy_store``) queued behind another
       session, every reader of ``papers`` queued behind *us*. Committing each
       statement on its own means a wait on ``strategy_store`` can never hold
       ``papers``.
    2. **A bounded wait.** ``SET LOCAL lock_timeout`` applies to the enclosing
       transaction only, so it is set inside each patch's own transaction and
       needs no engine-wide configuration. Postgres only — SQLite has no such
       statement and no such lock queue.

    A patch that cannot take its lock in time is a WARNING and boot continues:
    these patches are transitional and already declared non-fatal (Alembic owns
    Postgres schema, #1028). Returns True when the statement ran.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    try:
        with engine.begin() as conn:
            if _bind_is_postgres(engine):
                # Interpolated, not bound: SET LOCAL takes a literal, and the
                # value is a module constant, never user input.
                conn.execute(text(f"SET LOCAL lock_timeout = '{PATCH_LOCK_TIMEOUT}'"))
            conn.execute(text(stmt))
    except OperationalError as exc:
        # The #1818 shape: someone else holds the lock. Do not wait, do not
        # fail the boot — say so and move on to the next patch.
        logger.warning(
            "init_db: %s patch could not take its lock within %s (non-fatal, boot continues): %s — %s",
            context,
            PATCH_LOCK_TIMEOUT,
            stmt,
            exc,
        )
        return False
    except Exception as exc:
        logger.warning("init_db: %s patch failed (non-fatal): %s — %s", context, stmt, exc)
        return False
    return True


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

    Boot safety (#1818): every patch statement runs in its OWN transaction,
    with ``SET LOCAL lock_timeout`` on Postgres — see
    :func:`_apply_patch_statement` for the incident that bought that rule. This
    function is a BOOT step, not a per-cycle one: the only callers that should
    reach it repeatedly are read paths that need the transitional columns to
    exist, and the paper-advance tick deliberately no longer calls it at all
    (``services/paper_trading.py``).
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
        # One transaction per statement, each with its own lock_timeout —
        # see _apply_patch_statement for why (#1818). Never raises, so a
        # blocked patch cannot stop the ones behind it or the boot.
        applied = sum(_apply_patch_statement(stmt, context="papers") for stmt in added_columns_sql)
        logger.info(
            "init_db: papers schema patches applied (idempotent) — %d/%d statements",
            applied,
            len(added_columns_sql),
        )

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

    Its DDL goes through :func:`_apply_patch_statement` for the same #1818
    reason: one transaction per statement, with a ``lock_timeout`` on Postgres.
    The ``CREATE INDEX IF NOT EXISTS`` at the end is the sharpest case — it
    takes a lock on ``strategy_store`` too, and it used to share a transaction
    with the ALTERs above it.
    """
    from sqlalchemy import inspect as sa_inspect

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
        statements: list[tuple[str, str]] = []
        for table, columns in wanted.items():
            if not inspector.has_table(table):
                continue
            existing = {col["name"] for col in inspector.get_columns(table)}
            statements.extend(
                (f"ALTER TABLE {table} ADD COLUMN {name} {ddl}", f"{table}.{name}")
                for name, ddl in columns
                if name not in existing
            )
        # The model declares index=True on strategy_store.owner_wallet;
        # create_all only builds it on fresh DBs, so mirror it here for
        # ALTERed ones. IF NOT EXISTS is valid on both SQLite and Postgres.
        if inspector.has_table("strategy_store"):
            statements.append(
                (
                    "CREATE INDEX IF NOT EXISTS ix_strategy_store_owner_wallet ON strategy_store (owner_wallet)",
                    "ix_strategy_store_owner_wallet",
                )
            )
    except Exception as exc:
        logger.warning("init_db: ownership column inspection failed (non-fatal): %s", exc)
        return

    for stmt, label in statements:
        if _apply_patch_statement(stmt, context="ownership"):
            logger.info("init_db: applied %s", label)


def get_session() -> Session:
    """Get a new DB session. Use as context manager."""
    return SessionLocal()

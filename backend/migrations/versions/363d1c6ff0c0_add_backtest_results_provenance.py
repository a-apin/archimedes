"""add row-level provenance to backtest_results

Audit 2026-08-03. PR #1203 fixed a routing defect: single-feed strategies
were backtested against a hardcoded SPY default instead of their declared
``ASSET_UNIVERSE``, and 13 cross-sectional strategies ran on one feed when
they need the whole universe simultaneously — both producing worthless
(often zero-trade / flat-return) rows that the rigor gate graded as if they
were real. Root cause: two independent pipelines
(``backend/archimedes/scripts/run_backtests.py``'s analytics-engine/backtrader
path and ``generation_pipeline.py``'s DSL-fusion/portfolio-simulator paths)
write the same ``backtest_results`` table and were INDISTINGUISHABLE — no
column said which script/pipeline produced a row, when it was actually
computed (as opposed to when the DB row was inserted), or what git SHA of the
code ran it. Re-running the backtests without fixing this just resets the
clock on the next occurrence of the same defect class.

Three fields were requested; two already existed on the model and are NOT
duplicated here:
  - "which engine computed it"      -> ``backtest_engine`` (existing).
  - "per-row code identity"         -> ``backtest_code_hash`` (existing; NULL
    for every dsl-fusion row today — that path scores a DSL spec, not a
    hashable Python file; unchanged by this migration).
  - "when the DB row landed"        -> ``created_at`` (existing).

Genuinely missing, added here:
  - ``source_pipeline`` (NOT NULL) — which SCRIPT/call path invoked
    ``insert_backtest_if_missing``: 'run_backtests' |
    'seed_backtests_from_artifacts' | 'generation_pipeline.dsl_fusion' |
    'generation_pipeline.portfolio_backtester'. ``backtest_engine`` names the
    COMPUTE engine, not the caller — a single engine tag ('backtrader') is
    shared by two different Python entrypoints that were otherwise
    indistinguishable in the data. This is the field the whole episode was
    actually missing.
  - ``computed_at`` (NOT NULL) — when the backtest was actually COMPUTED,
    which can predate ``created_at`` (``seed_backtests_from_artifacts.py``
    loads an artifact file written by an earlier run and inserts it later).
  - ``source_git_sha`` (nullable) — ``ARCHIMEDES_GIT_SHA``, the same
    build-provenance env var ``/health``'s "version" field already reads
    (issue #1039) — reused here rather than inventing a second mechanism.
    Stays NULL when unset (local/dev, or a historical row) — an honest
    unknown, never a fabricated value.
  - ``provenance_inferred`` (NOT NULL, default false) — true only for rows
    THIS migration's backfill reconstructed after the fact; false for every
    row a provenance-aware writer stamps live from here on.

Backfill ships WITH this migration (repo convention — not a follow-up): the
73 rows in prod as of 2026-08-03 are not left with ``source_pipeline`` NULL.
  - ``backtest_engine = 'dsl-fusion'``             -> source_pipeline =
    'generation_pipeline.dsl_fusion' (deterministic: only one writer has ever
    set that engine tag, so this is a reconstruction, not a guess).
  - ``backtest_engine = 'portfolio-simulator-v1'`` -> source_pipeline =
    'generation_pipeline.portfolio_backtester' (deterministic, same
    reasoning).
  - everything else (``backtest_engine = 'backtrader'`` or NULL)
    -> source_pipeline = 'unknown_pre_provenance'. Both run_backtests.py and
    seed_backtests_from_artifacts.py produce identical 'backtrader'-tagged
    artifacts through the same mapper (``backtest_mapper.py``), so there is
    no signal in the pre-existing columns to tell them apart for historical
    rows — an honestly-labelled unknown beats a confident guess.
  - ``computed_at`` <- ``created_at`` for every existing row (the best
    available proxy; the true compute time was never captured before this
    column existed).
  - ``source_git_sha`` stays NULL for every existing row (no historical
    record of which commit produced them).
  - ``provenance_inferred`` <- true for every row this backfill touches.

Revision ID: 363d1c6ff0c0
Revises: 3f643d292e04
Create Date: 2026-08-03 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "363d1c6ff0c0"
down_revision: str | Sequence[str] | None = "3f643d292e04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DSL_FUSION_ENGINE = "dsl-fusion"
_PORTFOLIO_ENGINE = "portfolio-simulator-v1"
_UNKNOWN_LEGACY = "unknown_pre_provenance"


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("backtest_results", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_pipeline", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("source_git_sha", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("provenance_inferred", sa.Boolean(), nullable=False, server_default=sa.false()))

    # ── Backfill every pre-existing row (ships with this migration) ───────
    # WHERE source_pipeline IS NULL scopes this to rows that predate the
    # column entirely — the only rows that can exist at this point in the
    # upgrade, since the column was just added.
    op.execute(
        f"""
        UPDATE backtest_results
        SET
            source_pipeline = CASE
                WHEN backtest_engine = '{_DSL_FUSION_ENGINE}' THEN 'generation_pipeline.dsl_fusion'
                WHEN backtest_engine = '{_PORTFOLIO_ENGINE}' THEN 'generation_pipeline.portfolio_backtester'
                ELSE '{_UNKNOWN_LEGACY}'
            END,
            computed_at = created_at,
            provenance_inferred = true
        WHERE source_pipeline IS NULL
        """
    )

    with op.batch_alter_table("backtest_results", schema=None) as batch_op:
        batch_op.alter_column("source_pipeline", existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column("computed_at", existing_type=sa.DateTime(timezone=True), nullable=False)

    op.create_index("ix_backtest_source_pipeline", "backtest_results", ["source_pipeline"])


def downgrade() -> None:
    """Downgrade schema.

    Structural-only — the reconstructed backfill values are not restored
    (there is nothing to restore them FROM; they did not exist pre-migration,
    same convention as every other backfill migration in this history).
    """
    op.drop_index("ix_backtest_source_pipeline", table_name="backtest_results")
    with op.batch_alter_table("backtest_results", schema=None) as batch_op:
        batch_op.drop_column("provenance_inferred")
        batch_op.drop_column("source_git_sha")
        batch_op.drop_column("computed_at")
        batch_op.drop_column("source_pipeline")

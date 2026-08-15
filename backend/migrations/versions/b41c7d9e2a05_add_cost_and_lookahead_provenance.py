"""add cost-model + look-ahead provenance to backtest_results, null out fabricated correlations

Follows 363d1c6ff0c0 (row-level provenance). That migration answered "which
pipeline produced this row"; this one answers "on what cost basis" and "what
does its look-ahead boolean actually mean", and stops one column asserting a
measurement nobody took.

Three engines write ``backtest_results`` and one gate ranks them together:
the curated backtrader path, the generated-weights portfolio simulator, and
the DSL-fusion path. Until the cost SSOT landed the curated path charged
commission AND slippage while the other two charged commission only, so rows
were being compared across different cost bases with nothing on the row to say
so.

Added:

  - ``cost_model_id`` (nullable) — fingerprint of the cost model actually
    installed on the broker, e.g. ``"cm1:d10:s5"``. Carries per-symbol
    overrides and any engine-specific extra (the portfolio simulator appends
    ``"+almgren"``) that ``transaction_cost_bps`` alone cannot express. Two
    rows are comparable when this matches.

  - ``look_ahead_audit_source`` (nullable) — where ``look_ahead_audit_passed``
    came from. The analytics engine's own check reads only
    ``cerebro.broker.p.coc``/``coo``, which it never sets, so it returns True
    for every run regardless of whether the strategy peeks at future data;
    ``_lookahead_audit_passed``'s docstring says exactly that. The real
    AST-based audit is ``rigor_evaluator.look_ahead_audit`` and it does not run
    on that path. Without this column a constant True is indistinguishable from
    an audit that actually passed. Vocabulary: ``broker_config_only`` |
    ``ast_audit`` | ``self_attested``.

Changed:

  - ``correlation_to_spy`` / ``correlation_to_btc`` become nullable. The mapper
    coerced a missing correlation to ``0.0``, which asserts "uncorrelated to
    SPY/BTC" — a real claim, and one nothing measured. Populating them needs a
    benchmark feed in every run, which is separate work, so the honest value is
    NULL.

Backfill ships with the migration (repo convention):

  - ``cost_model_id`` stays NULL for every existing row. Reconstructing it would
    mean asserting a cost basis for runs whose actual charges we cannot recover;
    NULL is the honest unknown and the whole reason the column exists.
  - ``look_ahead_audit_source`` <- ``'broker_config_only'`` for every existing
    row. This is a reconstruction, not a guess: no other producer has ever
    written this table's ``look_ahead_audit_passed``.
  - Existing ``0.0`` correlations are NOT nulled. A stored ``0.0`` may be a
    fabricated default or a genuine measurement, and there is no signal left to
    tell them apart. Rewriting real zeros to NULL would destroy data to fix a
    presentation problem. The library re-run replaces these rows wholesale;
    until then ``cost_model_id IS NULL`` marks the pre-SSOT cohort.

Revision ID: b41c7d9e2a05
Revises: 363d1c6ff0c0
Create Date: 2026-08-16 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b41c7d9e2a05"
down_revision: str | Sequence[str] | None = "363d1c6ff0c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BROKER_CONFIG_ONLY = "broker_config_only"


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("backtest_results", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cost_model_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("look_ahead_audit_source", sa.String(length=32), nullable=True))
        batch_op.alter_column("correlation_to_spy", existing_type=sa.Float(), nullable=True)
        batch_op.alter_column("correlation_to_btc", existing_type=sa.Float(), nullable=True)

    # Every pre-existing row's look-ahead boolean came from the broker-config
    # check — no other producer has ever written this column.
    op.execute(
        f"""
        UPDATE backtest_results
        SET look_ahead_audit_source = '{_BROKER_CONFIG_ONLY}'
        WHERE look_ahead_audit_source IS NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema.

    The correlation columns go back to NOT NULL, so any NULL written while this
    revision was live has to become something. It becomes ``0.0`` — the value
    the old mapper would have produced for exactly these rows, which is what
    downgrading to that schema means.
    """
    op.execute("UPDATE backtest_results SET correlation_to_spy = 0.0 WHERE correlation_to_spy IS NULL")
    op.execute("UPDATE backtest_results SET correlation_to_btc = 0.0 WHERE correlation_to_btc IS NULL")

    with op.batch_alter_table("backtest_results", schema=None) as batch_op:
        batch_op.alter_column("correlation_to_btc", existing_type=sa.Float(), nullable=False)
        batch_op.alter_column("correlation_to_spy", existing_type=sa.Float(), nullable=False)
        batch_op.drop_column("look_ahead_audit_source")
        batch_op.drop_column("cost_model_id")

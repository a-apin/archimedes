"""schema relations Phase 1 — validate the gated FKs

Follow-up to ``fb8d0bae8112``, deliberately its own revision rather than a
second step glued onto that migration's ``upgrade()``. ``fb8d0bae8112`` adds
four foreign keys ``NOT VALID`` and does nothing else with them; THIS
migration is the only place any of them is ever passed to ``VALIDATE
CONSTRAINT``.

WHY a separate revision instead of validating inline (the shape the original
draft of ``fb8d0bae8112`` used): prod Aurora is known, from this repo's
2026-07-05 data-architecture audit, to already contain unjoinable/orphaned
populations on exactly this kind of identity/ownership column. Gating
``VALIDATE CONSTRAINT`` on a live orphan count (below) means a *known*
orphan population can never turn into a hard Postgres error — but an
*unexpected* one still could (a race between this migration's own
``SELECT COUNT(*)`` and a concurrent write, or any other runtime surprise
during the table scan ``VALIDATE CONSTRAINT`` performs). If that check and
the constraint-adding step shared one migration, an unexpected failure here
would roll back the indices, the column widen, and the NOT VALID constraint
creation right along with it — turning a validation hiccup into a lost
additive deploy. Splitting the two means that risk is contained to this
migration alone; the indices/widen/NOT-VALID-constraints from ``fb8d0bae8112``
are unaffected by anything that happens here, whatever the migrations/env.py
transaction model of the day looks like.

One important caveat, stated honestly rather than assumed away:
``migrations/env.py``'s ``run_migrations_online()`` wraps a WHOLE
``alembic upgrade`` invocation in one ``context.begin_transaction()`` (no
``transaction_per_migration=True``). If ``fb8d0bae8112`` and this revision
are both pending and applied via a single ``alembic upgrade head`` call —
the normal case for this repo's build-on-deploy pipeline — they still run in
the SAME database transaction, and an uncaught error here would still roll
both back together. What splitting them still buys, even under that
default: (1) the *expected* failure mode — a known orphan population — is
turned into a print/log line, never a raised exception, by the live gate
below, so it cannot trigger that rollback in the first place; (2) operators
who want genuine transaction isolation for a prod deploy where orphan
counts are unmeasured can run `alembic upgrade fb8d0bae8112` and confirm it
before running `alembic upgrade head` to apply this one — two invocations,
two transactions, no code change required. Changing env.py's transaction
model globally is a separate, bigger decision (it touches every migration,
not just this pair) and is deliberately out of scope for this PR — flagged
here for Dan rather than applied unilaterally.

The orphan-count queries below are BYTE-IDENTICAL to ``fb8d0bae8112``'s own
``_GATED_FKS`` (drift-checked by
``test_phase1_validate_orphan_sql_matches_source_revision`` in
``test_alembic_migrations.py``) — duplicated rather than imported because
migrations should stand alone and keep working even if a sibling revision
module is later renamed or refactored.

An orphan count above zero is not a failure. It is the documented, expected
outcome for ``fk_paper_deployments_strategy_id`` / ``.owner_wallet`` (no
historical backfill has ever run against those columns) — the constraint is
left NOT VALID (already enforcing all future writes; historical rows stay
exactly as they were) and this migration still completes successfully. This
is the "operator-free follow-up" this repo's no-operator-rituals principle
calls for: nothing here requires a human to eyeball a count or run a manual
SQL script before it's safe to deploy — the gate is the code, and its
verdict is printed to the migration log for whoever reads it.

SQLite has no NOT VALID / VALIDATE CONSTRAINT concept at all —
``fb8d0bae8112`` already added these same constraints in FULL on SQLite (via
``batch_alter_table``'s table-rebuild path), so this migration is a no-op
there.

Revision ID: 9c2e7b5a1f4d
Revises: fb8d0bae8112
Create Date: 2026-08-20 00:00:01.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "9c2e7b5a1f4d"
down_revision: str | Sequence[str] | None = "fb8d0bae8112"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

# Kept byte-identical to fb8d0bae8112._GATED_FKS (table/orphan_sql columns
# only — the FK shape itself was already created by that revision, this one
# only needs enough to find and validate it). See module docstring for why
# this is a deliberate duplication, not drift.
_GATED_FKS: tuple[tuple[str, str, str], ...] = (
    (
        "fk_linked_wallets_address_wallet_identity",
        "linked_wallets",
        """
        SELECT COUNT(*) FROM linked_wallets lw
        LEFT JOIN wallet_identities wi ON lw.address = wi.wallet_address
        WHERE wi.wallet_address IS NULL
        """,
    ),
    (
        "fk_paper_deployments_owner_user_id",
        "paper_deployments",
        """
        SELECT COUNT(*) FROM paper_deployments pd
        LEFT JOIN auth_users au ON pd.owner_user_id = au.id
        WHERE pd.owner_user_id IS NOT NULL AND au.id IS NULL
        """,
    ),
    (
        "fk_paper_deployments_strategy_id",
        "paper_deployments",
        """
        SELECT COUNT(*) FROM paper_deployments pd
        LEFT JOIN strategy_store ss ON pd.strategy_id = ss.id
        WHERE ss.id IS NULL
        """,
    ),
    (
        "fk_paper_deployments_owner_wallet",
        "paper_deployments",
        """
        SELECT COUNT(*) FROM paper_deployments pd
        LEFT JOIN wallet_identities wi ON pd.owner_wallet = wi.wallet_address
        WHERE pd.owner_wallet IS NOT NULL AND wi.wallet_address IS NULL
        """,
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if not is_postgres:
        # SQLite: fb8d0bae8112 already added these constraints in full.
        # Nothing left to validate.
        return

    # Bounded lock acquisition — VALIDATE CONSTRAINT takes a SHARE UPDATE
    # EXCLUSIVE lock (blocks other DDL, not normal reads/writes) for the
    # duration of its table scan; without a timeout a blocked acquire would
    # queue silently rather than failing fast and leaving a clean,
    # retryable (no partial state) transaction to re-run later.
    op.execute("SET lock_timeout = '5s'")

    if context.is_offline_mode():
        # No live connection to count orphans against — render the
        # unconditional VALIDATE statements for review purposes (this is
        # what makes `alembic upgrade --sql` renderable end to end). The
        # ONLINE path below is what actually gates this on live data; the
        # rendered SQL here is not a claim about what will happen against
        # real data, only what the statement shape is.
        for constraint_name, table, _orphan_sql in _GATED_FKS:
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint_name}")
        return

    for constraint_name, table, orphan_sql in _GATED_FKS:
        orphan_count = bind.execute(sa.text(orphan_sql)).scalar_one()
        if orphan_count == 0:
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint_name}")
            print(f"[9c2e7b5a1f4d] {constraint_name}: 0 orphans -- validated")
        else:
            print(
                f"[9c2e7b5a1f4d] {constraint_name}: {orphan_count} orphan row(s) -- "
                "left NOT VALID (enforces future writes only; historical rows untouched; "
                "re-run this migration after cleanup to validate)"
            )


def downgrade() -> None:
    # No-op by design: Postgres has no "un-validate" operation short of
    # dropping and re-adding the constraint, and dropping it entirely is
    # fb8d0bae8112's downgrade's job — downgrading past THIS revision must
    # run before that drop happens (Alembic walks the chain in reverse
    # order automatically), so there is nothing for this step to undo.
    pass

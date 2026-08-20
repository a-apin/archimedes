"""schema relations Phase 1 — identity/ownership indices + gated FKs

Additive-only retrofit from the schema-relations audit. Three classes of
change, all reversible, none touching a writer:

1. **Ten indices** on columns that sit on hot or money-adjacent read paths
   and are seq-scanning today — either because Postgres never auto-indexes a
   foreign-key column, or because an existing composite index leads with the
   wrong column for the query shape that actually runs. Plain ``CREATE
   INDEX``, deliberately not ``CONCURRENTLY``: at this repo's measured scale
   (646 rows over 51 strategies is the only in-repo production row count on
   record — see ``f0ab58339d55``'s docstring) a plain index build is
   milliseconds, and ``CONCURRENTLY`` cannot run inside Alembic's
   transaction — it forfeits atomic rollback and can leave an ``INVALID``
   index behind on failure. A brief ``SHARE`` lock is the better trade here.

2. **A column widen** — ``paper_deployments.strategy_id`` VARCHAR(64) ->
   VARCHAR(128), matching ``strategy_store.id``'s width ahead of the FK
   below. Legal in Postgres (both are ``character varying``, same operator
   family — a widen is a metadata-only change, no rewrite). Actual values
   are ``content_hash[:16]`` (16 chars), so this is headroom, not a
   live-data change.

3. **Three foreign keys**, each added ``NOT VALID`` and gated on a runtime
   orphan check taken against the SAME connection this migration is running
   against, immediately after the constraint is created:

   - ``linked_wallets.address -> wallet_identities.wallet_address`` — the
     sole bridge between Better Auth and the SIWE identity ledger.
     ``_link_verified_wallet`` (``wallet_routes.py``) already writes the
     ``WalletIdentity`` row and flushes it BEFORE inserting the
     ``LinkedWallet`` row, so this converts an existing app-code invariant
     into a schema guarantee.
   - ``paper_deployments.owner_user_id -> auth_users.id`` (``ON DELETE SET
     NULL``, matching the five identical FKs ``b7e3f1a2c9d4`` already added
     to sibling ownership columns) — values come straight from an
     authenticated session (``paper_routes.py``, ``owner_user_id=user.id``).
   - ``paper_deployments.strategy_id -> strategy_store.id`` and
     ``paper_deployments.owner_wallet -> wallet_identities.wallet_address``
     — closes the gap this table's own model docstring already named
     ("Same pattern as strategy_store" — strategy_store carries these FKs,
     paper_deployments didn't).

   WHY a runtime gate instead of a purely manual pre-flight checklist: this
   repo has exactly one measured production row count on record (see above)
   — every other table's count, including these four, is unverified. A
   migration that blindly issues ``VALIDATE CONSTRAINT`` on unmeasured data
   is how a "minimal blast radius" change becomes an outage (a failed
   VALIDATE takes an ``ACCESS EXCLUSIVE`` lock for its duration). Running
   the same orphan-count query the audit specified for a human to run by
   hand, but running it FROM INSIDE the migration against the live
   connection, makes the safety property hold unconditionally — on a
   pristine dev SQLite db, on a freshly seeded test fixture, and on
   whatever prod's row counts turn out to be on the day this actually
   deploys — rather than depending on someone remembering to run the
   checklist first. Every constraint is created ``NOT VALID`` in every case;
   the gate only decides whether the immediate follow-up
   ``VALIDATE CONSTRAINT`` also runs in the SAME migration. An orphan count
   > 0 is not a failure — it is the documented, expected outcome for
   ``paper_deployments.strategy_id`` / ``.owner_wallet`` (no historical
   backfill has ever run against those columns) and simply leaves the
   constraint enforcing all FUTURE writes while historical rows stay
   untouched, exactly as the audit's own §1.3 table specifies.

   ``VALIDATE CONSTRAINT`` and ``NOT VALID`` are Postgres-only concepts (no
   SQLite equivalent — SQLite doesn't validate FK data on ``ALTER`` at all
   unless ``PRAGMA foreign_keys=ON``, which nothing in this repo's default
   connection sets). On SQLite this migration still adds the SAME named
   constraints (via ``batch_alter_table``'s table-rebuild path, this repo's
   established two-path pattern per ``migrations/env.py``), just without an
   orphan gate — a real orphan on SQLite becomes a live app-level FK
   violation the next time that row is touched, which is the correct
   SQLite-native failure mode for a fresh/local database, not a prod-outage
   risk on an unmeasured table.

Explicit skip, stated once and not repeated per-row below: this migration
does NOT add a FK from ``backtest_results.strategy_id`` to
``strategy_store.id``. Both id spaces are the same value in practice
(``main.py``'s startup seed + ``strategy_provider.py``'s unified-table sync),
but BOTH of those write paths are explicitly best-effort / non-fatal on
failure — the invariant is plausible, not provable, and adding an FK on
unverified data here is exactly the mistake this migration's gated pattern
exists to avoid making blind.

Revision ID: fb8d0bae8112
Revises: f0ab58339d55
Create Date: 2026-08-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fb8d0bae8112"
down_revision: str | Sequence[str] | None = "f0ab58339d55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

# (index_name, table, columns) — every index this revision adds. Applied
# with plain CREATE INDEX (see module docstring § 1) and dropped in the same
# order, reversed, on downgrade.
_INDICES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_linked_wallets_address", "linked_wallets", ("address",)),
    ("ix_paper_deployments_owner_user_id", "paper_deployments", ("owner_user_id",)),
    ("ix_identity_events_wallet_time", "identity_events", ("wallet", "occurred_at")),
    ("ix_subscriber_liabilities_sub", "subscriber_liabilities", ("sub_id",)),
    ("ix_subscriber_liabilities_strategy_created", "subscriber_liabilities", ("strategy_id", "created_at")),
    ("ix_settlement_intents_sub", "settlement_intents", ("sub_id",)),
    ("ix_settlement_intents_status_created", "settlement_intents", ("status", "created_at")),
    ("ix_marketplace_agents_subscriber_wallet", "marketplace_agents", ("subscriber_wallet",)),
    ("ix_marketplace_agents_creator_wallet", "marketplace_agents", ("creator_wallet",)),
    ("ix_generation_costs_recorded_at", "generation_costs", ("recorded_at",)),
)

# (constraint_name, table, local_cols, referent_table, remote_cols, ondelete,
#  orphan_sql) — every gated FK this revision adds. `orphan_sql` is the exact
# live-connection form of the audit's own pre-flight query for that FK (see
# module docstring § 3); it must return a single integer count.
_GatedFK = tuple[str, str, tuple[str, ...], str, tuple[str, ...], str | None, str]

_GATED_FKS: tuple[_GatedFK, ...] = (
    (
        "fk_linked_wallets_address_wallet_identity",
        "linked_wallets",
        ("address",),
        "wallet_identities",
        ("wallet_address",),
        None,
        """
        SELECT COUNT(*) FROM linked_wallets lw
        LEFT JOIN wallet_identities wi ON lw.address = wi.wallet_address
        WHERE wi.wallet_address IS NULL
        """,
    ),
    (
        "fk_paper_deployments_owner_user_id",
        "paper_deployments",
        ("owner_user_id",),
        "auth_users",
        ("id",),
        "SET NULL",
        """
        SELECT COUNT(*) FROM paper_deployments pd
        LEFT JOIN auth_users au ON pd.owner_user_id = au.id
        WHERE pd.owner_user_id IS NOT NULL AND au.id IS NULL
        """,
    ),
    (
        "fk_paper_deployments_strategy_id",
        "paper_deployments",
        ("strategy_id",),
        "strategy_store",
        ("id",),
        None,
        """
        SELECT COUNT(*) FROM paper_deployments pd
        LEFT JOIN strategy_store ss ON pd.strategy_id = ss.id
        WHERE ss.id IS NULL
        """,
    ),
    (
        "fk_paper_deployments_owner_wallet",
        "paper_deployments",
        ("owner_wallet",),
        "wallet_identities",
        ("wallet_address",),
        None,
        """
        SELECT COUNT(*) FROM paper_deployments pd
        LEFT JOIN wallet_identities wi ON pd.owner_wallet = wi.wallet_address
        WHERE pd.owner_wallet IS NOT NULL AND wi.wallet_address IS NULL
        """,
    ),
)


def _add_gated_fk(
    bind,
    *,
    constraint_name: str,
    table: str,
    local_cols: tuple[str, ...],
    referent_table: str,
    remote_cols: tuple[str, ...],
    ondelete: str | None,
    orphan_sql: str,
) -> None:
    """Create ``constraint_name`` NOT VALID (Postgres) / fully (SQLite), then
    VALIDATE it only if a live orphan count on THIS connection is zero.

    See the module docstring § 3 for why the gate runs here instead of (or
    rather, as the executable form of) a manual pre-migration checklist.
    """
    is_postgres = bind.dialect.name == "postgresql"
    fk_kwargs: dict[str, object] = {}
    if ondelete:
        fk_kwargs["ondelete"] = ondelete
    if is_postgres:
        fk_kwargs["postgresql_not_valid"] = True

    with op.batch_alter_table(table) as batch_op:
        batch_op.create_foreign_key(
            constraint_name,
            referent_table,
            list(local_cols),
            list(remote_cols),
            **fk_kwargs,
        )

    if not is_postgres:
        # SQLite has no NOT VALID / VALIDATE CONSTRAINT concept — the
        # batch-mode table rebuild above already applied the constraint in
        # full (see module docstring § 3's SQLite paragraph).
        return

    orphan_count = bind.execute(sa.text(orphan_sql)).scalar_one()
    if orphan_count == 0:
        op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint_name}")
        print(f"[fb8d0bae8112] {constraint_name}: 0 orphans -- validated")
    else:
        print(
            f"[fb8d0bae8112] {constraint_name}: {orphan_count} orphan row(s) -- "
            "left NOT VALID (enforces future writes only; historical rows untouched)"
        )


def upgrade() -> None:
    bind = op.get_bind()

    for name, table, columns in _INDICES:
        op.create_index(name, table, list(columns))

    # Width step ahead of fk_paper_deployments_strategy_id (see module
    # docstring § 2). Must happen before the FK below: Postgres allows a FK
    # across differing VARCHAR lengths, but keeping matching column widths
    # in place before pointing a FK at strategy_store.id is the honest
    # order — the FK's referent table dictates the ceiling this column
    # should actually carry.
    with op.batch_alter_table("paper_deployments") as batch_op:
        batch_op.alter_column(
            "strategy_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=False,
        )

    for constraint_name, table, local_cols, referent_table, remote_cols, ondelete, orphan_sql in _GATED_FKS:
        _add_gated_fk(
            bind,
            constraint_name=constraint_name,
            table=table,
            local_cols=local_cols,
            referent_table=referent_table,
            remote_cols=remote_cols,
            ondelete=ondelete,
            orphan_sql=orphan_sql,
        )


def downgrade() -> None:
    for constraint_name, table, *_rest in reversed(_GATED_FKS):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(constraint_name, type_="foreignkey")

    with op.batch_alter_table("paper_deployments") as batch_op:
        batch_op.alter_column(
            "strategy_id",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    for name, table, _columns in reversed(_INDICES):
        op.drop_index(name, table_name=table)

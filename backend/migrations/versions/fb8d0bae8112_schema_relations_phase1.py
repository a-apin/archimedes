"""schema relations Phase 1 — identity/ownership indices + gated FKs

Additive-only retrofit from the schema-relations audit. Three classes of
change, all reversible (see the downgrade-safety note in § 2 below for the
one honest exception), none touching a writer:

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
   live-data change. Downgrade narrows it back — see
   ``fb8d0bae8112_validate``'s sibling revision docstring for why the
   *narrow* direction is NOT unconditionally safe the way the widen is, and
   what this migration's ``downgrade()`` does about it.

3. **Four foreign keys**, each added ``NOT VALID`` here and ONLY here — this
   migration never calls ``VALIDATE CONSTRAINT``. Validation is a SEPARATE
   follow-up revision (``fb8d0bae8112_validate`` — see its own docstring):

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

   WHY ``NOT VALID`` is load-bearing here, not cosmetic: a 2026-07-05
   data-architecture investigation (team record, not a doc checked into this
   repo) found prod Aurora already contains unjoinable / orphaned
   populations across exactly this kind of identity/ownership column. A plain
   ``ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY`` (no ``NOT VALID``)
   scans and validates every existing row as part of adding the constraint
   and ABORTS THE WHOLE STATEMENT the instant it finds one violation — on a
   table with a known or even just unmeasured orphan population, that is a
   deploy-time hard failure, not a degraded-but-working state. ``NOT VALID``
   decouples "the constraint exists and enforces every future write" from
   "every historical row has been checked" — the first is unconditionally
   safe to ship additively; the second is exactly what
   ``fb8d0bae8112_validate`` attempts, separately, with its own live orphan
   gate, so that a known orphan population can only ever be *named*, never
   turned into a blocked deploy.

   This migration and its own row count are otherwise in the same boat as
   the audit's C1 finding: this repo has exactly one measured production row
   count on record (``f0ab58339d55``'s docstring) — every other table's
   count, including these four FK columns, is unverified. ``NOT VALID``
   makes that lack of measurement a non-issue for the additive step; it is
   the validation step (deferred, see above) that actually needs to reckon
   with whatever prod's row counts turn out to be on the day this deploys.

   ``NOT VALID`` is a Postgres-only concept (no SQLite equivalent — SQLite
   doesn't validate FK data on ``ALTER`` at all unless ``PRAGMA
   foreign_keys=ON``, which nothing in this repo's default connection
   sets). On SQLite this migration still adds the SAME named constraints in
   full (via ``batch_alter_table``'s table-rebuild path, this repo's
   established two-path pattern per ``migrations/env.py``) — a real orphan
   on SQLite becomes a live app-level FK violation the next time that row is
   touched, which is the correct SQLite-native failure mode for a
   fresh/local database, not a prod-outage risk on an unmeasured table.

4. **A bounded ``lock_timeout``** (Postgres only, set once at the top of
   ``upgrade()``): every statement below that touches an existing table
   (``CREATE INDEX``, ``ALTER COLUMN TYPE``, ``ADD CONSTRAINT ... NOT
   VALID``) takes a brief ``ACCESS EXCLUSIVE`` lock to update the catalog.
   Without a timeout, a blocked acquire queues silently behind whatever
   already holds a conflicting lock (a long-running query, an open
   transaction) — and because Postgres grants locks in request order, every
   subsequent query against that table queues behind THIS migration too,
   turning a should-be-instant metadata change into a visible outage. A
   failed acquire instead raises immediately. The whole migration runs in
   one transaction (``migrations/env.py``'s ``context.begin_transaction()``
   wraps the full ``alembic upgrade`` invocation), so that failure aborts
   the transaction cleanly — no partial schema change is left behind, and
   re-running ``alembic upgrade head`` afterwards is always safe.

Explicit skip, stated once and not repeated per-row below: this migration
does NOT add a FK from ``backtest_results.strategy_id`` to
``strategy_store.id``. Both id spaces are the same value in practice
(``main.py``'s startup seed + ``strategy_provider.py``'s unified-table sync),
but BOTH of those write paths are explicitly best-effort / non-fatal on
failure — the invariant is plausible, not provable, and adding an FK on
unverified data here is exactly the mistake this migration's ``NOT VALID``
pattern exists to avoid making blind.

Revision ID: fb8d0bae8112
Revises: 5cb798feef58
Create Date: 2026-08-20 00:00:00.000000

SEQUENCING: originally authored against ``f0ab58339d55``, the chain head when
this branch forked. Four revisions have landed on ``main`` since that fork —
``1752121b8d7c`` (payment_receipts), ``a3f19c7d2e84`` (generation_credits),
``5728d9ef1901`` (debate_transcripts) and ``5cb798feef58``
(strategy_store.brief_intent) — so ``down_revision`` is re-pointed at
``5cb798feef58``, ``main``'s current head, to collapse the fork this created
back to a single head. That is the exact failure mode
``.github/scripts/migration_chain_guard.py`` exists to catch; re-run it (or
``alembic heads``, which must print exactly one head) after any further merge
of ``main`` into this branch.

None of those four touches anything this revision reads or writes: three add
new tables only (payment_receipts, generation_credits, debate_transcripts)
and the fourth adds one nullable column plus a backfill on
``strategy_store.brief_intent`` — not a column, index or constraint named in
``_INDICES`` or ``_GATED_FKS`` below. Re-pointing is therefore a pure
serialization change, not a semantic one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "fb8d0bae8112"
down_revision: str | Sequence[str] | None = "5cb798feef58"
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
# orphan_sql) — every gated FK this revision adds NOT VALID. `orphan_sql` is
# NOT used by this migration (it never calls VALIDATE CONSTRAINT) — it is
# carried here as the single source of truth that the sibling
# ``fb8d0bae8112_validate`` revision's own copy is tested against (see
# ``test_phase1_validate_orphan_sql_matches_source_revision`` in
# ``test_alembic_migrations.py``), so the two files cannot silently drift
# apart. Migrations intentionally do not import each other's modules at
# runtime (a later rename/removal of one must not break replaying the
# other), so the duplication is deliberate, not an oversight.
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


def _add_gated_fk_not_valid(
    bind,
    *,
    constraint_name: str,
    table: str,
    local_cols: tuple[str, ...],
    referent_table: str,
    remote_cols: tuple[str, ...],
    ondelete: str | None,
) -> None:
    """Create ``constraint_name`` NOT VALID (Postgres) / fully (SQLite).

    Deliberately does NOT validate — see module docstring § 3. Only needs
    ``bind.dialect.name`` (works identically online and in ``--sql`` offline
    rendering; no live query is issued), so this function never needs an
    ``is_offline_mode()`` guard.
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


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        # Bounded lock acquisition — see module docstring § 4. No
        # is_offline_mode() guard needed: `op.execute` of a literal string
        # renders as-is in `--sql` mode, and `bind.dialect.name` above is
        # resolved from the configured URL even without a live connection.
        op.execute("SET lock_timeout = '5s'")

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

    for constraint_name, table, local_cols, referent_table, remote_cols, ondelete, _orphan_sql in _GATED_FKS:
        _add_gated_fk_not_valid(
            bind,
            constraint_name=constraint_name,
            table=table,
            local_cols=local_cols,
            referent_table=referent_table,
            remote_cols=remote_cols,
            ondelete=ondelete,
        )


def _fail_if_narrow_would_truncate(bind, is_postgres: bool, is_offline: bool | None = None) -> None:
    """Downgrade-safety check for the strategy_id width narrow (VARCHAR(128)
    -> VARCHAR(64)) — see the module docstring's § 2 pointer and
    ``fb8d0bae8112_validate``'s docstring for the full "this is the one
    honestly-irreversible piece" discussion.

    Skipped entirely offline (no live rows to check — `alembic downgrade
    --sql` renders the ALTER unconditionally for review) and on SQLite
    (SQLite never enforces VARCHAR(N) length at all, so a narrow can never
    truncate-fail there — the check would just be dead code). `is_offline`
    takes an explicit default of ``context.is_offline_mode()`` rather than
    calling it inline, so this function stays callable from a plain unit
    test with no live Alembic ``MigrationContext`` established.
    """
    if is_offline is None:
        is_offline = context.is_offline_mode()
    if is_offline or not is_postgres:
        return
    count = bind.execute(sa.text("SELECT COUNT(*) FROM paper_deployments WHERE length(strategy_id) > 64")).scalar_one()
    if count:
        raise RuntimeError(
            f"downgrade of fb8d0bae8112 aborted: {count} paper_deployments row(s) have "
            "strategy_id longer than 64 characters. Narrowing the column back to "
            "VARCHAR(64) would truncate-fail on those rows. This downgrade is NOT safe "
            "to run as-is — clean up or re-home the offending rows first, or accept that "
            "this widen is a one-way door in practice. Left untouched; no schema change "
            "was made."
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    for constraint_name, table, *_rest in reversed(_GATED_FKS):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(constraint_name, type_="foreignkey")

    _fail_if_narrow_would_truncate(bind, is_postgres)

    with op.batch_alter_table("paper_deployments") as batch_op:
        batch_op.alter_column(
            "strategy_id",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    for name, table, _columns in reversed(_INDICES):
        op.drop_index(name, table_name=table)

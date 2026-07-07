"""wallet-anchored identity ledger

Issue #1028 (chunk 2/5, identity-schema). Adds the three tables the epic's
Target schema locks in (D1/D1a/D2/D3):

  - ``wallet_identities`` — the identity anchor (SIWE-capable wallets only).
  - ``controlled_wallets`` — system-operated addresses (agent wallets, Circle
    DCWs, treasury). Created empty here; population (agent_runner, the
    marketplace engine, Circle DCWs) is Phase B work.
  - ``identity_events`` — the durable ledger. Created empty here; event
    emission at write points is Phase B work.

Then retrofits the FKs the issue's "FK retrofits" section calls for onto the
already-populated tables, in this order (each step depends on the one
before it):

  1. Relax ``marketplace_agents.creator_wallet`` / ``subscriber_wallet`` to
     nullable — they were ``NOT NULL DEFAULT ''``, using empty string as a
     not-applicable sentinel (only one of the two is meaningful per row's
     role: publisher rows never set subscriber_wallet and vice versa). Must
     happen before step 3 can null them out.
  2. Normalize casing (``lower()``) on every existing wallet / vault_address
     column. Required before step 5 adds CHECK/FK constraints — Postgres
     validates ALL existing rows when a constraint is added, so drifted
     casing would abort the migration.
  3. Backfill sentinels: marketplace_agents ``''`` → NULL (not-applicable,
     not ownerless); strategy_store / strategy_passports NULL owner_wallet →
     the D7 ``'system'`` identity; vault_metadata NULL/``''`` creator_address
     → ``'system'``.
  4. Backfill ``wallet_identities`` from every real wallet already sitting in
     the DB (D7's "backfill the 6 real wallets"), split by role so the chat
     AI persona lands as ``actor_class='agent'`` rather than ``'human'``.
  5. Width-normalize ``strategy_store.id`` / ``strategy_generators.strategy_id``
     from VARCHAR(64) to VARCHAR(128), matching the marketplace/billing
     tables (prep for a future cross-table FK — not added here).
  6. Add the FK / CHECK constraints themselves.

Casing fixes (write paths fixed in application code alongside this
migration): ``vault_metadata.vault_address`` (EIP-55 checksum bug —
executor.py never lowercased it, unlike chat_messages.vault_address) and
``marketplace_agents.gateway_seller_address`` (#958's un-lowercased Circle
API response). Both get a CHECK constraint here to make the invariant
durable.

Revision ID: 07e9c1489199
Revises: af9c6a9376e4
Create Date: 2026-07-06 23:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "07e9c1489199"
down_revision: str | Sequence[str] | None = "af9c6a9376e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# D7 sentinel identity — never a real wallet, always lowercase by construction.
SYSTEM_WALLET = "system"

# Portable JSON: JSONB on Postgres (matches the Target schema exactly), plain
# JSON on SQLite (no native JSONB there).
_JSON_VARIANT = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

# Portable autoincrement PK: BigInteger on Postgres (BIGSERIAL — matches the
# Target schema exactly), plain Integer on SQLite. SQLite only aliases a
# primary-key column to its ROWID (autoincrement-without-an-explicit-value)
# when the column's type affinity is exactly INTEGER; BIGINT does not get
# that aliasing, so a bare `alembic upgrade head` against a fresh SQLite DB
# would otherwise raise "NOT NULL constraint failed" on the first insert.
_ID_VARIANT = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _backfill_identities(table: str, column: str, actor_class: str = "human", where_extra: str = "") -> None:
    """INSERT DISTINCT non-empty ``table.column`` values into wallet_identities.

    Portable ``NOT EXISTS`` dedup guard (works identically on SQLite and
    Postgres, unlike ``ON CONFLICT`` / ``INSERT OR IGNORE`` which differ by
    dialect) — safe to call multiple times across different source tables
    without violating the primary key on overlapping wallets.
    """
    op.execute(
        f"INSERT INTO wallet_identities (wallet_address, actor_class, first_seen_at) "
        f"SELECT DISTINCT {column}, '{actor_class}', CURRENT_TIMESTAMP FROM {table} "
        f"WHERE {column} IS NOT NULL AND {column} != '' {where_extra} "
        f"AND NOT EXISTS (SELECT 1 FROM wallet_identities wi WHERE wi.wallet_address = {table}.{column})"
    )


def upgrade() -> None:
    """Upgrade schema."""
    # ── 1. New tables (D1/D1a/D2) ────────────────────────────────────────
    op.create_table(
        "wallet_identities",
        sa.Column("wallet_address", sa.String(length=42), nullable=False),
        sa.Column("actor_class", sa.String(length=20), nullable=False, server_default="human"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_auth_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("wallet_address"),
        sa.CheckConstraint("wallet_address = lower(wallet_address)", name="ck_wallet_identities_lower"),
        sa.CheckConstraint(
            "actor_class IN ('human','agent','operator_dogfood','system')",
            name="ck_wallet_identities_actor_class",
        ),
    )

    op.create_table(
        "controlled_wallets",
        sa.Column("address", sa.String(length=42), nullable=False),
        sa.Column("controller_wallet", sa.String(length=42), nullable=True),
        sa.Column("wallet_class", sa.String(length=32), nullable=False),
        sa.Column("key_binding", sa.String(length=128), nullable=True),
        sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("address"),
        sa.ForeignKeyConstraint(
            ["controller_wallet"], ["wallet_identities.wallet_address"], name="fk_controlled_wallets_controller"
        ),
        sa.CheckConstraint("address = lower(address)", name="ck_controlled_wallets_lower"),
        sa.CheckConstraint(
            "wallet_class IN ('trading_agent','subscriber_payment','publisher_settlement','treasury')",
            name="ck_controlled_wallets_class",
        ),
    )

    op.create_table(
        "identity_events",
        sa.Column("id", _ID_VARIANT, autoincrement=True, nullable=False),
        sa.Column("wallet", sa.String(length=42), nullable=True),
        sa.Column("vid", sa.String(length=32), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_class", sa.String(length=20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta", _JSON_VARIANT, nullable=False, server_default=sa.text("'{}'")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["wallet"], ["wallet_identities.wallet_address"], name="fk_identity_events_wallet"),
        sa.CheckConstraint(
            "actor_class IN ('human','agent','operator_dogfood','system')",
            name="ck_identity_events_actor_class",
        ),
    )
    op.create_index("ix_identity_events_wallet_type", "identity_events", ["wallet", "event_type"])
    op.create_index("ix_identity_events_type_time", "identity_events", ["event_type", "occurred_at"])

    # ── 2. D7 system identity — must exist before any 'system' backfill ──
    op.execute(
        f"INSERT INTO wallet_identities (wallet_address, actor_class, first_seen_at) "
        f"VALUES ('{SYSTEM_WALLET}', 'system', CURRENT_TIMESTAMP)"
    )

    # ── 3. Relax marketplace_agents sentinel columns to nullable ─────────
    # (Must precede step 5's ''→NULL backfill: you can't UPDATE a NOT NULL
    # column to NULL.)
    with op.batch_alter_table("marketplace_agents", schema=None) as batch_op:
        batch_op.alter_column(
            "creator_wallet", existing_type=sa.String(length=42), nullable=True, existing_server_default=""
        )
        batch_op.alter_column(
            "subscriber_wallet", existing_type=sa.String(length=42), nullable=True, existing_server_default=""
        )

    # ── 4. Normalize casing BEFORE adding CHECK/FK constraints ───────────
    # (Postgres validates all existing rows when a constraint is added.)
    op.execute("UPDATE user_profiles SET wallet_address = lower(wallet_address)")
    op.execute("UPDATE strategy_store SET owner_wallet = lower(owner_wallet) WHERE owner_wallet IS NOT NULL")
    op.execute("UPDATE strategy_generators SET wallet_address = lower(wallet_address)")
    op.execute("UPDATE strategy_passports SET owner_wallet = lower(owner_wallet) WHERE owner_wallet IS NOT NULL")
    op.execute("UPDATE strategy_passports SET curator_wallet = lower(curator_wallet) WHERE curator_wallet IS NOT NULL")
    op.execute("UPDATE vault_metadata SET creator_address = lower(creator_address)")
    op.execute("UPDATE vault_metadata SET vault_address = lower(vault_address)")
    op.execute("UPDATE chat_messages SET wallet_address = lower(wallet_address)")
    op.execute(
        "UPDATE marketplace_agents SET creator_wallet = lower(creator_wallet) "
        "WHERE creator_wallet IS NOT NULL AND creator_wallet != ''"
    )
    op.execute(
        "UPDATE marketplace_agents SET subscriber_wallet = lower(subscriber_wallet) "
        "WHERE subscriber_wallet IS NOT NULL AND subscriber_wallet != ''"
    )
    op.execute(
        "UPDATE marketplace_agents SET gateway_seller_address = lower(gateway_seller_address) "
        "WHERE gateway_seller_address IS NOT NULL"
    )

    # ── 5. Sentinel backfills ─────────────────────────────────────────────
    # D7: NULL-owner rows (curated passports, pre-SIWE strategies) → 'system'.
    op.execute(f"UPDATE strategy_store SET owner_wallet = '{SYSTEM_WALLET}' WHERE owner_wallet IS NULL")
    op.execute(f"UPDATE strategy_passports SET owner_wallet = '{SYSTEM_WALLET}' WHERE owner_wallet IS NULL")
    op.execute(
        f"UPDATE vault_metadata SET creator_address = '{SYSTEM_WALLET}' "
        f"WHERE creator_address IS NULL OR creator_address = ''"
    )
    # marketplace_agents: '' was a not-applicable sentinel (only one of
    # creator_wallet/subscriber_wallet is meaningful per row's role), not an
    # ownership gap — NULL, not 'system' (D7 is about ownerless rows, not
    # "the other role's column").
    op.execute("UPDATE marketplace_agents SET creator_wallet = NULL WHERE creator_wallet = ''")
    op.execute("UPDATE marketplace_agents SET subscriber_wallet = NULL WHERE subscriber_wallet = ''")

    # ── 6. Backfill wallet_identities from every real wallet already here ─
    _backfill_identities("user_profiles", "wallet_address")
    _backfill_identities("strategy_store", "owner_wallet")
    _backfill_identities("strategy_passports", "owner_wallet")
    _backfill_identities("strategy_passports", "curator_wallet")
    _backfill_identities("vault_metadata", "creator_address")
    _backfill_identities("marketplace_agents", "creator_wallet")
    _backfill_identities("marketplace_agents", "subscriber_wallet")
    _backfill_identities("strategy_generators", "wallet_address")
    # chat_messages splits by is_ai so the AI persona wallet lands as
    # actor_class='agent' rather than being misclassified as 'human'.
    _backfill_identities("chat_messages", "wallet_address", actor_class="human", where_extra="AND is_ai = false")
    _backfill_identities("chat_messages", "wallet_address", actor_class="agent", where_extra="AND is_ai = true")

    # ── 7. Width normalization ────────────────────────────────────────────
    # strategy_id is VARCHAR(128) in the marketplace/billing tables already;
    # widen strategy_store.id / strategy_generators.strategy_id to match
    # ahead of a future cross-table FK (not added in this chunk).
    with op.batch_alter_table("strategy_store", schema=None) as batch_op:
        batch_op.alter_column("id", existing_type=sa.String(length=64), type_=sa.String(length=128))
        batch_op.create_foreign_key(
            "fk_strategy_store_owner_wallet", "wallet_identities", ["owner_wallet"], ["wallet_address"]
        )
    with op.batch_alter_table("strategy_generators", schema=None) as batch_op:
        batch_op.alter_column("strategy_id", existing_type=sa.String(length=64), type_=sa.String(length=128))
        batch_op.create_foreign_key(
            "fk_strategy_generators_wallet_address", "wallet_identities", ["wallet_address"], ["wallet_address"]
        )

    # ── 8. Remaining FK / CHECK retrofits ─────────────────────────────────
    with op.batch_alter_table("user_profiles", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_user_profiles_wallet_address", "wallet_identities", ["wallet_address"], ["wallet_address"]
        )

    with op.batch_alter_table("strategy_passports", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_strategy_passports_owner_wallet", "wallet_identities", ["owner_wallet"], ["wallet_address"]
        )
        batch_op.create_foreign_key(
            "fk_strategy_passports_curator_wallet", "wallet_identities", ["curator_wallet"], ["wallet_address"]
        )

    with op.batch_alter_table("vault_metadata", schema=None) as batch_op:
        batch_op.create_check_constraint("ck_vault_metadata_lower", "vault_address = lower(vault_address)")
        batch_op.create_foreign_key(
            "fk_vault_metadata_creator_address", "wallet_identities", ["creator_address"], ["wallet_address"]
        )

    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_chat_messages_wallet_address", "wallet_identities", ["wallet_address"], ["wallet_address"]
        )

    with op.batch_alter_table("marketplace_agents", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_marketplace_agents_gateway_seller_lower",
            "gateway_seller_address IS NULL OR gateway_seller_address = lower(gateway_seller_address)",
        )
        batch_op.create_foreign_key(
            "fk_marketplace_agents_creator_wallet", "wallet_identities", ["creator_wallet"], ["wallet_address"]
        )
        batch_op.create_foreign_key(
            "fk_marketplace_agents_subscriber_wallet", "wallet_identities", ["subscriber_wallet"], ["wallet_address"]
        )


def downgrade() -> None:
    """Downgrade schema.

    Structural-only — reverses the added constraints/columns/tables. Does
    NOT attempt to restore pre-migration casing or the marketplace_agents
    ''-sentinel values (standard for a backfill migration: the backfilled
    data is not un-derivable from the post-migration state).
    """
    with op.batch_alter_table("marketplace_agents", schema=None) as batch_op:
        batch_op.drop_constraint("fk_marketplace_agents_subscriber_wallet", type_="foreignkey")
        batch_op.drop_constraint("fk_marketplace_agents_creator_wallet", type_="foreignkey")
        batch_op.drop_constraint("ck_marketplace_agents_gateway_seller_lower", type_="check")

    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.drop_constraint("fk_chat_messages_wallet_address", type_="foreignkey")

    with op.batch_alter_table("vault_metadata", schema=None) as batch_op:
        batch_op.drop_constraint("fk_vault_metadata_creator_address", type_="foreignkey")
        batch_op.drop_constraint("ck_vault_metadata_lower", type_="check")

    with op.batch_alter_table("strategy_passports", schema=None) as batch_op:
        batch_op.drop_constraint("fk_strategy_passports_curator_wallet", type_="foreignkey")
        batch_op.drop_constraint("fk_strategy_passports_owner_wallet", type_="foreignkey")

    with op.batch_alter_table("user_profiles", schema=None) as batch_op:
        batch_op.drop_constraint("fk_user_profiles_wallet_address", type_="foreignkey")

    with op.batch_alter_table("strategy_generators", schema=None) as batch_op:
        batch_op.drop_constraint("fk_strategy_generators_wallet_address", type_="foreignkey")
        batch_op.alter_column("strategy_id", existing_type=sa.String(length=128), type_=sa.String(length=64))

    with op.batch_alter_table("strategy_store", schema=None) as batch_op:
        batch_op.drop_constraint("fk_strategy_store_owner_wallet", type_="foreignkey")
        batch_op.alter_column("id", existing_type=sa.String(length=128), type_=sa.String(length=64))

    # Restore the ''-sentinel before re-tightening to NOT NULL (can't set a
    # column NOT NULL while it holds NULLs).
    op.execute("UPDATE marketplace_agents SET creator_wallet = '' WHERE creator_wallet IS NULL")
    op.execute("UPDATE marketplace_agents SET subscriber_wallet = '' WHERE subscriber_wallet IS NULL")

    with op.batch_alter_table("marketplace_agents", schema=None) as batch_op:
        batch_op.alter_column(
            "subscriber_wallet",
            existing_type=sa.String(length=42),
            nullable=False,
            server_default="",
            existing_server_default=None,
        )
        batch_op.alter_column(
            "creator_wallet",
            existing_type=sa.String(length=42),
            nullable=False,
            server_default="",
            existing_server_default=None,
        )

    op.drop_index("ix_identity_events_type_time", table_name="identity_events")
    op.drop_index("ix_identity_events_wallet_type", table_name="identity_events")
    op.drop_table("identity_events")
    op.drop_table("controlled_wallets")
    op.drop_table("wallet_identities")

"""Identity-ledger schema tests (issue #1028, "tests" chunk — 5/5).

Covers the two things the epic's Target schema locks in structurally:

  1. **FK retrofits** (AC4: "every wallet column in ``information_schema``
     either FKs to ``wallet_identities`` or is registered in
     ``controlled_wallets`` — zero unclassified wallet columns"). Verified
     via ``sqlalchemy.inspect`` against the live ``create_all()``-built
     engine (the fresh-DB path every test in this suite runs on) rather than
     a fresh alembic-only DB, so this pins the ORM models themselves — the
     thing ``archimedes/db.py``'s ``init_db()`` actually builds for local dev
     + every hermetic test in this repo. ``test_alembic_migrations.py``
     cross-checks the alembic-built path separately.
  2. **Lowercase ``CHECK`` constraints** (D1/D1a + the vault_metadata /
     marketplace_agents.gateway_seller_address casing fixes) are DB-level
     enforced, not just application-level convention. SQLite enforces CHECK
     constraints unconditionally (unlike FKs, which need ``PRAGMA
     foreign_keys=ON`` and are NOT turned on anywhere in this codebase —
     verified empirically; see the FK section below for why this suite tests
     FK *presence* via introspection rather than FK *enforcement*).

Hermetic: uses the same shared SQLite engine every other DB-backed test in
this suite uses (``archimedes.db.engine``); no network, no Postgres.
"""

from __future__ import annotations

import uuid

import pytest
from archimedes.db import engine, get_session, init_db
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError


@pytest.fixture(autouse=True, scope="module")
def _ensure_tables():
    """See test_identity_metrics.py's identical fixture for the rationale:
    this file must be runnable standalone under the hermetic per-module gate."""
    init_db()


def _unique_wallet() -> str:
    return "0x" + uuid.uuid4().hex.ljust(40, "0")[:40]


def _fk_targets(table: str) -> dict[str, str]:
    """``{constrained_column: referred_table}`` for every FK on ``table``."""
    inspector = inspect(engine)
    out: dict[str, str] = {}
    for fk in inspector.get_foreign_keys(table):
        referred = fk["referred_table"]
        for col in fk["constrained_columns"]:
            out[col] = referred
    return out


# ─── FK retrofits (AC4) ────────────────────────────────────────────────────
# One assertion per column named in the issue's "FK retrofits" section.

_EXPECTED_WALLET_FKS: list[tuple[str, str]] = [
    ("user_profiles", "wallet_address"),
    ("strategy_store", "owner_wallet"),
    ("strategy_passports", "owner_wallet"),
    ("strategy_passports", "curator_wallet"),
    ("vault_metadata", "creator_address"),
    ("chat_messages", "wallet_address"),
    ("marketplace_agents", "creator_wallet"),
    ("marketplace_agents", "subscriber_wallet"),
    ("strategy_generators", "wallet_address"),
    ("controlled_wallets", "controller_wallet"),
    ("identity_events", "wallet"),
]


@pytest.mark.parametrize("table,column", _EXPECTED_WALLET_FKS)
def test_identity_column_fks_to_wallet_identities(table, column):
    fks = _fk_targets(table)
    assert fks.get(column) == "wallet_identities", (
        f"{table}.{column} must FK to wallet_identities — got {fks.get(column)!r} (all FKs: {fks})"
    )


@pytest.mark.parametrize(
    "table,column",
    [
        ("marketplace_agents", "ephemeral_wallet"),
        ("marketplace_agents", "gateway_seller_address"),
    ],
)
def test_controlled_columns_have_no_anchor_fk(table, column):
    """D1a: system-operated addresses register in ``controlled_wallets`` (a
    separate, populated-at-runtime table) rather than FK'ing straight to the
    identity anchor — the anchor holds SIWE-capable wallets only."""
    fks = _fk_targets(table)
    assert column not in fks, f"{table}.{column} unexpectedly FKs to {fks.get(column)!r}"


def test_wallet_identities_own_pk_is_not_an_fk():
    """The anchor's own PK is the FK target for everything else, not itself an FK."""
    assert _fk_targets("wallet_identities") == {}


# ─── Lowercase / enum CHECK constraints — DB-level enforcement ────────────


def _assert_check_violation(sql: str, params: dict) -> None:
    """Run ``sql`` and assert it raises IntegrityError (a CHECK violation),
    then roll back so the failed statement doesn't poison later queries on
    the shared session/engine."""
    session = get_session()
    try:
        with pytest.raises(IntegrityError):
            session.execute(text(sql), params)
            session.commit()
    finally:
        session.rollback()
        session.close()


def test_wallet_identities_rejects_uppercase_address():
    _assert_check_violation(
        "INSERT INTO wallet_identities (wallet_address, actor_class, first_seen_at) "
        "VALUES (:addr, 'human', CURRENT_TIMESTAMP)",
        {"addr": "0x" + "AB" * 20},
    )


def test_wallet_identities_rejects_unknown_actor_class():
    _assert_check_violation(
        "INSERT INTO wallet_identities (wallet_address, actor_class, first_seen_at) "
        "VALUES (:addr, 'not_a_real_class', CURRENT_TIMESTAMP)",
        {"addr": _unique_wallet()},
    )


def test_wallet_identities_accepts_every_documented_actor_class():
    """The flip side of the enum CHECK: every value D3 documents must be
    accepted, not just rejected-by-default on typos."""
    for actor_class in ("human", "agent", "operator_dogfood", "system"):
        addr = _unique_wallet()
        session = get_session()
        try:
            session.execute(
                text(
                    "INSERT INTO wallet_identities (wallet_address, actor_class, first_seen_at) "
                    "VALUES (:addr, :actor_class, CURRENT_TIMESTAMP)"
                ),
                {"addr": addr, "actor_class": actor_class},
            )
            session.commit()
        finally:
            session.execute(text("DELETE FROM wallet_identities WHERE wallet_address = :addr"), {"addr": addr})
            session.commit()
            session.close()


def test_controlled_wallets_rejects_uppercase_address():
    _assert_check_violation(
        "INSERT INTO controlled_wallets (address, wallet_class, provisioned_at) "
        "VALUES (:addr, 'treasury', CURRENT_TIMESTAMP)",
        {"addr": "0x" + "CD" * 20},
    )


def test_controlled_wallets_rejects_unknown_wallet_class():
    _assert_check_violation(
        "INSERT INTO controlled_wallets (address, wallet_class, provisioned_at) "
        "VALUES (:addr, 'not_a_real_class', CURRENT_TIMESTAMP)",
        {"addr": _unique_wallet()},
    )


def test_identity_events_rejects_unknown_actor_class():
    _assert_check_violation(
        "INSERT INTO identity_events (wallet, event_type, actor_class, occurred_at, meta) "
        "VALUES (NULL, 'auth_verified', 'not_a_real_class', CURRENT_TIMESTAMP, '{}')",
        {},
    )


def test_vault_metadata_rejects_uppercase_vault_address():
    """Casing fix (issue #1028): the EIP-55-checksummed vault_address bug
    (executor.py) — the CHECK makes lower()-on-write durable at the DB layer."""
    _assert_check_violation(
        "INSERT INTO vault_metadata (vault_address, name, symbol, creator_address, strategy_ids, created_at) "
        "VALUES (:addr, 'v', 'V', 'system', '[]', CURRENT_TIMESTAMP)",
        {"addr": "0x" + "EF" * 20},
    )


def test_marketplace_agents_rejects_uppercase_gateway_seller_address():
    """Casing fix (issue #1028): #958's un-lowercased Circle API response."""
    _assert_check_violation(
        "INSERT INTO marketplace_agents "
        "(role, strategy_id, sub_id, pool_id, vault_address, ephemeral_wallet, status, halted, "
        "gateway_seller_address, created_at) "
        "VALUES ('publisher', :sid, '', '', '', '', 'running', 0, :addr, CURRENT_TIMESTAMP)",
        {"sid": f"schema-test-{uuid.uuid4().hex[:8]}", "addr": "0x" + "AB" * 20},
    )


def test_marketplace_agents_allows_null_gateway_seller_address():
    """The lowercase CHECK is ``IS NULL OR = lower(...)`` — NULL (subscriber
    rows, or a publisher pre-provisioning) must not be rejected."""
    sid = f"schema-test-null-{uuid.uuid4().hex[:8]}"
    session = get_session()
    try:
        session.execute(
            text(
                "INSERT INTO marketplace_agents "
                "(role, strategy_id, sub_id, pool_id, vault_address, ephemeral_wallet, status, halted, "
                "gateway_seller_address, created_at) "
                "VALUES ('publisher', :sid, '', '', '', '', 'running', 0, NULL, CURRENT_TIMESTAMP)"
            ),
            {"sid": sid},
        )
        session.commit()
    finally:
        session.execute(text("DELETE FROM marketplace_agents WHERE strategy_id = :sid"), {"sid": sid})
        session.commit()
        session.close()

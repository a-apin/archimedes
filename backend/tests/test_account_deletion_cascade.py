"""Account-deletion cascade-vs-anonymize policy (issue #1367, D3).

Verifies the per-table decision recorded in migration ``85ca5310b7a1`` is
what actually happens at the database level when the owning
``auth_users`` row is deleted — not merely that the FK exists (that is
covered elsewhere, e.g. ``test_canonical_user_ownership.py``'s
``test_application_owned_tables_reference_canonical_user``), but that
deleting the account really does detach-or-remove every one of the six
owner columns:

  * CASCADE  — ``user_profiles`` (the encrypted-email row), ``paper_deployments``
  * SET NULL — ``strategy_store``, ``strategy_passports``, ``strategy_proposals``,
    ``vault_metadata``

SQLite does not enforce FK actions (including ``ON DELETE CASCADE`` /
``SET NULL``) unless ``PRAGMA foreign_keys=ON`` is issued per-connection —
see ``test_identity_schema.py``'s module docstring, which is why every
other FK test in this suite checks presence via introspection rather than
enforcement. This file's entire point is to prove the ON DELETE *actions*
actually fire, so it turns the pragma on for its own throwaway in-memory
engine. Anti-goal (issue #1367): stay in-memory sqlite, no Postgres/Redis —
this satisfies that by enabling enforcement on sqlite itself rather than
widening to an integration test.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from archimedes.models.account import AuthUser
from archimedes.models.chat import Base, VaultMetadata
from archimedes.models.identity import WalletIdentity
from archimedes.models.paper_store import PaperDeployment
from archimedes.models.strategy_passport_record import StrategyPassportRecord
from archimedes.models.strategy_proposal import StrategyProposal
from archimedes.models.strategy_store import StrategyRecord, upsert_strategy
from archimedes.models.user_profile import UserProfile
from archimedes.services.email_crypto import encrypt_email
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

# The six columns the issue's evidence names — SET NULL first (content
# survives, ownership detaches), CASCADE last (the whole row goes).
_SET_NULL_TABLES = ("strategy_store", "strategy_passports", "strategy_proposals", "vault_metadata")
_CASCADE_TABLES = ("user_profiles", "paper_deployments")
_ALL_OWNED_TABLES = _SET_NULL_TABLES + _CASCADE_TABLES

_USER_ID = "user-cascade-del"
_WALLET = "0x" + "b" * 40
_VAULT_ADDR = "0x" + "c" * 40
_STRATEGY_ID = "strategy-cascade-1"
_PLAINTEXT_EMAIL = "private-profile-email@example.com"


def _fk_enforced_engine():
    """In-memory sqlite with ON DELETE actions actually enforced (see module docstring)."""
    engine = create_engine("sqlite://")  # single shared connection, per SQLAlchemy's sqlite:// default

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def engine():
    return _fk_enforced_engine()


def _seed_one_row_in_each_owned_table(session: Session) -> None:
    """Create one AuthUser plus one row it owns in each of the six tables."""
    now = datetime.now(UTC)
    session.add(
        AuthUser(
            id=_USER_ID,
            name="Deletable User",
            email=f"{_USER_ID}@example.com",
            email_verified=True,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(WalletIdentity(wallet_address=_WALLET, actor_class="human"))
    session.flush()  # wallet_identities row must exist before FK-dependent inserts below

    upsert_strategy(
        session,
        generation_method="fusion",
        strategy_name="Cascade test strategy",
        thesis="thesis",
        source_papers=[],
        asset_universe=["SPY"],
        owner_user_id=_USER_ID,
    )
    session.add(StrategyPassportRecord(id="passport-cascade-1", owner_user_id=_USER_ID))
    session.add(
        StrategyProposal(
            id="proposal-cascade-1",
            generation_id="gen-cascade-1",
            proposal_id="proposal-cascade-1",
            content_hash="0x" + "a" * 64,
            owner_user_id=_USER_ID,
        )
    )
    session.add(
        UserProfile(
            wallet_address=_WALLET,
            owner_user_id=_USER_ID,
            email=encrypt_email(_PLAINTEXT_EMAIL),
        )
    )
    session.add(VaultMetadata(vault_address=_VAULT_ADDR, creator_address=_WALLET, owner_user_id=_USER_ID))
    session.add(
        PaperDeployment(
            strategy_id=_STRATEGY_ID,
            owner_user_id=_USER_ID,
            spec_json="{}",
            deployed_at=date.today(),
        )
    )
    session.commit()


def _delete_the_account(session: Session) -> None:
    """Mirror the real trigger: a bare ``DELETE FROM auth_users`` — the migration's
    own rationale is that Postgres' FK actions must do this work regardless of
    which of the two services (Node Better Auth, or the Python backend) issues
    the delete, so the test issues the same bare statement rather than going
    through any application-level cleanup helper."""
    session.execute(text("DELETE FROM auth_users WHERE id = :id"), {"id": _USER_ID})
    session.commit()


def test_account_deletion_cascades_or_nulls_every_owned_table(engine):
    with Session(engine) as session:
        _seed_one_row_in_each_owned_table(session)
        assert session.get(AuthUser, _USER_ID) is not None, "seed did not create the account"

        _delete_the_account(session)

    # Fresh session/connection for assertions: no stale identity-map entry
    # from before the delete can mask a bug that only shows up on reload.
    with Session(engine) as session:
        # (a) Zero rows anywhere still carry the deleted user's id — true for
        # SET NULL tables by detachment, and trivially true for CASCADE
        # tables because the row itself is gone (checked explicitly below).
        for table in _ALL_OWNED_TABLES:
            # table is drawn from the fixed local _ALL_OWNED_TABLES tuple, never user input.
            remaining = session.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE owner_user_id = :uid"),
                {"uid": _USER_ID},
            ).scalar_one()
            assert remaining == 0, f"{table} still has a row with owner_user_id = {_USER_ID!r}"

        # SET NULL tables: the content survives — detached, not destroyed.
        assert (
            session.get(StrategyRecord, session.execute(text("SELECT id FROM strategy_store")).scalar_one()) is not None
        )
        assert session.get(StrategyPassportRecord, "passport-cascade-1") is not None
        assert session.get(StrategyProposal, "proposal-cascade-1") is not None
        vault_row = session.execute(
            text("SELECT owner_user_id FROM vault_metadata WHERE vault_address = :v"), {"v": _VAULT_ADDR}
        ).one_or_none()
        assert vault_row is not None, "vault_metadata row was deleted; expected SET NULL, not CASCADE"
        assert vault_row[0] is None

        # (b) CASCADE tables: the row itself is gone — including the
        # Fernet-encrypted email, not merely detached from the account.
        assert session.get(UserProfile, _WALLET) is None, (
            "user_profiles row survived account deletion; the encrypted email is still in the DB"
        )
        remaining_email_rows = session.execute(text("SELECT COUNT(*) FROM user_profiles")).scalar_one()
        assert remaining_email_rows == 0, "an encrypted email row remains in user_profiles after account deletion"

        remaining_deployments = session.execute(
            text("SELECT COUNT(*) FROM paper_deployments WHERE strategy_id = :sid"), {"sid": _STRATEGY_ID}
        ).scalar_one()
        assert remaining_deployments == 0, "paper_deployments row survived account deletion"

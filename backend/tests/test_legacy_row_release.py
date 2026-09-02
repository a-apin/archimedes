"""#1283 — releasing a platform-adopted row back to its real owner.

The adoption migration stamps ``owner_user_id`` onto rows whose wallet nobody
had ever linked. ``claim_legacy_wallet_data`` cannot reach a stamped row (it
filters on ``owner_user_id IS NULL``), so without a release path adoption
would be a one-way door: the real holder of that wallet could link it, prove
control with a fresh signature, and still never get their own rows back.

These tests pin the door open in both directions, and pin it CLOSED for
everyone else.
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from archimedes.models.account import PLATFORM_LEGACY_USER_ID, AuthUser
from archimedes.models.chat import Base
from archimedes.models.legacy_adoption import LegacyRowAdoption
from archimedes.models.strategy_passport_record import StrategyPassportRecord
from archimedes.models.strategy_proposal import StrategyProposal
from archimedes.models.strategy_store import StrategyRecord, upsert_strategy
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ADOPTED_WALLET = "0x3333333333333333333333333333333333333333"
OTHER_WALLET = "0x4444444444444444444444444444444444444444"
NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    assert LegacyRowAdoption.__table__.metadata is Base.metadata
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        [
            AuthUser(id=uid, name=uid, email=f"{uid}@example.com", email_verified=True, created_at=NOW, updated_at=NOW)
            for uid in ("user-1", "user-2", PLATFORM_LEGACY_USER_ID)
        ]
    )
    session.commit()
    return session


def _adopt(session: Session, wallet: str) -> str:
    """Create a strategy owned by *wallet*, then adopt it exactly as the
    migration does: stamp the platform id and record the prior wallet."""
    record = upsert_strategy(
        session,
        generation_method="fusion",
        strategy_name="Orphaned",
        thesis="pre-account row",
        source_papers=[],
        asset_universe=["SPY"],
        owner_wallet=wallet,
    )
    session.flush()
    strategy_id = record.id
    session.query(StrategyRecord).filter_by(id=strategy_id).update({StrategyRecord.owner_user_id: PLATFORM_LEGACY_USER_ID})
    session.add(
        LegacyRowAdoption(
            table_name="strategy_store",
            row_pk=strategy_id,
            prior_owner_wallet=wallet.lower(),
            adopted_by_user_id=PLATFORM_LEGACY_USER_ID,
            adopted_at=NOW,
        )
    )
    session.commit()
    return strategy_id


def test_linking_the_adopted_wallet_hands_the_row_back():
    from archimedes.api.wallet_routes import claim_legacy_wallet_data

    with _session() as session:
        strategy_id = _adopt(session, ADOPTED_WALLET)
        assert session.get(StrategyRecord, strategy_id).owner_user_id == PLATFORM_LEGACY_USER_ID

        claim_legacy_wallet_data(session, "user-1", ADOPTED_WALLET)
        session.commit()

        assert session.get(StrategyRecord, strategy_id).owner_user_id == "user-1"
        # The ledger entry is consumed — the row is no longer adopted, so a
        # downgrade must not re-orphan it out from under its new owner.
        assert session.query(LegacyRowAdoption).count() == 0


def test_a_different_wallet_gets_nothing():
    """The release is keyed on the wallet the row actually named. Proving
    control of some OTHER address must not move a platform-adopted row."""
    from archimedes.api.wallet_routes import claim_legacy_wallet_data

    with _session() as session:
        strategy_id = _adopt(session, ADOPTED_WALLET)

        claim_legacy_wallet_data(session, "user-2", OTHER_WALLET)
        session.commit()

        assert session.get(StrategyRecord, strategy_id).owner_user_id == PLATFORM_LEGACY_USER_ID
        assert session.query(LegacyRowAdoption).count() == 1


def test_a_narrowed_claim_cannot_widen_itself_through_the_release():
    """``rename_strategy`` passes the strategy-only trio. A release must obey
    the same narrowing — it may never reach a table the caller excluded."""
    from archimedes.api.wallet_routes import claim_legacy_wallet_data

    with _session() as session:
        strategy_id = _adopt(session, ADOPTED_WALLET)

        claim_legacy_wallet_data(
            session,
            "user-1",
            ADOPTED_WALLET,
            models=(
                (StrategyPassportRecord, StrategyPassportRecord.owner_wallet),
                (StrategyProposal, StrategyProposal.owner_wallet),
            ),
            include_profile=False,
        )
        session.commit()

        # strategy_store was not in the tuple, so its adopted row stays put.
        assert session.get(StrategyRecord, strategy_id).owner_user_id == PLATFORM_LEGACY_USER_ID
        assert session.query(LegacyRowAdoption).count() == 1


def test_release_is_a_no_op_when_nothing_was_ever_adopted():
    """Every database that has never run the adoption migration takes this
    path on every wallet link; it must cost nothing and change nothing."""
    from archimedes.api.wallet_routes import claim_legacy_wallet_data

    with _session() as session:
        upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="Unclaimed",
            thesis="pre-account row",
            source_papers=[],
            asset_universe=["SPY"],
            owner_wallet=ADOPTED_WALLET,
        )
        session.commit()

        claim_legacy_wallet_data(session, "user-1", ADOPTED_WALLET)
        session.commit()

        # The ordinary claim still worked...
        assert (
            session.query(StrategyRecord).filter(StrategyRecord.owner_user_id == "user-1").count() == 1
        )
        # ...and the release wrote no ledger rows.
        assert session.query(LegacyRowAdoption).count() == 0


def test_release_survives_a_database_that_has_no_ledger_table():
    """A database that has not reached the adoption revision has no
    ``legacy_row_adoptions``. The release runs on EVERY wallet link, so it
    must find that out cheaply and return — not raise, which would poison the
    claim's transaction and break wallet linking outright.
    """
    from archimedes.api.wallet_routes import claim_legacy_wallet_data

    with _session() as session:
        upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="Pre-migration",
            thesis="pre-account row",
            source_papers=[],
            asset_universe=["SPY"],
            owner_wallet=ADOPTED_WALLET,
        )
        session.commit()

        # Take the ledger away, exactly as a not-yet-migrated database has it.
        LegacyRowAdoption.__table__.drop(session.get_bind())
        assert not sa.inspect(session.connection()).has_table("legacy_row_adoptions")

        claim_legacy_wallet_data(session, "user-1", ADOPTED_WALLET)
        session.commit()

        # The ordinary claim still landed — the missing ledger changed nothing.
        assert session.query(StrategyRecord).filter(StrategyRecord.owner_user_id == "user-1").count() == 1

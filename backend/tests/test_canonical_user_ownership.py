from __future__ import annotations

from datetime import UTC, datetime

from archimedes.models.account import AuthUser
from archimedes.models.chat import Base
from archimedes.models.identity import WalletIdentity  # noqa: F401 — registers FK target
from archimedes.models.strategy_passport_record import StrategyPassportRecord  # noqa: F401
from archimedes.models.strategy_proposal import StrategyProposal  # noqa: F401
from archimedes.models.strategy_store import StrategyRecord, upsert_strategy
from archimedes.models.user_profile import UserProfile  # noqa: F401
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session


def _user(user_id: str, email: str) -> AuthUser:
    now = datetime.now(UTC)
    return AuthUser(
        id=user_id,
        name=user_id,
        email=email,
        email_verified=False,
        created_at=now,
        updated_at=now,
    )


def test_application_owned_tables_reference_canonical_user():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)

    for table in ("strategy_store", "strategy_passports", "strategy_proposals", "user_profiles", "vault_metadata"):
        assert "owner_user_id" in {column["name"] for column in inspector.get_columns(table)}


def test_real_user_metric_counts_canonical_accounts_without_wallets(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([_user("user-1", "one@example.com"), _user("user-2", "two@example.com")])
        session.commit()

    monkeypatch.setattr("archimedes.db.get_session", lambda: Session(engine))
    from archimedes.services import user_stats

    user_stats._reset_cache()
    try:
        assert user_stats.get_distinct_user_count() == 2
    finally:
        user_stats._reset_cache()


def test_strategy_dedup_never_transfers_canonical_owner():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    kwargs = {
        "generation_method": "fusion",
        "strategy_name": "Canonical owner",
        "thesis": "same content",
        "source_papers": [],
        "asset_universe": ["SPY"],
    }
    with Session(engine) as session:
        session.add_all([_user("user-1", "one@example.com"), _user("user-2", "two@example.com")])
        session.flush()
        first = upsert_strategy(session, **kwargs, owner_user_id="user-1")
        second = upsert_strategy(session, **kwargs, owner_user_id="user-2")
        session.commit()

        assert first.id == second.id
        assert session.get(StrategyRecord, first.id).owner_user_id == "user-1"

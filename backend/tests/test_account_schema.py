from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from archimedes.models.account import AuthUser, LinkedWallet
from archimedes.models.chat import Base
from archimedes.models.identity import WalletIdentity  # noqa: F401 — registers FK target


def _user(user_id: str, email: str) -> AuthUser:
    now = datetime.now(UTC)
    return AuthUser(
        id=user_id,
        name=email.split("@", 1)[0],
        email=email,
        email_verified=False,
        created_at=now,
        updated_at=now,
    )


def test_better_auth_and_wallet_tables_use_explicit_models():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())

    assert {"auth_users", "auth_accounts", "auth_sessions", "auth_verifications"} <= tables
    assert {"linked_wallets", "wallet_link_challenges"} <= tables
    assert {column["name"] for column in inspect(engine).get_columns("auth_sessions")} >= {
        "token",
        "userId",
        "expiresAt",
    }


def test_normalized_wallet_is_unique_across_users():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add_all([_user("user-1", "one@example.com"), _user("user-2", "two@example.com")])
        session.flush()
        session.add(
            LinkedWallet(
                id="wallet-1",
                user_id="user-1",
                normalized_identity="5042002:0x1111111111111111111111111111111111111111",
                address="0x1111111111111111111111111111111111111111",
                display_address="0x1111111111111111111111111111111111111111",
                chain_id=5042002,
                provider="metamask",
                is_primary=True,
            )
        )
        session.commit()

        session.add(
            LinkedWallet(
                id="wallet-2",
                user_id="user-2",
                normalized_identity="5042002:0x1111111111111111111111111111111111111111",
                address="0x1111111111111111111111111111111111111111",
                display_address="0x1111111111111111111111111111111111111111",
                chain_id=5042002,
                provider="browser",
                is_primary=True,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_user_has_at_most_one_primary_wallet():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(_user("user-1", "one@example.com"))
        session.flush()
        for index in (1, 2):
            address = f"0x{index:040x}"
            session.add(
                LinkedWallet(
                    id=f"wallet-{index}",
                    user_id="user-1",
                    normalized_identity=f"5042002:{address}",
                    address=address,
                    display_address=address,
                    chain_id=5042002,
                    provider="browser",
                    is_primary=True,
                )
            )
        with pytest.raises(IntegrityError):
            session.commit()

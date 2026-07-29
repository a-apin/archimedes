from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from archimedes.api.account_auth import CurrentUser
from archimedes.api.wallet_routes import (
    WalletChallengeRequest,
    WalletVerifyRequest,
    _verify_wallet_proof,
    issue_wallet_challenge,
    set_primary_wallet,
    unlink_wallet,
    verify_wallet_challenge,
)
from archimedes.models.account import AuthUser, LinkedWallet
from archimedes.models.chat import Base
from archimedes.models.identity import ControlledWallet, WalletIdentity
from archimedes.models.strategy_passport_record import StrategyPassportRecord  # noqa: F401
from archimedes.models.strategy_proposal import StrategyProposal  # noqa: F401
from archimedes.models.strategy_store import upsert_strategy
from archimedes.models.user_profile import UserProfile  # noqa: F401
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ADDRESS = "0x1111111111111111111111111111111111111111"
OTHER_ADDRESS = "0x2222222222222222222222222222222222222222"
NOW = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)


def _user(user_id: str) -> CurrentUser:
    return CurrentUser(user_id, user_id, f"{user_id}@example.com", True)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        [
            AuthUser(
                id=user_id,
                name=user_id,
                email=f"{user_id}@example.com",
                email_verified=True,
                created_at=NOW,
                updated_at=NOW,
            )
            for user_id in ("user-1", "user-2")
        ]
    )
    session.commit()
    return session


def _challenge(address: str = ADDRESS, provider: str = "metamask") -> WalletChallengeRequest:
    return WalletChallengeRequest(address=address, chain_id=5042002, provider=provider)


def _verify(message: str, signature: str = "0xproof") -> WalletVerifyRequest:
    return WalletVerifyRequest(message=message, signature=signature)


@pytest.mark.asyncio
async def test_eoa_signature_verifies_real_challenge():
    from eth_account import Account
    from eth_account.messages import encode_defunct

    account = Account.from_key("0x" + "11" * 32)
    with _session() as session:
        challenge = issue_wallet_challenge(
            session,
            _user("user-1"),
            _challenge(address=account.address),
            site_url="https://archimedes-arc.com",
            now=NOW,
        )
        signature = Account.sign_message(encode_defunct(text=challenge.message), account.key).signature.hex()
        assert await _verify_wallet_proof(account.address.lower(), challenge.message, signature, 5042002)


@pytest.mark.asyncio
async def test_connecting_without_signature_does_not_link_wallet():
    with _session() as session:
        issue_wallet_challenge(
            session,
            _user("user-1"),
            _challenge(),
            site_url="https://archimedes-arc.com",
            now=NOW,
        )
        assert session.query(LinkedWallet).count() == 0


@pytest.mark.asyncio
async def test_valid_proof_links_wallet_and_replay_is_rejected():
    verifier = AsyncMock(return_value=True)
    with _session() as session:
        challenge = issue_wallet_challenge(
            session,
            _user("user-1"),
            _challenge(),
            site_url="https://archimedes-arc.com",
            now=NOW,
        )
        linked = await verify_wallet_challenge(
            session,
            _user("user-1"),
            _verify(challenge.message),
            verifier=verifier,
            now=NOW,
        )
        assert linked.address == ADDRESS
        assert linked.normalized_identity == f"5042002:{ADDRESS}"
        assert linked.is_primary is True

        with pytest.raises(HTTPException) as replay:
            await verify_wallet_challenge(
                session,
                _user("user-1"),
                _verify(challenge.message),
                verifier=verifier,
                now=NOW,
            )
        assert replay.value.status_code == 409


@pytest.mark.asyncio
async def test_invalid_expired_and_domain_mismatched_proofs_are_rejected():
    with _session() as session:
        invalid = issue_wallet_challenge(
            session, _user("user-1"), _challenge(), site_url="https://archimedes-arc.com", now=NOW
        )
        with pytest.raises(HTTPException) as bad_signature:
            await verify_wallet_challenge(
                session,
                _user("user-1"),
                _verify(invalid.message),
                verifier=AsyncMock(return_value=False),
                now=NOW,
            )
        assert bad_signature.value.status_code == 401

        expired = issue_wallet_challenge(
            session, _user("user-1"), _challenge(), site_url="https://archimedes-arc.com", now=NOW
        )
        with pytest.raises(HTTPException) as stale:
            await verify_wallet_challenge(
                session,
                _user("user-1"),
                _verify(expired.message),
                verifier=AsyncMock(return_value=True),
                now=NOW + timedelta(minutes=6),
            )
        assert stale.value.status_code == 401

        mismatch = issue_wallet_challenge(
            session, _user("user-1"), _challenge(), site_url="https://archimedes-arc.com", now=NOW
        )
        tampered = mismatch.message.replace("archimedes-arc.com", "evil.example", 1)
        with pytest.raises(HTTPException) as wrong_domain:
            await verify_wallet_challenge(
                session,
                _user("user-1"),
                _verify(tampered),
                verifier=AsyncMock(return_value=True),
                now=NOW,
            )
        assert wrong_domain.value.status_code == 401

        exact = issue_wallet_challenge(
            session, _user("user-1"), _challenge(), site_url="https://archimedes-arc.com", now=NOW
        )
        changed_statement = exact.message.replace(
            "Link this wallet to your authenticated Archimedes account.",
            "Sign an unrelated statement.",
        )
        with pytest.raises(HTTPException) as wrong_message:
            await verify_wallet_challenge(
                session,
                _user("user-1"),
                _verify(changed_statement),
                verifier=AsyncMock(return_value=True),
                now=NOW,
            )
        assert wrong_message.value.status_code == 401


@pytest.mark.asyncio
async def test_wallet_linked_to_another_user_cannot_be_claimed():
    verifier = AsyncMock(return_value=True)
    with _session() as session:
        first = issue_wallet_challenge(
            session, _user("user-1"), _challenge(), site_url="https://archimedes-arc.com", now=NOW
        )
        await verify_wallet_challenge(session, _user("user-1"), _verify(first.message), verifier=verifier, now=NOW)

        second = issue_wallet_challenge(
            session, _user("user-2"), _challenge(), site_url="https://archimedes-arc.com", now=NOW
        )
        with pytest.raises(HTTPException) as conflict:
            await verify_wallet_challenge(session, _user("user-2"), _verify(second.message), verifier=verifier, now=NOW)
        assert conflict.value.status_code == 409
        assert session.query(LinkedWallet).filter_by(user_id="user-1").count() == 1


@pytest.mark.asyncio
async def test_verified_link_claims_only_matching_unowned_legacy_data():
    with _session() as session:
        session.add(WalletIdentity(wallet_address=ADDRESS, actor_class="human", first_seen_at=NOW))
        session.flush()
        record = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="Legacy",
            thesis="legacy wallet data",
            source_papers=[],
            asset_universe=["SPY"],
            owner_wallet=ADDRESS,
        )
        session.commit()

        challenge = issue_wallet_challenge(
            session, _user("user-1"), _challenge(), site_url="https://archimedes-arc.com", now=NOW
        )
        await verify_wallet_challenge(
            session,
            _user("user-1"),
            _verify(challenge.message),
            verifier=AsyncMock(return_value=True),
            now=NOW,
        )
        session.refresh(record)
        assert record.owner_user_id == "user-1"


@pytest.mark.asyncio
async def test_platform_controlled_circle_wallet_is_not_silently_claimed():
    with _session() as session:
        session.add(
            ControlledWallet(
                address=ADDRESS,
                controller_wallet=None,
                wallet_class="subscriber_payment",
                key_binding="circle-wallet-id",
                provisioned_at=NOW,
            )
        )
        session.commit()
        challenge = issue_wallet_challenge(
            session, _user("user-1"), _challenge(provider="circle"), site_url="https://archimedes-arc.com", now=NOW
        )
        with pytest.raises(HTTPException) as conflict:
            await verify_wallet_challenge(
                session,
                _user("user-1"),
                _verify(challenge.message),
                verifier=AsyncMock(return_value=True),
                now=NOW,
            )
        assert conflict.value.status_code == 409


def test_primary_selection_and_safe_unlink():
    with _session() as session:
        wallets = []
        for index, address in enumerate((ADDRESS, OTHER_ADDRESS), start=1):
            wallet = LinkedWallet(
                id=f"wallet-{index}",
                user_id="user-1",
                normalized_identity=f"5042002:{address}",
                address=address,
                display_address=address,
                chain_id=5042002,
                provider="browser",
                is_primary=index == 1,
                verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
            wallets.append(wallet)
            session.add(wallet)
        session.commit()

        set_primary_wallet(session, "user-1", "wallet-2")
        assert session.get(LinkedWallet, "wallet-2").is_primary is True
        unlink_wallet(session, "user-1", "wallet-2")
        assert session.get(LinkedWallet, "wallet-1").is_primary is True

"""Identity-ledger write-point tests for ChatService (issue #1028, "tests" chunk — 5/5).

``ChatService.post_message`` / ``post_ai_message`` are the one write path in
this codebase where the FK'd wallet may never have gone through SIWE (chat's
unverified-attribution path) — ``ensure_wallet_identity`` anchors the wallet
BEFORE the ``chat_messages`` insert (whose ``wallet_address`` FKs to
``wallet_identities``), then ``emit_identity_event`` ledgers the post.

Unlike ``backend/tests/services/test_chat_service.py`` (which mocks
``get_session`` entirely to unit-test message composition), this file
exercises the REAL DB boundary — the thing worth testing here is that a
never-before-seen wallet gets anchored and the post doesn't trip the FK it
now carries, matching ``test_identity_metrics.py``'s real-DB-boundary
justification.
"""

from __future__ import annotations

import uuid

import pytest
from archimedes.db import get_session, init_db
from archimedes.models.identity import IdentityEvent, WalletIdentity
from archimedes.services.chat_service import AI_WALLET_ADDRESS, ChatService


@pytest.fixture(autouse=True, scope="module")
def _ensure_tables():
    """See test_identity_metrics.py's identical fixture for the rationale."""
    init_db()


def _unique_wallet() -> str:
    return "0x" + uuid.uuid4().hex.ljust(40, "0")[:40]


def _cleanup(*wallets: str) -> None:
    session = get_session()
    try:
        for wallet in wallets:
            session.query(IdentityEvent).filter(IdentityEvent.wallet == wallet).delete(synchronize_session=False)
            session.query(WalletIdentity).filter(WalletIdentity.wallet_address == wallet).delete(
                synchronize_session=False
            )
        session.commit()
    finally:
        session.close()


def test_post_message_anchors_a_never_before_seen_wallet_as_human():
    """Chat allows unverified attribution — a wallet that never went through
    SIWE must still get anchored (ensure_wallet_identity) before the FK'd
    insert, or the insert itself would trip the FK on Postgres."""
    wallet = _unique_wallet()
    vault = "0x" + "1a" * 20
    try:
        result = ChatService().post_message(vault, wallet, "hello vault, no mention here", verified=False)
        assert result["wallet_address"] == wallet

        session = get_session()
        try:
            identity = session.get(WalletIdentity, wallet)
            assert identity is not None
            assert identity.actor_class == "human"
        finally:
            session.close()
    finally:
        _cleanup(wallet)


def test_post_message_ledgers_chat_posted_event():
    wallet = _unique_wallet()
    vault = "0x" + "1b" * 20
    try:
        ChatService().post_message(vault, wallet, "just a regular message", verified=True)

        session = get_session()
        try:
            events = session.query(IdentityEvent).filter(IdentityEvent.wallet == wallet).all()
        finally:
            session.close()
        matching = [e for e in events if e.event_type == "chat_posted"]
        assert len(matching) == 1
        assert matching[0].actor_class == "human"
        assert matching[0].meta["vault_address"] == vault.lower()
        assert matching[0].meta["verified"] is True
    finally:
        _cleanup(wallet)


def test_post_ai_message_anchors_the_ai_persona_as_agent():
    """D3: the AI persona is a first-class agent actor, not 'human'."""
    vault = "0x" + "1c" * 20
    try:
        result = ChatService().post_ai_message(vault, "the portfolio was rebalanced", trigger="rebalance")
        assert result is not None
        assert result["wallet_address"] == AI_WALLET_ADDRESS

        session = get_session()
        try:
            identity = session.get(WalletIdentity, AI_WALLET_ADDRESS)
            assert identity is not None
            assert identity.actor_class == "agent"

            events = session.query(IdentityEvent).filter(IdentityEvent.wallet == AI_WALLET_ADDRESS).all()
        finally:
            session.close()
        matching = [e for e in events if e.event_type == "chat_posted" and e.meta.get("trigger") == "rebalance"]
        assert len(matching) == 1
        assert matching[0].actor_class == "agent"
    finally:
        _cleanup(AI_WALLET_ADDRESS)

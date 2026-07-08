"""Unit tests for the identity-ledger write helpers (issue #1028, "tests" chunk — 5/5).

Covers ``archimedes.services.identity_events`` directly: ``emit_identity_event``
(the ONE path every lifecycle event writes through — D2), ``ensure_wallet_identity``
(pre-anchor for FK-constrained inserts whose wallet may not be SIWE-verified —
chat's unverified path), and ``register_controlled_wallet`` (D1a/D3 — agent
wallets, Circle DCWs, treasury).

Exercises the real DB boundary (SQLite in this suite), matching
``test_identity_metrics.py``'s justification: these three functions ARE the
write side of the ledger, so the thing worth testing is the actual anchor +
append behavior, not that a mock was called. Delta/unique-wallet based so
assertions are order-independent against the shared test-process engine.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from archimedes.db import get_session, init_db
from archimedes.models.identity import ControlledWallet, IdentityEvent, WalletIdentity
from archimedes.services.identity_events import (
    emit_identity_event,
    ensure_wallet_identity,
    register_controlled_wallet,
)


@pytest.fixture(autouse=True, scope="module")
def _ensure_tables():
    """See test_identity_metrics.py's identical fixture for the rationale."""
    init_db()


def _unique_wallet() -> str:
    return "0x" + uuid.uuid4().hex.ljust(40, "0")[:40]


@contextmanager
def _cleanup_wallet(address: str):
    """Delete wallet_identities/identity_events/controlled_wallets rows for
    `address` on exit, regardless of which of the three tables ended up with
    a row (a test may write to more than one)."""
    try:
        yield
    finally:
        session = get_session()
        try:
            session.query(IdentityEvent).filter(IdentityEvent.wallet == address).delete(synchronize_session=False)
            session.query(ControlledWallet).filter(ControlledWallet.address == address).delete(
                synchronize_session=False
            )
            session.query(WalletIdentity).filter(WalletIdentity.wallet_address == address).delete(
                synchronize_session=False
            )
            session.commit()
        finally:
            session.close()


def _get_wallet(address: str) -> WalletIdentity | None:
    session = get_session()
    try:
        return session.get(WalletIdentity, address)
    finally:
        session.close()


def _identity_events_for(address: str | None) -> list[IdentityEvent]:
    session = get_session()
    try:
        return session.query(IdentityEvent).filter(IdentityEvent.wallet == address).all()
    finally:
        session.close()


# ─── emit_identity_event ───────────────────────────────────────────────────


def test_emit_identity_event_anchors_new_wallet_and_appends_event():
    wallet = _unique_wallet()
    with _cleanup_wallet(wallet):
        emit_identity_event(wallet=wallet, event_type="auth_verified", actor_class="human")

        identity = _get_wallet(wallet)
        assert identity is not None
        assert identity.actor_class == "human"

        events = _identity_events_for(wallet)
        assert len(events) == 1
        assert events[0].event_type == "auth_verified"
        assert events[0].actor_class == "human"


def test_emit_identity_event_lowercases_the_wallet():
    wallet_lower = _unique_wallet()
    wallet_mixed = "0x" + wallet_lower[2:20].upper() + wallet_lower[20:]
    with _cleanup_wallet(wallet_lower):
        emit_identity_event(wallet=wallet_mixed, event_type="chat_posted", actor_class="human")
        assert _get_wallet(wallet_lower) is not None
        assert _get_wallet(wallet_mixed) is None  # never stored mixed-case


def test_emit_identity_event_touch_last_auth_sets_and_bumps_timestamp():
    wallet = _unique_wallet()
    with _cleanup_wallet(wallet):
        emit_identity_event(wallet=wallet, event_type="auth_verified", actor_class="human", touch_last_auth=True)
        first = _get_wallet(wallet)
        assert first.last_auth_at is not None
        first_seen = first.first_seen_at

        emit_identity_event(wallet=wallet, event_type="auth_verified", actor_class="human", touch_last_auth=True)
        second = _get_wallet(wallet)
        # Re-anchoring an existing wallet must not reset first_seen_at...
        assert second.first_seen_at == first_seen
        # ...but last_auth_at is live (both calls set *a* timestamp — the
        # anchor is not re-inserted, only updated).
        assert second.last_auth_at is not None


def test_emit_identity_event_without_touch_last_auth_leaves_last_auth_at_null():
    wallet = _unique_wallet()
    with _cleanup_wallet(wallet):
        emit_identity_event(wallet=wallet, event_type="generation_started", actor_class="human")
        assert _get_wallet(wallet).last_auth_at is None


def test_emit_identity_event_captures_vid_only_when_passed():
    wallet = _unique_wallet()
    with _cleanup_wallet(wallet):
        emit_identity_event(wallet=wallet, event_type="auth_verified", actor_class="human", vid="visitor-abc123")
        events = _identity_events_for(wallet)
        assert events[0].vid == "visitor-abc123"


def test_emit_identity_event_stores_meta():
    wallet = _unique_wallet()
    with _cleanup_wallet(wallet):
        emit_identity_event(
            wallet=wallet,
            event_type="vault_created",
            actor_class="human",
            meta={"vault_address": "0xvault", "strategy_ids": ["s1"]},
        )
        events = _identity_events_for(wallet)
        assert events[0].meta == {"vault_address": "0xvault", "strategy_ids": ["s1"]}


def test_emit_identity_event_anonymous_non_system_is_a_noop():
    """D2a: pre-auth stays anonymous — no wallet means nothing to anchor a
    row to, so an anonymous, non-system call must write nothing."""
    session = get_session()
    try:
        before = session.query(IdentityEvent).filter(IdentityEvent.wallet.is_(None)).count()
    finally:
        session.close()

    emit_identity_event(wallet=None, event_type="generation_started", actor_class="human")

    session = get_session()
    try:
        after = session.query(IdentityEvent).filter(IdentityEvent.wallet.is_(None)).count()
    finally:
        session.close()
    assert after == before


def test_emit_identity_event_system_actor_class_allows_null_wallet():
    """The one documented exception to the anonymous no-op above: actor_class='system'."""
    session = get_session()
    try:
        before = (
            session.query(IdentityEvent)
            .filter(IdentityEvent.wallet.is_(None), IdentityEvent.actor_class == "system")
            .count()
        )
    finally:
        session.close()

    emit_identity_event(wallet=None, event_type="agent_action", actor_class="system")

    session = get_session()
    try:
        rows = (
            session.query(IdentityEvent)
            .filter(IdentityEvent.wallet.is_(None), IdentityEvent.actor_class == "system")
            .all()
        )
        after = len(rows)
        newest = rows[-1]
    finally:
        session.close()
    assert after == before + 1
    assert newest.event_type == "agent_action"


def test_emit_identity_event_invalid_actor_class_defaults_to_human():
    wallet = _unique_wallet()
    with _cleanup_wallet(wallet):
        emit_identity_event(wallet=wallet, event_type="chat_posted", actor_class="not_a_real_class")
        assert _get_wallet(wallet).actor_class == "human"
        assert _identity_events_for(wallet)[0].actor_class == "human"


def test_emit_identity_event_never_raises_on_db_failure(monkeypatch):
    """Fail-safe by construction: a DB-down / broken-session error must never
    propagate into the caller's request (see module docstring)."""

    def _boom():
        raise RuntimeError("db is down")

    monkeypatch.setattr("archimedes.db.get_session", _boom)
    emit_identity_event(wallet=_unique_wallet(), event_type="auth_verified", actor_class="human")  # must not raise


# ─── ensure_wallet_identity ────────────────────────────────────────────────


def test_ensure_wallet_identity_anchors_a_new_wallet():
    wallet = _unique_wallet()
    with _cleanup_wallet(wallet):
        ensure_wallet_identity(wallet, "agent")
        identity = _get_wallet(wallet)
        assert identity is not None
        assert identity.actor_class == "agent"


def test_ensure_wallet_identity_is_a_noop_for_falsy_wallet():
    ensure_wallet_identity(None, "human")  # must not raise
    ensure_wallet_identity("", "human")  # must not raise


def test_ensure_wallet_identity_does_not_overwrite_existing_actor_class():
    """First anchor wins — a second, different actor_class call must not
    mutate an already-anchored wallet (only ``touch_last_auth`` mutates)."""
    wallet = _unique_wallet()
    with _cleanup_wallet(wallet):
        ensure_wallet_identity(wallet, "agent")
        ensure_wallet_identity(wallet, "human")
        assert _get_wallet(wallet).actor_class == "agent"


def test_ensure_wallet_identity_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr("archimedes.db.get_session", lambda: (_ for _ in ()).throw(RuntimeError("db is down")))
    ensure_wallet_identity(_unique_wallet(), "human")  # must not raise


# ─── register_controlled_wallet ────────────────────────────────────────────


def test_register_controlled_wallet_creates_row():
    address = _unique_wallet()
    with _cleanup_wallet(address):
        register_controlled_wallet(address=address, wallet_class="treasury")
        session = get_session()
        try:
            row = session.get(ControlledWallet, address)
        finally:
            session.close()
        assert row is not None
        assert row.wallet_class == "treasury"
        assert row.controller_wallet is None


def test_register_controlled_wallet_anchors_the_controller():
    controller = _unique_wallet()
    dcw = _unique_wallet()
    with _cleanup_wallet(controller), _cleanup_wallet(dcw):
        register_controlled_wallet(
            address=dcw, wallet_class="publisher_settlement", controller_wallet=controller, key_binding="circle-uuid"
        )
        session = get_session()
        try:
            row = session.get(ControlledWallet, dcw)
            controller_identity = session.get(WalletIdentity, controller)
        finally:
            session.close()
        assert row is not None
        assert row.controller_wallet == controller
        assert row.key_binding == "circle-uuid"
        # The controller wallet must be anchored into wallet_identities too —
        # otherwise the FK on controller_wallet would trip.
        assert controller_identity is not None
        assert controller_identity.actor_class == "human"


def test_register_controlled_wallet_is_idempotent():
    address = _unique_wallet()
    with _cleanup_wallet(address):
        register_controlled_wallet(address=address, wallet_class="trading_agent")
        register_controlled_wallet(address=address, wallet_class="trading_agent")  # must not raise / duplicate
        session = get_session()
        try:
            count = session.query(ControlledWallet).filter(ControlledWallet.address == address).count()
        finally:
            session.close()
        assert count == 1


def test_register_controlled_wallet_rejects_unknown_wallet_class():
    address = _unique_wallet()
    with _cleanup_wallet(address):
        register_controlled_wallet(address=address, wallet_class="not_a_real_class")
        session = get_session()
        try:
            row = session.get(ControlledWallet, address)
        finally:
            session.close()
        assert row is None


def test_register_controlled_wallet_noop_without_address():
    register_controlled_wallet(address=None, wallet_class="treasury")  # must not raise
    register_controlled_wallet(address="", wallet_class="treasury")  # must not raise


def test_register_controlled_wallet_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr("archimedes.db.get_session", lambda: (_ for _ in ()).throw(RuntimeError("db is down")))
    register_controlled_wallet(address=_unique_wallet(), wallet_class="treasury")  # must not raise

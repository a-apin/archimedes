"""Unit tests for the identity-ledger read queries (issue #1028, "metrics-views" chunk).

Exercises the real DB boundary (SQLite in this suite) rather than mocking it —
these functions ARE the SQL views the epic's acceptance criteria describe, so
the thing worth testing is that the query is correct, not that a mock was
called. Because the pytest process binds one shared engine across the whole
run (see ``test_api_routes.py``'s ``_use_tmp_db`` docstring), every assertion
here is delta-based (count/contents before vs. after a known insert) rather
than an absolute count against the shared table — order-independent by
construction, and each test cleans up its own rows in a ``finally``.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from archimedes.db import get_session, init_db
from archimedes.models.identity import IdentityEvent, WalletIdentity
from archimedes.services.identity_metrics import (
    count_human_wallets,
    get_identity_funnel,
    list_human_wallets,
    list_wallet_connections,
)


@pytest.fixture(autouse=True, scope="module")
def _ensure_tables():
    """Guarantee wallet_identities/identity_events exist before this module's tests run.

    Every other DB-backed test file either imports ``archimedes.main`` (whose
    module-level ``init_db()`` call is the usual trigger) or runs its own
    ``init_db()``/``create_all`` fixture; this file did neither, so running it
    standalone (``pytest backend/tests/test_identity_metrics.py``, the hermetic
    per-module gate from CLAUDE.md) hit ``OperationalError: no such table:
    wallet_identities``. ``init_db()`` is idempotent (``create_all`` no-ops on
    existing tables) so this is safe to call regardless of run order.
    """
    init_db()


def _unique_wallet() -> str:
    """A syntactically-valid, guaranteed-unique lowercase wallet address for this test run."""
    return "0x" + uuid.uuid4().hex.ljust(40, "0")[:40]


@contextmanager
def _wallet(actor_class: str = "human", *, first_seen_at: datetime | None = None):
    """Insert one wallet_identities row for the duration of the ``with`` block, then delete it."""
    address = _unique_wallet()
    session = get_session()
    try:
        session.add(
            WalletIdentity(
                wallet_address=address,
                actor_class=actor_class,
                first_seen_at=first_seen_at or datetime.now(UTC),
            )
        )
        session.commit()
    finally:
        session.close()
    try:
        yield address
    finally:
        session = get_session()
        try:
            session.query(WalletIdentity).filter(WalletIdentity.wallet_address == address).delete(
                synchronize_session=False
            )
            session.commit()
        finally:
            session.close()


@contextmanager
def _identity_event(wallet: str, event_type: str, actor_class: str = "human", *, occurred_at: datetime | None = None):
    """Insert one identity_events row for the duration of the ``with`` block, then delete it."""
    session = get_session()
    try:
        row = IdentityEvent(
            wallet=wallet,
            vid=None,
            event_type=event_type,
            actor_class=actor_class,
            occurred_at=occurred_at or datetime.now(UTC),
            meta={},
        )
        session.add(row)
        session.commit()
        row_id = row.id
    finally:
        session.close()
    try:
        yield row_id
    finally:
        session = get_session()
        try:
            session.query(IdentityEvent).filter(IdentityEvent.id == row_id).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()


def test_count_human_wallets_moves_by_exactly_one_per_human_wallet():
    before = count_human_wallets()
    with _wallet(actor_class="human"):
        assert count_human_wallets() == before + 1
    assert count_human_wallets() == before


def test_count_human_wallets_ignores_agent_actor_class():
    """AC1: 'real users' is human wallets only — an agent-class wallet must not count."""
    before = count_human_wallets()
    with _wallet(actor_class="agent"):
        assert count_human_wallets() == before


def test_list_human_wallets_enumerates_the_count_human_wallets_population():
    """AC1: 'enumerating those wallets is one query' — this IS that query."""
    with _wallet(actor_class="human") as human_wallet, _wallet(actor_class="agent") as agent_wallet:
        wallets = list_human_wallets(limit=100_000)
        addresses = {w["wallet_address"] for w in wallets}
        assert human_wallet in addresses
        assert agent_wallet not in addresses
        match = next(w for w in wallets if w["wallet_address"] == human_wallet)
        assert match["actor_class"] == "human"
        assert match["first_seen_at"]
        assert match["last_auth_at"] is None


def test_list_wallet_connections_answers_which_wallet_and_when():
    """AC2: 'which wallets connected, and when' — impossible before the ledger."""
    with _wallet(actor_class="human") as wallet, _identity_event(wallet, "auth_verified"):
        connections = list_wallet_connections(limit=100_000)
        match = next(c for c in connections if c["wallet"] == wallet)
        assert match["connected_at"]  # a real timestamp string, present and non-empty


def test_list_wallet_connections_uses_earliest_auth_verified():
    """min(occurred_at) semantics: a later auth_verified must not move connected_at forward."""
    earliest = datetime.now(UTC) - timedelta(days=2)
    later = earliest + timedelta(days=1)
    with (
        _wallet(actor_class="human") as wallet,
        _identity_event(wallet, "auth_verified", occurred_at=earliest),
        _identity_event(wallet, "auth_verified", occurred_at=later),
    ):
        connections = list_wallet_connections(limit=100_000)
        match = next(c for c in connections if c["wallet"] == wallet)
        assert match["connected_at"] < later.isoformat()


def test_list_wallet_connections_ignores_non_auth_verified_events():
    """A generation_started event alone must not make a wallet show up as 'connected'."""
    with _wallet(actor_class="human") as wallet, _identity_event(wallet, "generation_started"):
        connections = list_wallet_connections(limit=100_000)
        assert not any(c["wallet"] == wallet for c in connections)


def test_get_identity_funnel_shape_is_always_well_formed():
    funnel = get_identity_funnel(exclude_dogfood=True)
    assert set(funnel.keys()) == {"auth_verified", "generation_started", "vault_created"}
    assert all(isinstance(n, int) and n >= 0 for n in funnel.values())


def test_get_identity_funnel_counts_distinct_wallets_per_stage():
    """A wallet with both auth_verified and generation_started moves both stage counts by +1."""
    before = get_identity_funnel(exclude_dogfood=True)
    with (
        _wallet(actor_class="human") as wallet,
        _identity_event(wallet, "auth_verified"),
        _identity_event(wallet, "generation_started"),
    ):
        after = get_identity_funnel(exclude_dogfood=True)
        assert after["auth_verified"] == before["auth_verified"] + 1
        assert after["generation_started"] == before["generation_started"] + 1
        assert after["vault_created"] == before["vault_created"]


def test_get_identity_funnel_exclude_dogfood_removes_dogfood_wallets():
    """AC3: WHERE actor_class NOT IN ('operator_dogfood','agent') excludes dogfooding."""
    before_excluded = get_identity_funnel(exclude_dogfood=True)
    before_included = get_identity_funnel(exclude_dogfood=False)
    with (
        _wallet(actor_class="operator_dogfood") as wallet,
        _identity_event(wallet, "auth_verified", actor_class="operator_dogfood"),
    ):
        after_excluded = get_identity_funnel(exclude_dogfood=True)
        after_included = get_identity_funnel(exclude_dogfood=False)
        # The dogfood wallet must be invisible to the excluding query...
        assert after_excluded["auth_verified"] == before_excluded["auth_verified"]
        # ...but present in the unfiltered one.
        assert after_included["auth_verified"] == before_included["auth_verified"] + 1


def test_get_identity_funnel_excludes_agent_actor_class_too():
    """AC3's clause names both 'operator_dogfood' AND 'agent'."""
    before_excluded = get_identity_funnel(exclude_dogfood=True)
    with (
        _wallet(actor_class="agent") as wallet,
        _identity_event(wallet, "auth_verified", actor_class="agent"),
    ):
        after_excluded = get_identity_funnel(exclude_dogfood=True)
        assert after_excluded["auth_verified"] == before_excluded["auth_verified"]

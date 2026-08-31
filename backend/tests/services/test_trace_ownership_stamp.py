"""Ownership stamping + the visibility predicate — issue #1556.

``AgentStateStore.save_trace`` is the single write choke point for reasoning
traces (``publish_trace``, the agent runner's two persist sites, the reveal
reconciler and the generation-trace writer all land there). Stamping ownership
*there* is what makes "every persisted trace knows who owns it" true by
construction, and it is what lets the read gate answer without a database.

The predicate tests below are the unit-level companion to the route-level guard
tests in ``backend/tests/api/test_traces_ownership_gate.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from archimedes.services.redis_state import AgentStateStore
from archimedes.services.trace_visibility import (
    is_public_trace_vault,
    is_trace_visible,
    resolve_vault_owners,
    trace_owner_view,
)

from tests.db_isolation import redirect_to_tmp_sqlite

OWNER_USER_ID = "user-owner-1556"
OWNER_WALLET = "0x1111111111111111111111111111111111111111"
USER_VAULT = "0xbbbb000000000000000000000000000000000bbb"


@pytest.fixture
def _tmp_db(tmp_path):
    yield from redirect_to_tmp_sqlite(tmp_path)


@pytest.fixture(autouse=True)
def _unset_allowlist(monkeypatch):
    monkeypatch.delenv("PUBLIC_TRACE_VAULTS", raising=False)
    monkeypatch.delenv("AGENT_VAULT_ADDRESSES", raising=False)


def _fake_redis() -> MagicMock:
    # save_trace is a single write choke point shared by two features: the
    # ownership stamp under test here (#1556) and the reveal-reconciliation
    # index maintenance (#1353), which awaits sadd/srem/hsetnx/hdel on the same
    # client. Stub the choke point's full surface — a double that only knows
    # one feature's calls breaks the moment the other feature touches the
    # shared writer (this exact union broke on 2026-08-31).
    r = MagicMock()
    for method in ("get", "set", "zadd", "sadd", "srem", "hsetnx", "hdel", "aclose"):
        setattr(r, method, AsyncMock(return_value=None))
    r.get = AsyncMock(return_value=None)
    return r


def _store() -> tuple[AgentStateStore, MagicMock]:
    store = AgentStateStore(url="redis://fake/")
    fake = _fake_redis()
    store._redis = fake
    return store, fake


def _written_record(fake: MagicMock) -> dict:
    """The JSON body save_trace wrote under the trace-hash key."""
    for call in fake.set.await_args_list:
        key, value = call.args[0], call.args[1]
        if not key.startswith("archimedes:trace:id:"):
            return json.loads(value)
    raise AssertionError("save_trace never wrote a trace body")


def _seed_vault_owner() -> None:
    from archimedes.db import get_session
    from archimedes.models.account import AuthUser
    from archimedes.models.chat import VaultMetadata
    from archimedes.models.identity import WalletIdentity

    now = datetime.now(UTC)
    with get_session() as session:
        session.merge(
            AuthUser(id=OWNER_USER_ID, name="owner", email="owner@example.test", created_at=now, updated_at=now)
        )
        session.merge(WalletIdentity(wallet_address=OWNER_WALLET.lower(), actor_class="human", first_seen_at=now))
        session.merge(
            VaultMetadata(
                vault_address=USER_VAULT.lower(),
                name="v",
                symbol="V",
                creator_address=OWNER_WALLET.lower(),
                owner_user_id=OWNER_USER_ID,
                strategy_ids="[]",
            )
        )
        session.commit()


def _trace(**over) -> dict:
    base = {
        "id": "t1",
        "vault_address": USER_VAULT,
        "decision_type": "rebalance",
        "trigger": "drift",
        "timestamp": "2026-08-30T00:00:00+00:00",
        "reasoning": "private",
        "trace_hash": "0xhash1",
    }
    base.update(over)
    return base


async def test_save_trace_stamps_the_vaults_owner(_tmp_db):
    _seed_vault_owner()
    store, fake = _store()
    await store.save_trace(_trace())

    record = _written_record(fake)
    assert record["owner_user_id"] == OWNER_USER_ID
    assert record["owner_wallet"] == OWNER_WALLET.lower()


async def test_save_trace_does_not_mutate_the_callers_dict(_tmp_db):
    """The stamp goes on the persisted copy, not on the caller's object.

    The agent runner reuses its ``off_chain_data`` dict after the save; a
    stamping step that wrote through it would be an invisible side effect.
    """
    _seed_vault_owner()
    store, _ = _store()
    payload = _trace()
    await store.save_trace(payload)

    assert "owner_user_id" not in payload


async def test_save_trace_respects_an_explicit_owner(_tmp_db):
    """A writer that already knows the owner wins over the vault lookup."""
    _seed_vault_owner()
    store, fake = _store()
    await store.save_trace(_trace(owner_user_id="someone-else"))

    assert _written_record(fake)["owner_user_id"] == "someone-else"


async def test_explicit_none_owner_suppresses_the_vault_guess(_tmp_db):
    """ "I resolved the owner and there isn't one" must not be overwritten.

    This is the generation-trace shape (``vault_address=""``, owner unknown for
    a legacy job payload). Guessing an owner from a vault there would be a
    fabricated ownership claim.
    """
    _seed_vault_owner()
    store, fake = _store()
    await store.save_trace(_trace(owner_user_id=None, owner_wallet=None))

    record = _written_record(fake)
    assert record["owner_user_id"] is None
    assert record["owner_wallet"] is None


async def test_save_trace_persists_unstamped_when_ownership_lookup_fails(monkeypatch):
    """A trace must still persist when the identity DB is unreachable.

    Not a leak: the read gate falls back to resolving the vault owner itself,
    and to the allowlist floor below that.
    """
    monkeypatch.setattr(
        "archimedes.services.trace_visibility.resolve_vault_owners",
        MagicMock(side_effect=RuntimeError("db down")),
    )
    store, fake = _store()
    await store.save_trace(_trace())

    record = _written_record(fake)
    assert record["reasoning"] == "private"
    assert record.get("owner_user_id") is None


def test_resolve_vault_owners_reads_vault_metadata(_tmp_db):
    _seed_vault_owner()
    owners = resolve_vault_owners({USER_VAULT.upper()})
    assert owners[USER_VAULT.lower()] == (OWNER_USER_ID, OWNER_WALLET.lower())


def test_resolve_vault_owners_recovers_a_vault_that_never_wrote_metadata(_tmp_db):
    """`POST /api/vaults/create` emits a `vault_created` identity event but
    writes no metadata row. Without this second source that vault reads as
    unowned and its traces fall through to the house floor."""
    from archimedes.db import get_session
    from archimedes.models.identity import IdentityEvent, WalletIdentity

    unregistered = "0xdddd000000000000000000000000000000000ddd"
    with get_session() as session:
        session.merge(
            WalletIdentity(wallet_address=OWNER_WALLET.lower(), actor_class="human", first_seen_at=datetime.now(UTC))
        )
        session.add(
            IdentityEvent(
                wallet=OWNER_WALLET.lower(),
                event_type="vault_created",
                actor_class="human",
                meta={"vault_address": unregistered},
            )
        )
        session.commit()

    assert resolve_vault_owners({unregistered}) == {unregistered: (None, OWNER_WALLET.lower())}


# ── The predicate ───────────────────────────────────────────────────────────


def test_owner_user_id_is_the_only_key_once_it_exists():
    """Two-tier ownership, same rule as `is_strategy_visible`: a matching
    wallet must NOT grant access to a row that carries a canonical owner."""
    view = {"vault_address": USER_VAULT, "owner_user_id": OWNER_USER_ID, "owner_wallet": OWNER_WALLET}

    assert is_trace_visible(view, None, caller_user_id=OWNER_USER_ID)
    assert not is_trace_visible(view, OWNER_WALLET, caller_user_id=None)
    assert not is_trace_visible(view, OWNER_WALLET, caller_user_id="someone-else")


def test_legacy_wallet_owner_still_matches_case_insensitively():
    view = {"vault_address": USER_VAULT, "owner_user_id": None, "owner_wallet": OWNER_WALLET.lower()}
    assert is_trace_visible(view, OWNER_WALLET.upper(), caller_user_id=None)
    assert not is_trace_visible(view, "0x9999999999999999999999999999999999999999", caller_user_id=None)


def test_anonymous_caller_never_matches_an_owned_row():
    assert not is_trace_visible(
        {"vault_address": USER_VAULT, "owner_user_id": OWNER_USER_ID}, None, caller_user_id=None
    )
    assert not is_trace_visible({"vault_address": USER_VAULT, "owner_wallet": OWNER_WALLET}, None, caller_user_id=None)


def test_a_trace_with_no_vault_is_never_public():
    assert not is_public_trace_vault("")
    assert not is_public_trace_vault(None)
    assert not is_trace_visible({"vault_address": "", "owner_user_id": None}, None, caller_user_id=None)


def test_armed_allowlist_excludes_everything_outside_it(monkeypatch):
    monkeypatch.setenv("PUBLIC_TRACE_VAULTS", f" {USER_VAULT.upper()} , 0xother")
    assert is_public_trace_vault(USER_VAULT.lower())
    assert not is_public_trace_vault("0xcccc000000000000000000000000000000000ccc")


def test_agent_vault_addresses_is_the_allowlist_fallback(monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_ADDRESSES", USER_VAULT)
    assert is_public_trace_vault(USER_VAULT)
    assert not is_public_trace_vault("0xcccc000000000000000000000000000000000ccc")


def test_stamp_wins_over_the_looked_up_vault_owner():
    """A row records who produced it; a later vault transfer must not hand the
    old reasoning body to the new vault owner."""
    view = trace_owner_view(
        {"vault_address": USER_VAULT, "owner_user_id": "original-author"},
        {USER_VAULT.lower(): ("new-vault-owner", None)},
    )
    assert view["owner_user_id"] == "original-author"

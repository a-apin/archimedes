"""Tests for the per-subscriber-wallet rolling 24h spend cap (#713).

spend_cap.py caches a module-level AgentStateStore singleton (_store) rather
than accepting one via constructor injection (unlike MarketState). The
_fake_store fixture below injects a fresh fakeredis-backed AgentStateStore as
that singleton for the duration of one test and restores whatever was there
before, so no test leaks Redis state or a mock into another — mirroring the
fakeredis pattern already used for MarketState in test_state.py, and the
class-level AgentStateStore._get_redis mock used for the Redis-down scenario
in test_api_routes.py::TestAgentRoutes::test_agent_status_redis_down_defaults.

try_reserve_usdc/is_over_cap are both built on the same atomic Lua
check-and-reserve script (#1099 review — see spend_cap._CHECK_AND_RESERVE_LUA).
fakeredis's Lua support (the `fakeredis[lua]` extra, already in environment.yml
for test_runner_lease.py's lease-acquire coverage) executes the real script, so
these tests exercise the actual atomicity rather than a mocked stand-in.
"""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from archimedes.marketplace import spend_cap
from archimedes.services.redis_state import AgentStateStore

_WALLET = "0xSubscriberWallet0000000000000000000001"


@pytest.fixture(autouse=True)
def _fake_store():
    """Inject a fakeredis-backed AgentStateStore as spend_cap's module-level
    singleton for one test, then restore the prior value (None on a clean
    run) so tests never leak Redis state or a mock into each other."""
    store = AgentStateStore()
    store._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    previous = spend_cap._store
    spend_cap._store = store
    yield store
    spend_cap._store = previous


@pytest.fixture(autouse=True)
def _pinned_cap(monkeypatch):
    """Pin a known, small cap for every test unless a test overrides it —
    keeps the arithmetic in each test easy to read vs. the real default (50)."""
    monkeypatch.setenv("MARKETPLACE_SPEND_CAP_USDC", "10")


def _raw(usdc: str) -> int:
    """USDC decimal string -> raw 6-decimal int (matches payments.py convention)."""
    return int(Decimal(usdc) * Decimal(10**6))


async def _seed(wallet: str, charge_id: str, amount_raw: int) -> None:
    """Seed existing spend directly in Redis, independent of the cap.

    Several tests below set up "this wallet already spent exactly/over the
    cap" state to test is_over_cap's/try_reserve_usdc's boundary — going
    through try_reserve_usdc itself for that setup would be circular (it
    would refuse to seed the very state the test wants to assert against).
    Writes the same member format (f"{charge_id}:{amount_raw}") the real
    write path uses, so seeded state is indistinguishable from a real one on
    read.
    """
    store = spend_cap._get_store()
    r = await store._get_redis()
    key = spend_cap._key(wallet)
    await r.zadd(key, {f"{charge_id}:{amount_raw}": time.time()})
    await r.expire(key, spend_cap._KEY_TTL_SECONDS)


# ── spend_cap_usdc() — config parsing ───────────────────────────────────


def test_spend_cap_usdc_reads_configured_value(monkeypatch):
    monkeypatch.setenv("MARKETPLACE_SPEND_CAP_USDC", "25.5")
    assert spend_cap.spend_cap_usdc() == Decimal("25.5")


def test_spend_cap_usdc_default_is_50(monkeypatch):
    monkeypatch.delenv("MARKETPLACE_SPEND_CAP_USDC", raising=False)
    assert spend_cap.spend_cap_usdc() == Decimal("50")


def test_spend_cap_usdc_zero_disables(monkeypatch):
    monkeypatch.setenv("MARKETPLACE_SPEND_CAP_USDC", "0")
    assert spend_cap.spend_cap_usdc() == Decimal("0")


def test_spend_cap_usdc_non_numeric_disables_and_warns(monkeypatch, caplog):
    monkeypatch.setenv("MARKETPLACE_SPEND_CAP_USDC", "not-a-number")
    with caplog.at_level(logging.WARNING, logger=spend_cap.__name__):
        result = spend_cap.spend_cap_usdc()
    assert result == Decimal("0")
    assert "not a valid number" in caplog.text


# ── round-trip: seeded spend -> get_24h_spend_usdc ──────────────────────


@pytest.mark.asyncio
async def test_get_24h_spend_usdc_starts_at_zero():
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal(0)


@pytest.mark.asyncio
async def test_reserve_and_read_back_round_trip():
    await _seed(_WALLET, "tick1:rebalance", _raw("3"))
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("3")


@pytest.mark.asyncio
async def test_reserve_sums_multiple_charges():
    await _seed(_WALLET, "c1", _raw("2"))
    await _seed(_WALLET, "c2", _raw("1.5"))
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("3.5")


@pytest.mark.asyncio
async def test_reserve_zero_or_negative_amount_is_noop():
    assert await spend_cap.try_reserve_usdc(_WALLET, 0, charge_id="c1") is True
    assert await spend_cap.try_reserve_usdc(_WALLET, -100, charge_id="c2") is True
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal(0)


@pytest.mark.asyncio
async def test_spend_is_independent_per_wallet():
    await _seed(_WALLET, "c1", _raw("4"))
    await _seed("0xOtherWallet00000000000000000000000002", "c2", _raw("9"))
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("4")


@pytest.mark.asyncio
async def test_wallet_key_is_case_insensitive():
    """Subscriber wallets are lowercased for the Redis key — a charge recorded
    under mixed case must be visible when read back with lowercase (or any
    other case), matching how sub.subscriber_wallet is used inconsistently
    across call sites (some pre-lowered, some not)."""
    await _seed(_WALLET.upper(), "c1", _raw("2"))
    assert await spend_cap.get_24h_spend_usdc(_WALLET.lower()) == Decimal("2")


# ── try_reserve_usdc — the atomic check-and-reserve ─────────────────────


@pytest.mark.asyncio
async def test_try_reserve_succeeds_when_under_cap():
    assert await spend_cap.try_reserve_usdc(_WALLET, _raw("5"), charge_id="c1") is True
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("5")


@pytest.mark.asyncio
async def test_try_reserve_refuses_when_it_would_exactly_meet_cap():
    await _seed(_WALLET, "c1", _raw("5"))
    assert await spend_cap.try_reserve_usdc(_WALLET, _raw("5"), charge_id="c2") is False
    # Refused — nothing written for the refused attempt.
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("5")


@pytest.mark.asyncio
async def test_try_reserve_refuses_when_it_would_exceed_cap():
    await _seed(_WALLET, "c1", _raw("8"))
    assert await spend_cap.try_reserve_usdc(_WALLET, _raw("3"), charge_id="c2") is False
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("8")


@pytest.mark.asyncio
async def test_try_reserve_disabled_when_cap_is_zero(monkeypatch):
    monkeypatch.setenv("MARKETPLACE_SPEND_CAP_USDC", "0")
    assert await spend_cap.try_reserve_usdc(_WALLET, _raw("999999"), charge_id="c1") is True
    # Disabled short-circuits before touching Redis at all — nothing written.
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal(0)


@pytest.mark.asyncio
async def test_try_reserve_retry_of_same_charge_id_does_not_double_count():
    """A retry that reserves the SAME charge_id + amount (e.g. a caller that
    ignored the idempotency guard) must not double-count — Redis ZADD upserts
    an existing member's score rather than duplicating it, so the total
    stays the sum of distinct entries, not distinct calls."""
    assert await spend_cap.try_reserve_usdc(_WALLET, _raw("5"), charge_id="c1") is True
    assert await spend_cap.try_reserve_usdc(_WALLET, _raw("5"), charge_id="c1") is True
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("5")


# ── the actual race: concurrent try_reserve_usdc calls near the cap ────


@pytest.mark.asyncio
async def test_concurrent_reserves_never_exceed_the_cap():
    """Direct proof for the #1099 review finding: fire more concurrent
    reservations at a wallet than the cap allows and confirm the atomic Lua
    script serializes them correctly — exactly as many as fit succeed, the
    rest are refused, and the final total never exceeds the cap. Before the
    fix, is_over_cap (read) and record_charge_usdc (write) were two separate
    Redis round-trips with an await in between, so this exact scenario could
    let every concurrent caller read "under cap" and all of them proceed.

    Cap is pinned to 10 and refuses at >= cap (test_is_over_cap_true_when_
    spend_exactly_meets_cap), so with 1-USDC charges only 9 fit — a 10th
    would land exactly on the cap.
    """
    per_charge = _raw("1")
    results = await asyncio.gather(
        *[spend_cap.try_reserve_usdc(_WALLET, per_charge, charge_id=f"concurrent-{i}") for i in range(25)]
    )
    assert sum(results) == 9
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("9")


# ── release_reservation ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_release_reservation_removes_the_entry():
    await spend_cap.try_reserve_usdc(_WALLET, _raw("4"), charge_id="c1")
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("4")
    await spend_cap.release_reservation(_WALLET, "c1", _raw("4"))
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal(0)


@pytest.mark.asyncio
async def test_release_reservation_only_removes_the_matching_entry():
    await spend_cap.try_reserve_usdc(_WALLET, _raw("4"), charge_id="c1")
    await spend_cap.try_reserve_usdc(_WALLET, _raw("3"), charge_id="c2")
    await spend_cap.release_reservation(_WALLET, "c1", _raw("4"))
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("3")


@pytest.mark.asyncio
async def test_release_reservation_zero_or_negative_amount_is_noop():
    await spend_cap.release_reservation(_WALLET, "c1", 0)  # must not raise or touch Redis
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal(0)


@pytest.mark.asyncio
async def test_release_reservation_swallows_redis_error():
    """Best-effort — a Redis failure releasing a reservation must not raise,
    since payments.charge() has already failed by the time this runs and the
    caller has no further recourse."""
    with patch.object(AgentStateStore, "_get_redis", AsyncMock(side_effect=ConnectionError("redis down"))):
        await spend_cap.release_reservation(_WALLET, "c1", _raw("1"))  # must not raise


# ── window boundary: is_over_cap ────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_over_cap_false_when_under():
    await _seed(_WALLET, "c1", _raw("5"))
    assert await spend_cap.is_over_cap(_WALLET) is False


@pytest.mark.asyncio
async def test_is_over_cap_true_when_spend_exactly_meets_cap():
    await _seed(_WALLET, "c1", _raw("10"))
    assert await spend_cap.is_over_cap(_WALLET) is True


@pytest.mark.asyncio
async def test_is_over_cap_true_when_over():
    await _seed(_WALLET, "c1", _raw("15"))
    assert await spend_cap.is_over_cap(_WALLET) is True


@pytest.mark.asyncio
async def test_is_over_cap_unaffected_by_corrupted_empty_member(_fake_store):
    """Regression test for a Copilot review finding on #1099: is_over_cap's
    internal member is always "" (it never reserves), so the read-only path
    must never let a stray empty-string member in the set (corrupted data,
    or some future bug) short-circuit the projection. Before the fix, the
    Lua script ran ZSCORE key "" unconditionally, so a real "" member here
    would have made it wrongly treat additional_amount_raw as "already
    counted" and drop it from the projection — undercounting exactly the
    hypothetical amount the caller asked about.
    """
    key = spend_cap._key(_WALLET)
    await _fake_store._redis.zadd(key, {"": time.time()})  # simulated corrupted entry
    # No real spend recorded — only the additional_amount_raw (== the pinned
    # cap) should decide this. The old, buggy script would have dropped it
    # entirely and returned False here.
    assert await spend_cap.is_over_cap(_WALLET, additional_amount_raw=_raw("10")) is True


@pytest.mark.asyncio
async def test_is_over_cap_considers_pending_additional_amount():
    """additional_amount_raw models a charge not yet reserved — current spend
    alone is under cap, but current + pending crosses it."""
    await _seed(_WALLET, "c1", _raw("8"))
    assert await spend_cap.is_over_cap(_WALLET, additional_amount_raw=_raw("1")) is False
    assert await spend_cap.is_over_cap(_WALLET, additional_amount_raw=_raw("2")) is True


@pytest.mark.asyncio
async def test_is_over_cap_never_reserves_anything():
    """is_over_cap is read-only regardless of additional_amount_raw — unlike
    try_reserve_usdc, a "would this fit" probe must never itself count."""
    assert await spend_cap.is_over_cap(_WALLET, additional_amount_raw=_raw("7")) is False
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal(0)


@pytest.mark.asyncio
async def test_is_over_cap_disabled_when_cap_is_zero(monkeypatch):
    monkeypatch.setenv("MARKETPLACE_SPEND_CAP_USDC", "0")
    await _seed(_WALLET, "c1", _raw("999999"))
    assert await spend_cap.is_over_cap(_WALLET) is False


@pytest.mark.asyncio
async def test_is_over_cap_new_subscription_check_uses_no_additional():
    """No additional_amount_raw checks current standing only — the shape
    subscribe_strategy uses to gate a brand-new subscription (#713)."""
    await _seed(_WALLET, "c1", _raw("9.99"))
    assert await spend_cap.is_over_cap(_WALLET) is False
    await _seed(_WALLET, "c2", _raw("0.01"))
    assert await spend_cap.is_over_cap(_WALLET) is True


# ── rolling window: entries age out after 24h ───────────────────────────


@pytest.mark.asyncio
async def test_old_entry_outside_window_does_not_count(_fake_store):
    """An entry older than the 24h window must not count toward current
    spend. Seeded directly with a controlled score so the test doesn't need
    a real 24h wait."""
    old_ts = time.time() - (25 * 60 * 60)  # 25h ago — just outside the window
    key = spend_cap._key(_WALLET)
    stale_amount_raw = _raw("100")  # would blow past the cap if it counted
    await _fake_store._redis.zadd(key, {f"old_charge:{stale_amount_raw}": old_ts})

    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal(0)
    assert await spend_cap.is_over_cap(_WALLET) is False

    await _seed(_WALLET, "new_charge", _raw("3"))
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("3")


@pytest.mark.asyncio
async def test_entry_just_inside_window_still_counts(_fake_store):
    """An entry from 23h59m ago (inside the 24h window) still counts."""
    recent_ts = time.time() - (23 * 60 * 60 + 59 * 60)  # 23h59m ago
    key = spend_cap._key(_WALLET)
    await _fake_store._redis.zadd(key, {f"recent_charge:{_raw('7')}": recent_ts})
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("7")


@pytest.mark.asyncio
async def test_malformed_member_is_skipped_not_fatal(_fake_store):
    """A member string that doesn't parse as '<charge_id>:<amount_raw>' must
    be skipped, not blow up the whole read — defensive, "should not happen"
    per the module's own comment, but still worth pinning."""
    key = spend_cap._key(_WALLET)
    now = time.time()
    await _fake_store._redis.zadd(key, {"totally-malformed-no-amount": now})
    await _seed(_WALLET, "good", _raw("2"))
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("2")


@pytest.mark.asyncio
async def test_mixed_old_and_new_entries_only_new_counts(_fake_store):
    """A wallet with both a stale (>24h) and a live entry sums only the live one."""
    old_ts = time.time() - (30 * 60 * 60)
    key = spend_cap._key(_WALLET)
    await _fake_store._redis.zadd(key, {f"stale:{_raw('40')}": old_ts})
    await _seed(_WALLET, "live", _raw("6"))
    assert await spend_cap.get_24h_spend_usdc(_WALLET) == Decimal("6")
    # Cap is pinned to 10 by the autouse fixture — 6 alone must not trip it.
    assert await spend_cap.is_over_cap(_WALLET) is False


# ── fail-open on Redis error ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_over_cap_fails_open_on_redis_error():
    """A Redis read failure must never block a charge — is_over_cap degrades
    to False (allow), the documented fail-open contract: this is an additive
    safety guard, not the sole backstop against overcharging."""
    with patch.object(AgentStateStore, "_get_redis", AsyncMock(side_effect=ConnectionError("redis down"))):
        assert await spend_cap.is_over_cap(_WALLET) is False


@pytest.mark.asyncio
async def test_is_over_cap_fails_open_with_pending_amount_on_redis_error():
    with patch.object(AgentStateStore, "_get_redis", AsyncMock(side_effect=ConnectionError("redis down"))):
        assert await spend_cap.is_over_cap(_WALLET, additional_amount_raw=_raw("999999")) is False


@pytest.mark.asyncio
async def test_try_reserve_fails_open_on_redis_error():
    """Mirrors is_over_cap's fail-open contract: a Redis failure must never
    block a charge from proceeding — this is an additive guard, not the sole
    backstop against overcharging."""
    with patch.object(AgentStateStore, "_get_redis", AsyncMock(side_effect=ConnectionError("redis down"))):
        assert await spend_cap.try_reserve_usdc(_WALLET, _raw("999999"), charge_id="c1") is True


@pytest.mark.asyncio
async def test_get_24h_spend_usdc_raises_on_redis_error():
    """get_24h_spend_usdc itself does not swallow errors — only is_over_cap's
    (and try_reserve_usdc's) caller-facing fail-open wrapper does. Pins the
    boundary so a future refactor can't accidentally silence errors two
    layers deep."""
    with (
        patch.object(AgentStateStore, "_get_redis", AsyncMock(side_effect=ConnectionError("redis down"))),
        pytest.raises(ConnectionError),
    ):
        await spend_cap.get_24h_spend_usdc(_WALLET)


# ── module-level store singleton ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_store_lazily_constructs_on_first_use():
    """With no store yet injected, _get_store() must construct one (and
    reuse it on a second call) rather than requiring a caller to seed it."""
    spend_cap._store = None
    store1 = spend_cap._get_store()
    assert isinstance(store1, AgentStateStore)
    assert spend_cap._get_store() is store1  # cached, not reconstructed

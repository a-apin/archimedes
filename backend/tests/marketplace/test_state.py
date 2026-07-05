"""Tests for MarketState Redis persistence (real fakeredis)."""
from __future__ import annotations

import pytest
from archimedes.marketplace.state import MarketState
from archimedes.services.redis_state import AgentStateStore


@pytest.fixture
def state():
    """MarketState backed by fakeredis (real in-memory Redis emulation)."""
    store = AgentStateStore()
    store._redis = __import__("fakeredis").FakeAsyncRedis(decode_responses=True)
    return MarketState(store=store)


@pytest.mark.asyncio
async def test_append_and_get_events(state: MarketState):
    await state.append_event("strat_a", {"type": "evaluation_step", "tick_id": "t1"})
    await state.append_event("strat_a", {"type": "rebalance", "tick_id": "t2", "action_count": 3})
    events = await state.get_events("strat_a", count=10)
    assert len(events) == 2
    assert events[0]["type"] == "rebalance"  # most recent first (LPUSH)
    assert events[1]["type"] == "evaluation_step"


@pytest.mark.asyncio
async def test_events_capped_at_200(state: MarketState):
    for i in range(250):
        await state.append_event("strat_b", {"type": "test", "i": i})
    events = await state.get_events("strat_b", count=300)
    assert len(events) == 200


@pytest.mark.asyncio
async def test_save_and_load_subscribers(state: MarketState):
    subs = {"sub_1": {"active": True}, "sub_2": {"active": False}}
    await state.save_subscribers("strat_a", subs)
    loaded = await state.load_subscribers("strat_a")
    assert loaded == subs


@pytest.mark.asyncio
async def test_load_subscribers_empty(state: MarketState):
    loaded = await state.load_subscribers("nonexistent")
    assert loaded == {}


@pytest.mark.asyncio
async def test_per_strategy_leader_lock(state: MarketState):
    """Each strategy gets its own independent lock (C2)."""
    # Acquire lock for strategy A — returns a token (str), not None
    token_a = await state.try_acquire_leader("strat_a", ttl_seconds=10)
    assert token_a is not None
    # Second acquire for same strategy should fail
    assert await state.try_acquire_leader("strat_a", ttl_seconds=10) is None
    # Different strategy can acquire independently
    token_b = await state.try_acquire_leader("strat_b", ttl_seconds=10)
    assert token_b is not None

    # Renew strategy A lock with the correct token
    await state.renew_leader("strat_a", token=token_a, ttl_seconds=10)
    # Still held after renew
    assert await state.try_acquire_leader("strat_a", ttl_seconds=10) is None

    # Release strategy A with the correct token
    await state.release_leader("strat_a", token=token_a)
    # Now can re-acquire
    token_a2 = await state.try_acquire_leader("strat_a", ttl_seconds=10)
    assert token_a2 is not None

    # Strategy B lock is unaffected by A's release
    assert await state.try_acquire_leader("strat_b", ttl_seconds=10) is None


@pytest.mark.asyncio
async def test_release_leader_rejects_wrong_token(state: MarketState):
    """Stale-owner release is a no-op."""
    token = await state.try_acquire_leader("strat_x", ttl_seconds=10)
    assert token is not None

    # A different token should NOT release the lock
    await state.release_leader("strat_x", token="wrong-token")
    # Lock still held
    assert await state.try_acquire_leader("strat_x", ttl_seconds=10) is None

    # Correct token does release
    await state.release_leader("strat_x", token=token)
    assert await state.try_acquire_leader("strat_x", ttl_seconds=10) is not None


@pytest.mark.asyncio
async def test_renew_leader_rejects_wrong_token(state: MarketState):
    """Stale-owner renew is a no-op."""
    token = await state.try_acquire_leader("strat_y", ttl_seconds=10)
    assert token is not None

    # A different token should NOT extend the lock
    await state.renew_leader("strat_y", token="wrong-token", ttl_seconds=10)
    # Lock still held by original owner
    assert await state.try_acquire_leader("strat_y", ttl_seconds=10) is None

    # Correct token extends
    await state.renew_leader("strat_y", token=token, ttl_seconds=10)
    assert await state.try_acquire_leader("strat_y", ttl_seconds=10) is None


# ---- x402 payment state tests ------------------------------------------


@pytest.mark.asyncio
async def test_save_and_get_payment(state: MarketState):
    """Round-trip a payment record through Redis."""
    await state.save_payment("0xsub1", {"paid": True, "amount_usdc_raw": 100_000})
    payment = await state.get_payment("0xsub1")
    assert payment is not None
    assert payment["paid"] is True
    assert payment["amount_usdc_raw"] == 100_000
    assert "recorded_at" in payment  # auto-added timestamp


@pytest.mark.asyncio
async def test_get_payment_nonexistent(state: MarketState):
    """Nonexistent payment returns None."""
    payment = await state.get_payment("0xnonexistent")
    assert payment is None


@pytest.mark.asyncio
async def test_has_active_payment_true(state: MarketState):
    """has_active_payment returns True for a recorded payment."""
    await state.save_payment("0xsub2", {"paid": True})
    assert await state.has_active_payment("0xsub2") is True


@pytest.mark.asyncio
async def test_has_active_payment_false_when_not_paid(state: MarketState):
    """has_active_payment returns False when payment exists but paid=False."""
    await state.save_payment("0xsub3", {"paid": False})
    assert await state.has_active_payment("0xsub3") is False


@pytest.mark.asyncio
async def test_has_active_payment_false_when_missing(state: MarketState):
    """has_active_payment returns False when no payment record exists."""
    assert await state.has_active_payment("0xnonexistent") is False


@pytest.mark.asyncio
async def test_delete_payment(state: MarketState):
    """Deleting a payment removes it from Redis."""
    await state.save_payment("0xsub4", {"paid": True})
    assert await state.has_active_payment("0xsub4") is True
    await state.delete_payment("0xsub4")
    assert await state.has_active_payment("0xsub4") is False


@pytest.mark.asyncio
async def test_payment_independent_per_subscriber(state: MarketState):
    """Each subscriber has an independent payment record."""
    await state.save_payment("0xsub_a", {"paid": True})
    await state.save_payment("0xsub_b", {"paid": False})
    assert await state.has_active_payment("0xsub_a") is True
    assert await state.has_active_payment("0xsub_b") is False

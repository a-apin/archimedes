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
async def test_leader_lock_acquire_and_renew(state: MarketState):
    assert await state.try_acquire_leader(ttl_seconds=10) is True
    # second acquire should fail (already held)
    assert await state.try_acquire_leader(ttl_seconds=10) is False
    # renew
    await state.renew_leader(ttl_seconds=10)
    # still held after renew
    assert await state.try_acquire_leader(ttl_seconds=10) is False

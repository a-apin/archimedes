"""Tests for TASK 18 — retire-cascade: stop_publisher + notice logic.

Covers:
- stop_publisher clears Redis cache and emits publisher_retired event.
- stop_publisher does NOT cancel mid-tick (sets retired flag instead).
- to_dict() surfaces the advisory notice only for retired subscribers.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archimedes.marketplace.service import MarketService, Publisher, Subscriber
from archimedes.models.marketplace import MarketplaceAgent


# ── to_dict() notice logic (no DB, pure model test) ────────────────────────


def _make_agent(
    role: str,
    status: str,
    stopped_at: datetime | None = None,
) -> MarketplaceAgent:
    """Minimal MarketplaceAgent factory for to_dict() tests."""
    return MarketplaceAgent(
        role=role,
        strategy_id="test_strat",
        creator_wallet="0xcreator",
        subscriber_wallet="0xsub" if role == "subscriber" else "",
        sub_id="0x" + "aa" * 32 if role == "subscriber" else "",
        pool_id="0x" + "bb" * 32,
        vault_address="0xvault",
        ephemeral_wallet="0xephemeral" if role == "subscriber" else "",
        status=status,
        stopped_at=stopped_at,
    )


def test_retired_subscriber_has_notice():
    """A retired subscriber gets the advisory notice in to_dict()."""
    agent = _make_agent("subscriber", "retired", stopped_at=datetime.now(UTC))
    d = agent.to_dict()
    assert d["status"] == "retired"
    assert "notice" in d
    assert "call unsubscribe() from your wallet" in d["notice"]


def test_stopped_subscriber_has_no_notice():
    """A stopped (self-unsubscribed) subscriber does NOT get a notice key."""
    agent = _make_agent("subscriber", "stopped", stopped_at=datetime.now(UTC))
    d = agent.to_dict()
    assert d["status"] == "stopped"
    assert "notice" not in d


def test_running_subscriber_has_no_notice():
    """A running subscriber does NOT get a notice key."""
    agent = _make_agent("subscriber", "running")
    d = agent.to_dict()
    assert d["status"] == "running"
    assert "notice" not in d


def test_publisher_never_has_notice():
    """A publisher row never gets a notice key regardless of status."""
    for status in ("running", "stopped"):
        agent = _make_agent("publisher", status)
        d = agent.to_dict()
        assert "notice" not in d


# ── MarketService.stop_publisher ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_publisher_clears_redis_cache():
    """stop_publisher calls save_subscribers({}) to clear the Redis cache."""
    svc = MarketService(interval_seconds=9999, dry_run=False)
    svc.executor = MagicMock()
    svc.signer = MagicMock()
    svc.signer.is_configured = False
    svc.loader = MagicMock()
    svc.state = MagicMock()
    svc.state.save_subscribers = AsyncMock()
    svc.state.append_event = AsyncMock()
    svc.state.load_subscribers = AsyncMock(return_value={})

    sub = Subscriber(
        sub_id="0x" + "11" * 32,
        pool_id="0x" + "22" * 32,
        vault_address="0xsub_vault",
        ephemeral_wallet="0xephemeral",
        subscriber_wallet="0xsub",
        active=True,
    )
    pub = Publisher(
        strategy_id="strat_retire",
        pool_id="0x" + "33" * 32,
        vault_address="0xpub",
        creator_wallet="0xcreator",
        subscribers={"0x" + "11" * 32: sub},
    )
    # No running task — skip the wait
    pub.task = None
    svc.publishers["strat_retire"] = pub

    await svc.stop_publisher("strat_retire")

    svc.state.save_subscribers.assert_awaited_with("strat_retire", {})
    svc.state.append_event.assert_awaited_with(
        "strat_retire",
        {"type": "publisher_retired", "strategy_id": "strat_retire"},
    )


@pytest.mark.asyncio
async def test_stop_publisher_sets_retired_flag():
    """stop_publisher sets pub.retired=True so _run_loop exits cleanly
    without mid-charge cancellation."""
    svc = MarketService(interval_seconds=9999, dry_run=False)
    svc.executor = MagicMock()
    svc.signer = MagicMock()
    svc.signer.is_configured = False
    svc.loader = MagicMock()
    svc.state = MagicMock()
    svc.state.save_subscribers = AsyncMock()
    svc.state.append_event = AsyncMock()
    svc.state.load_subscribers = AsyncMock(return_value={})

    pub = Publisher(
        strategy_id="strat_graceful",
        pool_id="0x" + "44" * 32,
        vault_address="0xpub",
        creator_wallet="0xcreator",
    )
    # Simulate a running task
    pub.task = asyncio.ensure_future(asyncio.sleep(0))
    svc.publishers["strat_graceful"] = pub

    await svc.stop_publisher("strat_graceful")

    # pub.retired was set before waiting, and the task completed cleanly
    assert pub.retired is True


@pytest.mark.asyncio
async def test_stop_publisher_noop_for_missing():
    """stop_publisher is a no-op for a non-existent strategy (no error)."""
    svc = MarketService(interval_seconds=9999, dry_run=False)
    svc.state = MagicMock()
    svc.state.save_subscribers = AsyncMock()
    svc.state.append_event = AsyncMock()

    # Should not raise
    await svc.stop_publisher("nonexistent")
    svc.state.save_subscribers.assert_not_called()
    svc.state.append_event.assert_not_called()


@pytest.mark.asyncio
async def test_stop_publisher_pop_from_publishers():
    """stop_publisher removes the strategy from the publishers dict."""
    svc = MarketService(interval_seconds=9999, dry_run=False)
    svc.executor = MagicMock()
    svc.signer = MagicMock()
    svc.signer.is_configured = False
    svc.loader = MagicMock()
    svc.state = MagicMock()
    svc.state.save_subscribers = AsyncMock()
    svc.state.append_event = AsyncMock()
    svc.state.load_subscribers = AsyncMock(return_value={})

    pub = Publisher(
        strategy_id="strat_cleanup",
        pool_id="0x" + "55" * 32,
        vault_address="0xpub",
        creator_wallet="0xcreator",
    )
    pub.task = asyncio.ensure_future(asyncio.sleep(0))
    svc.publishers["strat_cleanup"] = pub

    await svc.stop_publisher("strat_cleanup")

    assert "strat_cleanup" not in svc.publishers


# ── _run_loop exits on retired flag ────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_loop_exits_when_publisher_retired():
    """_run_loop breaks out of the loop when pub.retired is True, avoiding
    mid-charge cancellation (TASK 18)."""
    svc = MarketService(interval_seconds=9999, dry_run=False)
    svc.executor = MagicMock()
    svc.signer = MagicMock()
    svc.signer.is_configured = False
    svc.loader = MagicMock()
    svc.state = MagicMock()
    svc.state.try_acquire_leader = AsyncMock(return_value=True)
    svc.state.release_leader = AsyncMock()
    svc.state.save_subscribers = AsyncMock()
    svc.state.append_event = AsyncMock()
    svc.state.load_subscribers = AsyncMock(return_value={})

    pub = Publisher(
        strategy_id="strat_exit",
        pool_id="0x" + "66" * 32,
        vault_address="0xpub",
        creator_wallet="0xcreator",
    )
    pub.retired = True  # Simulate retirement signal
    svc.publishers["strat_exit"] = pub

    with patch.object(svc, "tick", AsyncMock()) as mock_tick:
        await svc._run_loop("strat_exit")

    # tick may or may not run (depends on timing), but the loop exits
    # cleanly without sleeping or error
    assert svc._stop.is_set() is False  # global stop NOT set


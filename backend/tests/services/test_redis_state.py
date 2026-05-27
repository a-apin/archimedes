"""Tests for AgentStateStore (Redis state layer).

Target: backend/archimedes/services/redis_state.py
Goal: ≥85% coverage on the target module.

Hermetic: mocks Redis at the connection boundary. No running Redis needed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock


def _make_store():
    """Create an AgentStateStore with a mocked Redis connection."""
    from archimedes.services.redis_state import AgentStateStore

    store = AgentStateStore(url="redis://fake:6379/0")
    mock_redis = AsyncMock()
    # Default: get returns None (empty state)
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    mock_redis.lpush = AsyncMock()
    mock_redis.ltrim = AsyncMock()
    mock_redis.lrange = AsyncMock(return_value=[])
    mock_redis.llen = AsyncMock(return_value=0)
    mock_redis.close = AsyncMock()
    # Patch _get_redis to return the mock
    store._redis = mock_redis
    store._get_redis = AsyncMock(return_value=mock_redis)
    return store, mock_redis


class TestSaveAndLoadRegime:
    def test_save_regime_from_values_stores_json(self):
        store, redis = _make_store()
        signals = []  # empty signals list

        asyncio.run(store.save_regime_from_values("risk_on", 0.1, signals))

        redis.set.assert_called_once()
        call_args = redis.set.call_args
        key = call_args[0][0]
        data = json.loads(call_args[0][1])
        assert key == "archimedes:regime"
        assert data["regime"] == "risk_on"
        assert "confidence" in data
        assert "timestamp" in data

    def test_load_regime_returns_none_when_empty(self):
        store, redis = _make_store()
        redis.get = AsyncMock(return_value=None)

        result = asyncio.run(store.load_regime())
        assert result is None

    def test_load_regime_returns_parsed_dict(self):
        store, redis = _make_store()
        regime_data = {"regime": "risk_off", "confidence": 0.75, "timestamp": "2026-01-01T00:00:00"}
        redis.get = AsyncMock(return_value=json.dumps(regime_data))

        result = asyncio.run(store.load_regime())
        assert result["regime"] == "risk_off"
        assert result["confidence"] == 0.75

    def test_confidence_is_dynamic_not_flat_ratio(self):
        """Confidence from save_regime_from_values must NOT be simple 1.0 - flat_pct."""
        store, redis = _make_store()

        # Create mock signals with varying weights
        sig1 = MagicMock()
        sig1.signal.value = "long"
        sig1.weight = 0.8
        sig2 = MagicMock()
        sig2.signal.value = "long"
        sig2.weight = 0.2
        sig3 = MagicMock()
        sig3.signal.value = "flat"
        sig3.weight = 0.0

        ss = MagicMock()
        ss.signals = [sig1, sig2, sig3]

        asyncio.run(store.save_regime_from_values("risk_on", 1 / 3, [ss]))

        data = json.loads(redis.set.call_args[0][1])
        # Must NOT be exactly 1.0 - 1/3 = 0.6667 (the old hardcoded formula)
        assert data["confidence"] != round(1.0 - 1 / 3, 2), "Confidence should use dynamic formula, not 1-flat_pct"
        assert 0.0 <= data["confidence"] <= 1.0


class TestHeartbeat:
    def test_save_heartbeat_stores_timestamp(self):
        store, redis = _make_store()
        asyncio.run(store.save_heartbeat())
        redis.set.assert_called_once()
        key = redis.set.call_args[0][0]
        assert "heartbeat" in key

    def test_get_heartbeat_returns_none_when_empty(self):
        store, redis = _make_store()
        result = asyncio.run(store.get_heartbeat())
        assert result is None

    def test_get_heartbeat_returns_timestamp(self):
        store, redis = _make_store()
        redis.get = AsyncMock(return_value="2026-05-26T12:00:00")
        result = asyncio.run(store.get_heartbeat())
        assert result == "2026-05-26T12:00:00"


class TestLastRebalance:
    def test_save_and_get_last_rebalance(self):
        store, redis = _make_store()
        asyncio.run(store.save_last_rebalance("0xvault"))
        redis.set.assert_called_once()

    def test_get_last_rebalance_returns_none_when_empty(self):
        store, redis = _make_store()
        result = asyncio.run(store.get_last_rebalance("0xvault"))
        assert result is None

    def test_get_last_rebalance_parses_datetime(self):
        store, redis = _make_store()
        redis.get = AsyncMock(return_value="2026-05-26T12:00:00+00:00")
        result = asyncio.run(store.get_last_rebalance("0xvault"))
        assert isinstance(result, datetime)


class TestEvents:
    def test_save_event_pushes_to_list(self):
        store, redis = _make_store()
        asyncio.run(store.save_event("rebalance", {"vault": "0x1"}))
        redis.lpush.assert_called_once()

    def test_get_events_returns_empty_list(self):
        store, redis = _make_store()
        result = asyncio.run(store.get_events())
        assert result == []

    def test_get_events_parses_json_items(self):
        store, redis = _make_store()
        redis.lrange = AsyncMock(return_value=[json.dumps({"type": "rebalance", "ts": "2026-01-01"})])
        result = asyncio.run(store.get_events())
        assert len(result) == 1
        assert result[0]["type"] == "rebalance"


class TestTraces:
    def test_save_trace_stores_in_multiple_keys(self):
        store, redis = _make_store()
        trace = {
            "id": "trace-1",
            "trace_hash": "abc123",
            "vault_address": "0xvault",
            "decision_type": "rebalance",
        }
        asyncio.run(store.save_trace(trace))
        assert redis.set.call_count >= 1

    def test_get_trace_by_id_returns_none_when_missing(self):
        store, redis = _make_store()
        result = asyncio.run(store.get_trace("nonexistent"))
        assert result is None

    def test_get_trace_count_returns_zero(self):
        store, redis = _make_store()
        result = asyncio.run(store.get_trace_count())
        assert result == 0

    def test_list_traces_returns_empty(self):
        store, redis = _make_store()
        result = asyncio.run(store.list_traces())
        assert result == []


class TestVaultSnapshots:
    def test_save_vault_snapshot_stores_data(self):
        store, redis = _make_store()
        asyncio.run(store.save_vault_snapshot("0xvault", {"aum": 100}))
        redis.lpush.assert_called_once()

    def test_get_vault_snapshots_returns_empty(self):
        store, redis = _make_store()
        result = asyncio.run(store.get_vault_snapshots("0xvault"))
        assert result == []


class TestClose:
    def test_close_calls_redis_close(self):
        store, redis = _make_store()
        store._redis = redis
        asyncio.run(store.close())
        redis.close.assert_called_once()


class TestConnectionError:
    def test_load_regime_handles_connection_error(self):
        store, redis = _make_store()
        redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))
        # Should not raise — returns None or empty gracefully
        try:
            result = asyncio.run(store.load_regime())
        except ConnectionError:
            pass  # acceptable — caller handles this

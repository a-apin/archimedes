"""Unit coverage for AgentStateStore — Redis is mocked via a fake client.

Verifies the key-shape conventions, JSON serialization, sorted-set indexing,
and the list/get/save trace flows. No real Redis connection.

Added 2026-05-24 as part of the #147 coverage-gate lift.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from archimedes.models.regime import (
    ConsensusLabel,
    EnsembleConsensus,
    Regime,
    RegimeClassification,
    RegimeSignals,
)
from archimedes.services.redis_state import (
    KEY_ENSEMBLE_CONSENSUS,
    KEY_HEARTBEAT,
    KEY_LAST_REBALANCE_PREFIX,
    KEY_REGIME,
    KEY_SIWE_NONCE_PREFIX,
    KEY_TRACE_INDEX,
    AgentStateStore,
)


def _fake_redis() -> MagicMock:
    """Build an AsyncMock-flavored Redis client."""
    r = MagicMock()
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock()
    r.setex = AsyncMock()
    r.getdel = AsyncMock(return_value=None)
    r.lpush = AsyncMock()
    r.ltrim = AsyncMock()
    r.lrange = AsyncMock(return_value=[])
    r.zadd = AsyncMock()
    r.zrevrange = AsyncMock(return_value=[])
    r.zcard = AsyncMock(return_value=0)
    r.aclose = AsyncMock()
    return r


async def _store_with_fake_redis() -> tuple[AgentStateStore, MagicMock]:
    """Bind a fake Redis to a fresh AgentStateStore."""
    store = AgentStateStore(url="redis://fake/")
    fake = _fake_redis()
    store._redis = fake  # bypass _get_redis lazy init
    return store, fake


def _classification(regime: Regime = Regime.RISK_ON, confidence: float = 0.8) -> RegimeClassification:
    return RegimeClassification(
        regime=regime,
        confidence=confidence,
        signals=RegimeSignals(
            vix_level=15.0,
            vix_rate_of_change=0.0,
            sp500_above_ma50=True,
            sp500_above_ma200=True,
        ),
        timestamp=datetime.now(UTC),
    )


class TestRegime:
    @pytest.mark.asyncio
    async def test_save_regime_writes_canonical_json(self) -> None:
        store, fake = await _store_with_fake_redis()
        await store.save_regime(_classification())
        fake.set.assert_awaited_once()
        key, value = fake.set.await_args.args
        assert key == KEY_REGIME
        payload = json.loads(value)
        assert payload["regime"] == Regime.RISK_ON.value
        assert payload["confidence"] == 0.8

    @pytest.mark.asyncio
    async def test_save_ensemble_consensus_writes_to_distinct_key(self) -> None:
        """Ensemble consensus is persisted under its OWN key, not KEY_REGIME (#659)."""
        store, fake = await _store_with_fake_redis()
        signal_summary = MagicMock()
        signal_summary.signals = []
        signal_summary.paper_title = "Faber 2007"
        consensus = EnsembleConsensus.from_signal_counts(flat_count=2, total_count=5)
        await store.save_ensemble_consensus(consensus, [signal_summary])
        fake.set.assert_awaited_once()
        key, value = fake.set.await_args.args
        # Critical: must NOT shadow the market-regime key.
        assert key == KEY_ENSEMBLE_CONSENSUS
        assert key != KEY_REGIME
        payload = json.loads(value)
        # The persisted payload carries a "label" (consensus bucket), not a
        # "regime" — the consensus is not a market regime.
        assert payload["label"] == ConsensusLabel.TRANSITION.value
        assert "regime" not in payload
        assert payload["flat_pct"] == 0.4
        assert payload["strategy_count"] == 5
        # Dynamic confidence: with flat_pct=0.4 and the empty-signals fixture:
        #   vote_ratio = 1.0 - 0.4 = 0.6 ; avg_strength = 0.0 ; dispersion = 0.0
        #   dyn = clamp(0.6 * (0.5 + 0.5 * 0.0) - 0.0, 0.05, 0.99) = 0.3
        assert payload["confidence"] == 0.3
        assert payload["source"] == "strategy_consensus"

    @pytest.mark.asyncio
    async def test_load_regime_returns_none_when_missing(self) -> None:
        store, _ = await _store_with_fake_redis()
        assert await store.load_regime() is None

    @pytest.mark.asyncio
    async def test_load_regime_parses_json(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.get.return_value = json.dumps({"regime": "risk_on", "confidence": 0.9})
        loaded = await store.load_regime()
        assert loaded["regime"] == "risk_on"

    @pytest.mark.asyncio
    async def test_load_ensemble_consensus_returns_none_when_missing(self) -> None:
        store, _ = await _store_with_fake_redis()
        assert await store.load_ensemble_consensus() is None

    @pytest.mark.asyncio
    async def test_load_ensemble_consensus_parses_json(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.get.return_value = json.dumps({"label": "risk_off", "confidence": 0.2, "flat_pct": 0.8})
        loaded = await store.load_ensemble_consensus()
        assert loaded["label"] == "risk_off"
        assert loaded["flat_pct"] == 0.8


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_save_heartbeat_writes_iso_timestamp(self) -> None:
        store, fake = await _store_with_fake_redis()
        await store.save_heartbeat()
        key, ts = fake.set.await_args.args
        assert key == KEY_HEARTBEAT
        # ISO format roundtrips
        datetime.fromisoformat(ts)

    @pytest.mark.asyncio
    async def test_get_heartbeat_returns_raw_string(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.get.return_value = "2026-05-24T12:00:00+00:00"
        assert await store.get_heartbeat() == "2026-05-24T12:00:00+00:00"


class TestLastRebalance:
    @pytest.mark.asyncio
    async def test_save_uses_lowercased_vault_key(self) -> None:
        store, fake = await _store_with_fake_redis()
        await store.save_last_rebalance("0xV-UPPER")
        key = fake.set.await_args.args[0]
        assert key == f"{KEY_LAST_REBALANCE_PREFIX}0xv-upper"

    @pytest.mark.asyncio
    async def test_get_returns_parsed_datetime(self) -> None:
        store, fake = await _store_with_fake_redis()
        ts = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        fake.get.return_value = ts
        result = await store.get_last_rebalance("0xV")
        assert isinstance(result, datetime)

    @pytest.mark.asyncio
    async def test_get_returns_none_when_missing(self) -> None:
        store, _ = await _store_with_fake_redis()
        assert await store.get_last_rebalance("0xV") is None


class TestSiweNonces:
    @pytest.mark.asyncio
    async def test_save_nonce_setex_with_ttl(self) -> None:
        store, fake = await _store_with_fake_redis()
        await store.save_nonce("abc123", 300)
        fake.setex.assert_awaited_once_with(f"{KEY_SIWE_NONCE_PREFIX}abc123", 300, "1")

    @pytest.mark.asyncio
    async def test_pop_nonce_present_returns_true(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.getdel.return_value = "1"
        assert await store.pop_nonce("abc123") is True
        fake.getdel.assert_awaited_once_with(f"{KEY_SIWE_NONCE_PREFIX}abc123")

    @pytest.mark.asyncio
    async def test_pop_nonce_missing_returns_false(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.getdel.return_value = None
        assert await store.pop_nonce("abc123") is False

    @pytest.mark.asyncio
    async def test_pop_nonce_is_single_use(self) -> None:
        """GETDEL semantics: a second pop for the same key returns False
        once Redis has deleted it (simulated here via side_effect)."""
        store, fake = await _store_with_fake_redis()
        fake.getdel = AsyncMock(side_effect=["1", None])
        assert await store.pop_nonce("abc123") is True
        assert await store.pop_nonce("abc123") is False


class TestEvents:
    @pytest.mark.asyncio
    async def test_save_event_lpushes_and_trims_to_100(self) -> None:
        store, fake = await _store_with_fake_redis()
        await store.save_event("rebalance", {"vault": "0xV"})
        fake.lpush.assert_awaited_once()
        fake.ltrim.assert_awaited_once_with("archimedes:agent:events", 0, 99)
        # JSON payload includes type + data + timestamp
        payload = json.loads(fake.lpush.await_args.args[1])
        assert payload["type"] == "rebalance"
        assert payload["data"] == {"vault": "0xV"}

    @pytest.mark.asyncio
    async def test_get_events_parses_each_entry(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.lrange.return_value = [
            json.dumps({"type": "a", "data": {}, "timestamp": "x"}),
            json.dumps({"type": "b", "data": {}, "timestamp": "y"}),
        ]
        events = await store.get_events(count=2)
        assert [e["type"] for e in events] == ["a", "b"]


class TestVaultSnapshots:
    @pytest.mark.asyncio
    async def test_save_lpushes_and_trims_to_288(self) -> None:
        store, fake = await _store_with_fake_redis()
        await store.save_vault_snapshot("0xV", {"aum_usdc": 1000})
        fake.lpush.assert_awaited_once()
        # 287 inclusive → keeps 288 entries
        fake.ltrim.assert_awaited_once_with("archimedes:vault:snapshots:0xv", 0, 287)

    @pytest.mark.asyncio
    async def test_get_returns_decoded_snapshots(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.lrange.return_value = [json.dumps({"aum_usdc": 1100})]
        snaps = await store.get_vault_snapshots("0xV", count=5)
        assert snaps == [{"aum_usdc": 1100}]


class TestTraces:
    @pytest.mark.asyncio
    async def test_save_trace_without_hash_is_silently_dropped(self) -> None:
        store, fake = await _store_with_fake_redis()
        await store.save_trace({"id": "abc", "decision_type": "rebalance"})
        fake.set.assert_not_awaited()
        fake.zadd.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_trace_writes_hash_uuid_and_index(self) -> None:
        store, fake = await _store_with_fake_redis()
        trace = {
            "id": "uuid-1",
            "trace_hash": "0xhash",
            "timestamp": "2026-05-24T12:00:00+00:00",
            "vault_address": "0xV",
        }
        await store.save_trace(trace)
        # 2 sets: hash → data, id → hash
        assert fake.set.await_count == 2
        fake.zadd.assert_awaited_once()
        zadd_key, mapping = fake.zadd.await_args.args
        assert zadd_key == KEY_TRACE_INDEX
        assert "0xhash" in mapping

    @pytest.mark.asyncio
    async def test_save_trace_malformed_timestamp_uses_now_score(self) -> None:
        store, fake = await _store_with_fake_redis()
        await store.save_trace({"id": "x", "trace_hash": "0xh", "timestamp": "not-iso"})
        fake.zadd.assert_awaited_once()  # still indexed, just with current ts

    @pytest.mark.asyncio
    async def test_get_trace_direct_hash_hit(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.get = AsyncMock(side_effect=[json.dumps({"trace_hash": "0xh"})])
        result = await store.get_trace("0xh")
        assert result == {"trace_hash": "0xh"}

    @pytest.mark.asyncio
    async def test_get_trace_uuid_lookup_chain(self) -> None:
        store, fake = await _store_with_fake_redis()
        # First .get (hash key) misses; then UUID → hash; then hash → data
        fake.get = AsyncMock(side_effect=[None, "0xhash", json.dumps({"trace_hash": "0xhash"})])
        result = await store.get_trace("uuid-1")
        assert result == {"trace_hash": "0xhash"}

    @pytest.mark.asyncio
    async def test_get_trace_returns_none_when_missing(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.get = AsyncMock(return_value=None)
        assert await store.get_trace("missing") is None

    @pytest.mark.asyncio
    async def test_list_traces_filters_vault_and_decision(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.zrevrange.return_value = ["h1", "h2", "h3"]
        traces = [
            {"vault_address": "0xV", "decision_type": "rebalance"},
            {"vault_address": "0xV", "decision_type": "skip"},
            {"vault_address": "0xOTHER", "decision_type": "rebalance"},
        ]
        fake.get = AsyncMock(side_effect=[json.dumps(t) for t in traces])
        window, total = await store.list_traces(vault_address="0xV", decision_type="rebalance")
        assert total == 1
        assert window[0]["decision_type"] == "rebalance"

    @pytest.mark.asyncio
    async def test_list_recent_traces_bounds_the_range_at_the_index(self) -> None:
        """#1276: the tick's reconciliation scan must be O(limit), not O(history)
        — the zrevrange itself carries the bound, unlike list_traces."""
        store, fake = await _store_with_fake_redis()
        fake.zrevrange.return_value = ["h1", "h2"]
        fake.get = AsyncMock(side_effect=[json.dumps({"id": "t1"}), json.dumps({"id": "t2"})])

        traces = await store.list_recent_traces(limit=2)

        assert [t["id"] for t in traces] == ["t1", "t2"]
        key, start, stop = fake.zrevrange.await_args.args
        assert (key, start, stop) == (KEY_TRACE_INDEX, 0, 1)  # inclusive stop => exactly 2

    @pytest.mark.asyncio
    async def test_list_recent_traces_skips_missing_and_malformed_entries(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.zrevrange.return_value = ["h1", "h2", "h3"]
        fake.get = AsyncMock(side_effect=[None, "{not json", json.dumps({"id": "t3"})])

        traces = await store.list_recent_traces(limit=3)

        assert [t["id"] for t in traces] == ["t3"]  # a corrupt entry never breaks the scan

    @pytest.mark.asyncio
    async def test_get_last_trace_returns_first(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.zrevrange.return_value = ["h1"]
        fake.get = AsyncMock(side_effect=[json.dumps({"vault_address": "0xV", "id": "t1"})])
        result = await store.get_last_trace("0xV")
        assert result["id"] == "t1"

    @pytest.mark.asyncio
    async def test_get_last_trace_returns_none_when_empty(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.zrevrange.return_value = []
        assert await store.get_last_trace("0xV") is None

    @pytest.mark.asyncio
    async def test_get_trace_count(self) -> None:
        store, fake = await _store_with_fake_redis()
        fake.zcard.return_value = 42
        assert await store.get_trace_count() == 42


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_close_clears_singleton(self) -> None:
        store, fake = await _store_with_fake_redis()
        await store.close()
        fake.aclose.assert_awaited_once()
        assert store._redis is None

    @pytest.mark.asyncio
    async def test_close_no_op_when_never_opened(self) -> None:
        store = AgentStateStore(url="redis://fake/")
        # _redis stays None; close should be a clean no-op
        await store.close()

"""Agent-tick commit-reveal wiring + claim-integrity tests (#714 / T0.3).

Hermetic: the chain client, executor, trace_publisher, IPFS pin, provider, and state
store are all mocked at the boundary (mirrors ``test_agent_runner.py``'s runner fixture).

Covers:
  - the reveal phase uses the real ``trace_publisher.reveal()`` (NOT publishTrace) when a
    commit-reveal trace_id exists, and gracefully falls back to ``publish()`` when it does
    not (pre-v1.5 registry, #588 still open);
  - ``temporal_binding_source`` is "chain" ONLY on the real commit-reveal path, and the
    persisted ``temporal_binding_valid`` requires commit < trade <= reveal block ordering;
  - the TraceResponse schema guard can never surface a True binding without a chain source
    (closes AUDIT_2026-06-14 #3 — the "Temporal Binding VERIFIED" badge off a Redis bool).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.models.trace import DecisionType, ReasoningTrace


def _make_trace() -> ReasoningTrace:
    trace = ReasoningTrace(
        id="tick-trace-001",
        vault_address="0x1234567890abcdef1234567890abcdef12345678",
        decision_type=DecisionType.REBALANCE,
        trigger="strategy_signal_drift",
        timestamp=datetime.now(UTC),
        reasoning="Agent tick commit-reveal test",
        confidence=0.9,
    )
    trace.compute_hash()
    return trace


@pytest.fixture()
def runner_env():
    """A StrategyRunner with all chain boundaries mocked and the on-chain path armed."""
    with (
        patch("archimedes.chain.agent_runner.chain_client"),
        patch("archimedes.chain.agent_runner.chain_executor"),
        patch("archimedes.chain.agent_runner.trace_publisher") as mock_tp,
        patch("archimedes.chain.agent_runner.default_provider"),
        patch("archimedes.chain.agent_runner.AgentStateStore"),
        patch("archimedes.chain.agent_runner.pin_public_provenance", new=AsyncMock(return_value=(None, None))),
        patch("archimedes.chain.agent_runner.DRY_RUN", False),
    ):
        from archimedes.chain.agent_runner import StrategyRunner

        runner = StrategyRunner()
        runner.state = MagicMock()
        runner.state.save_trace = AsyncMock()
        yield runner, mock_tp


def _saved(runner) -> dict:
    return runner.state.save_trace.call_args[0][0]


class TestRevealWiring:
    def test_reveal_uses_commit_reveal_not_publish(self, runner_env):
        runner, mock_tp = runner_env
        mock_tp.supports_commit_reveal = MagicMock(return_value=True)
        mock_tp.reveal = AsyncMock(return_value=("0xREVEAL", 102))
        mock_tp.publish = AsyncMock(return_value=None)

        asyncio.run(
            runner._reveal_trace(
                _make_trace(),
                trace_id=42,
                tick_id="t1",
                tx_hashes=["0xtrade"],
                commit_tx="0xcommit",
                commit_block=100,
                trade_block=101,
            )
        )

        mock_tp.reveal.assert_called_once()
        mock_tp.publish.assert_not_called()  # the live path is commit-reveal, never publishTrace

    def test_reveal_falls_back_to_publish_without_trace_id(self, runner_env):
        runner, mock_tp = runner_env
        mock_tp.supports_commit_reveal = MagicMock(return_value=True)
        mock_tp.reveal = AsyncMock(return_value=("0xREVEAL", 102))
        mock_tp.publish = AsyncMock(return_value=None)

        # trace_id None => pre-v1.5 registry path => graceful publishTrace fallback (#588).
        asyncio.run(runner._reveal_trace(_make_trace(), trace_id=None, tick_id="t1", tx_hashes=["0xtrade"]))

        mock_tp.publish.assert_called_once()
        mock_tp.reveal.assert_not_called()


class TestTemporalBindingPersistence:
    def test_source_chain_and_valid_on_real_commit_reveal(self, runner_env):
        runner, mock_tp = runner_env
        mock_tp.supports_commit_reveal = MagicMock(return_value=True)
        mock_tp.reveal = AsyncMock(return_value=("0xREVEAL", 102))
        mock_tp.publish = AsyncMock(return_value=None)

        asyncio.run(
            runner._reveal_trace(
                _make_trace(),
                trace_id=42,
                tick_id="t1",
                tx_hashes=["0xtrade"],
                commit_tx="0xcommit",
                commit_block=100,
                trade_block=101,
            )
        )

        saved = _saved(runner)
        assert saved["temporal_binding_source"] == "chain"
        # commit(100) < trade(101) <= reveal(102) -> a genuine, verified binding.
        assert saved["temporal_binding_valid"] is True

    def test_source_none_and_not_valid_on_fallback(self, runner_env):
        runner, mock_tp = runner_env
        mock_tp.supports_commit_reveal = MagicMock(return_value=True)
        mock_tp.reveal = AsyncMock(return_value=("0xREVEAL", 102))
        mock_tp.publish = AsyncMock(return_value="0xPUB")

        asyncio.run(
            runner._reveal_trace(
                _make_trace(),
                trace_id=None,
                tick_id="t1",
                tx_hashes=["0xtrade"],
                commit_tx=None,
                commit_block=99,
                trade_block=101,
            )
        )

        saved = _saved(runner)
        assert saved["temporal_binding_source"] == "none"
        # No real commit-reveal trace_id -> binding cannot be asserted, even though
        # a fallback commit_block exists (this is the exact masquerade #714 closes).
        assert not saved["temporal_binding_valid"]


class TestTraceResponseClaimGuard:
    """The schema is the last line of defense against a stale Redis True binding."""

    @staticmethod
    def _resp(**kw):
        from archimedes.api.schemas import TraceResponse

        base = dict(
            id="t",
            vault_address="0xv",
            decision_type="rebalance",
            trigger="x",
            timestamp="2026-06-29T00:00:00Z",
            reasoning="r",
            confidence=0.5,
            trace_hash="ab",
        )
        base.update(kw)
        return TraceResponse(**base)

    def test_true_binding_coerced_to_none_without_chain_source(self):
        r = self._resp(temporal_binding_valid=True, temporal_binding_source="none")
        assert r.temporal_binding_valid is None  # cannot claim a binding off a non-chain source

    def test_true_binding_preserved_with_chain_source(self):
        r = self._resp(temporal_binding_valid=True, temporal_binding_source="chain")
        assert r.temporal_binding_valid is True

    def test_is_verified_left_honest(self):
        # A publishTrace anchor is a genuine on-chain hash confirmation: is_verified stays
        # honest even when the stronger temporal binding is absent.
        r = self._resp(is_verified=True, temporal_binding_source="none")
        assert r.is_verified is True

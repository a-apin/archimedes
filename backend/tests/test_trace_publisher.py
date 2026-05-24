"""Trace publisher tests — hash computation, verify round-trip, publish path.

Hermetic: no testnet, no Circle SDK, no real chain calls. All mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.models.trace import DecisionType, ReasoningTrace


def _make_trace(**overrides) -> ReasoningTrace:
    """Create a test trace with defaults."""
    defaults = dict(
        id="test-trace-001",
        vault_address="0x1234567890abcdef1234567890abcdef12345678",
        decision_type=DecisionType.REBALANCE,
        trigger="strategy_signal_drift",
        timestamp=datetime.now(UTC),
        reasoning="Test trace for unit testing",
        confidence=0.85,
    )
    defaults.update(overrides)
    return ReasoningTrace(**defaults)


class TestTraceHashComputation:
    """Test that trace hash computation is deterministic."""

    def test_hash_is_deterministic(self):
        trace = _make_trace()
        h1 = trace.compute_hash()
        h2 = trace.compute_hash()
        assert h1 == h2
        assert h1 is not None
        assert len(h1) == 64  # keccak256 hex

    def test_different_content_different_hash(self):
        t1 = _make_trace(reasoning="Strategy A signal")
        t2 = _make_trace(reasoning="Strategy B signal")
        h1 = t1.compute_hash()
        h2 = t2.compute_hash()
        assert h1 != h2

    def test_hash_format(self):
        trace = _make_trace()
        h = trace.compute_hash()
        # Should be a hex string without 0x prefix (or with it)
        assert all(c in "0123456789abcdef" for c in h.removeprefix("0x"))


class TestTracePublishMocked:
    """Test publish path with mocked chain client and signer."""

    @pytest.fixture()
    def mock_loader(self):
        loader = MagicMock()
        loader.trace_registry = MagicMock()
        return loader

    def test_publish_circle_path(self, mock_loader):
        """Circle signer path: publish returns tx hash."""
        with (
            patch("archimedes.chain.trace_publisher.circle_signer") as mock_signer,
            patch("archimedes.chain.trace_publisher.chain_client") as mock_client,
        ):
            mock_signer.is_configured = True
            mock_signer.execute_contract = AsyncMock(return_value="0xabc123")
            mock_client.to_checksum = lambda x: x
            mock_client.settings = MagicMock(
                reasoning_trace_registry_address="0xregistry",
            )

            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=mock_loader)

            trace = _make_trace()
            trace.compute_hash()

            # Run the async publish
            import asyncio

            tx_hash = asyncio.get_event_loop().run_until_complete(publisher.publish(trace))

            assert tx_hash == "0xabc123"
            assert trace.arc_tx_hash == "0xabc123"

    def test_publish_no_signer_no_account(self, mock_loader):
        """No signer, no account — returns None."""
        with (
            patch("archimedes.chain.trace_publisher.circle_signer") as mock_signer,
            patch("archimedes.chain.trace_publisher.chain_client") as mock_client,
        ):
            mock_signer.is_configured = False
            mock_client.settings = MagicMock(agent_account=None)

            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=mock_loader)

            trace = _make_trace()
            trace.compute_hash()

            import asyncio

            tx_hash = asyncio.get_event_loop().run_until_complete(publisher.publish(trace))
            assert tx_hash is None


class TestTraceVerify:
    """Test verify round-trips against mocked registry."""

    def test_verify_no_hash(self):
        """Trace without hash returns False."""
        with (
            patch("archimedes.chain.trace_publisher.chain_client") as mock_client,
            patch("archimedes.chain.trace_publisher.get_contract_loader") as mock_get_loader,
        ):
            mock_client.to_checksum = lambda x: x

            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=mock_get_loader.return_value)

            trace = _make_trace()
            trace.trace_hash = None

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(publisher.verify(trace))
            assert result is False

    def test_verify_matching_hash(self):
        """Verify returns True when on-chain hash matches."""
        trace = _make_trace()
        trace.compute_hash()
        trace_hash_bytes = bytes.fromhex(trace.trace_hash.removeprefix("0x"))

        mock_registry = MagicMock()
        # getTracesByVault returns [1, 2, 3]
        mock_registry.functions.getTracesByVault.return_value.call = AsyncMock(return_value=[1, 2, 3])
        # getTraceById returns (agent, vault, hash_bytes, timestamp, metadata)
        mock_registry.functions.getTraceById.return_value.call = AsyncMock(
            return_value=("0xagent", "0xvault", trace_hash_bytes, 12345, b"")
        )

        mock_loader = MagicMock()
        mock_loader.trace_registry = mock_registry

        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.to_checksum = lambda x: x

            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=mock_loader)

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(publisher.verify(trace))
            assert result is True

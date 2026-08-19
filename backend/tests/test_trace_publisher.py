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
    defaults = {
        "id": "test-trace-001",
        "vault_address": "0x1234567890abcdef1234567890abcdef12345678",
        "decision_type": DecisionType.REBALANCE,
        "trigger": "strategy_signal_drift",
        "timestamp": datetime.now(UTC),
        "reasoning": "Test trace for unit testing",
        "confidence": 0.85,
    }
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

            tx_hash = asyncio.run(publisher.publish(trace))

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

            tx_hash = asyncio.run(publisher.publish(trace))
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

            result = asyncio.run(publisher.verify(trace))
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

            result = asyncio.run(publisher.verify(trace))
            assert result is True


class TestGetTraceByTxHash:
    """Test the O(1) tx-receipt-based lookup used by /verify."""

    def test_returns_decoded_event(self):
        """When the receipt contains a TracePublished log, the helper decodes it."""
        trace_hash_bytes = bytes.fromhex("aa" * 32)

        # Mock the receipt with a single log.
        fake_log = MagicMock()
        fake_receipt = MagicMock()
        fake_receipt.logs = [fake_log]

        mock_registry = MagicMock()
        mock_registry.events.TracePublished.return_value.process_log = MagicMock(
            return_value={
                "args": {
                    "traceId": 7,
                    "agent": "0xagent",
                    "vault": "0xvault",
                    "traceHash": trace_hash_bytes,
                    "timestamp": 99999,
                }
            }
        )

        mock_loader = MagicMock()
        mock_loader.trace_registry = mock_registry

        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.w3.eth.get_transaction_receipt = AsyncMock(return_value=fake_receipt)

            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=mock_loader)

            import asyncio

            result = asyncio.run(publisher.get_trace_by_tx_hash("0xdeadbeef"))

            assert result is not None
            assert result["agent"] == "0xagent"
            assert result["vault"] == "0xvault"
            assert result["trace_hash"] == trace_hash_bytes.hex()
            assert result["timestamp"] == 99999
            assert result["trace_id"] == 7
            # Single RPC roundtrip — receipt fetched exactly once.
            mock_client.w3.eth.get_transaction_receipt.assert_awaited_once_with("0xdeadbeef")

    def test_returns_none_when_no_event_in_receipt(self):
        """If no log decodes to TracePublished, return None."""
        fake_log = MagicMock()
        fake_receipt = MagicMock()
        fake_receipt.logs = [fake_log]

        mock_registry = MagicMock()
        mock_registry.events.TracePublished.return_value.process_log = MagicMock(
            side_effect=Exception("not this event")
        )

        mock_loader = MagicMock()
        mock_loader.trace_registry = mock_registry

        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.w3.eth.get_transaction_receipt = AsyncMock(return_value=fake_receipt)

            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=mock_loader)

            import asyncio

            result = asyncio.run(publisher.get_trace_by_tx_hash("0xdeadbeef"))
            assert result is None

    def test_returns_none_when_receipt_fetch_fails(self):
        """If get_transaction_receipt raises, the helper returns None (no crash)."""
        mock_loader = MagicMock()
        mock_loader.trace_registry = MagicMock()

        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.w3.eth.get_transaction_receipt = AsyncMock(side_effect=Exception("RPC error"))

            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=mock_loader)

            import asyncio

            result = asyncio.run(publisher.get_trace_by_tx_hash("0xdeadbeef"))
            assert result is None

    def test_returns_none_for_empty_tx_hash(self):
        """Empty or falsy tx_hash short-circuits to None without an RPC call."""
        mock_loader = MagicMock()
        mock_loader.trace_registry = MagicMock()

        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.w3.eth.get_transaction_receipt = AsyncMock()

            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=mock_loader)

            import asyncio

            result = asyncio.run(publisher.get_trace_by_tx_hash(""))
            assert result is None
            mock_client.w3.eth.get_transaction_receipt.assert_not_awaited()


# ── G6 (audit 2026-08-18): the verify button's negative case and the chain
# reads behind it. Only "no hash → False" and "exact match → True" existed;
# nothing proved a TAMPERED trace is rejected — the mechanism that shows an
# AI rebalance decision's reasoning wasn't altered after the fact. The four
# chain-read helpers behind /verify were 1/8, 1/7, 1/8 covered.


def _mock_loader_with(trace_ids, stored_hash):
    mock_registry = MagicMock()
    mock_registry.functions.getTracesByVault.return_value.call = AsyncMock(return_value=trace_ids)
    mock_registry.functions.getTraceById.return_value.call = AsyncMock(
        return_value=("0xagent", "0xvault", stored_hash, 12345, b"")
    )
    loader = MagicMock()
    loader.trace_registry = mock_registry
    return loader


class TestTraceVerifyRejectsTampering:
    def test_verify_rejects_a_hash_off_by_one_byte(self):
        """THE load-bearing negative: an on-chain hash differing in a single
        byte must fail verification. A mutant that compares prefixes, lengths,
        or nothing at all passes the existing exact-match test and dies here."""
        trace = _make_trace()
        trace.compute_hash()
        good = bytearray(bytes.fromhex(trace.trace_hash.removeprefix("0x")))
        good[-1] ^= 0x01  # flip one bit of the last byte
        tampered = bytes(good)

        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.to_checksum = lambda x: x
            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=_mock_loader_with([1], tampered))
            import asyncio

            assert asyncio.run(publisher.verify(trace)) is False

    def test_verify_scans_past_nonmatching_traces_to_the_match(self):
        """A vault holds newer traces than the one being verified — the scan
        must reach the older matching id, not stop at the first mismatch."""
        trace = _make_trace()
        trace.compute_hash()
        match = bytes.fromhex(trace.trace_hash.removeprefix("0x"))
        other = bytes(32)

        mock_registry = MagicMock()
        mock_registry.functions.getTracesByVault.return_value.call = AsyncMock(return_value=[7, 8, 9])
        per_id = {7: match, 8: other, 9: other}

        def _by_id(trace_id):
            m = MagicMock()
            m.call = AsyncMock(return_value=("0xagent", "0xvault", per_id[trace_id], 0, b""))
            return m

        mock_registry.functions.getTraceById.side_effect = _by_id
        loader = MagicMock()
        loader.trace_registry = mock_registry

        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.to_checksum = lambda x: x
            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=loader)
            import asyncio

            assert asyncio.run(publisher.verify(trace)) is True

    def test_verify_empty_vault_returns_false(self):
        trace = _make_trace()
        trace.compute_hash()
        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.to_checksum = lambda x: x
            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=_mock_loader_with([], bytes(32)))
            import asyncio

            assert asyncio.run(publisher.verify(trace)) is False

    def test_verify_chain_failure_returns_false_never_raises(self):
        trace = _make_trace()
        trace.compute_hash()
        mock_registry = MagicMock()
        mock_registry.functions.getTracesByVault.return_value.call = AsyncMock(side_effect=RuntimeError("rpc down"))
        loader = MagicMock()
        loader.trace_registry = mock_registry

        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.to_checksum = lambda x: x
            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=loader)
            import asyncio

            assert asyncio.run(publisher.verify(trace)) is False


class TestTraceChainReads:
    """The four read helpers behind the verify button (previously ~1 stmt each)."""

    def _publisher(self, loader):
        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.to_checksum = lambda x: x
            from archimedes.chain.trace_publisher import TracePublisher

            return TracePublisher(loader=loader), mock_client

    def test_get_trace_by_id_returns_decoded_dict(self):
        stored_hash = b"\x11" * 32
        loader = _mock_loader_with([1], stored_hash)
        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.to_checksum = lambda x: x
            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=loader)
            import asyncio

            out = asyncio.run(publisher.get_trace_by_id(1))
        assert out == {
            "agent": "0xagent",
            "vault": "0xvault",
            "trace_hash": stored_hash.hex(),
            "timestamp": 12345,
            "metadata": b"",
        }

    def test_get_trace_by_id_failure_returns_none(self):
        mock_registry = MagicMock()
        mock_registry.functions.getTraceById.return_value.call = AsyncMock(side_effect=RuntimeError("boom"))
        loader = MagicMock()
        loader.trace_registry = mock_registry
        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.to_checksum = lambda x: x
            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=loader)
            import asyncio

            assert asyncio.run(publisher.get_trace_by_id(1)) is None

    def test_get_traces_by_vault_returns_ids_and_fails_to_empty(self):
        loader = _mock_loader_with([4, 5], bytes(32))
        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.to_checksum = lambda x: x
            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=loader)
            import asyncio

            assert asyncio.run(publisher.get_traces_by_vault("0xvault")) == [4, 5]

            loader.trace_registry.functions.getTracesByVault.return_value.call = AsyncMock(
                side_effect=RuntimeError("rpc down")
            )
            assert asyncio.run(publisher.get_traces_by_vault("0xvault")) == []

    def test_trace_counts_and_fail_to_zero(self):
        loader = _mock_loader_with([1, 2, 3], bytes(32))
        loader.trace_registry.functions.traceCount.return_value.call = AsyncMock(return_value=42)
        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            mock_client.to_checksum = lambda x: x
            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=loader)
            import asyncio

            assert asyncio.run(publisher.get_trace_count("0xvault")) == 3
            assert asyncio.run(publisher.get_total_trace_count()) == 42

            loader.trace_registry.functions.getTracesByVault.return_value.call = AsyncMock(
                side_effect=RuntimeError("rpc down")
            )
            loader.trace_registry.functions.traceCount.return_value.call = AsyncMock(
                side_effect=RuntimeError("rpc down")
            )
            assert asyncio.run(publisher.get_trace_count("0xvault")) == 0
            assert asyncio.run(publisher.get_total_trace_count()) == 0

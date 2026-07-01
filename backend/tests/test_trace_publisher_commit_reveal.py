"""Commit-reveal path tests for TracePublisher (#714 / T0.3).

Hermetic: no testnet, no Circle SDK, no real chain calls — circle_signer and the
chain client / contract loader are mocked at the boundary, mirroring the precedent in
``test_trace_publisher.py`` (per CLAUDE.md §"Mock at boundaries, not internals").

Covers the real ``commit()`` / ``reveal()`` ABI calls the live agent path now uses,
the trace_id parse from the ``TraceCommitted`` event, the ``claimedExecutionTime``
argument, and the graceful fallback when the deployed registry is pre-v1.5 (#588).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.models.trace import DecisionType, ReasoningTrace


def _make_trace(**overrides) -> ReasoningTrace:
    defaults = {
        "id": "test-trace-cr-001",
        "vault_address": "0x1234567890abcdef1234567890abcdef12345678",
        "decision_type": DecisionType.REBALANCE,
        "trigger": "strategy_signal_drift",
        "timestamp": datetime.now(UTC),
        "reasoning": "Commit-reveal unit test trace",
        "confidence": 0.85,
    }
    defaults.update(overrides)
    return ReasoningTrace(**defaults)


@pytest.fixture()
def supported_loader():
    """A loader whose registry ABI exposes commit()/reveal() (v1.5).

    A bare MagicMock auto-creates the ``commit``/``reveal`` attributes, so
    ``supports_commit_reveal()`` (which does ``hasattr(functions, "commit")``) is True.
    The TraceCommitted event decode is stubbed to yield a deterministic trace_id.
    """
    loader = MagicMock()
    loader.trace_registry = MagicMock()
    loader.trace_registry.events.TraceCommitted.return_value.process_log.return_value = {"args": {"traceId": 42}}
    return loader


@pytest.fixture()
def unsupported_loader():
    """A loader whose registry has NO commit()/reveal() (pre-v1.5, #588 pending)."""
    loader = MagicMock()
    loader.trace_registry = MagicMock()
    loader.trace_registry.functions = MagicMock(spec=[])  # hasattr(..., "commit") -> False
    return loader


def _patch_chain(mock_client):
    mock_client.to_checksum = lambda x: x
    mock_client.settings = MagicMock(reasoning_trace_registry_address="0xregistry", chain_id=5042002)
    mock_client.w3.eth.get_transaction_receipt = AsyncMock(return_value=MagicMock(blockNumber=100, logs=[MagicMock()]))


class TestCommit:
    def test_commit_circle_path_calls_correct_abi(self, supported_loader):
        with (
            patch("archimedes.chain.trace_publisher.circle_signer") as mock_signer,
            patch("archimedes.chain.trace_publisher.chain_client") as mock_client,
        ):
            mock_signer.is_configured = True
            mock_signer.execute_contract = AsyncMock(return_value="0xCOMMIT")
            _patch_chain(mock_client)

            from archimedes.chain.trace_publisher import TracePublisher

            publisher = TracePublisher(loader=supported_loader)
            trace = _make_trace()
            trace.compute_hash()
            claimed = 2_000_000_000

            trace_id, tx, block = asyncio.run(publisher.commit(trace, claimed, b"\x01"))

            assert (trace_id, tx, block) == (42, "0xCOMMIT", 100)
            _, kwargs = mock_signer.execute_contract.call_args
            assert kwargs["abi_function"] == "commit(address,bytes32,uint64,bytes)"
            # vault, contentHash, claimedExecutionTime (as str), intent
            assert kwargs["abi_params"][0] == trace.vault_address
            assert kwargs["abi_params"][2] == str(claimed)

    def test_commit_parses_trace_id_from_event(self, supported_loader):
        with (
            patch("archimedes.chain.trace_publisher.circle_signer") as mock_signer,
            patch("archimedes.chain.trace_publisher.chain_client") as mock_client,
        ):
            mock_signer.is_configured = True
            mock_signer.execute_contract = AsyncMock(return_value="0xCOMMIT")
            _patch_chain(mock_client)
            from archimedes.chain.trace_publisher import TracePublisher

            trace = _make_trace()
            trace.compute_hash()
            trace_id, _, _ = asyncio.run(TracePublisher(loader=supported_loader).commit(trace, 2_000_000_000))
            assert trace_id == 42  # decoded from TraceCommitted, not the getTracesByVault fallback

    def test_commit_returns_none_when_registry_pre_v1_5(self, unsupported_loader):
        with (
            patch("archimedes.chain.trace_publisher.circle_signer") as mock_signer,
            patch("archimedes.chain.trace_publisher.chain_client") as mock_client,
        ):
            mock_signer.is_configured = True
            _patch_chain(mock_client)
            from archimedes.chain.trace_publisher import TracePublisher

            trace = _make_trace()
            trace.compute_hash()
            result = asyncio.run(TracePublisher(loader=unsupported_loader).commit(trace, 2_000_000_000))
            assert result == (None, None, None)


class TestReveal:
    def test_reveal_circle_path_calls_correct_abi(self, supported_loader):
        with (
            patch("archimedes.chain.trace_publisher.circle_signer") as mock_signer,
            patch("archimedes.chain.trace_publisher.chain_client") as mock_client,
        ):
            mock_signer.is_configured = True
            mock_signer.execute_contract = AsyncMock(return_value="0xREVEAL")
            _patch_chain(mock_client)
            from archimedes.chain.trace_publisher import TracePublisher

            trace = _make_trace()
            trace.compute_hash()
            cid = "ipfs://bafytest"

            reveal_tx, block = asyncio.run(
                TracePublisher(loader=supported_loader).reveal(42, trace, storage_pointer=cid)
            )

            assert (reveal_tx, block) == ("0xREVEAL", 100)
            _, kwargs = mock_signer.execute_contract.call_args
            assert kwargs["abi_function"] == "reveal(uint256,string,bytes)"
            assert kwargs["abi_params"][0] == "42"
            assert kwargs["abi_params"][1] == cid  # the IPFS CID is the storage pointer

    def test_reveal_returns_none_without_trace_id(self, supported_loader):
        with (
            patch("archimedes.chain.trace_publisher.circle_signer") as mock_signer,
            patch("archimedes.chain.trace_publisher.chain_client") as mock_client,
        ):
            mock_signer.is_configured = True
            mock_signer.execute_contract = AsyncMock(return_value="0xREVEAL")
            _patch_chain(mock_client)
            from archimedes.chain.trace_publisher import TracePublisher

            trace = _make_trace()
            trace.compute_hash()
            assert asyncio.run(TracePublisher(loader=supported_loader).reveal(None, trace)) == (None, None)

    def test_reveal_returns_none_when_registry_pre_v1_5(self, unsupported_loader):
        with (
            patch("archimedes.chain.trace_publisher.circle_signer") as mock_signer,
            patch("archimedes.chain.trace_publisher.chain_client") as mock_client,
        ):
            mock_signer.is_configured = True
            _patch_chain(mock_client)
            from archimedes.chain.trace_publisher import TracePublisher

            trace = _make_trace()
            trace.compute_hash()
            assert asyncio.run(TracePublisher(loader=unsupported_loader).reveal(42, trace)) == (None, None)

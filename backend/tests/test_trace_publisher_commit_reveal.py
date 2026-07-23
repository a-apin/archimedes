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
import logging
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
            trade_id = b"\x11" * 32

            trace_id, tx, block, reverted = asyncio.run(publisher.commit(trace, claimed, trade_id, b"\x01"))

            assert (trace_id, tx, block, reverted) == (42, "0xCOMMIT", 100, False)
            _, kwargs = mock_signer.execute_contract.call_args
            assert kwargs["abi_function"] == "commit(address,bytes32,uint64,bytes32,bytes)"
            # vault, contentHash, claimedExecutionTime (as str), tradeId, intent
            assert kwargs["abi_params"][0] == trace.vault_address
            assert kwargs["abi_params"][2] == str(claimed)
            assert kwargs["abi_params"][3] == "0x" + trade_id.hex()

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
            trace_id, _, _, _ = asyncio.run(
                TracePublisher(loader=supported_loader).commit(trace, 2_000_000_000, b"\x22" * 32)
            )
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
            result = asyncio.run(TracePublisher(loader=unsupported_loader).commit(trace, 2_000_000_000, b"\x33" * 32))
            assert result == (None, None, None, False)


class TestFinalizeCommitRevertHandling:
    """#714 follow-up: a reverted commit must never surface a stale trace_id.

    ``getTracesByVault()[-1]`` is a reasonable last-resort guess ONLY when the
    receipt confirms the tx succeeded but the ``TraceCommitted`` event couldn't be
    decoded. A confirmed revert (``status == 0`` — the #1047-class failure mode:
    the client builds a call shape the deployed bytecode doesn't expose) must
    short-circuit straight to ``trace_id=None`` instead, or the fallback would
    silently hand back an unrelated, already-committed trace's id.
    """

    def test_reverted_commit_does_not_fall_back_to_stale_trace_id(self, supported_loader, caplog):
        with (
            patch("archimedes.chain.trace_publisher.circle_signer") as mock_signer,
            patch("archimedes.chain.trace_publisher.chain_client") as mock_client,
        ):
            mock_signer.is_configured = True
            mock_signer.execute_contract = AsyncMock(return_value="0xREVERTED")
            mock_client.to_checksum = lambda x: x
            mock_client.settings = MagicMock(reasoning_trace_registry_address="0xregistry", chain_id=5042002)
            mock_client.w3.eth.get_transaction_receipt = AsyncMock(
                return_value=MagicMock(blockNumber=100, status=0, logs=[])
            )
            # If the buggy fallback fired, it would return this id — belonging to
            # some earlier, unrelated commit for the same vault.
            supported_loader.trace_registry.functions.getTracesByVault.return_value.call = AsyncMock(return_value=[999])
            from archimedes.chain.trace_publisher import TracePublisher

            trace = _make_trace()
            trace.compute_hash()
            with caplog.at_level(logging.WARNING, logger="archimedes.chain.trace_publisher"):
                trace_id, tx, block, reverted = asyncio.run(
                    TracePublisher(loader=supported_loader).commit(trace, 2_000_000_000, b"\x44" * 32)
                )

            assert trace_id is None  # NOT 999 — the confirmed revert must not be masked
            assert tx == "0xREVERTED"  # tx hash still recorded for the diagnostic trail
            assert block == 100
            # A confirmed revert MUST surface as reverted=True: a consumer (e.g.
            # agent_runner's Phase 2 guard) that only checks tx is not None would
            # wrongly treat this reverted-but-truthy tx as a landed commit (#1095
            # review — see agent_runner._commit_trace / commit_reverted).
            assert reverted is True
            supported_loader.trace_registry.functions.getTracesByVault.assert_not_called()
            # The revert must be loud: the commit path logs an INFO success line
            # before the receipt comes back, so without this warning the log
            # stream would claim the commit succeeded.
            assert "reverted on-chain (status=0)" in caplog.text

    def test_successful_commit_with_undecodable_event_still_uses_fallback(self, supported_loader):
        """Regression guard: only a confirmed revert skips the fallback — a
        genuinely successful receipt (status=1) whose event can't be decoded
        should still use getTracesByVault() as before."""
        with (
            patch("archimedes.chain.trace_publisher.circle_signer") as mock_signer,
            patch("archimedes.chain.trace_publisher.chain_client") as mock_client,
        ):
            mock_signer.is_configured = True
            mock_signer.execute_contract = AsyncMock(return_value="0xOK")
            mock_client.to_checksum = lambda x: x
            mock_client.settings = MagicMock(reasoning_trace_registry_address="0xregistry", chain_id=5042002)
            mock_client.w3.eth.get_transaction_receipt = AsyncMock(
                return_value=MagicMock(blockNumber=101, status=1, logs=[])  # no logs -> event decode finds nothing
            )
            supported_loader.trace_registry.functions.getTracesByVault.return_value.call = AsyncMock(
                return_value=[7, 8, 9]
            )
            from archimedes.chain.trace_publisher import TracePublisher

            trace = _make_trace()
            trace.compute_hash()
            trace_id, tx, block, reverted = asyncio.run(
                TracePublisher(loader=supported_loader).commit(trace, 2_000_000_000, b"\x55" * 32)
            )

            assert trace_id == 9  # last element of the fallback lookup
            assert tx == "0xOK"
            assert block == 101
            assert reverted is False


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

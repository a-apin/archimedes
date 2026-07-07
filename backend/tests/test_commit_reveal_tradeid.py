"""Commit-reveal tradeId ABI-mismatch regression tests (funds-critical, #588).

Two things went wrong before this fix:

1. ``trace_publisher.commit()`` called the registry's ``commit()`` with the OLD
   4-arg selector (``commit(address,bytes32,uint64,bytes)``), but the deployed /
   checked-in ABI (``contracts/abis/ReasoningTraceRegistry.json``) exposes the
   v1.5 5-arg ``commit(address,bytes32,uint64,bytes32,bytes)`` (with a
   ``bytes32 tradeId``). Every commit call raised ``MismatchedABI`` and was
   swallowed by the existing fail-soft ``try/except`` — so no commitment ever
   landed on-chain, and the trade proceeded UNPROTECTED. Post T3.2 redeploy,
   ``Vault.rebalance()`` calls ``traceRegistry.executeTrade(tradeId)``, which
   REVERTS with no matching commitment — every rebalance would fail outright.

2. Even with the right selector, the ``tradeId`` bound at commit time MUST be
   ``keccak256(abi.encode(tokensIn, amountsIn, tokensOut, amountsOut))`` computed
   from the EXACT arrays ``Vault.rebalance()`` (Vault.sol:428) will later hash —
   same Solidity types, same order, same values. This module proves the Python
   ``eth_abi``/``eth_utils`` encoding matches real Solidity ``abi.encode`` +
   ``keccak256`` byte-for-byte (verified independently via Foundry's `chisel`/
   `cast` against the fixtures below — see PR description for the exact
   commands), and that ``trace_publisher.commit()`` now builds a valid
   transaction against the checked-in 5-arg ABI (no live chain).

Hermetic: no testnet, no Circle SDK, no live RPC. Uses the checked-in ABI file
directly with a disconnected ``web3.Web3()`` instance for the ABI-shape checks
(pure local ABI encoding, no network I/O) and the existing circle_signer/
chain_client boundary mocks (mirrors ``test_trace_publisher_commit_reveal.py``)
for the ``TracePublisher.commit()`` behavior checks.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.chain.executor import compute_trade_id
from archimedes.models.trace import DecisionType, ReasoningTrace
from web3 import Web3
from web3.exceptions import MismatchedABI

ABI_PATH = Path(__file__).resolve().parents[2] / "contracts" / "abis" / "ReasoningTraceRegistry.json"

# ── Fixtures whose expected tradeId was computed independently via Foundry
# (NOT via eth_abi — a genuine second oracle), proving the Python encoding
# matches Solidity `abi.encode` byte-for-byte:
#
#   chisel <<'EOF'
#   address[] memory tokensIn = new address[](1);
#   tokensIn[0] = 0x1111111111111111111111111111111111111111;
#   uint256[] memory amountsIn = new uint256[](1);
#   amountsIn[0] = 1000000;
#   address[] memory tokensOut = new address[](2);
#   tokensOut[0] = 0x2222222222222222222222222222222222222222;
#   tokensOut[1] = 0x3333333333333333333333333333333333333333;
#   uint256[] memory amountsOut = new uint256[](2);
#   amountsOut[0] = 2000000000000000000;
#   amountsOut[1] = 500000000000000000;
#   bytes32 h = keccak256(abi.encode(tokensIn, amountsIn, tokensOut, amountsOut));
#   h
#   EOF
#   => 0xe28398ae52f239509c556342507d5c165cd56e51732fff6686b92b3a273a9191
#
# (and equivalently `cast abi-encode "f(address[],uint256[],address[],uint256[])" ... | cast keccak`)
_FIXTURES = [
    pytest.param(
        ["0x1111111111111111111111111111111111111111"],
        [1000000],
        ["0x2222222222222222222222222222222222222222", "0x3333333333333333333333333333333333333333"],
        [2000000000000000000, 500000000000000000],
        "0xe28398ae52f239509c556342507d5c165cd56e51732fff6686b92b3a273a9191",
        id="mixed_buy_and_two_sells",
    ),
    pytest.param(
        [],
        [],
        [],
        [],
        "0x8f7a465e16e165d99d808555fd56765cdfbe8c2b406708a7af187dbd07306d8c",
        id="all_empty_arrays",
    ),
    pytest.param(
        ["0x444444444444444444444444444444444444440A"],
        [8000000],
        [],
        [],
        "0x659b0989ce1df104ea2c287a3f4c6a9030ed4ef0a71a0244915075bcc2939152",
        id="buy_only",
    ),
]


class TestComputeTradeIdMatchesSolidity:
    """Prove executor.compute_trade_id reproduces Vault.sol's on-chain hash exactly."""

    @pytest.mark.parametrize("tokens_in,amounts_in,tokens_out,amounts_out,expected_hex", _FIXTURES)
    def test_matches_independently_computed_solidity_hash(
        self, tokens_in, amounts_in, tokens_out, amounts_out, expected_hex
    ):
        trade_id = compute_trade_id(tokens_in, amounts_in, tokens_out, amounts_out)
        assert isinstance(trade_id, bytes)
        assert len(trade_id) == 32
        assert "0x" + trade_id.hex() == expected_hex

    def test_different_arrays_produce_different_trade_ids(self):
        """Sanity: the hash is actually sensitive to the array contents/order —
        not a constant or a collision-prone truncation."""
        a = compute_trade_id(["0x" + "11" * 20], [1], [], [])
        b = compute_trade_id(["0x" + "11" * 20], [2], [], [])  # amount differs
        c = compute_trade_id(["0x" + "22" * 20], [1], [], [])  # token differs
        assert len({a, b, c}) == 3


class TestRegistryABIExposesFiveArgCommit:
    """The checked-in ABI must expose the v1.5 5-arg commit(), not the old 4-arg one."""

    def test_checked_in_abi_has_5_arg_commit(self):
        abi = json.loads(ABI_PATH.read_text())
        commit_fns = [item for item in abi if item.get("type") == "function" and item.get("name") == "commit"]
        assert len(commit_fns) == 1, "expected exactly one commit() overload in the ABI"
        input_types = [inp["type"] for inp in commit_fns[0]["inputs"]]
        assert input_types == ["address", "bytes32", "uint64", "bytes32", "bytes"]

    def test_old_4arg_call_raises_mismatched_abi(self):
        """Regression guard: the OLD call shape this bug shipped
        (commit(address,bytes32,uint64,bytes)) must fail loudly against the real
        ABI — proving the bug was real and the fix is necessary, not cosmetic."""
        abi = json.loads(ABI_PATH.read_text())
        w3 = Web3()
        contract = w3.eth.contract(address=Web3.to_checksum_address("0x" + "11" * 20), abi=abi)
        vault = Web3.to_checksum_address("0x" + "22" * 20)

        with pytest.raises(MismatchedABI):
            contract.functions.commit(vault, b"\xaa" * 32, 12345, b"").build_transaction(
                {
                    "from": Web3.to_checksum_address("0x" + "33" * 20),
                    "nonce": 0,
                    "chainId": 5042002,
                    "gas": 300_000,
                    "gasPrice": 1,
                }
            )

    def test_new_5arg_call_builds_transaction_without_error(self):
        """The FIXED call shape (with tradeId) must build cleanly against the
        real checked-in ABI — no live chain, pure local ABI encoding."""
        abi = json.loads(ABI_PATH.read_text())
        w3 = Web3()
        contract = w3.eth.contract(address=Web3.to_checksum_address("0x" + "11" * 20), abi=abi)
        vault = Web3.to_checksum_address("0x" + "22" * 20)
        content_hash = b"\xaa" * 32
        trade_id = compute_trade_id(["0x" + "44" * 20], [1_000_000], [], [])

        tx = contract.functions.commit(vault, content_hash, 12345, trade_id, b"").build_transaction(
            {
                "from": Web3.to_checksum_address("0x" + "33" * 20),
                "nonce": 0,
                "chainId": 5042002,
                "gas": 300_000,
                "gasPrice": 1,
            }
        )
        assert tx["to"] == Web3.to_checksum_address("0x" + "11" * 20)
        assert tx["data"].startswith("0x")


# ── TracePublisher.commit() — boundary-mocked, real ABI-shape assertions ──


def _make_trace() -> ReasoningTrace:
    trace = ReasoningTrace(
        id="tradeid-test-001",
        vault_address="0x1234567890abcdef1234567890abcdef12345678",
        decision_type=DecisionType.REBALANCE,
        trigger="strategy_signal_drift",
        timestamp=datetime.now(UTC),
        reasoning="tradeId ABI-mismatch regression test",
        confidence=0.9,
    )
    trace.compute_hash()
    return trace


@pytest.fixture()
def supported_loader():
    """A loader whose registry ABI exposes commit()/reveal() (v1.5)."""
    loader = MagicMock()
    loader.trace_registry = MagicMock()
    loader.trace_registry.events.TraceCommitted.return_value.process_log.return_value = {"args": {"traceId": 7}}
    return loader


def _patch_chain(mock_client):
    mock_client.to_checksum = lambda x: x
    mock_client.settings = MagicMock(reasoning_trace_registry_address="0xregistry", chain_id=5042002)
    mock_client.w3.eth.get_transaction_receipt = AsyncMock(return_value=MagicMock(blockNumber=100, logs=[MagicMock()]))


class _Awaitable:
    """Wraps a plain value so ``await mock.attr`` works (attribute-access await
    pattern) — mirrors the helper in ``backend/tests/chain/test_chain_executor.py``."""

    def __init__(self, value):
        self._value = value

    def __await__(self):
        async def _coro():
            return self._value

        return _coro().__await__()


class TestTracePublisherCommitWithTradeId:
    def test_commit_circle_path_uses_5arg_selector_with_trade_id(self, supported_loader):
        """The Circle path must request the 5-arg selector and place tradeId in
        the correct ABI-param position (matching contracts/abis/ReasoningTraceRegistry.json's
        commit(vault, contentHash, claimedExecutionTime, tradeId, tradeIntentSummary))."""
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
            claimed = 2_000_000_000
            trade_id = compute_trade_id(["0x" + "55" * 20], [1_000_000], [], [])

            trace_id, tx, block = asyncio.run(publisher.commit(trace, claimed, trade_id, b"\x01"))

            assert (trace_id, tx, block) == (7, "0xCOMMIT", 100)
            _, kwargs = mock_signer.execute_contract.call_args
            assert kwargs["abi_function"] == "commit(address,bytes32,uint64,bytes32,bytes)"
            params = kwargs["abi_params"]
            assert params[0] == trace.vault_address
            assert params[2] == str(claimed)
            assert params[3] == "0x" + trade_id.hex()

    def test_commit_raw_key_path_passes_trade_id_as_4th_positional(self, supported_loader):
        """The raw-key path must call registry.functions.commit(vault, contentHash,
        claimedExecutionTime, tradeId, tradeIntentSummary) — 5 positional args in
        the exact order the checked-in ABI expects."""
        with patch("archimedes.chain.trace_publisher.chain_client") as mock_client:
            _patch_chain(mock_client)
            mock_client.settings.agent_account = MagicMock(address="0xagent")
            mock_client.w3.eth.get_transaction_count = AsyncMock(return_value=1)
            mock_client.w3.eth.gas_price = _Awaitable(1)
            mock_client.w3.eth.send_raw_transaction = AsyncMock(return_value=MagicMock(hex=lambda: "0xRAWTX"))

            build_tx = AsyncMock(return_value={"to": "0xregistry"})
            supported_loader.trace_registry.functions.commit = MagicMock(
                return_value=MagicMock(build_transaction=build_tx)
            )

            with patch("archimedes.chain.trace_publisher.circle_signer") as mock_signer:
                mock_signer.is_configured = False

                from archimedes.chain.trace_publisher import TracePublisher

                publisher = TracePublisher(loader=supported_loader)
                trace = _make_trace()
                claimed = 2_000_000_000
                trade_id = compute_trade_id(["0x" + "66" * 20], [2_000_000], [], [])
                content_hash_bytes = bytes.fromhex(trace.trace_hash.removeprefix("0x"))

                trace_id, tx, block = asyncio.run(publisher.commit(trace, claimed, trade_id, b"\x02"))

                assert (trace_id, tx, block) == (7, "0xRAWTX", 100)
                supported_loader.trace_registry.functions.commit.assert_called_once_with(
                    trace.vault_address, content_hash_bytes, claimed, trade_id, b"\x02"
                )

    def test_commit_rejects_non_32_byte_trade_id(self, supported_loader):
        """Defensive guard: a malformed tradeId must fail loudly rather than
        silently hit the contract's `require(tradeId != bytes32(0))` (or worse,
        an ABI-encode error) with an unhelpful stack trace."""
        from archimedes.chain.trace_publisher import TracePublisher

        publisher = TracePublisher(loader=supported_loader)
        trace = _make_trace()

        with pytest.raises(ValueError, match="32 bytes"):
            asyncio.run(publisher.commit(trace, 2_000_000_000, b"\x01\x02"))

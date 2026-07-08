"""Trace publisher — anchors reasoning traces on-chain.

Implements ITracePublisher from archimedes/interfaces/chain.py.
Publishes keccak256 hashes to ReasoningTraceRegistry on Arc.
"""

from __future__ import annotations

import logging

from archimedes.chain.circle_signer import circle_signer
from archimedes.chain.client import chain_client
from archimedes.chain.contracts import ContractLoader, get_contract_loader
from archimedes.models.trace import ReasoningTrace

logger = logging.getLogger(__name__)


class TracePublisher:
    """Publishes reasoning trace hashes to on-chain ReasoningTraceRegistry.

    Two anchoring paths:
      - ``publish`` → ``publishTrace`` (v1 anchor-after-the-fact; kept for the
        legacy SKIP/error path and existing callers).
      - ``commit`` + ``reveal`` (v1.5 temporal binding): the agent commits the
        trace hash BEFORE the trade and reveals the canonical content AFTER it
        settles. The contract recomputes keccak256 on reveal and enforces
        commit block < execution < reveal block — proving "trace existed before
        the trade". commit/reveal require the v1.5 registry; ``supports_commit_reveal``
        detects whether the deployed ABI exposes them (redeploy gated on #588).
    """

    def __init__(self, loader: ContractLoader | None = None):
        self.loader = loader or get_contract_loader()

    def supports_commit_reveal(self) -> bool:
        """True iff the deployed registry ABI exposes commit() + reveal().

        The v1.5 commit-reveal pair lives in the Solidity source but the deployed
        ABI may still be v1 (publishTrace only) until the registry is redeployed
        (#588). Callers use this to fall back to publishTrace gracefully instead
        of throwing an AttributeError mid-tick.
        """
        try:
            fns = self.loader.trace_registry.functions
            return hasattr(fns, "commit") and hasattr(fns, "reveal")
        except Exception:
            return False

    async def publish(self, trace: ReasoningTrace) -> str | None:
        """Publish a reasoning trace hash on-chain.

        Steps:
          1. trace.compute_hash() → keccak256 hex (32 bytes)
          2. Call ReasoningTraceRegistry.publishTrace(vault, hash, metadata)
          3. Return tx hash
        """
        trace_hash = trace.compute_hash()
        if not trace_hash:
            logger.warning("Trace hash is empty — skipping publish")
            return None

        # keccak256 output is exactly 32 bytes
        trace_hash_bytes = bytes.fromhex(trace_hash.removeprefix("0x"))  # 32 bytes

        # Encode metadata
        metadata = self._encode_metadata(trace)

        vault_addr = chain_client.to_checksum(trace.vault_address)
        registry_addr = chain_client.to_checksum(chain_client.settings.reasoning_trace_registry_address)

        # ── Path 1: Circle Developer-Controlled Wallet ──
        if circle_signer.is_configured:
            try:
                # Circle SDK expects hex strings for bytes/bytes32 types
                trace_hash_hex = "0x" + trace_hash if not trace_hash.startswith("0x") else trace_hash
                metadata_hex = "0x" + metadata.hex() if metadata else "0x"
                logger.info(
                    f"Publishing trace via Circle: vault={vault_addr}, "
                    f"hash={trace_hash_hex[:18]}..., metadata_len={len(metadata)}"
                )
                tx_hash = await circle_signer.execute_contract(
                    contract_address=registry_addr,
                    abi_function="publishTrace(address,bytes32,bytes)",
                    abi_params=[vault_addr, trace_hash_hex, metadata_hex],
                )
                logger.info(f"Trace published via Circle: {tx_hash[:16]}...")
                trace.arc_tx_hash = tx_hash
                return tx_hash
            except Exception as e:
                logger.error(f"Circle publish failed, falling back: {e}")
                # Fall through to raw key path

        # ── Path 2: Raw private key ──
        account = chain_client.settings.agent_account
        if not account:
            logger.warning("No agent account configured — skipping trace publish")
            return None

        registry = self.loader.trace_registry
        nonce = await chain_client.w3.eth.get_transaction_count(account.address)

        try:
            tx = await registry.functions.publishTrace(vault_addr, trace_hash_bytes, metadata).build_transaction(
                {
                    "from": account.address,
                    "nonce": nonce,
                    "chainId": chain_client.settings.chain_id,
                    "gas": 300_000,
                    "gasPrice": await chain_client.w3.eth.gas_price,
                }
            )

            signed = account.sign_transaction(tx)
            tx_hash_bytes = await chain_client.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_hash = tx_hash_bytes.hex()

            logger.info(f"Trace published on-chain: {tx_hash[:16]}...")
            trace.arc_tx_hash = tx_hash
            return tx_hash

        except Exception as e:
            logger.error(f"Failed to publish trace on-chain: {e}")
            return None

    # ── Commit-Reveal (v1.5 temporal binding) ─────────────────────────

    async def commit(
        self,
        trace: ReasoningTrace,
        claimed_execution_time: int,
        trade_id: bytes,
        trade_intent_summary: bytes = b"",
    ) -> tuple[int | None, str | None, int | None]:
        """Commit the trace hash on-chain BEFORE the covered trade executes.

        Calls ``ReasoningTraceRegistry.commit(vault, contentHash, claimedExecutionTime,
        tradeId, tradeIntentSummary)``. The committed ``contentHash`` is keccak256 of
        the trace's canonical JSON — the SAME bytes ``reveal`` will later submit, so
        the on-chain hash binding holds. ``tradeId`` binds this commitment to the
        specific rebalance ``Vault.rebalance()`` will later recompute
        (``keccak256(abi.encode(tokensIn, amountsIn, tokensOut, amountsOut))`` —
        see ``archimedes.chain.executor.compute_trade_id``): the caller MUST derive
        it from the EXACT arrays that will be submitted to
        ``ChainExecutor.execute_trades()``, or the on-chain recompute won't find a
        matching commitment and ``rebalance()`` reverts (#588).

        Args:
            trace: the trace being committed (its hash is computed here if absent).
            claimed_execution_time: unix time the trade is claimed to land at; must be
                strictly after the commit block's timestamp (the contract enforces this).
            trade_id: 32-byte trade identifier matching what ``Vault.rebalance()`` will
                recompute for the covered trade. Must be non-empty (the contract rejects
                ``bytes32(0)``).
            trade_intent_summary: ABI/opaque bytes summarizing intended trades (metadata).

        Returns:
            (trace_id, tx_hash, commit_block) — trace_id is the on-chain id needed to
            reveal; any field is None on failure. Falls back to None if the deployed
            registry has no commit() (pre-#588 redeploy).
        """
        if not self.supports_commit_reveal():
            logger.warning(
                "Registry ABI has no commit() — deployed contract is pre-v1.5 "
                "(redeploy gated on #588). Falling back to publishTrace anchor."
            )
            return None, None, None

        if not trade_id or len(trade_id) != 32:
            raise ValueError(f"trade_id must be 32 bytes, got {len(trade_id) if trade_id else 0}")

        if trade_id == b"\x00" * 32:
            raise ValueError("trade_id must be non-zero (bytes32(0) is rejected on-chain)")

        content_hash = trace.trace_hash or trace.compute_hash()
        content_hash_bytes = bytes.fromhex(content_hash.removeprefix("0x"))  # 32 bytes
        vault_addr = chain_client.to_checksum(trace.vault_address)
        registry_addr = chain_client.to_checksum(chain_client.settings.reasoning_trace_registry_address)

        # ── Path 1: Circle wallet ──
        if circle_signer.is_configured:
            try:
                content_hash_hex = "0x" + content_hash.removeprefix("0x")
                trade_id_hex = "0x" + trade_id.hex()
                intent_hex = "0x" + trade_intent_summary.hex() if trade_intent_summary else "0x"
                tx_hash = await circle_signer.execute_contract(
                    contract_address=registry_addr,
                    abi_function="commit(address,bytes32,uint64,bytes32,bytes)",
                    abi_params=[vault_addr, content_hash_hex, str(claimed_execution_time), trade_id_hex, intent_hex],
                )
                logger.info(f"Trace committed via Circle: {tx_hash[:16]}...")
                return await self._finalize_commit(trace, tx_hash, vault_addr)
            except Exception as e:
                logger.error(f"Circle commit failed, falling back: {e}")

        # ── Path 2: Raw private key ──
        account = chain_client.settings.agent_account
        if not account:
            logger.warning("No agent account configured — skipping trace commit")
            return None, None, None

        registry = self.loader.trace_registry
        try:
            nonce = await chain_client.w3.eth.get_transaction_count(account.address)
            tx = await registry.functions.commit(
                vault_addr, content_hash_bytes, claimed_execution_time, trade_id, trade_intent_summary
            ).build_transaction(
                {
                    "from": account.address,
                    "nonce": nonce,
                    "chainId": chain_client.settings.chain_id,
                    "gas": 300_000,
                    "gasPrice": await chain_client.w3.eth.gas_price,
                }
            )
            signed = account.sign_transaction(tx)
            tx_hash_bytes = await chain_client.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_hash = tx_hash_bytes.hex()
            logger.info(f"Trace committed on-chain: {tx_hash[:16]}...")
            return await self._finalize_commit(trace, tx_hash, vault_addr)
        except Exception as e:
            logger.error(f"Failed to commit trace on-chain: {e}")
            return None, None, None

    async def _finalize_commit(
        self, trace: ReasoningTrace, tx_hash: str, vault_addr: str
    ) -> tuple[int | None, str | None, int | None]:
        """Resolve the on-chain trace_id + block from a commit tx receipt.

        Decodes the TraceCommitted event to read the auto-incremented trace_id; falls
        back to getTracesByVault()[-1] if the event can't be decoded.
        """
        trace.commit_tx_hash = tx_hash
        registry = self.loader.trace_registry
        block_num = None
        trace_id = None
        try:
            receipt = await chain_client.w3.eth.get_transaction_receipt(tx_hash)
            block_num = receipt.blockNumber
            for log in receipt.logs:
                try:
                    decoded = registry.events.TraceCommitted().process_log(log)
                    trace_id = int(decoded["args"]["traceId"])
                    break
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Cannot read commit receipt: {e}")

        if trace_id is None:
            # Fallback: newest trace id for the vault.
            try:
                ids = await registry.functions.getTracesByVault(vault_addr).call()
                trace_id = int(ids[-1]) if ids else None
            except Exception:
                trace_id = None

        trace.commit_block_number = block_num
        return trace_id, tx_hash, block_num

    async def reveal(
        self,
        trace_id: int,
        trace: ReasoningTrace,
        storage_pointer: str = "",
    ) -> tuple[str | None, int | None]:
        """Reveal the full canonical trace content AFTER the trade settles.

        Calls ``ReasoningTraceRegistry.reveal(traceId, storagePointer, fullTraceContent)``.
        ``fullTraceContent`` MUST be the exact canonical bytes whose keccak256 equals the
        committed hash; we derive them from ``trace.canonical_json()`` (the same source the
        commit hash was computed from). ``storage_pointer`` is the IPFS CID (or any URL) so
        verifiers can fetch the off-chain public provenance.

        Returns (reveal_tx_hash, reveal_block) — None on failure or pre-v1.5 registry.
        """
        if not self.supports_commit_reveal():
            logger.warning("Registry ABI has no reveal() — skipping reveal (redeploy gated on #588).")
            return None, None
        if trace_id is None:
            logger.warning("No trace_id to reveal (commit likely failed) — skipping reveal")
            return None, None

        full_content = trace.canonical_json().encode("utf-8")
        registry_addr = chain_client.to_checksum(chain_client.settings.reasoning_trace_registry_address)

        # ── Path 1: Circle wallet ──
        if circle_signer.is_configured:
            try:
                content_hex = "0x" + full_content.hex()
                tx_hash = await circle_signer.execute_contract(
                    contract_address=registry_addr,
                    abi_function="reveal(uint256,string,bytes)",
                    abi_params=[str(trace_id), storage_pointer, content_hex],
                )
                logger.info(f"Trace revealed via Circle: {tx_hash[:16]}...")
                return await self._finalize_reveal(trace, tx_hash)
            except Exception as e:
                logger.error(f"Circle reveal failed, falling back: {e}")

        # ── Path 2: Raw private key ──
        account = chain_client.settings.agent_account
        if not account:
            logger.warning("No agent account configured — skipping trace reveal")
            return None, None

        registry = self.loader.trace_registry
        try:
            nonce = await chain_client.w3.eth.get_transaction_count(account.address)
            tx = await registry.functions.reveal(trace_id, storage_pointer, full_content).build_transaction(
                {
                    "from": account.address,
                    "nonce": nonce,
                    "chainId": chain_client.settings.chain_id,
                    "gas": 500_000,
                    "gasPrice": await chain_client.w3.eth.gas_price,
                }
            )
            signed = account.sign_transaction(tx)
            tx_hash_bytes = await chain_client.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_hash = tx_hash_bytes.hex()
            logger.info(f"Trace revealed on-chain: {tx_hash[:16]}...")
            return await self._finalize_reveal(trace, tx_hash)
        except Exception as e:
            logger.error(f"Failed to reveal trace on-chain: {e}")
            return None, None

    async def _finalize_reveal(self, trace: ReasoningTrace, tx_hash: str) -> tuple[str, int | None]:
        """Record reveal tx + block on the trace and return them."""
        trace.reveal_tx_hash = tx_hash
        trace.arc_tx_hash = tx_hash  # reveal is the canonical anchor tx for this trace
        block_num = None
        try:
            receipt = await chain_client.w3.eth.get_transaction_receipt(tx_hash)
            block_num = receipt.blockNumber
        except Exception:
            logger.debug("reveal receipt block lookup failed", exc_info=True)
        trace.reveal_block_number = block_num
        return tx_hash, block_num

    async def verify(self, trace: ReasoningTrace) -> bool:
        """Verify a trace against its on-chain hash."""
        if not trace.trace_hash:
            return False

        registry = self.loader.trace_registry

        try:
            # Get trace by searching through vault traces
            vault_addr = chain_client.to_checksum(trace.vault_address)
            trace_ids = await registry.functions.getTracesByVault(vault_addr).call()

            if not trace_ids:
                return False

            # Check the most recent traces
            for trace_id in reversed(trace_ids):
                stored = await registry.functions.getTraceById(trace_id).call()
                stored_hash = stored[2]  # bytes32 at index 2

                # Compare
                expected = bytes.fromhex(trace.trace_hash.removeprefix("0x"))  # 32 bytes from keccak256
                if stored_hash == expected:
                    return True

            return False

        except Exception as e:
            logger.error(f"Failed to verify trace: {e}")
            return False

    async def get_trace_count(self, vault_address: str) -> int:
        """Get total published traces for a vault."""
        registry = self.loader.trace_registry

        try:
            vault_addr = chain_client.to_checksum(vault_address)
            ids = await registry.functions.getTracesByVault(vault_addr).call()
            return len(ids)
        except Exception:
            return 0

    async def get_total_trace_count(self) -> int:
        """Get total trace count across all vaults."""
        registry = self.loader.trace_registry
        try:
            return await registry.functions.traceCount().call()
        except Exception:
            return 0

    async def get_trace_by_id(self, trace_id: int) -> dict | None:
        """Get trace details by on-chain ID."""
        registry = self.loader.trace_registry
        try:
            result = await registry.functions.getTraceById(trace_id).call()
            return {
                "agent": result[0],
                "vault": result[1],
                "trace_hash": result[2].hex(),
                "timestamp": result[3],
                "metadata": result[4],
            }
        except Exception as e:
            logger.error(f"Failed to get trace {trace_id}: {e}")
            return None

    async def get_traces_by_vault(self, vault_address: str) -> list[int]:
        """Get on-chain trace IDs for a specific vault."""
        registry = self.loader.trace_registry
        try:
            vault_addr = chain_client.to_checksum(vault_address)
            return await registry.functions.getTracesByVault(vault_addr).call()
        except Exception:
            return []

    async def get_trace_by_tx_hash(self, tx_hash: str) -> dict | None:
        """Get trace details from the TracePublished event in a known tx receipt.

        O(1) verification path — single RPC roundtrip — used by /verify when the
        off-chain trace already remembers its `arc_tx_hash`. Avoids the O(N)
        getTracesByVault → getTraceById scan over a vault's full trace history.

        Returns the same shape as `get_trace_by_id` (agent / vault / trace_hash /
        timestamp / metadata=None) or None if the receipt is missing or the
        TracePublished event cannot be decoded.
        """
        if not tx_hash:
            return None

        registry = self.loader.trace_registry
        try:
            receipt = await chain_client.w3.eth.get_transaction_receipt(tx_hash)
        except Exception as e:
            logger.error(f"Failed to fetch receipt for {tx_hash}: {e}")
            return None

        for log in receipt.logs:
            try:
                decoded = registry.events.TracePublished().process_log(log)
            except Exception:
                continue
            args = decoded["args"]
            trace_hash_raw = args["traceHash"]
            trace_hash_hex = (
                trace_hash_raw.hex() if isinstance(trace_hash_raw, (bytes, bytearray)) else str(trace_hash_raw)
            )
            return {
                "agent": args["agent"],
                "vault": args["vault"],
                "trace_hash": trace_hash_hex,
                "timestamp": args["timestamp"],
                "metadata": None,
                "trace_id": args.get("traceId", 0),
            }

        return None

    def _encode_metadata(self, trace: ReasoningTrace) -> bytes:
        """Encode trace metadata as ABI-encoded bytes for on-chain storage."""
        import json

        metadata_dict = {
            "decision_type": trace.decision_type.value,
            "trigger": trace.trigger,
            "confidence": trace.confidence,
        }
        return json.dumps(metadata_dict).encode("utf-8")


# Singleton
trace_publisher = TracePublisher()

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
    """Publishes reasoning trace hashes to on-chain ReasoningTraceRegistry."""

    def __init__(self, loader: ContractLoader | None = None):
        self.loader = loader or get_contract_loader()

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

"""Strategy publisher — anchors Tier-1 strategies on-chain.

Implements the StrategyRegistry.sol integration. Registers keccak256 hashes
of strategies that pass the rigor gate (DSR + PBO + walk-forward OOS +
look-ahead audit).  Only Tier-1 promotions are anchored — candidate /
rejected strategies are NEVER registered.

Follows the same dual-path signing pattern as trace_publisher.py:
  Path 1: Circle Developer-Controlled Wallet (primary)
  Path 2: Raw private key (fallback)
"""

from __future__ import annotations

import logging

from web3 import Web3

from archimedes.chain.circle_signer import circle_signer
from archimedes.chain.client import chain_client
from archimedes.chain.contracts import ContractLoader, get_contract_loader

logger = logging.getLogger(__name__)


class StrategyPublisher:
    """Registers Tier-1 strategies on-chain via StrategyRegistry.sol."""

    def __init__(self, loader: ContractLoader | None = None):
        self.loader = loader or get_contract_loader()

    def _hash_regime_tag(self, tag: str | None) -> bytes:
        """keccak256 of the regime classification tag (bull/bear/transition/neutral)."""
        return Web3.keccak(text=(tag or "unclassified"))

    def _hash_paper_corpus(self, paper_hashes: list[str]) -> bytes:
        """keccak256 of sorted concatenated paper hashes."""
        if not paper_hashes:
            return Web3.keccak(b"")
        combined = "".join(sorted(paper_hashes))
        return Web3.keccak(text=combined)

    async def anchor(
        self,
        *,
        strategy_id: str,
        methodology_hash: str,
        paper_hashes: list[str] | None = None,
        regime_tag: str | None = None,
        metadata_uri: str = "",
    ) -> str | None:
        """Anchor a Tier-1 strategy on-chain.

        Parameters
        ----------
        strategy_id : str
            The strategy content hash (0x-prefixed keccak256).
        methodology_hash : str
            keccak256 of the strategy methodology / DSL spec.
        paper_hashes : list[str], optional
            Hashes of source papers for the paper corpus hash.
        regime_tag : str, optional
            Regime classification (bull/bear/transition/neutral).
        metadata_uri : str, optional
            Off-chain URI for the full passport JSON.

        Returns
        -------
        str | None
            Transaction hash if successful, None on failure.
        """
        registry_addr = chain_client.settings.strategy_registry_address
        if not registry_addr:
            logger.warning("StrategyRegistry address not configured — skipping anchor")
            return None

        # Prepare arguments
        strategy_id_bytes = bytes.fromhex(strategy_id.removeprefix("0x"))
        methodology_bytes = bytes.fromhex(methodology_hash.removeprefix("0x"))
        paper_corpus_bytes = self._hash_paper_corpus(paper_hashes or [])
        regime_tag_bytes = self._hash_regime_tag(regime_tag)

        registry_checksum = chain_client.to_checksum(registry_addr)

        # ── Path 1: Circle Developer-Controlled Wallet ──
        if circle_signer.is_configured:
            try:
                sid_hex = "0x" + strategy_id_bytes.hex()
                meth_hex = "0x" + methodology_bytes.hex()
                corpus_hex = "0x" + paper_corpus_bytes.hex()
                regime_hex = "0x" + regime_tag_bytes.hex()

                logger.info(f"Anchoring strategy via Circle: id={sid_hex[:18]}..., methodology={meth_hex[:18]}...")
                tx_hash = await circle_signer.execute_contract(
                    contract_address=registry_checksum,
                    abi_function="registerStrategy(bytes32,bytes32,bytes32,bytes32,string)",
                    abi_params=[sid_hex, meth_hex, corpus_hex, regime_hex, metadata_uri],
                )
                logger.info(f"Strategy anchored via Circle: {tx_hash[:16]}...")
                return tx_hash
            except Exception as e:
                logger.error(f"Circle anchor failed, falling back: {e}")

        # ── Path 2: Raw private key ──
        account = chain_client.settings.agent_account
        if not account:
            logger.warning("No agent account configured — skipping strategy anchor")
            return None

        registry = self.loader.strategy_registry
        nonce = await chain_client.w3.eth.get_transaction_count(account.address)

        try:
            tx = await registry.functions.registerStrategy(
                strategy_id_bytes,
                methodology_bytes,
                paper_corpus_bytes,
                regime_tag_bytes,
                metadata_uri,
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
            logger.info(f"Strategy anchored on-chain: {tx_hash[:16]}...")
            return tx_hash

        except Exception as e:
            logger.error(f"Failed to anchor strategy on-chain: {e}")
            return None

    async def is_anchored(self, strategy_id: str) -> bool:
        """Check if a strategy is registered on-chain."""
        registry_addr = chain_client.settings.strategy_registry_address
        if not registry_addr:
            return False

        try:
            registry = self.loader.strategy_registry
            sid_bytes = bytes.fromhex(strategy_id.removeprefix("0x"))
            return await registry.functions.isRegistered(sid_bytes).call()
        except Exception as e:
            logger.error(f"Failed to check strategy registration: {e}")
            return False

    async def get_strategy(self, strategy_id: str) -> dict | None:
        """Get on-chain registration details for a strategy."""
        registry_addr = chain_client.settings.strategy_registry_address
        if not registry_addr:
            return None

        try:
            registry = self.loader.strategy_registry
            sid_bytes = bytes.fromhex(strategy_id.removeprefix("0x"))
            result = await registry.functions.getStrategy(sid_bytes).call()
            return {
                "registrar": result[0],
                "methodology_hash": result[1].hex(),
                "paper_corpus_hash": result[2].hex(),
                "regime_tag": result[3].hex(),
                "timestamp": result[4],
                "metadata_uri": result[5],
            }
        except Exception as e:
            logger.error(f"Failed to get strategy {strategy_id}: {e}")
            return None

    async def strategy_count(self) -> int:
        """Get total number of registered strategies."""
        registry_addr = chain_client.settings.strategy_registry_address
        if not registry_addr:
            return 0

        try:
            registry = self.loader.strategy_registry
            return await registry.functions.strategyCount().call()
        except Exception:
            return 0


# Singleton
strategy_publisher = StrategyPublisher()

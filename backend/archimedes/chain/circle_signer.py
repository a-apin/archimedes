"""Circle Developer-Controlled Wallet signer — executes on-chain txs via Circle API.

Replaces raw private key signing with Circle's managed wallet infrastructure.
Uses the same pattern as oracle_updater.py: encrypt entity secret with Circle's
RSA public key, submit contract execution via REST API, poll until COMPLETE.

Env vars:
  CIRCLE_API_KEY       — Circle API key (TEST_API_KEY:UUID:SECRET)
  CIRCLE_ENTITY_SECRET — 32-byte hex entity secret
  WALLET_ID            — Circle wallet UUID
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import uuid

import aiohttp

logger = logging.getLogger(__name__)

CIRCLE_API_BASE = "https://api.circle.com/v1/w3s"
CIRCLE_BLOCKCHAIN = "ARC-TESTNET"

# Transaction terminal states
_TERMINAL = {"COMPLETE", "FAILED", "DENIED", "CANCELLED"}
_POLL_INTERVAL = 2.0  # seconds
_MAX_POLLS = 60  # 2 minutes max


def _encrypt_entity_secret(entity_secret_hex: str, public_key_pem: str) -> str:
    """Encrypt entity secret with Circle's RSA public key (OAEP/SHA-256)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    public_key = serialization.load_pem_public_key(public_key_pem.encode())
    plaintext = bytes.fromhex(entity_secret_hex)
    ciphertext = public_key.encrypt(
        plaintext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode()


class CircleSigner:
    """Signs and submits on-chain transactions via Circle Developer-Controlled Wallets.

    Usage:
        signer = CircleSigner()
        tx_hash = await signer.execute_contract(
            contract_address="0x...",
            abi_function="setTargetAllocations(address[],uint256[])",
            abi_params=[tokens, weights],
        )
    """

    def __init__(self) -> None:
        self._api_key: str = os.getenv("CIRCLE_API_KEY", "")
        self._entity_secret: str = os.getenv("CIRCLE_ENTITY_SECRET", "")
        self._wallet_id: str = os.getenv("WALLET_ID", "")
        self._circle_public_key: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self._entity_secret and self._wallet_id)

    async def _get_public_key(self, session: aiohttp.ClientSession) -> str | None:
        """Fetch Circle's RSA public key (cached)."""
        if self._circle_public_key:
            return self._circle_public_key

        async with session.get(
            f"{CIRCLE_API_BASE}/config/entity/publicKey",
            headers={"Authorization": f"Bearer {self._api_key}"},
        ) as resp:
            if resp.status == 200:
                body = await resp.json()
                self._circle_public_key = body["data"]["publicKey"]
                return self._circle_public_key
            logger.error("Failed to fetch Circle public key: %d", resp.status)
        return None

    async def execute_contract(
        self,
        contract_address: str,
        abi_function: str,
        abi_params: list,
        fee_level: str = "MEDIUM",
    ) -> str:
        """Execute a write function on a deployed contract via Circle.

        Args:
            contract_address: The deployed contract address.
            abi_function: ABI function signature, e.g. "setTargetAllocations(address[],uint256[])"
            abi_params: List of ABI-encoded parameters.
            fee_level: Gas fee level — "LOW", "MEDIUM", or "HIGH".

        Returns:
            The on-chain transaction hash.

        Raises:
            RuntimeError: If Circle credentials are missing or the tx fails.
        """
        if not self.is_configured:
            raise RuntimeError("Circle credentials not configured (CIRCLE_API_KEY / CIRCLE_ENTITY_SECRET / WALLET_ID)")

        async with aiohttp.ClientSession() as session:
            public_key = await self._get_public_key(session)
            if not public_key:
                raise RuntimeError("Failed to fetch Circle public key")

            ciphertext = _encrypt_entity_secret(self._entity_secret, public_key)

            payload = {
                "idempotencyKey": str(uuid.uuid4()),
                "walletId": self._wallet_id,
                "contractAddress": contract_address,
                "abiFunctionSignature": abi_function,
                "abiParameters": abi_params,  # Circle handles ABI encoding
                "feeLevel": fee_level,
                "blockchain": CIRCLE_BLOCKCHAIN,
                "entitySecretCiphertext": ciphertext,
            }

            # Submit transaction
            async with session.post(
                f"{CIRCLE_API_BASE}/developer/transactions/contractExecution",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                body = await resp.json()
                if resp.status != 201:
                    raise RuntimeError(f"Circle contract execution failed ({resp.status}): {body}")
                circle_tx_id = body["data"]["id"]
                logger.info("Circle tx submitted: %s", circle_tx_id)

            # Poll until terminal state
            tx_hash = await self._poll_transaction(session, circle_tx_id)
            return tx_hash

    async def sign_and_broadcast(
        self,
        tx_object: dict,
    ) -> str:
        """Sign a raw EVM transaction and broadcast it.

        Lower-level alternative to execute_contract() when you need full
        control over the transaction object (e.g. custom gas params).

        Args:
            tx_object: EVM transaction dict matching Ethereum JSON-RPC shape.
                       Must include chainId, nonce, to, value, gas,
                       maxFeePerGas, maxPriorityFeePerGas, and optionally data.

        Returns:
            The on-chain transaction hash.
        """
        if not self.is_configured:
            raise RuntimeError("Circle credentials not configured")

        async with aiohttp.ClientSession() as session:
            payload = {
                "walletId": self._wallet_id,
                "transaction": tx_object if isinstance(tx_object, str) else str(tx_object),
            }

            # Sign
            async with session.post(
                f"{CIRCLE_API_BASE}/wallets/{self._wallet_id}/transactions/sign",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                body = await resp.json()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"Circle sign failed ({resp.status}): {body}")

                signed_tx = body["data"]["signedTransaction"]
                tx_hash = body["data"].get("txHash", "")

            # Broadcast via Arc RPC
            if signed_tx:
                from archimedes.chain.client import chain_client

                tx_hash = await chain_client.w3.eth.send_raw_transaction(bytes.fromhex(signed_tx.removeprefix("0x")))
                return tx_hash.hex()

            return tx_hash

    async def _poll_transaction(self, session: aiohttp.ClientSession, circle_tx_id: str) -> str:
        """Poll Circle transaction until terminal state."""
        for _ in range(_MAX_POLLS):
            # Use the list endpoint — Circle's GET /transactions returns
            # all recent txs for the wallet. Find ours by ID.
            async with session.get(
                f"{CIRCLE_API_BASE}/transactions?pageSize=50",
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    txs = body.get("data", {}).get("transactions", [])
                    for tx in txs:
                        if tx.get("id") == circle_tx_id:
                            state = tx.get("state", "UNKNOWN")
                            tx_hash = tx.get("txHash", "")

                            if state in _TERMINAL:
                                if state == "COMPLETE":
                                    logger.info(
                                        "Circle tx %s complete: %s",
                                        circle_tx_id,
                                        tx_hash,
                                    )
                                    return tx_hash
                                else:
                                    raise RuntimeError(f"Circle tx {circle_tx_id} ended in {state}: {tx}")
                            # Still processing
                            break
                await asyncio.sleep(_POLL_INTERVAL)

        raise RuntimeError(f"Circle tx {circle_tx_id} timed out after {_MAX_POLLS} polls")


# Singleton
circle_signer = CircleSigner()

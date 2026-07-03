"""Circle Developer-Controlled Wallet provisioning for subscribers.

Creates a Circle Developer-Controlled Wallet for each subscriber at
subscription time. The wallet serves triple duty:
  1. x402 signing (replaces raw private-key signing — kills D2)
  2. Funded balance address (the readiness gate checks it — kills D3)
  3. The monolith signs micropayments in-process via CircleWalletSigner

Uses the same Circle API credentials and encrypt-entity-secret pattern
as ``chain/circle_signer.py``. Idempotent: passes ``sub_id`` in the
wallet metadata/ref field so a retry does not create a duplicate wallet.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import uuid

import aiohttp

logger = logging.getLogger(__name__)

CIRCLE_API_BASE = "https://api.circle.com/v1/w3s"
CIRCLE_BLOCKCHAIN = "ARC-TESTNET"


def _encrypt_entity_secret(entity_secret_hex: str, public_key_pem: str) -> str:
    """Encrypt entity secret with Circle's RSA public key (OAEP/SHA-256).

    Mirrors ``archimedes.chain.circle_signer._encrypt_entity_secret``.
    """
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


async def provision_subscriber_wallet(sub_id: str) -> tuple[str, str]:
    """Create a Circle Developer-Controlled Wallet for a subscriber.

    Args:
        sub_id: The 0x-prefixed 32-byte subscriber ID (bytes32 hex).

    Returns:
        A tuple ``(wallet_id, wallet_address)``.

    Raises:
        RuntimeError: If Circle credentials are missing or the API call fails.
    """
    api_key = os.getenv("CIRCLE_API_KEY", "")
    entity_secret = os.getenv("CIRCLE_ENTITY_SECRET", "")

    if not api_key or not entity_secret:
        raise RuntimeError(
            "Circle credentials not configured (CIRCLE_API_KEY / CIRCLE_ENTITY_SECRET)"
        )

    async with aiohttp.ClientSession() as session:
        # 1. Fetch Circle's RSA public key
        public_key: str | None = None
        async with session.get(
            f"{CIRCLE_API_BASE}/config/entity/publicKey",
            headers={"Authorization": f"Bearer {api_key}"},
        ) as resp:
            if resp.status == 200:
                body = await resp.json()
                public_key = body["data"]["publicKey"]
            else:
                raise RuntimeError(
                    f"Failed to fetch Circle public key: {resp.status}"
                )

        ciphertext = _encrypt_entity_secret(entity_secret, public_key)

        # 2. Create the wallet with sub_id in the ref/metadata for idempotency
        payload = {
            "idempotencyKey": str(uuid.uuid4()),
            "blockchain": CIRCLE_BLOCKCHAIN,
            "metadata": [
                {"name": "ref", "value": f"sub:{sub_id}"},
                {"name": "purpose", "value": "x402_subscriber_signing"},
            ],
            "entitySecretCiphertext": ciphertext,
        }

        async with session.post(
            f"{CIRCLE_API_BASE}/developer/wallets",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        ) as resp:
            body = await resp.json()
            if resp.status == 201:
                wallet_id: str = body["data"]["wallet"]["id"]
                wallet_address: str = body["data"]["wallet"]["address"]
                logger.info(
                    "Created Circle wallet %s for subscriber %s (address=%s)",
                    wallet_id, sub_id, wallet_address,
                )
                return wallet_id, wallet_address
            # 409 Conflict means the idempotency key already created a wallet
            if resp.status == 409:
                logger.warning(
                    "Wallet creation conflict for sub %s (idempotent retry): %s",
                    sub_id, body,
                )
                # Try to find the existing wallet by listing
                existing = await _find_wallet_by_ref(session, api_key, f"sub:{sub_id}")
                if existing:
                    return existing["id"], existing["address"]
                raise RuntimeError(
                    f"Wallet creation conflict but could not find existing wallet "
                    f"for sub {sub_id}"
                )

            raise RuntimeError(
                f"Circle wallet creation failed ({resp.status}): {body}"
            )


async def _find_wallet_by_ref(
    session: aiohttp.ClientSession,
    api_key: str,
    ref: str,
) -> dict | None:
    """List Circle wallets and find one whose metadata matches *ref*."""
    async with session.get(
        f"{CIRCLE_API_BASE}/developer/wallets?pageSize=100",
        headers={"Authorization": f"Bearer {api_key}"},
    ) as resp:
        if resp.status == 200:
            body = await resp.json()
            wallets = body.get("data", {}).get("wallets", [])
            for w in wallets:
                meta = w.get("metadata", [])
                if isinstance(meta, list):
                    for entry in meta:
                        if entry.get("name") == "ref" and entry.get("value") == ref:
                            return {"id": w["id"], "address": w["address"]}
        return None

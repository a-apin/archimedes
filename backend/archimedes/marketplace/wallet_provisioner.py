"""Circle Developer-Controlled Wallet provisioning for subscribers and publishers.

Creates a Circle Developer-Controlled Wallet for each subscriber at
subscription time and for each publisher at publish time. The wallet serves
triple duty:
  1. x402 signing (replaces raw private-key signing — kills D2)
  2. Funded balance address (the readiness gate checks it — kills D3)
  3. The monolith signs micropayments in-process via CircleWalletSigner

Uses the same Circle API credentials and encrypt-entity-secret pattern
as ``chain/circle_signer.py``. Truly idempotent: the idempotency key is
deterministically derived from the reference value (``sub:<sub_id>`` /
``pub:<pool_id>``), so a retry reuses the same key and Circle returns
the existing wallet instead of provisioning a duplicate.
"""

from __future__ import annotations

import base64
import logging
import os
import uuid

import aiohttp

logger = logging.getLogger(__name__)

CIRCLE_API_BASE = "https://api.circle.com/v1/w3s"
# Circle blockchain identifier for provisioned wallets. Env-overridable so
# moving to mainnet or another testnet is a config change, not a code change
# (a hardcoded mismatch surfaces only as opaque 502s at provision time).
CIRCLE_BLOCKCHAIN = os.getenv("CIRCLE_BLOCKCHAIN", "ARC-TESTNET")


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


async def _create_circle_wallet(ref_value: str, ref_purpose: str) -> tuple[str, str]:
    """Shared Circle DCW creation flow used by both subscriber and publisher
    provisioning.  Fetches the RSA public key, encrypts the entity secret, and
    creates a wallet with the given metadata reference for idempotency.

    Args:
        ref_value: Unique reference value (e.g. ``"sub:<sub_id>"`` or ``"pub:<pool_id>"``).
        ref_purpose: Human-readable purpose string for wallet metadata.

    Returns:
        A tuple ``(wallet_id, wallet_address)``.

    Raises:
        RuntimeError: If Circle credentials are missing or the API call fails.
    """
    api_key = os.getenv("CIRCLE_API_KEY", "")
    entity_secret = os.getenv("CIRCLE_ENTITY_SECRET", "")

    if not api_key or not entity_secret:
        raise RuntimeError("Circle credentials not configured (CIRCLE_API_KEY / CIRCLE_ENTITY_SECRET)")

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
                raise RuntimeError(f"Failed to fetch Circle public key: {resp.status}")

        ciphertext = _encrypt_entity_secret(entity_secret, public_key)

        # 2. Create the wallet with a deterministic idempotency key derived from the ref.
        #    Retries for the same ref_value reuse the same key → Circle returns the
        #    existing wallet (409) instead of provisioning a duplicate.
        payload = {
            "idempotencyKey": str(uuid.uuid5(uuid.NAMESPACE_URL, f"archimedes-wallet:{ref_value}")),
            "blockchain": CIRCLE_BLOCKCHAIN,
            "metadata": [
                {"name": "ref", "value": ref_value},
                {"name": "purpose", "value": ref_purpose},
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
                    "Created Circle wallet %s for ref=%s (address=%s)",
                    wallet_id,
                    ref_value,
                    wallet_address,
                )
                return wallet_id, wallet_address
            # 409 Conflict means the idempotency key already created a wallet
            if resp.status == 409:
                logger.warning(
                    "Wallet creation conflict for ref=%s (idempotent retry): %s",
                    ref_value,
                    body,
                )
                # Try to find the existing wallet by listing
                existing = await _find_wallet_by_ref(session, api_key, ref_value)
                if existing:
                    return existing["id"], existing["address"]
                raise RuntimeError(f"Wallet creation conflict but could not find existing wallet for ref={ref_value}")

            raise RuntimeError(f"Circle wallet creation failed ({resp.status}): {body}")


async def provision_subscriber_wallet(sub_id: str) -> tuple[str, str]:
    """Create a Circle Developer-Controlled Wallet for a subscriber.

    Args:
        sub_id: The 0x-prefixed 32-byte subscriber ID (bytes32 hex).

    Returns:
        A tuple ``(wallet_id, wallet_address)``.

    Raises:
        RuntimeError: If Circle credentials are missing or the API call fails.
    """
    return await _create_circle_wallet(
        ref_value=f"sub:{sub_id}",
        ref_purpose="x402_subscriber_signing",
    )


async def provision_publisher_wallet(pool_id: str) -> tuple[str, str]:
    """Create a Circle Developer-Controlled Wallet for a publisher.

    Args:
        pool_id: The pool ID (bytes32 hex string) identifying the publisher's pool.

    Returns:
        A tuple ``(wallet_id, wallet_address)``.

    Raises:
        RuntimeError: If Circle credentials are missing or the API call fails.
    """
    return await _create_circle_wallet(
        ref_value=f"pub:{pool_id}",
        ref_purpose="publisher_settlement",
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

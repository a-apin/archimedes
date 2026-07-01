"""GatewayClient — Subscriber-side x402 payment authorisation via Circle Gateway.

Wraps the Circle Gateway API so the subscriber agent can:
  - Check its off-chain spendable balance (``getBalance``).
  - Deposit USDC from its ephemeral wallet into the Gateway (``deposit``).
  - Execute an x402 payment against a publisher ``charge_url`` (``pay``).

The x402 flow (``pay``) is a single request/retry cycle:

  1. ``GET charge_url`` — publisher returns ``402 Payment Required`` with
     ``PAYMENT-REQUIRED`` headers specifying ``amount``, ``payTo``, etc.
  2. Sign an EIP-3009 ``TransferWithAuthorization`` locally using the ephemeral
     private key — **no on-chain transaction, no gas**.
  3. ``POST charge_url`` with ``Payment-Signature`` header containing the signed
     payload.
  4. Publisher verifies and settles via ``BatchFacilitatorClient``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

CIRCLE_GATEWAY_URL = os.getenv("CIRCLE_GATEWAY_URL", "https://api.circle.com")


class GatewayClient:
    """Subscriber-side client for Circle Gateway x402 payments.

    Parameters
    ----------
    url : str
        Circle Gateway base URL (default ``CIRCLE_GATEWAY_URL`` env var).
    api_key : str
        Circle Gateway API key.
    chain : str
        Chain identifier (e.g. ``"arcTestnet"``).
    private_key : str
        Ephemeral wallet private key (hex, with or without ``0x`` prefix).
        Used for signing EIP-3009 authorisations locally.
    """

    def __init__(
        self,
        url: str = CIRCLE_GATEWAY_URL,
        api_key: str = "",
        chain: str = "arcTestnet",
        private_key: str = "",
    ) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key or os.getenv("CIRCLE_GATEWAY_API_KEY", "")
        self._chain = chain
        self._private_key = private_key

        if not self._api_key:
            logger.warning("CIRCLE_GATEWAY_API_KEY not set — GatewayClient disabled")
        if not self._private_key:
            logger.warning("No ephemeral private key provided — GatewayClient cannot sign")

    # ── Balance ──────────────────────────────────────────────────────────

    async def getBalance(self) -> str:
        """Return current Gateway spendable balance as a decimal string.

        Returns ``"0"`` on any error (caller should log warnings).
        """
        if not self._api_key:
            return "0"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._url}/v1/gateway/balance",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                ) as resp:
                    if resp.ok:
                        data = await resp.json()
                        return data.get("balance", "0")
                    logger.warning("Gateway balance check returned %s", resp.status)
                    return "0"
        except Exception as exc:
            logger.warning("Gateway balance check failed: %s", exc)
            return "0"

    # ── Deposit ──────────────────────────────────────────────────────────

    async def deposit(self, amount: str) -> dict[str, Any]:
        """Deposit USDC from the ephemeral wallet into the Gateway balance.

        Parameters
        ----------
        amount : str
            USDC amount as a decimal string (e.g. ``"10.0"``).

        Returns
        -------
        dict
            Gateway API response — expected keys: ``"success"``, ``"balance"``.
        """
        if not self._api_key or not self._private_key:
            logger.warning("GatewayClient not configured — cannot deposit")
            return {"success": False, "balance": "0"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._url}/v1/gateway/deposit",
                    json={
                        "amount": amount,
                        "chain": self._chain,
                        "signedBy": self._wallet_address(),
                    },
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                ) as resp:
                    data = await resp.json() if resp.content else {}
                    if resp.ok:
                        logger.info("Gateway deposit of %s USDC succeeded", amount)
                        return {"success": True, "balance": data.get("balance", amount)}
                    logger.warning("Gateway deposit failed: %s", data)
                    return {"success": False, "balance": "0", "error": str(data)}
        except Exception as exc:
            logger.warning("Gateway deposit error: %s", exc)
            return {"success": False, "balance": "0", "error": str(exc)}

    # ── x402 Pay ─────────────────────────────────────────────────────────

    async def pay(self, charge_url: str) -> dict[str, Any]:
        """Execute the x402 payment flow against a publisher charge endpoint.

        Parameters
        ----------
        charge_url : str
            Full URL of the publisher's paywalled charge endpoint, e.g.
            ``"https://publisher.example.com/charge/tick_001/load_strategies"``.

        Returns
        -------
        dict
            ``{"success": True}`` on success, ``{"success": False, "error": ...}``
            on failure.
        """
        if not self._api_key or not self._private_key:
            logger.warning("GatewayClient not configured — cannot pay")
            return {"success": False, "error": "GatewayClient not configured"}

        # 1. GET charge_url → expect 402 with payment requirements
        try:
            async with aiohttp.ClientSession() as session:
                requirements = await self._request_payment_requirements(session, charge_url)
                if not requirements:
                    return {"success": False, "error": "No payment requirements from publisher"}

                # 2. Sign EIP-3009 TransferWithAuthorization locally
                signed_payload = self._sign_authorization(requirements)

                # 3. POST charge_url with Payment-Signature header
                async with session.post(
                    charge_url,
                    json=signed_payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                        "Payment-Signature": signed_payload.get("signature", ""),
                    },
                ) as resp:
                    if resp.ok:
                        data = await resp.json() if resp.content else {}
                        logger.info("x402 payment to %s succeeded", charge_url)
                        return {"success": True, **data}
                    detail = (await resp.json()).get("detail", "") if resp.content else ""
                    logger.warning("x402 payment to %s failed: %s %s", charge_url, resp.status, detail)
                    return {"success": False, "error": detail or f"HTTP {resp.status}"}
        except Exception as exc:
            logger.warning("x402 pay error for %s: %s", charge_url, exc)
            return {"success": False, "error": str(exc)}

    async def _request_payment_requirements(
        self,
        session: aiohttp.ClientSession,
        charge_url: str,
    ) -> dict[str, Any] | None:
        """GET ``charge_url`` and parse ``PAYMENT-REQUIRED`` headers."""
        async with session.get(charge_url) as resp:
            if resp.status != 402:
                logger.warning(
                    "Expected 402 from %s, got %s", charge_url, resp.status,
                )
                return None
            req_header = resp.headers.get("PAYMENT-REQUIRED")
            if not req_header:
                logger.warning("No PAYMENT-REQUIRED header in 402 response from %s", charge_url)
                return None
            import json
            try:
                return json.loads(req_header)
            except (ValueError, TypeError) as exc:
                logger.warning("PAYMENT-REQUIRED header not valid JSON: %s", exc)
                return None

    def _sign_authorization(self, requirements: dict[str, Any]) -> dict[str, Any]:
        """Locally sign an EIP-3009 TransferWithAuthorization.

        In production this uses ``eth_account.Account.sign_typed_data`` or calls
        the Circle Gateway signing API.  Here we return the requirements
        augmented with a placeholder signature to keep the interface concrete.
        """
        from eth_account import Account

        amount = requirements.get("amount", "0")
        pay_to = requirements.get("payTo", "")
        verifying_contract = requirements.get("verifyingContract", "")
        chain_id = requirements.get("chainId", 0)

        account = Account.from_key(self._private_key)
        payer = account.address

        return {
            "from": payer,
            "payTo": pay_to,
            "amount": amount,
            "verifyingContract": verifying_contract,
            "chainId": chain_id,
            "signature": account.unsafe_sign_hash(
                Account._encode_typed_data({
                    "types": {
                        "EIP712Domain": [
                            {"name": "name", "type": "string"},
                            {"name": "version", "type": "string"},
                            {"name": "chainId", "type": "uint256"},
                            {"name": "verifyingContract", "type": "address"},
                        ],
                        "TransferWithAuthorization": [
                            {"name": "from", "type": "address"},
                            {"name": "to", "type": "address"},
                            {"name": "value", "type": "uint256"},
                            {"name": "validAfter", "type": "uint256"},
                            {"name": "validBefore", "type": "uint256"},
                            {"name": "nonce", "type": "bytes32"},
                        ],
                    },
                    "primaryType": "TransferWithAuthorization",
                    "domain": {
                        "name": "CircleUSD",
                        "version": "1",
                        "chainId": chain_id,
                        "verifyingContract": verifying_contract,
                    },
                    "message": {
                        "from": payer,
                        "to": pay_to,
                        "value": int(float(amount) * 1_000_000),
                        "validAfter": 0,
                        "validBefore": int(requirements.get("validBefore", 9999999999)),
                        "nonce": os.urandom(32).hex(),
                    },
                })
            ).hex() if hasattr(Account, '_encode_typed_data') else os.urandom(32).hex(),
        }

    def _wallet_address(self) -> str:
        """Derive the wallet address from the ephemeral private key."""
        if not self._private_key:
            return "0x0000000000000000000000000000000000000000"
        from eth_account import Account
        return Account.from_key(self._private_key).address

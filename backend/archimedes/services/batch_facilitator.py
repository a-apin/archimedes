"""BatchFacilitatorClient — Publisher-side x402 payment verification & settlement.

Wraps the Circle Gateway ``BatchFacilitator`` API so the publisher agent can:

  - Verify an EIP-3009 ``TransferWithAuthorization`` submitted by a subscriber
    (``verify``).
  - Settle a verified authorisation, queuing it for on-chain batched settlement
    (``settle``).
  - Track settlement results, including on-chain transaction hashes when the
    Gateway has flushed a batch.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

CIRCLE_GATEWAY_URL = os.getenv("CIRCLE_GATEWAY_URL", "https://api.circle.com")


class BatchFacilitatorClient:
    """Publisher-side client for Circle Gateway batch-facilitated settlement.

    Parameters
    ----------
    url : str
        Circle Gateway base URL (default ``CIRCLE_GATEWAY_URL`` env var).
    api_key : str
        Circle Gateway API key.
    """

    def __init__(
        self,
        url: str = CIRCLE_GATEWAY_URL,
        api_key: str = "",
    ) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key or os.getenv("CIRCLE_GATEWAY_API_KEY", "")

        if not self._api_key:
            logger.warning("CIRCLE_GATEWAY_API_KEY not set — BatchFacilitatorClient disabled")

    # ── Verify ───────────────────────────────────────────────────────────

    async def verify(
        self,
        payment_header: str,
        requirements: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify an EIP-3009 ``TransferWithAuthorization``.

        Delegates to the Circle Gateway verification endpoint.  Returns a
        dict with at least ``"success": True | False``.

        Parameters
        ----------
        payment_header : str
            The ``Payment-Signature`` header value from the subscriber's
            charge request.
        requirements : dict
            Payment requirements dict with ``amount``, ``payTo``,
            ``verifyingContract``, ``chainId``, etc.
        """
        if not self._api_key:
            logger.warning("BatchFacilitatorClient not configured — cannot verify")
            return {"success": False, "error": "not configured"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._url}/v1/gateway/verify",
                    json={
                        "paymentHeader": payment_header,
                        "requirements": requirements,
                    },
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                ) as resp:
                    data = await resp.json() if resp.content else {}
                    if resp.ok and data.get("success"):
                        logger.info("Gateway verification passed")
                        return {"success": True}
                    logger.warning("Gateway verification failed: %s", data)
                    return {"success": False, "error": str(data)}
        except Exception as exc:
            logger.warning("Gateway verification error: %s", exc)
            return {"success": False, "error": str(exc)}

    # ── Settle ───────────────────────────────────────────────────────────

    async def settle(
        self,
        payment_header: str,
        requirements: dict[str, Any],
    ) -> dict[str, Any]:
        """Settle a verified authorisation via the Circle Gateway.

        Queues the authorisation for batched on-chain settlement.  Returns a
        dict that may include ``transaction_hash`` when the Gateway has
        flushed a batch on-chain.

        Parameters
        ----------
        payment_header : str
            The ``Payment-Signature`` header value from the subscriber's
            charge request.
        requirements : dict
            Payment requirements dict.
        """
        if not self._api_key:
            logger.warning("BatchFacilitatorClient not configured — cannot settle")
            return {"success": False, "error": "not configured"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._url}/v1/gateway/settle",
                    json={
                        "paymentHeader": payment_header,
                        "requirements": requirements,
                    },
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                ) as resp:
                    data = await resp.json() if resp.content else {}
                    if resp.ok and data.get("success"):
                        tx_hash = data.get("transaction_hash") or data.get("txHash")
                        logger.info(
                            "Gateway settlement%s",
                            f" — tx {tx_hash}" if tx_hash else " (queued)",
                        )
                        return {
                            "success": True,
                            "payer": data.get("payer", ""),
                            "amount_settled_usdc_raw": data.get("amountSettled", 0),
                            "transaction_hash": tx_hash,
                        }
                    logger.warning("Gateway settlement failed: %s", data)
                    return {"success": False, "error": str(data)}
        except Exception as exc:
            logger.warning("Gateway settlement error: %s", exc)
            return {"success": False, "error": str(exc)}

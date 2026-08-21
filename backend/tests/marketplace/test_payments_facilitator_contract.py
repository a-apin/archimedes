"""Pins the two facilitator-contract fixes from the first real settle (2026-08-20).

The generation paywall's verify+settle branch first executed against Circle's
live Gateway facilitator during the #834 flip smoke, which rejected every
payment twice over: (1) `paymentPayload.resource.{url,description,mimeType}`
are REQUIRED but circlekit defaults resource to {} when the caller omits it;
(2) the SDK's DEFAULT_MAX_TIMEOUT_SECONDS (345600 = 4 days) is below the
facilitator's minimum authorization validity — everything under 604800 (7
days) returns `authorization_validity_too_short`. Both fixes live in
archimedes.marketplace.payments (the declared circlekit-drift seam); these
tests fail against the unpatched module (adversarially demonstrated in the PR).

Hermetic: `middleware.require()` computes requirements locally — no network.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

RECIPIENT = "0x00000000000000000000000000000000000000a1"


def _decoded_requirements():
    from archimedes.marketplace.payments import get_gateway_middleware

    required = get_gateway_middleware(RECIPIENT).require("2.00", "/api/generate/start")
    header = required["headers"]["PAYMENT-REQUIRED"]
    return json.loads(base64.b64decode(header))


def test_402_advertises_at_least_the_facilitator_minimum_validity() -> None:
    """Every accepts[] entry must carry maxTimeoutSeconds >= 604800.

    The UI derives the signed authorization's validBefore from the SERVER's
    advertised window — a smaller value bricks every honest browser payment
    with `authorization_validity_too_short`.
    """
    from archimedes.marketplace.payments import GATEWAY_MIN_AUTH_VALIDITY_SECONDS

    decoded = _decoded_requirements()
    accepts = decoded.get("accepts") or []
    assert accepts, "402 carried no payment options"
    for option in accepts:
        assert option["maxTimeoutSeconds"] >= 604_800, option
    assert GATEWAY_MIN_AUTH_VALIDITY_SECONDS == 604_800


def test_402_resource_object_is_complete() -> None:
    """The resource the 402 hands out must carry the three REQUIRED fields the
    facilitator validates on the payload echo."""
    resource = _decoded_requirements().get("resource") or {}
    for field in ("url", "description", "mimeType"):
        assert resource.get(field), f"402 resource missing {field}"


async def test_charge_passes_resource_through_to_the_header_builder() -> None:
    """charge() must forward the 402's resource into create_payment_header —
    omitting it makes circlekit send resource={} and the facilitator 400s."""
    import archimedes.marketplace.payments as payments

    header_builder = MagicMock(return_value="hdr")
    middleware = MagicMock()
    middleware.require = payments.get_gateway_middleware(RECIPIENT).require
    middleware.verify = AsyncMock(return_value=MagicMock(is_valid=True))
    middleware.settle = AsyncMock(return_value=MagicMock())

    with (
        patch.object(payments, "create_payment_header", header_builder),
        patch.object(payments, "get_gateway_middleware", return_value=middleware),
        patch.object(payments, "_get_signer", return_value=MagicMock(address=RECIPIENT)),
    ):
        ok = await payments.charge(
            sub_id="sub-1",
            wallet_id="w-1",
            wallet_address=RECIPIENT,
            seller_address=RECIPIENT,
            strategy_id="strat-1",
            tick_id="tick-1",
            action_count=1,
            flat_fee_raw=2_000_000,
        )
    assert ok is True
    _, kwargs = header_builder.call_args
    resource = kwargs.get("resource")
    assert isinstance(resource, dict) and resource.get("url"), (
        "charge() did not forward the 402's resource object to create_payment_header"
    )

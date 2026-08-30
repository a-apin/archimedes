"""A Circle Gateway fake that enforces the rule Circle actually enforces.

Why this exists: both sweep paths (services/revenue_sweep.py Stage A and
marketplace/settlement.py Stage A) shipped asking Circle to withdraw the
*entire* available Gateway balance, and both were covered by tests using a
bare ``AsyncMock`` for ``withdraw`` — a mock that accepts every argument and
therefore cannot fail. The bug surfaced only in production, on the first real
sweep::

    POST /v1/transfer -> 400
    {"success": false, "message": "Insufficient balance for depositor
     0xffa7abba...56c1: available 36.000000, required 36.0035"}

Circle charges its withdrawal fee **on top of** the burn amount. The real
constraint is ``amount + fee <= available``, and a fake that does not model it
is worse than no fake at all, because it certifies the broken behaviour.

``FakeGatewayClient`` models it, so a caller that asks for the whole balance
raises exactly the way prod did.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

# The fee Circle charged on the 36.000000 USDC revenue withdrawal measured on
# Arc testnet, 2026-08-26. Tests may override to probe other schedules.
OBSERVED_FEE_RAW = 3_500

_USDC = Decimal(10) ** 6


def raw_to_str(raw: int) -> str:
    return f"{Decimal(raw) / _USDC:.6f}"


def make_balances(available_raw: int) -> MagicMock:
    b = MagicMock()
    b.available = available_raw
    b.formatted_available = raw_to_str(available_raw)
    b.formatted_total = b.formatted_available
    return b


class FakeGatewayClient:
    """Stands in for ``circlekit.client.GatewayClient``.

    Records the arguments of every ``withdraw`` call, and rejects the ones
    Circle rejects:

    * ``amount + fee > available`` -> the production 400 message;
    * ``fee > max_fee`` -> Circle refusing an under-authorised burn intent.
    """

    def __init__(
        self,
        available_raw: int,
        fee_raw: int = OBSERVED_FEE_RAW,
        depositor: str = "0xffa7abba5f17cb8471ebf150bf808bd6fb8856c1",
        mint_tx_hash: str = "0xmint",
    ) -> None:
        self.available_raw = available_raw
        self.fee_raw = fee_raw
        self.depositor = depositor
        self.mint_tx_hash = mint_tx_hash
        self.withdraw_calls: list[dict] = []

    async def get_gateway_balance(self):
        return make_balances(self.available_raw)

    async def withdraw(self, amount: str, max_fee: int | None = None, **kwargs):
        self.withdraw_calls.append({"amount": amount, "max_fee": max_fee, **kwargs})
        amount_raw = int(Decimal(amount) * _USDC)

        # circlekit defaults maxFee to 2.01 USDC when the caller passes none.
        authorised = 2_010_000 if max_fee is None else max_fee
        if self.fee_raw > authorised:
            raise ValueError(
                f"Withdrawal failed (status 400): fee {raw_to_str(self.fee_raw)} "
                f"exceeds maxFee {raw_to_str(authorised)}"
            )

        if amount_raw + self.fee_raw > self.available_raw:
            raise ValueError(
                f"Withdrawal failed (status 400): "
                f'{{"success":false,"message":"Insufficient balance for depositor '
                f"{self.depositor}: available {raw_to_str(self.available_raw)}, "
                f'required {Decimal(amount_raw + self.fee_raw) / _USDC}"}}'
            )

        self.available_raw -= amount_raw + self.fee_raw
        return MagicMock(mint_tx_hash=self.mint_tx_hash)

    # convenience for assertions
    @property
    def last_withdraw(self) -> dict:
        assert self.withdraw_calls, "withdraw was never called"
        return self.withdraw_calls[-1]

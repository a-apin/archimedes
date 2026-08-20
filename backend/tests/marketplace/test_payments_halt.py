"""Tests for the #1240 PAYMENTS_HALT kill switch on the tick-charging rail.

``MarketService.payments_dry_run`` is read once at construction (main.py
boot) and cached — flipping it off needs a container restart.
``PAYMENTS_HALT`` (``marketplace.config.payments_halted``) is read fresh from
``os.environ`` on every ``_charge_one`` call instead, so an operator can stop
real charges without a redeploy. These tests exercise ``_charge_one``
directly rather than the full ``tick()`` pipeline (see
``test_per_step_charging.py`` / ``test_service_tick.py`` for that) since the
halt check is a narrow, self-contained gate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from archimedes.marketplace.service import MarketService, Publisher, Subscriber
from archimedes.marketplace.tick_registry import TickStep


def _svc() -> MarketService:
    return MarketService(interval_seconds=9999, payments_dry_run=False, paper_trading=True)


def _pub() -> Publisher:
    return Publisher(
        strategy_id="strat_a",
        pool_id="0x" + "aa" * 32,
        vault_address="0xpublisher_vault",
        creator_wallet="0xpublisher",
        gateway_seller_address="0xgateway_seller",
    )


def _sub() -> Subscriber:
    return Subscriber(
        sub_id="0x" + "s1" * 32,
        pool_id="0x" + "bb" * 32,
        vault_address="0xsub_vault",
        ephemeral_wallet="0xephemeral",
        subscriber_wallet="0xsubscriber",
        active=True,
        circle_wallet_id="wallet_circle_id",
    )


@pytest.mark.asyncio
async def test_payments_halt_stops_real_charge(monkeypatch):
    """PAYMENTS_HALT=true short-circuits BEFORE the settlement-intent claim,
    the spend-cap reservation, or payments.charge — none of them run."""
    monkeypatch.setenv("PAYMENTS_HALT", "true")
    svc = _svc()
    pub, sub = _pub(), _sub()

    with (
        patch("archimedes.marketplace.service.payments.charge", new=AsyncMock()) as mock_charge,
        patch("archimedes.marketplace.service.spend_cap.try_reserve_usdc", new=AsyncMock()) as mock_reserve,
        patch.object(svc, "_claim_settlement_intent") as mock_claim,
    ):
        paid, override = await svc._charge_one(pub, sub, "strat_a", "tick_1", TickStep.LOAD_STRATEGY, action_count=1)

    assert paid is True
    assert override is None
    mock_claim.assert_not_called()
    mock_reserve.assert_not_awaited()
    mock_charge.assert_not_awaited()


@pytest.mark.asyncio
async def test_payments_halt_true_case_insensitive_and_numeric(monkeypatch):
    svc = _svc()
    pub, sub = _pub(), _sub()
    for value in ("true", "TRUE", "1", "yes", "Yes"):
        monkeypatch.setenv("PAYMENTS_HALT", value)
        with patch("archimedes.marketplace.service.payments.charge", new=AsyncMock()) as mock_charge:
            paid, _ = await svc._charge_one(pub, sub, "strat_a", "tick_1", TickStep.LOAD_STRATEGY, action_count=1)
        assert paid is True, f"PAYMENTS_HALT={value!r} should halt"
        mock_charge.assert_not_awaited()


@pytest.mark.asyncio
async def test_payments_halt_false_reaches_the_real_charge_path(monkeypatch):
    """Adversarial companion: PAYMENTS_HALT unset (default false) reaches the
    spend-cap reservation — proves the halted test above exercises a real
    short-circuit rather than some other always-unpaid path. A spend-cap
    refusal downstream is expected and orthogonal to this guard."""
    monkeypatch.delenv("PAYMENTS_HALT", raising=False)
    svc = _svc()
    pub, sub = _pub(), _sub()

    with (
        patch.object(svc, "_claim_settlement_intent", return_value="claimed"),
        patch(
            "archimedes.marketplace.service.spend_cap.try_reserve_usdc",
            new=AsyncMock(return_value=False),
        ) as mock_reserve,
    ):
        paid, override = await svc._charge_one(pub, sub, "strat_a", "tick_1", TickStep.LOAD_STRATEGY, action_count=1)

    mock_reserve.assert_awaited_once()
    assert paid is False
    assert override == "24h spend cap reached"

"""Money-safety tests for PR #958: x402 charge idempotency (#7) + auto-withdraw
on unsubscribe (#8).

x402 / Circle Gateway is NOT idempotent for a crash-retry (a retry signs a
fresh EIP-3009 nonce that settles as a new payment). The SettlementIntent row
is the load-bearing guard; these tests pin its behaviour and the unsubscribe
refund path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import archimedes.db as archimedes_db
from archimedes.db import Base, get_session
from archimedes.marketplace.service import MarketService, Publisher, Subscriber
from archimedes.marketplace.tick_registry import TickStep
from archimedes.models.marketplace import SettlementIntent


@pytest.fixture(autouse=True)
def _db():
    Base.metadata.create_all(bind=archimedes_db.engine)
    yield
    Base.metadata.drop_all(bind=archimedes_db.engine)


def _svc(dry_run: bool = False) -> MarketService:
    return MarketService(interval_seconds=9999, payments_dry_run=dry_run, paper_trading=True)


def _pub():
    return Publisher(
        strategy_id="strat_a",
        pool_id="0x" + "aa" * 32,
        vault_address="0xvault",
        creator_wallet="0xcreator",
        gateway_seller_address="0xgateway_seller",
    )


def _sub(sub_id="0x" + "cc" * 32):
    return Subscriber(
        sub_id=sub_id,
        pool_id="0x" + "bb" * 32,
        vault_address="0xsubvault",
        ephemeral_wallet="0xdcw",
        subscriber_wallet="0xsubscriber",
        circle_wallet_id="wallet-uuid",
    )


# ── #7 — idempotency guard ────────────────────────────────────────────────


def test_claim_settlement_intent_lifecycle():
    svc = _svc()
    key = dict(strategy_id="strat_a", tick_id="strat_a:1", sub_id="0x" + "cc" * 32, step="REBALANCE")

    assert svc._claim_settlement_intent(**key) == "claimed"
    # A second claim for the same logical charge is blocked (pending in-flight).
    assert svc._claim_settlement_intent(**key) == "in_flight"

    # Once finalized as settled, a re-claim reports already-settled (idempotent).
    svc._finalize_settlement_intent(**key, settled=True)
    assert svc._claim_settlement_intent(**key) == "already_settled"

    with get_session() as session:
        row = session.query(SettlementIntent).filter_by(**key).one()
        assert row.status == "settled"
        assert row.settled_at is not None


async def test_charge_one_settles_once_then_skips_on_retry():
    svc = _svc()
    pub, sub = _pub(), _sub()
    with patch("archimedes.marketplace.payments.charge", new=AsyncMock(return_value=True)) as m_charge:
        first = await svc._charge_one(pub, sub, "strat_a", "strat_a:1", TickStep.REBALANCE, 1)
        second = await svc._charge_one(pub, sub, "strat_a", "strat_a:1", TickStep.REBALANCE, 1)

    assert first is True
    # The retry of the SAME (strategy, tick, sub, step) returns paid WITHOUT a
    # second settle — the idempotency guard prevented a double charge.
    assert second is True
    assert m_charge.await_count == 1


async def test_charge_one_in_flight_pending_does_not_recharge():
    """A pre-existing PENDING intent (a prior attempt that settled-but-crashed
    before recording) must block a re-charge, not settle again."""
    svc = _svc()
    pub, sub = _pub(), _sub()
    # Simulate a crashed prior attempt: pending intent already present (use the
    # enum value the charge path writes, TickStep.REBALANCE.value == "rebalance").
    svc._claim_settlement_intent("strat_a", "strat_a:1", sub.sub_id, TickStep.REBALANCE.value)

    with patch("archimedes.marketplace.payments.charge", new=AsyncMock(return_value=True)) as m_charge:
        result = await svc._charge_one(pub, sub, "strat_a", "strat_a:1", TickStep.REBALANCE, 1)

    assert result is False
    m_charge.assert_not_awaited()


async def test_charge_one_dry_run_never_claims_or_charges():
    svc = _svc(dry_run=True)
    pub, sub = _pub(), _sub()
    with patch("archimedes.marketplace.payments.charge", new=AsyncMock(return_value=True)) as m_charge:
        assert await svc._charge_one(pub, sub, "strat_a", "strat_a:1", TickStep.REBALANCE, 1) is True
    m_charge.assert_not_awaited()
    with get_session() as session:
        assert session.query(SettlementIntent).count() == 0


# ── #8 — auto-withdraw on unsubscribe ─────────────────────────────────────


async def test_refund_subscriber_noop_under_dry_run():
    svc = _svc(dry_run=True)
    svc._sweeper = MagicMock()
    svc._sweeper.withdraw_subscriber = AsyncMock(return_value="0xtx")
    tx = await svc.refund_subscriber(
        circle_wallet_id="w", dcw_address="0xdcw", to_wallet="0xsub", sub_id="0x" + "cc" * 32
    )
    assert tx is None
    svc._sweeper.withdraw_subscriber.assert_not_awaited()


async def test_refund_subscriber_sweeps_when_live():
    svc = _svc(dry_run=False)
    svc._sweeper = MagicMock()
    svc._sweeper.withdraw_subscriber = AsyncMock(return_value="0xtx")
    tx = await svc.refund_subscriber(
        circle_wallet_id="w", dcw_address="0xdcw", to_wallet="0xsub", sub_id="0x" + "cc" * 32
    )
    assert tx == "0xtx"
    svc._sweeper.withdraw_subscriber.assert_awaited_once()
    kwargs = svc._sweeper.withdraw_subscriber.await_args.kwargs
    assert kwargs["to_wallet"] == "0xsub"
    assert kwargs["dcw_address"] == "0xdcw"

"""Per-step charging tests for the marketplace pipeline.

Verifies the 13 + REBALANCE charge-boundary model (F7) against the
behavioural invariants in the spec §9 acceptance criteria.
"""
from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archimedes.marketplace.service import MarketService, Publisher, Subscriber
from archimedes.marketplace.tick_registry import HaltSource, TickStep
from archimedes.services.strategy_signal_evaluator import StrategySignals


# ── helpers ─────────────────────────────────────────────────────────────

def _dummy_strategy():
    return MagicMock(spec=["id"], id="test_strat")


def _dummy_signals():
    from archimedes.services.strategy_signal_evaluator import AssetSignal, Signal

    ss = MagicMock(spec=StrategySignals)
    ss.signals = [
        AssetSignal(
            asset="ETH", signal=Signal.LONG, weight=0.5, reason="test",
            strategy_id="test_strat", strategy_name="test",
        ),
    ]
    ss.paper_title = "test"
    ss.strategy_id = "test_strat"
    return [ss]


def _dummy_targets():
    from archimedes.models.portfolio import TargetAllocation

    return [
        TargetAllocation(symbol="ETH", token_address="0x" + "ee" * 20, weight=0.5, strategy_ids=["test_strat"]),
        TargetAllocation(symbol="USDC", token_address="0x" + "ff" * 20, weight=0.5, strategy_ids=[]),
    ]


def _dummy_trades():
    from archimedes.models.portfolio import TradeDirection, TradeOrder
    return [
        TradeOrder(
            symbol="ETH", token_address="", direction=TradeDirection.BUY,
            amount=100.0, estimated_usdc_value=100.0,
        ),
    ]


def _patch_evaluator(stack: ExitStack, weights: dict | None = None):
    w = weights if weights is not None else {"ETH": 0.5}
    stack.enter_context(
        patch("archimedes.marketplace.service.strategy_evaluator.evaluate_strategies",
              return_value=_dummy_signals()),
    )
    stack.enter_context(
        patch("archimedes.marketplace.service.strategy_evaluator.aggregate_signals",
              return_value=w),
    )


# ── fixture ─────────────────────────────────────────────────────────────

@pytest.fixture
def market():
    svc = MarketService(interval_seconds=9999, payments_dry_run=True, paper_trading=True)
    svc.executor = MagicMock()
    svc.executor.read_portfolio = AsyncMock(
        return_value=MagicMock(total_value_usdc=10000, weights_dict={})
    )
    svc.executor.execute_trades = AsyncMock()
    svc.executor.set_token_oracles = AsyncMock()
    svc.executor.set_target_allocations = AsyncMock()
    svc.signer = MagicMock()
    svc.signer.is_configured = False
    svc.loader = MagicMock()
    svc.state = MagicMock()
    svc.state.try_acquire_leader = AsyncMock(return_value="test-token")
    svc.state.renew_leader = AsyncMock()
    svc.state.release_leader = AsyncMock()
    svc.state.append_event = AsyncMock()
    svc.state.save_subscribers = AsyncMock()
    svc.state.load_subscribers = AsyncMock(return_value={})
    svc.state.try_acquire_regime_lock = AsyncMock(return_value=False)
    svc.state.store = MagicMock()
    svc.state.store.load_regime = AsyncMock(return_value=None)
    svc.state.store.save_last_rebalance = AsyncMock()
    svc.portfolio_constructor = MagicMock()
    svc.portfolio_constructor.construct = MagicMock(return_value=[
        MagicMock(symbol="ETH", weight=0.5, token_address="0x" + "ee" * 20),
        MagicMock(symbol="USDC", weight=0.5, token_address="0x" + "ff" * 20),
    ])
    svc._weights_to_targets = MagicMock(return_value=_dummy_targets())
    svc._save_publisher_consensus = AsyncMock()
    svc.provider = MagicMock()
    svc.provider.get_strategy = MagicMock(return_value=_dummy_strategy())
    svc._subscriber_is_ready = AsyncMock(return_value=True)
    return svc


def _add_publisher(market, sid="strat_a"):
    pub = Publisher(
        strategy_id=sid, pool_id="0x" + "aa" * 32,
        vault_address="0xpublisher_vault", creator_wallet="0xpublisher",
        gateway_seller_address="0xgateway_seller",
    )
    market.publishers[sid] = pub
    return pub


def _add_sub(pub, sub_id="sub_1", active=True):
    sub = Subscriber(
        sub_id="0x" + sub_id * 32, pool_id="0x" + "bb" * 32,
        vault_address="0xsub_vault", ephemeral_wallet="0xephemeral",
        subscriber_wallet="0xsubscriber", active=active,
        circle_wallet_id="wallet_circle_id",
    )
    pub.subscribers[sub_id] = sub
    return sub


# ── Tests ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_payments_dry_run_happy_path(market: MarketService):
    """Dry-run: one ready subscriber, non-empty trades → 13 charged pipeline
    records + 1 REBALANCE record; zero halted."""
    records = []

    async def _capture_record(rec):
        records.append(rec)

    market.record_subscriber_tick = _capture_record

    with ExitStack() as stack:
        _patch_evaluator(stack)
        stack.enter_context(
            patch("archimedes.marketplace.service.compute_trades", return_value=_dummy_trades()))

        pub = _add_publisher(market)
        _add_sub(pub)
        await market.tick("strat_a")

    assert len(records) >= 14, f"expected ≥14 records, got {len(records)}"
    charged = [r for r in records if r.charged]
    halted = [r for r in records if r.halted]
    assert len(halted) == 0, f"expected 0 halted, got {len(halted)}"
    assert len(charged) >= 14


@pytest.mark.asyncio
async def test_publisher_halt_at_vcheck(market: MarketService):
    """Publisher halt at V_CHECK → records up to V_CHECK with PUBLISHER halt;
    no REBALANCE record."""
    records = []

    async def _capture_record(rec):
        records.append(rec)

    market.record_subscriber_tick = _capture_record

    with ExitStack() as stack:
        _patch_evaluator(stack)
        stack.enter_context(
            patch("archimedes.marketplace.service.compute_trades", return_value=_dummy_trades()))
        stack.enter_context(
            patch("archimedes.marketplace.service.VCheck.run",
                  return_value=MagicMock(passed=False, failures=["bad_weight"])))

        pub = _add_publisher(market)
        _add_sub(pub)
        await market.tick("strat_a")

    vcheck_records = [r for r in records if r.step_reached == TickStep.V_CHECK]
    vcheck_halted = [r for r in vcheck_records if r.halted]
    assert len(vcheck_halted) >= 1, (
        f"expected halted V_CHECK record, got {[(r.halted, r.halt_source, r.halt_reason) for r in vcheck_records]}"
    )
    vc = vcheck_halted[0]
    assert vc.halt_source == HaltSource.PUBLISHER
    assert "v_check" in (vc.halt_reason or "")
    rebalance_records = [r for r in records if r.step_reached == TickStep.REBALANCE]
    assert len(rebalance_records) == 0


@pytest.mark.asyncio
async def test_no_drift_halt(market: MarketService):
    """compute_trades → [] halts at NO_DRIFT_DEDUP; no REBALANCE."""
    records = []

    async def _capture_record(rec):
        records.append(rec)

    market.record_subscriber_tick = _capture_record

    with ExitStack() as stack:
        _patch_evaluator(stack)
        stack.enter_context(
            patch("archimedes.marketplace.service.compute_trades", return_value=[]))

        pub = _add_publisher(market)
        _add_sub(pub)
        await market.tick("strat_a")

    dedup_records = [r for r in records if r.step_reached == TickStep.NO_DRIFT_DEDUP]
    dedup_halted = [r for r in dedup_records if r.halted]
    assert len(dedup_halted) >= 1, (
        f"expected halted NO_DRIFT_DEDUP record, got {[(r.halted, r.halt_source, r.halt_reason) for r in dedup_records]}"
    )
    dr = dedup_halted[0]
    assert dr.halt_source == HaltSource.PUBLISHER
    rebalance_records = [r for r in records if r.step_reached == TickStep.REBALANCE]
    assert len(rebalance_records) == 0


@pytest.mark.asyncio
async def test_cant_pay_deferral(market: MarketService):
    """Non-dry-run: sub with _charge_one→False at REGIME_CLASSIFY is deferred,
    other sub still reaches REBALANCE."""
    market.payments_dry_run = False
    records = []

    async def _capture_record(rec):
        records.append(rec)

    market.record_subscriber_tick = _capture_record

    async def _charge_side_effect(pub, sub, strategy_id, tick_id, step, action_count):
        if sub.sub_id == "0x" + "bad" * 32 and step == TickStep.REGIME_CLASSIFY:
            return False
        return True

    with ExitStack() as stack:
        _patch_evaluator(stack)
        stack.enter_context(
            patch("archimedes.marketplace.service.compute_trades", return_value=_dummy_trades()))
        stack.enter_context(patch.object(market, "_charge_one", _charge_side_effect))

        pub = _add_publisher(market)
        _add_sub(pub, sub_id="good")
        _add_sub(pub, sub_id="bad")
        await market.tick("strat_a")

    # Bad sub should have PAYMENT halt at REGIME_CLASSIFY
    bad_halt = [r for r in records
                if r.sub_id == "0x" + "bad" * 32 and r.step_reached == TickStep.REGIME_CLASSIFY
                and r.halted and r.halt_source == HaltSource.PAYMENT]
    assert len(bad_halt) >= 1, (
        f"expected bad-sub halt at REGIME_CLASSIFY, "
        f"got {[(r.sub_id, r.step_reached, r.halt_source) for r in records if r.halted]}"
    )

    # Bad sub should NOT be in any records after REGIME_CLASSIFY
    bad_after = [r for r in records
                 if r.sub_id == "0x" + "bad" * 32 and r.step_reached in (
                     TickStep.THROTTLE_WEIGHTS, TickStep.REBALANCE)]
    assert len(bad_after) == 0

    # Good sub should reach REBALANCE
    good_rebalance = [r for r in records
                      if r.sub_id == "0x" + "good" * 32 and r.step_reached == TickStep.REBALANCE]
    assert len(good_rebalance) >= 1

    # Bad sub should be inactive
    bad_sub = market.publishers["strat_a"].subscribers.get("bad")
    if bad_sub:
        assert bad_sub.active is False


@pytest.mark.asyncio
async def test_mirror_failure(market: MarketService):
    """_apply_to_subscriber → (False, exc) → EXECUTION record + liability."""
    market.paper_trading = False
    records = []

    async def _capture_record(rec):
        records.append(rec)

    market.record_subscriber_tick = _capture_record

    with ExitStack() as stack:
        _patch_evaluator(stack)
        stack.enter_context(
            patch("archimedes.marketplace.service.compute_trades", return_value=_dummy_trades()))
        stack.enter_context(patch.object(market, "_charge_one", AsyncMock(return_value=True)))
        mock_liability = stack.enter_context(
            patch.object(market, "_record_liability", AsyncMock()))
        stack.enter_context(
            patch.object(market, "_apply_to_subscriber",
                         AsyncMock(return_value=(False, RuntimeError("execution failed")))))

        pub = _add_publisher(market)
        _add_sub(pub)
        await market.tick("strat_a")

    rebalance_records = [r for r in records if r.step_reached == TickStep.REBALANCE]
    exec_halt = [r for r in rebalance_records if r.halted and r.halt_source == HaltSource.EXECUTION]
    assert len(exec_halt) >= 1

    # Publisher vault trade still runs
    market.executor.execute_trades.assert_any_call("0xpublisher_vault", _dummy_trades())

    # Liability recorded
    mock_liability.assert_awaited()


@pytest.mark.asyncio
async def test_underfunded_sub_excluded(market: MarketService):
    """Subscriber below min_vault_usdc → excluded, no charge attempted."""
    records = []

    async def _capture_record(rec):
        records.append(rec)

    market.record_subscriber_tick = _capture_record

    with ExitStack() as stack:
        _patch_evaluator(stack)
        stack.enter_context(
            patch("archimedes.marketplace.service.compute_trades", return_value=_dummy_trades()))
        stack.enter_context(
            patch.object(market, "_subscriber_is_ready", AsyncMock(return_value=False)))

        pub = _add_publisher(market)
        _add_sub(pub)
        await market.tick("strat_a")

    # No records for this sub since they were never charged
    sub_records = [r for r in records if r.sub_id == "0x" + "sub_1" * 32]
    assert len(sub_records) == 0


@pytest.mark.asyncio
async def test_empty_active_set(market: MarketService):
    """All subscribers unready → pipeline still clears, publisher vault trades."""
    market.paper_trading = False
    records = []

    async def _capture_record(rec):
        records.append(rec)

    market.record_subscriber_tick = _capture_record

    with ExitStack() as stack:
        _patch_evaluator(stack)
        stack.enter_context(
            patch("archimedes.marketplace.service.compute_trades", return_value=_dummy_trades()))
        stack.enter_context(
            patch.object(market, "_subscriber_is_ready", AsyncMock(return_value=False)))

        pub = _add_publisher(market)
        _add_sub(pub)
        _add_sub(pub, sub_id="sub_2")
        await market.tick("strat_a")

    # No subscriber records (active set was empty)
    assert len(records) == 0

    # Publisher vault trades still execute
    market.executor.execute_trades.assert_any_call("0xpublisher_vault", _dummy_trades())

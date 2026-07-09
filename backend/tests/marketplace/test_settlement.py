"""Tests for SettlementSweeper (P5).

All SDK/clients are mocked at module boundary.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archimedes.marketplace.settlement import (
    SettlementSweeper,
    SWEEP_MIN_DEPOSIT_RAW,
    GATEWAY_CHAIN,
    _THRESHOLD_RAW,
)


@pytest.fixture
def settings():
    s = MagicMock()
    s.payment_splitter_address = "0xSplitter0000000000000000000000000000000001"
    s.usdc_address = "0xUsdc000000000000000000000000000000000001"
    return s


@pytest.fixture
def pub():
    p = MagicMock()
    p.strategy_id = "strat_1"
    p.pool_id = "0xpool0000000000000000000000000000000000000000000001"
    p.gateway_seller_address = "0xAgent000000000000000000000000000000000001"
    p.agent_wallet_id = "wallet-abc-123"
    return p


@pytest.fixture
def sweeper(settings):
    return SettlementSweeper(settings)


@pytest.fixture
def splitter_addr():
    return "0xSplitter0000000000000000000000000000000001"


# ── PAYMENTS_DRY_RUN guard (issue: manual withdraw endpoint bypassed dry-run) ──
# Every fund-moving method must be an inert no-op under dry-run, moving no real
# value and never touching a signer/executor/RPC. The guard lives in the sweeper
# so no caller can forget it.


@pytest.mark.asyncio
async def test_dry_run_sweep_publisher_is_noop(settings, pub):
    """Under PAYMENTS_DRY_RUN, sweep_publisher moves no value — no signer/executor touched."""
    sweeper = SettlementSweeper(settings, payments_dry_run=True)
    sweeper._get_signer = MagicMock(side_effect=AssertionError("signer must not run under dry-run"))
    sweeper._get_executor = MagicMock(side_effect=AssertionError("executor must not run under dry-run"))
    result = await sweeper.sweep_publisher(pub)
    assert result is None
    sweeper._get_signer.assert_not_called()
    sweeper._get_executor.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_withdraw_publisher_returns_none_without_chain_call(settings, pub):
    """Stage C (PaymentSplitter.withdraw) is skipped entirely under dry-run."""
    sweeper = SettlementSweeper(settings, payments_dry_run=True)
    sweeper._get_executor = MagicMock(side_effect=AssertionError("executor must not run under dry-run"))
    tx = await sweeper.withdraw_publisher(pub, 5_000_000)
    assert tx is None
    sweeper._get_executor.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_withdraw_subscriber_returns_none_without_chain_call(settings):
    """Subscriber DCW return-transfer is skipped entirely under dry-run."""
    sweeper = SettlementSweeper(settings, payments_dry_run=True)
    sweeper._get_executor = MagicMock(side_effect=AssertionError("executor must not run under dry-run"))
    sweeper._usdc_balance_of = MagicMock(side_effect=AssertionError("balance read must not run under dry-run"))
    tx = await sweeper.withdraw_subscriber(
        circle_wallet_id="w-1", dcw_address="0xdcw", to_wallet="0xto", sub_id="sub-1"
    )
    assert tx is None
    sweeper._get_executor.assert_not_called()


def test_sweeper_defaults_to_live_mode(settings):
    """Constructing without the flag keeps live behavior (backwards-compatible)."""
    assert SettlementSweeper(settings)._payments_dry_run is False
    assert SettlementSweeper(settings, payments_dry_run=True)._payments_dry_run is True


# ── Stage A: Gateway balance below threshold → no withdraw ──────────────


@pytest.mark.asyncio
async def test_stage_a_below_threshold_does_not_withdraw(sweeper, pub):
    """Available balance below threshold => no withdraw call."""
    mock_balance = MagicMock()
    mock_balance.available = _THRESHOLD_RAW - 1  # just below threshold

    with (
        patch("archimedes.marketplace.settlement.CircleWalletSigner"),
        patch("archimedes.marketplace.settlement.CircleTxExecutor"),
        patch("archimedes.marketplace.settlement.GatewayClient") as MockGW,
    ):
        instance = MockGW.return_value
        instance.get_gateway_balance = AsyncMock(return_value=mock_balance)
        instance.withdraw = AsyncMock()

        await sweeper._stage_a_gateway_to_wallet(pub)

        instance.withdraw.assert_not_called()


# ── Stage A: Gateway balance above threshold → withdraw called ──────────


@pytest.mark.asyncio
async def test_stage_a_above_threshold_withdraws(sweeper, pub):
    """Available balance above threshold => withdraw called once."""
    mock_balance = MagicMock()
    mock_balance.available = _THRESHOLD_RAW + 5000000  # above threshold
    mock_balance.formatted_available = "15.00"

    mock_result = MagicMock()
    mock_result.mint_tx_hash = "0xminttx"

    with (
        patch("archimedes.marketplace.settlement.CircleWalletSigner"),
        patch("archimedes.marketplace.settlement.CircleTxExecutor"),
        patch("archimedes.marketplace.settlement.GatewayClient") as MockGW,
    ):
        instance = MockGW.return_value
        instance.get_gateway_balance = AsyncMock(return_value=mock_balance)
        instance.withdraw = AsyncMock(return_value=mock_result)

        await sweeper._stage_a_gateway_to_wallet(pub)

        instance.withdraw.assert_awaited_once_with(amount="15.00")


# ── Stage B: wallet balance below min deposit → no action ───────────────


@pytest.mark.asyncio
async def test_stage_b_below_min_deposit_skips(sweeper, pub):
    """Wallet USDC below SWEEP_MIN_DEPOSIT_RAW => no approve/deposit."""
    with (
        patch.object(sweeper, "_usdc_balance_of") as mock_balance,
        patch("archimedes.marketplace.settlement.CircleWalletSigner"),
        patch("archimedes.marketplace.settlement.CircleTxExecutor") as MockExec,
    ):
        mock_balance.return_value = SWEEP_MIN_DEPOSIT_RAW - 1
        exec_instance = MockExec.return_value
        exec_instance.execute_approve = MagicMock()
        exec_instance._submit_and_wait = MagicMock()

        await sweeper._stage_b_wallet_to_pool(pub)

        exec_instance.execute_approve.assert_not_called()
        exec_instance._submit_and_wait.assert_not_called()


# ── Stage B: wallet balance above min → approve then depositToPool ──────


@pytest.mark.asyncio
async def test_stage_b_approve_then_deposit(sweeper, pub, splitter_addr):
    """Wallet USDC above min => approve then depositToPool in order."""
    amount = SWEEP_MIN_DEPOSIT_RAW + 5000000

    with (
        patch.object(sweeper, "_usdc_balance_of") as mock_balance,
        patch("archimedes.marketplace.settlement.CircleWalletSigner"),
        patch("archimedes.marketplace.settlement.CircleTxExecutor") as MockExec,
    ):
        mock_balance.return_value = amount
        exec_instance = MockExec.return_value
        exec_instance.execute_approve = MagicMock(return_value="0xapprv")
        exec_instance._submit_and_wait = MagicMock(return_value="0xdeposit")

        await sweeper._stage_b_wallet_to_pool(pub)

        # approve called first with correct params
        exec_instance.execute_approve.assert_called_once_with(
            GATEWAY_CHAIN,
            pub.gateway_seller_address,
            splitter_addr,
            amount,
        )
        # then depositToPool
        exec_instance._submit_and_wait.assert_called_once_with(
            splitter_addr,
            "depositToPool(bytes32,uint256)",
            [pub.pool_id, str(amount)],
        )


# ── Stage A raises => Stage B still runs ─────────────────────────────────


@pytest.mark.asyncio
async def test_stage_a_failure_does_not_block_stage_b(sweeper, pub):
    """When Stage A raises, Stage B still executes."""
    with (
        patch("archimedes.marketplace.settlement.CircleWalletSigner"),
        patch("archimedes.marketplace.settlement.CircleTxExecutor") as MockExec,
        patch("archimedes.marketplace.settlement.GatewayClient") as MockGW,
        patch.object(sweeper, "_usdc_balance_of") as mock_bal,
    ):
        gw_instance = MockGW.return_value
        gw_instance.get_gateway_balance = AsyncMock(side_effect=RuntimeError("boom"))
        gw_instance.withdraw = AsyncMock()

        mock_bal.return_value = SWEEP_MIN_DEPOSIT_RAW + 1000000
        exec_instance = MockExec.return_value
        exec_instance.execute_approve = MagicMock(return_value="0xapprv")
        exec_instance._submit_and_wait = MagicMock(return_value="0xdep")

        await sweeper._stage_a_gateway_to_wallet(pub)
        await sweeper._stage_b_wallet_to_pool(pub)

        exec_instance.execute_approve.assert_called_once()


# ── sweep_publisher runs both stages ────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_publisher_runs_both_stages(sweeper, pub):
    """sweep_publisher calls both Stage A and Stage B."""
    with (
        patch.object(sweeper, "_stage_a_gateway_to_wallet") as mock_a,
        patch.object(sweeper, "_stage_b_wallet_to_pool") as mock_b,
    ):
        mock_a.return_value = None
        mock_b.return_value = None

        await sweeper.sweep_publisher(pub)

        mock_a.assert_awaited_once_with(pub)
        mock_b.assert_awaited_once_with(pub)


# ── sweep_publisher skips when missing wallet info ──────────────────────


@pytest.mark.asyncio
async def test_sweep_skips_missing_wallet_info(sweeper, pub):
    """Missing agent_wallet_id or gateway_seller_address => no stages run."""
    pub.agent_wallet_id = ""

    with (
        patch.object(sweeper, "_stage_a_gateway_to_wallet") as mock_a,
        patch.object(sweeper, "_stage_b_wallet_to_pool") as mock_b,
    ):
        await sweeper.sweep_publisher(pub)
        mock_a.assert_not_called()
        mock_b.assert_not_called()

    pub.agent_wallet_id = "wallet-abc-123"
    pub.gateway_seller_address = ""

    with (
        patch.object(sweeper, "_stage_a_gateway_to_wallet") as mock_a,
        patch.object(sweeper, "_stage_b_wallet_to_pool") as mock_b,
    ):
        await sweeper.sweep_publisher(pub)
        mock_a.assert_not_called()
        mock_b.assert_not_called()

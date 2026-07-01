"""
Tests for SubscriptionManager.vy (A3) — subscription lifecycle with PaymentSplitter integration.

Contracts changed (contracts/vyper/SubscriptionManager.vy):
  - IPaymentSplitter interface updated for 6-field return (D6 struct change)
  - Event logging uses kwargs (vyper ^0.4.1 best-practice)
"""

import pytest
import boa


# ═══════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def subscription_manager(usdc, splitter, accounts):
    """Deploy a fresh SubscriptionManager per test.

    Also creates an active pool on the splitter so subscribe can
    validate pool existence.
    """
    pool_id = b"pool-sm".ljust(32, b"\x00")
    with boa.env.prank(accounts["owner"]):
        splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

    flat_fee = 10 * 10**6  # 10 USDC per action
    with boa.env.prank(accounts["owner"]):
        mgr = boa.load(
            "contracts/vyper/SubscriptionManager.vy",
            splitter.address,
            usdc.address,
            flat_fee,
        )
    return mgr


@pytest.fixture
def pool_id():
    """Default pool ID used across tests."""
    return b"pool-sm".ljust(32, b"\x00")


# ═══════════════════════════════════════════════════════════════════════
# DEPLOYMENT
# ═══════════════════════════════════════════════════════════════════════


class TestDeployment:
    """A3 §1 — Contract construction and initial state."""

    def test_initial_state(self, subscription_manager, usdc, splitter, accounts):
        """Contract records splitter, USDC, owner, and flat fee."""
        assert subscription_manager.splitter() == splitter.address
        assert subscription_manager.usdc() == usdc.address
        assert subscription_manager.owner() == accounts["owner"]
        assert subscription_manager.flat_fee_per_action() == 10 * 10**6


# ═══════════════════════════════════════════════════════════════════════
# SUBSCRIBE
# ═══════════════════════════════════════════════════════════════════════


class TestSubscribe:
    """A3 §2 — Subscribe flow with pool validation."""

    def test_subscribe_success(self, subscription_manager, usdc, accounts, pool_id):
        """Happy path: subscribe with initial deposit returns a sub_id."""
        deposit = 100 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(subscription_manager.address, deposit)
            sub_id = subscription_manager.subscribe(pool_id, "https://example.com/webhook", deposit)

        assert isinstance(sub_id, bytes) and len(sub_id) == 32

        sub = subscription_manager.subscriptions(sub_id)
        assert sub[0] == accounts["funder"]  # subscriber
        assert sub[1] == pool_id             # pool_id
        assert sub[3] == deposit             # reserved_usdc
        assert sub[5] is True                # active

    def test_subscribe_no_initial_deposit(self, subscription_manager, accounts, pool_id):
        """Subscribe with zero initial_deposit succeeds (no transfer needed)."""
        with boa.env.prank(accounts["funder"]):
            sub_id = subscription_manager.subscribe(pool_id, "https://example.com/webhook", 0)

        sub = subscription_manager.subscriptions(sub_id)
        assert sub[3] == 0   # reserved_usdc
        assert sub[5] is True  # active

    def test_subscribe_empty_webhook_reverts(self, subscription_manager, accounts, pool_id):
        """Webhook URL must not be empty."""
        with boa.env.prank(accounts["funder"]):
            with pytest.raises(boa.BoaError, match="webhook_url required"):
                subscription_manager.subscribe(pool_id, "", 0)

    def test_subscribe_inactive_pool_reverts(self, subscription_manager, splitter, accounts):
        """Subscribing to a non-existent or inactive pool must revert."""
        bad_pool = b"dead-pool".ljust(32, b"\x00")
        with boa.env.prank(accounts["funder"]):
            with pytest.raises(boa.BoaError, match="pool not active"):
                subscription_manager.subscribe(bad_pool, "https://example.com/webhook", 0)

    def test_subscribe_duplicate_reverts(self, subscription_manager, usdc, accounts, pool_id):
        """Subscribing again with same parameters must revert."""
        deposit = 50 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(subscription_manager.address, deposit)
            subscription_manager.subscribe(pool_id, "https://example.com/webhook", deposit)

            with pytest.raises(boa.BoaError, match="already subscribed"):
                subscription_manager.subscribe(pool_id, "https://example.com/webhook", deposit)

    def test_subscribe_transfers_usdc(self, subscription_manager, usdc, accounts, pool_id):
        """Initial deposit is transferred from subscriber to the contract."""
        deposit = 200 * 10**6
        bal_before = usdc.balanceOf(accounts["funder"])
        with boa.env.prank(accounts["funder"]):
            usdc.approve(subscription_manager.address, deposit)
            subscription_manager.subscribe(pool_id, "https://example.com/webhook", deposit)

        assert usdc.balanceOf(accounts["funder"]) == bal_before - deposit
        assert usdc.balanceOf(subscription_manager.address) == deposit


# ═══════════════════════════════════════════════════════════════════════
# RENEW EPHEMERAL WALLET
# ═══════════════════════════════════════════════════════════════════════


class TestRenewEphemeralWallet:
    """A3 §3 — Top-up an existing subscription."""

    def test_renew_top_up(self, subscription_manager, usdc, accounts, pool_id):
        """Subscriber can top up their reserved balance."""
        deposit = 100 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(subscription_manager.address, deposit + 50 * 10**6)
            sub_id = subscription_manager.subscribe(pool_id, "https://example.com/webhook", deposit)

            subscription_manager.renewEphemeralWallet(sub_id, 50 * 10**6)

        sub = subscription_manager.subscriptions(sub_id)
        assert sub[3] == deposit + 50 * 10**6  # reserved_usdc updated

    def test_renew_by_non_subscriber_reverts(self, subscription_manager, usdc, accounts, pool_id):
        """Only the subscriber can renew their wallet."""
        deposit = 100 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(subscription_manager.address, deposit)
            sub_id = subscription_manager.subscribe(pool_id, "https://example.com/webhook", deposit)

        with boa.env.prank(accounts["attacker"]):
            with pytest.raises(boa.BoaError, match="not subscriber"):
                subscription_manager.renewEphemeralWallet(sub_id, 10 * 10**6)


# ═══════════════════════════════════════════════════════════════════════
# CHARGE ACTIONS
# ═══════════════════════════════════════════════════════════════════════


class TestChargeActions:
    """A3 §4 — Owner-only action charging with balance checks."""

    def test_charge_actions_success(self, subscription_manager, usdc, accounts, pool_id):
        """Owner can charge for actions when balance is sufficient."""
        deposit = 100 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(subscription_manager.address, deposit)
            sub_id = subscription_manager.subscribe(pool_id, "https://example.com/webhook", deposit)

        with boa.env.prank(accounts["owner"]):
            bal_before = usdc.balanceOf(subscription_manager.address)
            subscription_manager.chargeActions(sub_id, 3)  # 3 * 10 USDC = 30 USDC
            bal_after = usdc.balanceOf(subscription_manager.address)

        # USDC transferred from SubscriptionManager to PaymentSplitter
        assert bal_before - bal_after == 30 * 10**6

        sub = subscription_manager.subscriptions(sub_id)
        assert sub[3] == deposit - 30 * 10**6  # reserved_usdc reduced

    def test_charge_non_owner_reverts(self, subscription_manager, usdc, accounts, pool_id):
        """Only owner may call chargeActions."""
        deposit = 100 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(subscription_manager.address, deposit)
            sub_id = subscription_manager.subscribe(pool_id, "https://example.com/webhook", deposit)

        with boa.env.prank(accounts["attacker"]):
            with pytest.raises(boa.BoaError, match="only owner"):
                subscription_manager.chargeActions(sub_id, 1)

    def test_charge_insufficient_balance_reverts(self, subscription_manager, usdc, accounts, pool_id):
        """chargeActions reverts if reserved balance is insufficient."""
        deposit = 5 * 10**6  # only enough for 0 actions (flat fee = 10 USDC)
        with boa.env.prank(accounts["funder"]):
            usdc.approve(subscription_manager.address, deposit)
            sub_id = subscription_manager.subscribe(pool_id, "https://example.com/webhook", deposit)

        with boa.env.prank(accounts["owner"]):
            with pytest.raises(boa.BoaError, match="insufficient balance"):
                subscription_manager.chargeActions(sub_id, 1)

    def test_charge_zero_actions_reverts(self, subscription_manager, usdc, accounts, pool_id):
        """action_count must be > 0."""
        deposit = 100 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(subscription_manager.address, deposit)
            sub_id = subscription_manager.subscribe(pool_id, "https://example.com/webhook", deposit)

        with boa.env.prank(accounts["owner"]):
            with pytest.raises(boa.BoaError, match="action_count must be > 0"):
                subscription_manager.chargeActions(sub_id, 0)


# ═══════════════════════════════════════════════════════════════════════
# UNSUBSCRIBE
# ═══════════════════════════════════════════════════════════════════════


class TestUnsubscribe:
    """A3 §5 — Self-unsubscribe with refund."""

    def test_unsubscribe_refunds_balance(self, subscription_manager, usdc, accounts, pool_id):
        """Unsubscribing refunds remaining reserved USDC to the subscriber."""
        deposit = 100 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(subscription_manager.address, deposit)
            sub_id = subscription_manager.subscribe(pool_id, "https://example.com/webhook", deposit)

        bal_before = usdc.balanceOf(accounts["funder"])
        with boa.env.prank(accounts["funder"]):
            subscription_manager.unsubscribe(sub_id)

        assert usdc.balanceOf(accounts["funder"]) == bal_before + deposit
        sub = subscription_manager.subscriptions(sub_id)
        assert sub[5] is False  # active = False
        assert sub[3] == 0      # reserved_usdc = 0

    def test_unsubscribe_no_balance(self, subscription_manager, usdc, accounts, pool_id):
        """Unsubscribe with no remaining balance succeeds (no-op refund)."""
        deposit = 100 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(subscription_manager.address, deposit)
            sub_id = subscription_manager.subscribe(pool_id, "https://example.com/webhook", deposit)

        # Charge all the balance
        actions = deposit // (10 * 10**6)
        with boa.env.prank(accounts["owner"]):
            subscription_manager.chargeActions(sub_id, actions)

        bal_before = usdc.balanceOf(accounts["funder"])
        with boa.env.prank(accounts["funder"]):
            subscription_manager.unsubscribe(sub_id)

        # No refund because balance was zero
        assert usdc.balanceOf(accounts["funder"]) == bal_before

    def test_unsubscribe_non_subscriber_reverts(self, subscription_manager, usdc, accounts, pool_id):
        """Only the subscriber can unsubscribe."""
        deposit = 100 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(subscription_manager.address, deposit)
            sub_id = subscription_manager.subscribe(pool_id, "https://example.com/webhook", deposit)

        with boa.env.prank(accounts["attacker"]):
            with pytest.raises(boa.BoaError, match="not subscriber"):
                subscription_manager.unsubscribe(sub_id)


# ═══════════════════════════════════════════════════════════════════════
# SET FLAT FEE
# ═══════════════════════════════════════════════════════════════════════


class TestSetFlatFee:
    """A3 §6 — Owner-only fee adjustment."""

    def test_owner_sets_fee(self, subscription_manager, accounts):
        """Owner can update the flat fee."""
        with boa.env.prank(accounts["owner"]):
            subscription_manager.setFlatFee(25 * 10**6)

        assert subscription_manager.flat_fee_per_action() == 25 * 10**6

    def test_non_owner_cannot_set_fee(self, subscription_manager, accounts):
        """Only owner may call setFlatFee."""
        with boa.env.prank(accounts["attacker"]):
            with pytest.raises(boa.BoaError, match="only owner"):
                subscription_manager.setFlatFee(5 * 10**6)

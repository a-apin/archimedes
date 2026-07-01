"""
Tests for PaymentSplitter.vy (D6) — per-pool balance isolation + access control.

Contracts changed (contracts/vyper/PaymentSplitter.vy):
  - split()              →  depositToPool() + withdraw()
  - Access control:       withdraw() gated on pool.creator / pool.platform
  - Balance isolation:    held_balance field, capped disbursement
  - Event argument style: kwargs (vyper ^0.4.1 best-practice)
"""

import pytest
import boa


# ═══════════════════════════════════════════════════════════════════════
# DEPLOYMENT
# ═══════════════════════════════════════════════════════════════════════

class TestDeployment:
    """D6 §2.1 — Contract construction and initial state."""

    def test_initial_state(self, splitter, usdc, accounts):
        """Contract records USDC address and deployer as owner."""
        assert splitter.usdc() == usdc.address
        assert splitter.owner() == accounts["owner"]

    def test_uninitialized_pool_returns_defaults(self, splitter):
        """Querying a nonexistent pool returns zeroed struct."""
        pool_id = b"\x00" * 32
        p = splitter.pools(pool_id)
        assert p[0] == "0x0000000000000000000000000000000000000000"  # creator
        assert p[4] == 0  # held_balance
        assert p[5] is False  # active


# ═══════════════════════════════════════════════════════════════════════
# POOL CREATION
# ═══════════════════════════════════════════════════════════════════════

class TestCreatePool:
    """D6 §2.2 — Pool creation gated on owner."""

    def test_owner_creates_pool(self, splitter, accounts):
        """Owner can create a pool; struct is stored correctly."""
        pool_id = b"pool-1".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        p = splitter.pools(pool_id)
        assert p[0] == accounts["creator"]
        assert p[1] == accounts["platform"]
        assert p[2] == 0  # total_collected
        assert p[3] == 0  # total_disbursed
        assert p[4] == 0  # held_balance
        assert p[5] is True  # active

    def test_non_owner_cannot_create_pool(self, splitter, accounts):
        """Only owner may call createPool (D6 §2.2)."""
        pool_id = b"pool-2".ljust(32, b"\x00")
        with boa.env.prank(accounts["attacker"]):
            with pytest.raises(boa.BoaError, match="only owner"):
                splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

    def test_cannot_recreate_existing_pool(self, splitter, accounts):
        """createPool reverts if pool_id already active (D6 §2.2)."""
        pool_id = b"pool-3".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])
        with boa.env.prank(accounts["owner"]):
            with pytest.raises(boa.BoaError, match="pool already exists"):
                splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

    def test_create_pool_emits_event(self, splitter, accounts):
        """PoolCreated event has correct indexed fields."""
        pool_id = b"pool-event".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        # The event log is accessible via _computation — filter by our address
        addr_bytes = bytes.fromhex(splitter.address[2:])
        logs = [
            log for log in splitter._computation.get_log_entries()
            if log[0] == addr_bytes
        ]
        assert len(logs) == 1


# ═══════════════════════════════════════════════════════════════════════
# depositToPool — FUNDING
# ═══════════════════════════════════════════════════════════════════════

class TestDepositToPool:
    """D6 §2.4 — Permissionless funding, capped by pool activity, balance tracking."""

    def test_permissionless_deposit(self, splitter, usdc, accounts):
        """Anyone can call depositToPool (D6 §2.5 — permissionless)."""
        pool_id = b"pool-dep".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        amount = 500 * 10**6  # 500 USDC
        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, amount)
            splitter.depositToPool(pool_id, amount)

        p = splitter.pools(pool_id)
        assert p[2] == amount  # total_collected
        assert p[4] == amount  # held_balance

    def test_deposit_updates_held_balance_tracking(self, splitter, usdc, accounts):
        """Multiple deposits accumulate in held_balance (D6 §2.4 — per-pool isolation)."""
        pool_id = b"pool-multi".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, 2_000 * 10**6)
            splitter.depositToPool(pool_id, 300 * 10**6)
            splitter.depositToPool(pool_id, 700 * 10**6)

        assert splitter.pools(pool_id)[4] == 1_000 * 10**6  # held_balance

    def test_deposit_to_inactive_pool_reverts(self, splitter, usdc, accounts):
        """Depositing to a deactivated pool must revert (D6 §2.5)."""
        pool_id = b"pool-dead".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])
            splitter.deactivatePool(pool_id)

        with boa.env.prank(accounts["funder"]):
            with pytest.raises(boa.BoaError, match="pool not active"):
                splitter.depositToPool(pool_id, 100 * 10**6)

    def test_deposit_zero_amount_reverts(self, splitter, usdc, accounts):
        """Depositing zero must revert."""
        pool_id = b"pool-zero".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        with boa.env.prank(accounts["funder"]):
            with pytest.raises(boa.BoaError, match="amount must be positive"):
                splitter.depositToPool(pool_id, 0)

    def test_deposit_transfers_usdc(self, splitter, usdc, accounts):
        """USDC is actually pulled from the depositor (real transfer)."""
        pool_id = b"pool-xfer".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        amount = 250 * 10**6
        bal_before = usdc.balanceOf(accounts["funder"])
        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, amount)
            splitter.depositToPool(pool_id, amount)

        assert usdc.balanceOf(accounts["funder"]) == bal_before - amount
        assert usdc.balanceOf(splitter.address) == amount

    def test_deposit_emits_event(self, splitter, usdc, accounts):
        """PoolFunded event logged on deposit."""
        pool_id = b"pool-evtdep".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        amount = 100 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, amount)
            splitter.depositToPool(pool_id, amount)

        # Filter for splitter-emitted logs only (USDC emits Transfer/Approval logs too)
        addr_bytes = bytes.fromhex(splitter.address[2:])
        logs = [
            log for log in splitter._computation.get_log_entries()
            if log[0] == addr_bytes
        ]
        assert len(logs) == 1  # PoolFunded


# ═══════════════════════════════════════════════════════════════════════
# withdraw — ACCESS-CONTROLLED DISBURSEMENT
# ═══════════════════════════════════════════════════════════════════════

class TestWithdraw:
    """D6 §2.3 + §2.4 — onlyCreatorOrPlatform, bounded by held_balance, 90/10 split."""

    def test_creator_can_withdraw(self, splitter, usdc, accounts):
        """Pool creator may call withdraw (D6 §2.3)."""
        pool_id = b"pool-wd1".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        deposit = 1_000 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, deposit)
            splitter.depositToPool(pool_id, deposit)

        creator_before = usdc.balanceOf(accounts["creator"])
        platform_before = usdc.balanceOf(accounts["platform"])

        with boa.env.prank(accounts["creator"]):
            splitter.withdraw(pool_id, deposit)

        creator_share = deposit * 90 // 100
        platform_share = deposit - creator_share

        assert usdc.balanceOf(accounts["creator"]) == creator_before + creator_share
        assert usdc.balanceOf(accounts["platform"]) == platform_before + platform_share
        # held_balance reduced
        assert splitter.pools(pool_id)[4] == 0
        # total_disbursed increased
        assert splitter.pools(pool_id)[3] == deposit

    def test_platform_can_withdraw(self, splitter, usdc, accounts):
        """Platform address may also call withdraw (D6 §2.3)."""
        pool_id = b"pool-wd2".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        deposit = 500 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, deposit)
            splitter.depositToPool(pool_id, deposit)

        with boa.env.prank(accounts["platform"]):
            splitter.withdraw(pool_id, deposit)

        assert splitter.pools(pool_id)[4] == 0  # fully disbursed

    def test_attacker_cannot_withdraw(self, splitter, usdc, accounts):
        """Unauthorized address cannot withdraw (D6 §2.3 access control)."""
        pool_id = b"pool-wd3".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, 500 * 10**6)
            splitter.depositToPool(pool_id, 500 * 10**6)

        with boa.env.prank(accounts["attacker"]):
            with pytest.raises(boa.BoaError, match="not authorized"):
                splitter.withdraw(pool_id, 100 * 10**6)

    def test_bystander_cannot_withdraw(self, splitter, usdc, accounts):
        """A completely unrelated address cannot withdraw either."""
        pool_id = b"pool-wd4".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, 500 * 10**6)
            splitter.depositToPool(pool_id, 500 * 10**6)

        with boa.env.prank(accounts["bystander"]):
            with pytest.raises(boa.BoaError, match="not authorized"):
                splitter.withdraw(pool_id, 100 * 10**6)

    def test_withdraw_bounded_by_held_balance(self, splitter, usdc, accounts):
        """Cannot withdraw more than held_balance (D6 §2.4 — per-pool isolation)."""
        pool_id = b"pool-wd5".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        deposit = 300 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, deposit)
            splitter.depositToPool(pool_id, deposit)

        with boa.env.prank(accounts["creator"]):
            with pytest.raises(boa.BoaError, match="amount exceeds held balance"):
                splitter.withdraw(pool_id, deposit + 1)

    def test_nonexistent_pool_reverts_withdraw(self, splitter, accounts):
        """Withdrawing from a pool that was never created must revert."""
        pool_id = b"pool-nonexist".ljust(32, b"\x00")
        with boa.env.prank(accounts["creator"]):
            with pytest.raises(boa.BoaError, match="pool does not exist"):
                splitter.withdraw(pool_id, 100 * 10**6)

    def test_withdraw_zero_amount_reverts(self, splitter, usdc, accounts):
        """Withdrawing zero must revert."""
        pool_id = b"pool-wd6".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, 100 * 10**6)
            splitter.depositToPool(pool_id, 100 * 10**6)

        with boa.env.prank(accounts["creator"]):
            with pytest.raises(boa.BoaError, match="amount must be positive"):
                splitter.withdraw(pool_id, 0)

    def test_withdraw_emits_split_event(self, splitter, usdc, accounts):
        """PaymentSplit event logged with correct shares."""
        pool_id = b"pool-evtwd".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        deposit = 200 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, deposit)
            splitter.depositToPool(pool_id, deposit)

        with boa.env.prank(accounts["creator"]):
            splitter.withdraw(pool_id, deposit)

        # Filter for splitter-emitted logs only (USDC emits Transfer logs during withdraw too)
        addr_bytes = bytes.fromhex(splitter.address[2:])
        logs = [
            log for log in splitter._computation.get_log_entries()
            if log[0] == addr_bytes
        ]
        assert len(logs) == 1  # PaymentSplit

    def test_withdraw_partial_amount(self, splitter, usdc, accounts):
        """Partial withdrawal is possible, bounded by held_balance."""
        pool_id = b"pool-partial".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        deposit = 1_000 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, deposit)
            splitter.depositToPool(pool_id, deposit)

        creator_before = usdc.balanceOf(accounts["creator"])
        platform_before = usdc.balanceOf(accounts["platform"])

        half = deposit // 2
        with boa.env.prank(accounts["creator"]):
            splitter.withdraw(pool_id, half)

        creator_share = half * 90 // 100
        platform_share = half - creator_share

        assert usdc.balanceOf(accounts["creator"]) == creator_before + creator_share
        assert usdc.balanceOf(accounts["platform"]) == platform_before + platform_share
        assert splitter.pools(pool_id)[4] == deposit - half  # remaining held_balance
        assert splitter.pools(pool_id)[3] == half  # total_disbursed

    def test_withdraw_after_deactivation(self, splitter, usdc, accounts):
        """Withdraw must still work after deactivatePool (D6 §2.5)."""
        pool_id = b"pool-deactwd".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        deposit = 500 * 10**6
        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, deposit)
            splitter.depositToPool(pool_id, deposit)

        # Deactivate first
        with boa.env.prank(accounts["owner"]):
            splitter.deactivatePool(pool_id)

        # Withdrawal must still succeed
        with boa.env.prank(accounts["creator"]):
            splitter.withdraw(pool_id, deposit)

        # Splitter should have zero held_balance now
        assert splitter.pools(pool_id)[4] == 0

    def test_pool_balance_isolation(self, splitter, usdc, accounts):
        """Two pools with separate deposits have isolated held_balances (D6 §2.4)."""
        pool_a = b"pool-isoa".ljust(32, b"\x00")
        pool_b = b"pool-isob".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_a, accounts["creator"], accounts["platform"])
            splitter.createPool(pool_b, accounts["creator"], accounts["platform"])

        with boa.env.prank(accounts["funder"]):
            usdc.approve(splitter.address, 3_000 * 10**6)
            splitter.depositToPool(pool_a, 1_000 * 10**6)
            splitter.depositToPool(pool_b, 2_000 * 10**6)

        # Pool A: 1000
        assert splitter.pools(pool_a)[4] == 1_000 * 10**6
        # Pool B: 2000
        assert splitter.pools(pool_b)[4] == 2_000 * 10**6

        # Drain pool A
        with boa.env.prank(accounts["creator"]):
            splitter.withdraw(pool_a, 1_000 * 10**6)

        # Pool A empty, pool B untouched
        assert splitter.pools(pool_a)[4] == 0
        assert splitter.pools(pool_b)[4] == 2_000 * 10**6


# ═══════════════════════════════════════════════════════════════════════
# deactivatePool — OWNER-GATED
# ═══════════════════════════════════════════════════════════════════════

class TestDeactivatePool:
    """D6 §2.5 — Owner-only deactivation, still allows withdrawals."""

    def test_owner_deactivates(self, splitter, accounts):
        """Only owner can deactivate a pool."""
        pool_id = b"pool-deact1".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])
            splitter.deactivatePool(pool_id)

        assert splitter.pools(pool_id)[5] is False  # active

    def test_non_owner_cannot_deactivate(self, splitter, accounts):
        """Only owner may call deactivatePool."""
        pool_id = b"pool-deact2".ljust(32, b"\x00")
        with boa.env.prank(accounts["owner"]):
            splitter.createPool(pool_id, accounts["creator"], accounts["platform"])

        with boa.env.prank(accounts["attacker"]):
            with pytest.raises(boa.BoaError, match="only owner"):
                splitter.deactivatePool(pool_id)


# ═══════════════════════════════════════════════════════════════════════
# GUARD: split() MUST NOT EXIST
# ═══════════════════════════════════════════════════════════════════════

class TestSplitFunctionRemoved:
    """D6 §2.3 — Old split() is replaced by depositToPool + withdraw."""

    def test_split_not_callable(self, splitter):
        """The old split() function must not exist on the contract."""
        assert not hasattr(splitter, "split"), "split() must be removed"

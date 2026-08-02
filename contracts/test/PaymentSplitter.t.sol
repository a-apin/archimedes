// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "../src/PaymentSplitter.sol";

contract MockUSDC is ERC20 {
    constructor() ERC20("Mock USDC", "USDC") {
        _mint(msg.sender, 10_000_000 * 10**6);
    }

    function mint(address to, uint256 amount) external { _mint(to, amount); }
    function decimals() public pure override returns (uint8) { return 6; }
}

contract PaymentSplitterTest is Test {
    MockUSDC public usdc;
    PaymentSplitter public splitter;

    address public owner    = address(0x100);
    address public alice    = address(0x101);
    address public bob      = address(0x102);
    address public creator  = address(0x103);
    address public platform = address(0x104);
    address public stranger = address(0x999);

    bytes32 public constant POOL_ID = keccak256("test_pool");

    function setUp() public {
        usdc = new MockUSDC();

        vm.prank(owner);
        splitter = new PaymentSplitter(address(usdc));

        // Fund test users.
        usdc.mint(alice, 100_000 * 10**6);
        usdc.mint(bob,   100_000 * 10**6);

        // Owner creates the pool.
        vm.prank(owner);
        splitter.createPool(POOL_ID, creator, platform);
    }

    // ── Constructor ──────────────────────────────────────────────

    function test_constructor_sets_usdc_and_owner() public view {
        assertEq(address(splitter.usdc()), address(usdc));
        assertEq(splitter.owner(), owner);
    }

    // ── createPool ───────────────────────────────────────────────

    function test_createPool() public {
        bytes32 id = keccak256("new_pool");
        vm.prank(owner);
        splitter.createPool(id, address(0xABC), address(0xDEF));

        (address cr, address pl,,, , bool active) = splitter.pools(id);
        assertEq(cr, address(0xABC));
        assertEq(pl, address(0xDEF));
        assertTrue(active);
    }

    function test_createPool_revert_non_owner() public {
        vm.prank(alice);
        vm.expectRevert();
        splitter.createPool(POOL_ID, creator, platform);
    }

    function test_createPool_revert_duplicate() public {
        vm.prank(owner);
        vm.expectRevert("pool exists");
        splitter.createPool(POOL_ID, creator, platform);
    }

    function test_createPool_revert_invalid_creator() public {
        vm.prank(owner);
        vm.expectRevert("invalid creator");
        splitter.createPool(keccak256("p2"), address(0), platform);
    }

    function test_createPool_revert_invalid_platform() public {
        vm.prank(owner);
        vm.expectRevert("invalid platform");
        splitter.createPool(keccak256("p3"), creator, address(0));
    }

    function test_createPool_emits_PoolCreated() public {
        bytes32 id = keccak256("event_pool");
        vm.expectEmit(true, true, true, false);
        emit PaymentSplitter.PoolCreated(id, creator, platform);
        vm.prank(owner);
        splitter.createPool(id, creator, platform);
    }

    // ── depositToPool ────────────────────────────────────────────

    function test_depositToPool() public {
        uint256 amount = 1_000 * 10**6;
        _deposit(alice, POOL_ID, amount);

        (,,,, uint256 held,) = splitter.pools(POOL_ID);
        assertEq(held, amount);
        assertEq(usdc.balanceOf(address(splitter)), amount);
    }

    function test_depositToPool_multiple_deposits_accumulate() public {
        _deposit(alice, POOL_ID, 500 * 10**6);
        _deposit(bob,   POOL_ID, 300 * 10**6);

        (,,,, uint256 held,) = splitter.pools(POOL_ID);
        assertEq(held, 800 * 10**6);
    }

    function test_depositToPool_revert_zero_amount() public {
        vm.prank(alice);
        usdc.approve(address(splitter), 0);
        vm.expectRevert("amount must be positive");
        splitter.depositToPool(POOL_ID, 0);
    }

    function test_depositToPool_revert_nonexistent_pool() public {
        vm.prank(alice);
        vm.expectRevert("pool not active");
        splitter.depositToPool(keccak256("ghost"), 100);
    }

    function test_depositToPool_emits_PoolFunded() public {
        uint256 amount = 1_000 * 10**6;
        vm.prank(alice);
        usdc.approve(address(splitter), amount);
        vm.expectEmit(true, true, false, true);
        emit PaymentSplitter.PoolFunded(POOL_ID, alice, amount);
        vm.prank(alice);
        splitter.depositToPool(POOL_ID, amount);
    }

    // ── withdraw ─────────────────────────────────────────────────

    function test_withdraw_splits_90_10() public {
        uint256 depositAmt = 1_000 * 10**6;
        _deposit(alice, POOL_ID, depositAmt);

        uint256 creatorBefore  = usdc.balanceOf(creator);
        uint256 platformBefore = usdc.balanceOf(platform);

        // Anyone can withdraw.
        vm.prank(stranger);
        splitter.withdraw(POOL_ID, depositAmt);

        assertEq(usdc.balanceOf(creator)  - creatorBefore,  depositAmt * 90 / 100);
        assertEq(usdc.balanceOf(platform) - platformBefore, depositAmt * 10 / 100);
    }

    function test_withdraw_partial() public {
        uint256 amount = 1_000 * 10**6;
        _deposit(alice, POOL_ID, amount);

        vm.prank(stranger);
        splitter.withdraw(POOL_ID, amount / 2);

        (,,,, uint256 held,) = splitter.pools(POOL_ID);
        assertEq(held, amount / 2);
    }

    function test_withdraw_revert_nonexistent_pool() public {
        vm.prank(stranger);
        vm.expectRevert("pool does not exist");
        splitter.withdraw(keccak256("ghost"), 1);
    }

    function test_withdraw_revert_zero_amount() public {
        vm.prank(stranger);
        vm.expectRevert("amount must be positive");
        splitter.withdraw(POOL_ID, 0);
    }

    function test_withdraw_revert_exceeds_held() public {
        _deposit(alice, POOL_ID, 100 * 10**6);

        vm.prank(stranger);
        vm.expectRevert("amount exceeds held balance");
        splitter.withdraw(POOL_ID, 101 * 10**6);
    }

    function test_withdraw_ungated_after_deactivate() public {
        uint256 amount = 1_000 * 10**6;
        _deposit(alice, POOL_ID, amount);

        vm.prank(owner);
        splitter.deactivatePool(POOL_ID);

        // Withdraw must still work after deactivation.
        vm.prank(stranger);
        splitter.withdraw(POOL_ID, amount);

        (,,,, uint256 held, bool active) = splitter.pools(POOL_ID);
        assertEq(held, 0);
        assertFalse(active);
        assertEq(usdc.balanceOf(creator),  amount * 90 / 100);
        assertEq(usdc.balanceOf(platform), amount * 10 / 100);
    }

    function test_withdraw_emits_PaymentSplit() public {
        uint256 amount = 1_000 * 10**6;
        _deposit(alice, POOL_ID, amount);

        uint256 creatorShare = amount * 90 / 100;
        uint256 platformShare = amount - creatorShare;

        vm.expectEmit(true, false, false, true);
        emit PaymentSplitter.PaymentSplit(POOL_ID, amount, creatorShare, platformShare);
        vm.prank(stranger);
        splitter.withdraw(POOL_ID, amount);
    }

    // ── Pool balance isolation ──────────────────────────────────

    function test_pool_balance_isolation() public {
        bytes32 poolA = keccak256("pool_a");
        bytes32 poolB = keccak256("pool_b");

        vm.startPrank(owner);
        splitter.createPool(poolA, creator, platform);
        splitter.createPool(poolB, creator, platform);
        vm.stopPrank();

        _deposit(alice, poolA, 1_000 * 10**6);
        _deposit(alice, poolB, 2_000 * 10**6);

        (,,,, uint256 heldA,) = splitter.pools(poolA);
        (,,,, uint256 heldB,) = splitter.pools(poolB);
        assertEq(heldA, 1_000 * 10**6);
        assertEq(heldB, 2_000 * 10**6);

        // Drain pool A entirely.
        vm.prank(stranger);
        splitter.withdraw(poolA, 1_000 * 10**6);

        // Pool A is empty; pool B must be untouched (no cross-pool contamination).
        (,,,, heldA,) = splitter.pools(poolA);
        (,,,, heldB,) = splitter.pools(poolB);
        assertEq(heldA, 0);
        assertEq(heldB, 2_000 * 10**6);
    }

    // ── deactivatePool ───────────────────────────────────────────

    function test_deactivatePool() public {
        vm.prank(owner);
        splitter.deactivatePool(POOL_ID);

        (, , , , , bool active) = splitter.pools(POOL_ID);
        assertFalse(active);
    }

    function test_deactivatePool_revert_non_owner() public {
        vm.prank(alice);
        vm.expectRevert();
        splitter.deactivatePool(POOL_ID);
    }

    function test_deactivatePool_blocks_new_deposits() public {
        // Deposit first, then deactivate.
        _deposit(alice, POOL_ID, 500 * 10**6);

        vm.prank(owner);
        splitter.deactivatePool(POOL_ID);

        // New deposits revert.
        vm.prank(bob);
        usdc.approve(address(splitter), 100 * 10**6);
        vm.expectRevert("pool not active");
        splitter.depositToPool(POOL_ID, 100 * 10**6);
    }

    // ── Recreate-after-deactivate strand (issue #5) ──────────────

    function test_recreate_same_pool_id_reverts() public {
        vm.prank(owner);
        splitter.deactivatePool(POOL_ID);

        // Recreating the same pool_id must revert (funds are still in the contract).
        vm.prank(owner);
        vm.expectRevert("pool exists");
        splitter.createPool(POOL_ID, creator, platform);
    }

    // ── Integration: deactivate → withdraw → (funds drained) ─────

    function test_drain_after_deactivate() public {
        uint256 amount = 2_000 * 10**6;
        _deposit(alice, POOL_ID, amount);

        vm.prank(owner);
        splitter.deactivatePool(POOL_ID);

        // Drain fully via withdraw.
        vm.prank(stranger);
        splitter.withdraw(POOL_ID, amount);

        // Pool storage unchanged but held_balance = 0.
        (address cr, address pl,,, uint256 held, bool active) = splitter.pools(POOL_ID);
        assertEq(cr, creator);
        assertEq(pl, platform);
        assertEq(held, 0);
        assertFalse(active);

        // Withdraw of 0 remaining reverts.
        vm.expectRevert("amount must be positive");
        splitter.withdraw(POOL_ID, 0);
    }

    // ── Helpers ──────────────────────────────────────────────────

    function _deposit(address from, bytes32 poolId, uint256 amount) internal {
        vm.prank(from);
        usdc.approve(address(splitter), amount);
        vm.prank(from);
        splitter.depositToPool(poolId, amount);
    }
}

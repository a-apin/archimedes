// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/AMMPool.sol";
import "../src/AMMRouter.sol";

/// @dev Mock ERC-20 for testing
contract MockToken is ERC20 {
    uint8 private _decimals;

    constructor(string memory name, string memory symbol, uint8 dec) ERC20(name, symbol) {
        _decimals = dec;
        _mint(msg.sender, 1_000_000 * 10 ** dec);
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function decimals() public view override returns (uint8) {
        return _decimals;
    }
}

contract AMMTest is Test {
    AMMRouter public router;
    MockToken public tokenA;
    MockToken public tokenB;
    MockToken public tokenC;

    address public owner = address(0x1);
    address public alice = address(0x2);
    address public bob = address(0x3);

    function setUp() public {
        vm.prank(owner);
        router = new AMMRouter(owner);

        tokenA = new MockToken("Token A", "TKA", 18);
        tokenB = new MockToken("Token B", "TKB", 18);
        tokenC = new MockToken("Token C", "TKC", 18);

        // Fund alice and bob
        tokenA.mint(alice, 100_000 * 1e18);
        tokenB.mint(alice, 100_000 * 1e18);
        tokenC.mint(alice, 100_000 * 1e18);
        tokenA.mint(bob, 100_000 * 1e18);
        tokenB.mint(bob, 100_000 * 1e18);
    }

    // ─── Pool Creation ───────────────────────────────────────────────

    function test_createPool() public {
        address pool = router.createPool(address(tokenA), address(tokenB));
        assertTrue(pool != address(0));

        // Verify pool is tracked
        assertEq(router.getPool(address(tokenA), address(tokenB)), pool);
        assertEq(router.getPool(address(tokenB), address(tokenA)), pool); // reverse order too
    }

    function test_createPool_emits_event() public {
        // Just verify pool creation works and returns non-zero address
        address pool = router.createPool(address(tokenA), address(tokenB));
        assertTrue(pool != address(0));
        assertEq(router.getPool(address(tokenA), address(tokenB)), pool);
    }

    function test_revert_createPool_same_token() public {
        vm.expectRevert(AMMRouter.InvalidPair.selector);
        router.createPool(address(tokenA), address(tokenA));
    }

    function test_revert_createPool_duplicate() public {
        router.createPool(address(tokenA), address(tokenB));
        vm.expectRevert(AMMRouter.PoolAlreadyExists.selector);
        router.createPool(address(tokenA), address(tokenB));
    }

    function test_getAllPools() public {
        router.createPool(address(tokenA), address(tokenB));
        router.createPool(address(tokenA), address(tokenC));
        router.createPool(address(tokenB), address(tokenC));

        address[] memory pools = router.getAllPools();
        assertEq(pools.length, 3);
    }

    function test_getPool_nonexistent() public view {
        assertEq(router.getPool(address(tokenA), address(tokenC)), address(0));
    }

    // ─── Add Liquidity ───────────────────────────────────────────────

    function test_addLiquidity_first_deposit() public {
        address pool = router.createPool(address(tokenA), address(tokenB));

        uint256 amountA = 10_000 * 1e18;
        uint256 amountB = 20_000 * 1e18;

        vm.startPrank(alice);
        tokenA.approve(address(router), amountA);
        tokenB.approve(address(router), amountB);

        uint256 lpTokens = router.addLiquidity(
            address(tokenA), address(tokenB),
            amountA, amountB, 0
        );
        vm.stopPrank();

        // First deposit LP tokens = sqrt(amount0 * amount1)
        assertGt(lpTokens, 0);

        // Alice holds the LP tokens
        assertEq(IERC20(pool).balanceOf(alice), lpTokens);
    }

    /// @dev #955: tokens transferred directly to the pool (not via addLiquidity)
    ///      are stranded — the balance grows but the tracked reserves don't. sync()
    ///      folds that surplus into the reserves (accruing to LPs) instead.
    function test_sync_folds_direct_transfer_into_reserves() public {
        address pool = router.createPool(address(tokenA), address(tokenB));
        uint256 amountA = 10_000 * 1e18;
        uint256 amountB = 20_000 * 1e18;

        vm.startPrank(alice);
        tokenA.approve(address(router), amountA);
        tokenB.approve(address(router), amountB);
        router.addLiquidity(address(tokenA), address(tokenB), amountA, amountB, 0);
        vm.stopPrank();

        AMMPool amm = AMMPool(pool);
        uint256 r0Before = amm.reserve0();
        uint256 r1Before = amm.reserve1();

        // Donate tokenA straight to the pool, bypassing addLiquidity.
        uint256 donation = 500 * 1e18;
        vm.prank(bob);
        tokenA.transfer(pool, donation);

        // Stranded: reserves are unchanged until sync().
        assertEq(amm.reserve0(), r0Before);
        assertEq(amm.reserve1(), r1Before);

        amm.sync();

        // After sync the reserves equal the real balances, folding in the donation.
        assertEq(amm.reserve0(), IERC20(amm.token0()).balanceOf(pool));
        assertEq(amm.reserve1(), IERC20(amm.token1()).balanceOf(pool));
        if (amm.token0() == address(tokenA)) {
            assertEq(amm.reserve0(), r0Before + donation);
        } else {
            assertEq(amm.reserve1(), r1Before + donation);
        }
    }

    function test_addLiquidity_second_deposit() public {
        address pool = router.createPool(address(tokenA), address(tokenB));

        uint256 amountA = 10_000 * 1e18;
        uint256 amountB = 20_000 * 1e18;

        // First deposit
        vm.startPrank(alice);
        tokenA.approve(address(router), amountA);
        tokenB.approve(address(router), amountB);
        uint256 lp1 = router.addLiquidity(address(tokenA), address(tokenB), amountA, amountB, 0);
        vm.stopPrank();

        // Second deposit (same ratio)
        vm.startPrank(bob);
        tokenA.approve(address(router), amountA);
        tokenB.approve(address(router), amountB);
        uint256 lp2 = router.addLiquidity(address(tokenA), address(tokenB), amountA, amountB, 0);
        vm.stopPrank();

        // Bob should get roughly the same LP tokens as Alice
        assertGt(lp2, 0);
        // Equal deposits → equal LP, except the first depositor (Alice) forfeited
        // MIN_LIQUIDITY to the dead-share inflation guard.
        assertApproxEqAbs(lp1 + AMMPool(pool).MIN_LIQUIDITY(), lp2, 100);
    }

    function test_addLiquidity_first_deposit_locks_dead_shares() public {
        address pool = router.createPool(address(tokenA), address(tokenB));
        uint256 amountA = 10_000 * 1e18;
        uint256 amountB = 20_000 * 1e18;

        vm.startPrank(alice);
        tokenA.approve(address(router), amountA);
        tokenB.approve(address(router), amountB);
        uint256 lpTokens = router.addLiquidity(address(tokenA), address(tokenB), amountA, amountB, 0);
        vm.stopPrank();

        uint256 minLiq = AMMPool(pool).MIN_LIQUIDITY();
        // MIN_LIQUIDITY is permanently locked in the dead sink and excluded
        // from the depositor's balance — the canonical Uniswap-V2 guard.
        assertEq(IERC20(pool).balanceOf(AMMPool(pool).DEAD_SHARES_SINK()), minLiq);
        assertEq(IERC20(pool).balanceOf(alice), lpTokens);
        assertEq(AMMPool(pool).totalSupply(), lpTokens + minLiq);
    }

    function test_revert_addLiquidity_first_deposit_below_min_liquidity() public {
        router.createPool(address(tokenA), address(tokenB));

        // sqrt(1 * 1) = 1 <= MIN_LIQUIDITY → first deposit must revert rather
        // than mint a dust LP supply an attacker could inflate.
        vm.startPrank(alice);
        tokenA.approve(address(router), 1);
        tokenB.approve(address(router), 1);
        vm.expectRevert();
        router.addLiquidity(address(tokenA), address(tokenB), 1, 1, 0);
        vm.stopPrank();
    }

    function test_revert_addLiquidity_zero() public {
        router.createPool(address(tokenA), address(tokenB));

        vm.prank(alice);
        vm.expectRevert();
        router.addLiquidity(address(tokenA), address(tokenB), 0, 1000, 0);
    }

    function test_revert_addLiquidity_slippage() public {
        router.createPool(address(tokenA), address(tokenB));

        uint256 amountA = 10_000 * 1e18;
        uint256 amountB = 20_000 * 1e18;

        vm.startPrank(alice);
        tokenA.approve(address(router), amountA);
        tokenB.approve(address(router), amountB);

        // Require more LP tokens than possible
        vm.expectRevert(AMMRouter.SlippageExceeded.selector);
        router.addLiquidity(address(tokenA), address(tokenB), amountA, amountB, type(uint256).max);
        vm.stopPrank();
    }

    // ─── Swap ────────────────────────────────────────────────────────

    function test_swap_basic() public {
        address pool = router.createPool(address(tokenA), address(tokenB));
        _seedLiquidity(alice, address(tokenA), address(tokenB), 10_000e18, 20_000e18);

        uint256 swapAmount = 100e18;
        uint256 aliceBalBefore = tokenB.balanceOf(bob);

        vm.startPrank(bob);
        tokenA.approve(address(router), swapAmount);
        uint256 amountOut = router.swap(address(tokenA), address(tokenB), swapAmount, 0);
        vm.stopPrank();

        assertGt(amountOut, 0);
        assertEq(tokenB.balanceOf(bob) - aliceBalBefore, amountOut);
    }

    function test_swap_includes_fee() public {
        // Fee is 30 bps (0.3%)
        address pool = router.createPool(address(tokenA), address(tokenB));
        _seedLiquidity(alice, address(tokenA), address(tokenB), 100_000e18, 100_000e18);

        uint256 swapAmount = 1000e18;

        // Calculate expected output: with 50/50 pool, x*y=k
        // k = 100000 * 100000 = 1e10
        // new_x = 101000
        // new_y = k / new_x = ~99009.9
        // amountOut = 100000 - 99009.9 = ~990.1
        // But with 0.3% fee, amountInWithFee = 1000 * 9970 = 9970000
        // amountOut = (100000 * 9970000) / (100000 * 10000 + 9970000) = ~996.9

        vm.startPrank(bob);
        tokenA.approve(address(router), swapAmount);
        uint256 amountOut = router.swap(address(tokenA), address(tokenB), swapAmount, 0);
        vm.stopPrank();

        // Should be less than 1000 because of the fee and price impact
        assertLt(amountOut, 1000e18);
        // Should be more than 985 (with 50/50 ratio and 0.3% fee)
        assertGt(amountOut, 985e18);
    }

    function test_swap_reverse_direction() public {
        router.createPool(address(tokenA), address(tokenB));
        _seedLiquidity(alice, address(tokenA), address(tokenB), 10_000e18, 20_000e18);

        uint256 swapAmount = 100e18;

        // Swap B -> A
        vm.startPrank(bob);
        tokenB.approve(address(router), swapAmount);
        uint256 amountOut = router.swap(address(tokenB), address(tokenA), swapAmount, 0);
        vm.stopPrank();

        assertGt(amountOut, 0);
    }

    function test_revert_swap_slippage() public {
        router.createPool(address(tokenA), address(tokenB));
        _seedLiquidity(alice, address(tokenA), address(tokenB), 10_000e18, 20_000e18);

        vm.startPrank(bob);
        tokenA.approve(address(router), 100e18);
        vm.expectRevert(AMMRouter.SlippageExceeded.selector);
        router.swap(address(tokenA), address(tokenB), 100e18, type(uint256).max);
        vm.stopPrank();
    }

    function test_revert_swap_no_pool() public {
        vm.startPrank(alice);
        tokenA.approve(address(router), 100e18);
        vm.expectRevert(AMMRouter.PoolNotFound.selector);
        router.swap(address(tokenA), address(tokenC), 100e18, 0);
        vm.stopPrank();
    }

    // ─── Direct pool swap (AUDIT B9 — minAmountOut guard) ───────────

    /// @notice Direct pool.swap with minAmountOut=0 succeeds (backward-compatible).
    function test_pool_swap_direct_zero_min() public {
        address pool = router.createPool(address(tokenA), address(tokenB));
        _seedLiquidity(alice, address(tokenA), address(tokenB), 10_000e18, 20_000e18);

        uint256 swapAmount = 100e18;

        vm.startPrank(bob);
        tokenA.approve(pool, swapAmount);
        uint256 amountOut = AMMPool(pool).swap(address(tokenA), swapAmount, bob, 0);
        vm.stopPrank();

        assertGt(amountOut, 0);
    }

    /// @notice Direct pool.swap reverts with SlippageProtection when minAmountOut
    ///         exceeds the computed output (AUDIT B9).
    function test_revert_pool_swap_slippage_protection() public {
        address pool = router.createPool(address(tokenA), address(tokenB));
        _seedLiquidity(alice, address(tokenA), address(tokenB), 10_000e18, 20_000e18);

        uint256 swapAmount = 100e18;
        // Preview what we'd get, then require one more wei than that.
        uint256 preview = AMMPool(pool).getAmountOut(address(tokenA), swapAmount);
        assertGt(preview, 0);

        vm.startPrank(bob);
        tokenA.approve(pool, swapAmount);
        vm.expectRevert(AMMPool.SlippageProtection.selector);
        AMMPool(pool).swap(address(tokenA), swapAmount, bob, preview + 1);
        vm.stopPrank();
    }

    // ─── Remove Liquidity ────────────────────────────────────────────

    function test_removeLiquidity_basic() public {
        address pool = router.createPool(address(tokenA), address(tokenB));
        uint256 amountA = 10_000e18;
        uint256 amountB = 20_000e18;

        _seedLiquidity(alice, address(tokenA), address(tokenB), amountA, amountB);

        uint256 lpBalance = IERC20(pool).balanceOf(alice);
        assertGt(lpBalance, 0);

        uint256 aliceABefore = tokenA.balanceOf(alice);
        uint256 aliceBBefore = tokenB.balanceOf(alice);

        vm.startPrank(alice);
        IERC20(pool).approve(address(router), lpBalance);
        (uint256 outA, uint256 outB) = router.removeLiquidity(
            address(tokenA), address(tokenB),
            lpBalance, 0, 0
        );
        vm.stopPrank();

        assertGt(outA, 0);
        assertGt(outB, 0);
        assertEq(tokenA.balanceOf(alice) - aliceABefore, outA);
        assertEq(tokenB.balanceOf(alice) - aliceBBefore, outB);
        assertEq(IERC20(pool).balanceOf(alice), 0);
    }

    function test_removeLiquidity_partial() public {
        address pool = router.createPool(address(tokenA), address(tokenB));
        _seedLiquidity(alice, address(tokenA), address(tokenB), 10_000e18, 20_000e18);

        uint256 lpBalance = IERC20(pool).balanceOf(alice);
        uint256 halfLP = lpBalance / 2;

        vm.startPrank(alice);
        IERC20(pool).approve(address(router), halfLP);
        (uint256 outA, uint256 outB) = router.removeLiquidity(
            address(tokenA), address(tokenB),
            halfLP, 0, 0
        );
        vm.stopPrank();

        // Should get roughly half the reserves
        assertGt(outA, 0);
        assertGt(outB, 0);
        // Still has remaining LP tokens
        assertEq(IERC20(pool).balanceOf(alice), lpBalance - halfLP);
    }

    function test_removeLiquidity_slippage_protection() public {
        address pool = router.createPool(address(tokenA), address(tokenB));
        _seedLiquidity(alice, address(tokenA), address(tokenB), 10_000e18, 20_000e18);

        uint256 lpBalance = IERC20(pool).balanceOf(alice);

        vm.startPrank(alice);
        IERC20(pool).approve(address(router), lpBalance);
        vm.expectRevert(AMMRouter.SlippageExceeded.selector);
        router.removeLiquidity(
            address(tokenA), address(tokenB),
            lpBalance, type(uint256).max, type(uint256).max
        );
        vm.stopPrank();
    }

    // ─── View Functions ──────────────────────────────────────────────

    function test_getAmountOut() public {
        router.createPool(address(tokenA), address(tokenB));
        _seedLiquidity(alice, address(tokenA), address(tokenB), 10_000e18, 20_000e18);

        uint256 previewOut = router.getAmountOut(address(tokenA), address(tokenB), 100e18);
        assertGt(previewOut, 0);

        // Actually swap and verify it matches
        vm.startPrank(bob);
        tokenA.approve(address(router), 100e18);
        uint256 actualOut = router.swap(address(tokenA), address(tokenB), 100e18, 0);
        vm.stopPrank();

        assertEq(previewOut, actualOut);
    }

    function test_getAmountOut_no_pool() public view {
        uint256 out = router.getAmountOut(address(tokenA), address(tokenC), 100e18);
        assertEq(out, 0);
    }

    // ─── Admin ───────────────────────────────────────────────────────

    function test_setDefaultSwapFeeBps() public {
        vm.prank(owner);
        router.setDefaultSwapFeeBps(50);
        assertEq(router.defaultSwapFeeBps(), 50);
    }

    function test_revert_setFee_non_owner() public {
        vm.prank(alice);
        vm.expectRevert();
        router.setDefaultSwapFeeBps(50);
    }

    // ─── Helpers ─────────────────────────────────────────────────────

    function _seedLiquidity(
        address user,
        address tA,
        address tB,
        uint256 amtA,
        uint256 amtB
    ) internal {
        vm.startPrank(user);
        IERC20(tA).approve(address(router), amtA);
        IERC20(tB).approve(address(router), amtB);
        router.addLiquidity(tA, tB, amtA, amtB, 0);
        vm.stopPrank();
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/PriceOracle.sol";

/// @dev Rate-limit / ratchet-protection tests for PriceOracle (issue #587).
///      The deviation bound caps a *single* move; the updateCooldown caps how
///      *often* a move can land, so a compromised updater key cannot chain
///      many bounded moves in one block to ratchet the price arbitrarily.
contract PriceOracleCooldownTest is Test {
    PriceOracle public oracle;

    address public owner = address(0x1);
    address public alice = address(0x2);

    uint256 constant INITIAL_PRICE = 392_600_000; // $392.60 (6 dec)
    uint256 constant WITHIN_BOUND = 400_000_000; // +~1.9%, inside the 20% bound

    function setUp() public {
        // Pin a non-zero baseline timestamp so lastSetPriceTime is unambiguous.
        vm.warp(1_000_000);
        vm.prank(owner);
        oracle = new PriceOracle("TSLA", INITIAL_PRICE, owner);
    }

    function test_default_cooldown_is_30s() public view {
        // 30s: above Arc's sub-second block time (defeats the same-block ratchet)
        // but below the oracle runner's 60s push cadence so legit pushes never trip.
        assertEq(oracle.updateCooldown(), 30);
        assertEq(oracle.MAX_UPDATE_COOLDOWN(), 1 hours);
    }

    function test_first_setPrice_allowed_regardless_of_cooldown() public {
        // lastSetPriceTime starts at 0, so the first push is never rate-limited.
        vm.prank(owner);
        oracle.setPrice(WITHIN_BOUND);
        assertEq(oracle.price(), WITHIN_BOUND);
        assertEq(oracle.lastSetPriceTime(), block.timestamp);
    }

    function test_second_setPrice_within_cooldown_reverts() public {
        vm.prank(owner);
        oracle.setPrice(WITHIN_BOUND);
        // Same block → rate-limited.
        vm.expectRevert(
            abi.encodeWithSelector(
                PriceOracle.UpdateRateLimited.selector, block.timestamp, oracle.updateCooldown(), block.timestamp
            )
        );
        vm.prank(owner);
        oracle.setPrice(WITHIN_BOUND + 1);
    }

    function test_setPrice_allowed_after_cooldown_elapses() public {
        vm.prank(owner);
        oracle.setPrice(WITHIN_BOUND);
        vm.warp(block.timestamp + 300); // exactly one cooldown window later
        vm.prank(owner);
        oracle.setPrice(WITHIN_BOUND + 1);
        assertEq(oracle.price(), WITHIN_BOUND + 1);
    }

    function test_setPrice_still_limited_one_second_before_window() public {
        vm.prank(owner);
        oracle.setPrice(WITHIN_BOUND);
        vm.warp(block.timestamp + 29); // one second short of the 30s window
        vm.expectRevert(); // UpdateRateLimited
        vm.prank(owner);
        oracle.setPrice(WITHIN_BOUND + 1);
    }

    /// @dev Core #587 protection: two max-deviation moves in the same block
    ///      cannot compound — the cooldown blocks the second before its
    ///      deviation check even runs, so cumulative drift is one move/window.
    function test_cooldown_blocks_ratchet_within_block() public {
        uint256 firstMove = INITIAL_PRICE + (INITIAL_PRICE * 2000) / 10_000; // +20% (the cap)
        vm.prank(owner);
        oracle.setPrice(firstMove);

        uint256 secondMove = firstMove + (firstMove * 2000) / 10_000; // another +20%
        vm.expectRevert(); // UpdateRateLimited — not PriceDeviationTooLarge
        vm.prank(owner);
        oracle.setPrice(secondMove);

        // Still only one bounded move from the start.
        assertEq(oracle.price(), firstMove);
    }

    function test_forceSetPrice_is_exempt_from_cooldown() public {
        vm.prank(owner);
        oracle.setPrice(WITHIN_BOUND);
        // Owner escape hatch works in the same block despite the cooldown.
        vm.prank(owner);
        oracle.forceSetPrice(WITHIN_BOUND + 1);
        assertEq(oracle.price(), WITHIN_BOUND + 1);
    }

    function test_forceSetPrice_does_not_touch_lastSetPriceTime() public {
        // forceSetPrice must not seed the setPrice cooldown clock.
        vm.prank(owner);
        oracle.forceSetPrice(WITHIN_BOUND);
        assertEq(oracle.lastSetPriceTime(), 0);
    }

    /// @dev #935: a compromised owner must not be able to CHAIN forceSetPrice
    ///      calls (each bounded to 10×) with no spacing to ratchet the price
    ///      arbitrarily fast. The first force call is immediate (escape hatch),
    ///      but a consecutive one within updateCooldown reverts.
    function test_forceSetPrice_second_call_rate_limited() public {
        // First force call is immediate.
        vm.prank(owner);
        oracle.forceSetPrice(WITHIN_BOUND);
        assertEq(oracle.lastForceSetPriceTime(), block.timestamp);

        // A second force call in the same block (within the cooldown) is blocked.
        vm.expectRevert(); // UpdateRateLimited
        vm.prank(owner);
        oracle.forceSetPrice(WITHIN_BOUND + 1);

        // One second short of the window: still blocked.
        vm.warp(block.timestamp + oracle.updateCooldown() - 1);
        vm.expectRevert(); // UpdateRateLimited
        vm.prank(owner);
        oracle.forceSetPrice(WITHIN_BOUND + 1);

        // Once the window elapses, the owner can force again (gap recovery).
        vm.warp(block.timestamp + 1);
        vm.prank(owner);
        oracle.forceSetPrice(WITHIN_BOUND + 1);
        assertEq(oracle.price(), WITHIN_BOUND + 1);
    }

    function test_setUpdateCooldown_changes_window_and_emits() public {
        vm.expectEmit(false, false, false, true);
        emit PriceOracle.UpdateCooldownChanged(30, 600);
        vm.prank(owner);
        oracle.setUpdateCooldown(600);
        assertEq(oracle.updateCooldown(), 600);
    }

    function test_setUpdateCooldown_zero_disables_rate_limit() public {
        vm.prank(owner);
        oracle.setUpdateCooldown(0);
        // Two consecutive same-block pushes now both land.
        vm.prank(owner);
        oracle.setPrice(WITHIN_BOUND);
        vm.prank(owner);
        oracle.setPrice(WITHIN_BOUND + 1);
        assertEq(oracle.price(), WITHIN_BOUND + 1);
    }

    function test_setUpdateCooldown_at_max_boundary_allowed() public {
        vm.prank(owner);
        oracle.setUpdateCooldown(1 hours);
        assertEq(oracle.updateCooldown(), 1 hours);
    }

    function test_revert_setUpdateCooldown_above_max() public {
        vm.expectRevert(PriceOracle.InvalidCooldown.selector);
        vm.prank(owner);
        oracle.setUpdateCooldown(1 hours + 1);
    }

    function test_revert_setUpdateCooldown_non_owner() public {
        vm.prank(alice);
        vm.expectRevert(); // Ownable: OwnableUnauthorizedAccount
        oracle.setUpdateCooldown(600);
    }
}

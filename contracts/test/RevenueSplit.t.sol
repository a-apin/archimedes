// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "../src/RevenueSplit.sol";

/// @dev Mock ERC-20 USDC for testing (6 decimals, mirrors the fixture in
///      SyntheticVault.t.sol).
contract MockUSDC is ERC20 {
    constructor() ERC20("Mock USDC", "USDC") {
        _mint(msg.sender, 1_000_000 * 10 ** 6); // 1M USDC
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }
}

contract RevenueSplitTest is Test {
    MockUSDC public usdc;
    RevenueSplit public splitter;

    address public creator = address(0xC0FFEE);
    address public platform = address(0xBEEF);

    function setUp() public {
        usdc = new MockUSDC();
        splitter = new RevenueSplit(address(usdc), creator, platform);
    }

    // ─── Split math ──────────────────────────────────────────────────

    function test_release_splits_90_10() public {
        // Settle 1.00 USDC into the splitter (the x402 facilitator transfer).
        uint256 amount = 1_000_000; // 1.00 USDC (6 dec)
        usdc.mint(address(splitter), amount);

        assertEq(splitter.pending(), amount);

        splitter.release();

        // 90% to creator, 10% to platform.
        assertEq(usdc.balanceOf(creator), 900_000);
        assertEq(usdc.balanceOf(platform), 100_000);
        assertEq(usdc.balanceOf(address(splitter)), 0);
    }

    function test_release_subcent_no_dust_stranded() public {
        // A sub-cent x402 payment: 0.001 USDC = 1000 base units.
        // 90% = 900, 10% = 100 — exact, but the contract computes platform as the
        // remainder so any rounding dust always lands with the platform, never
        // stranded in the contract.
        uint256 amount = 1000;
        usdc.mint(address(splitter), amount);

        splitter.release();

        assertEq(usdc.balanceOf(creator), 900);
        assertEq(usdc.balanceOf(platform), 100);
        assertEq(usdc.balanceOf(address(splitter)), 0);
    }

    function test_release_rounding_remainder_goes_to_platform() public {
        // 7 base units: creator = (7 * 9000) / 10000 = 6 (floored),
        // platform = 7 - 6 = 1. No dust stranded.
        uint256 amount = 7;
        usdc.mint(address(splitter), amount);

        splitter.release();

        assertEq(usdc.balanceOf(creator), 6);
        assertEq(usdc.balanceOf(platform), 1);
        assertEq(usdc.balanceOf(address(splitter)), 0);
    }

    function test_release_emits_event() public {
        uint256 amount = 1_000_000;
        usdc.mint(address(splitter), amount);

        vm.expectEmit(false, false, false, true);
        emit RevenueSplit.Released(amount, 900_000, 100_000);
        splitter.release();
    }

    function test_release_reverts_when_empty() public {
        vm.expectRevert(bytes("RevenueSplit: nothing to release"));
        splitter.release();
    }

    function test_release_is_permissionless() public {
        uint256 amount = 1_000_000;
        usdc.mint(address(splitter), amount);

        // A random caller can trigger the forward, but funds only route to the
        // two immutable recipients.
        vm.prank(address(0xD00D));
        splitter.release();

        assertEq(usdc.balanceOf(creator), 900_000);
        assertEq(usdc.balanceOf(platform), 100_000);
    }

    // ─── Construction guards ─────────────────────────────────────────

    function test_constructor_reverts_zero_usdc() public {
        vm.expectRevert(bytes("RevenueSplit: zero USDC"));
        new RevenueSplit(address(0), creator, platform);
    }

    function test_constructor_reverts_zero_creator() public {
        vm.expectRevert(bytes("RevenueSplit: zero creator"));
        new RevenueSplit(address(usdc), address(0), platform);
    }

    function test_constructor_reverts_zero_platform() public {
        vm.expectRevert(bytes("RevenueSplit: zero platform"));
        new RevenueSplit(address(usdc), creator, address(0));
    }

    // ─── No redirect after deploy (no admin function) ────────────────

    function test_recipients_are_immutable() public view {
        // The contract exposes no setter for creator/platform — the only way to
        // read them is the immutable getters, and they match construction args.
        assertEq(splitter.creator(), creator);
        assertEq(splitter.platform(), platform);
        assertEq(address(splitter.usdc()), address(usdc));
        assertEq(splitter.CREATOR_BPS(), 9_000);
        assertEq(splitter.PLATFORM_BPS(), 1_000);
        assertEq(splitter.BPS_DENOMINATOR(), 10_000);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/PriceOracle.sol";
import "../src/SyntheticToken.sol";
import "../src/SyntheticVault.sol";

/// @dev Mock ERC-20 USDC for testing
contract MockUSDC is ERC20 {
    constructor() ERC20("Mock USDC", "USDC") {
        _mint(msg.sender, 1_000_000 * 10**6); // 1M USDC
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }
}

contract SyntheticVaultTest is Test {
    MockUSDC public usdc;
    PriceOracle public oracle;
    SyntheticToken public sTSLA;
    SyntheticVault public vault;

    address public owner = address(0x1);
    address public alice = address(0x2);
    address public bob   = address(0x3);

    uint256 constant INITIAL_PRICE = 392_600_000; // $392.60 (6 dec)

    function setUp() public {
        usdc = new MockUSDC();

        vm.prank(owner);
        oracle = new PriceOracle("TSLA", INITIAL_PRICE, owner);

        vm.prank(owner);
        sTSLA = new SyntheticToken("Synthetic TSLA", "sTSLA", owner);

        vm.prank(owner);
        vault = new SyntheticVault(
            address(usdc),
            address(sTSLA),
            address(oracle),
            owner
        );

        vm.prank(owner);
        sTSLA.setVault(address(vault));

        // Fund alice and bob with USDC
        usdc.mint(alice, 100_000 * 10**6);
        usdc.mint(bob,   100_000 * 10**6);
    }

    // ─── Oracle Tests ─────────────────────────────────────────────

    function test_oracle_initial_price() public view {
        assertEq(oracle.price(), INITIAL_PRICE);
        assertEq(oracle.lastUpdated(), block.timestamp);
    }

    function test_oracle_update_price() public {
        vm.prank(owner);
        oracle.setPrice(400_000_000);

        assertEq(oracle.price(), 400_000_000);
    }

    function test_revert_oracle_non_owner() public {
        vm.prank(alice);
        vm.expectRevert();
        oracle.setPrice(400_000_000);
    }

    function test_revert_stale_price() public {
        vm.warp(block.timestamp + 2 hours); // past MAX_STALENESS
        vm.expectRevert();
        oracle.getPrice();
    }

    function test_isFresh() public {
        assertTrue(oracle.isFresh());
        vm.warp(block.timestamp + 2 hours);
        assertFalse(oracle.isFresh());
    }

    // ─── Mint Tests ───────────────────────────────────────────────

    function test_mint_basic() public {
        uint256 usdcAmount = 10_000 * 10**6; // $10,000 USDC

        vm.startPrank(alice);
        usdc.approve(address(vault), usdcAmount);
        uint256 sTslaOut = vault.mint(usdcAmount);
        vm.stopPrank();

        // sTSLA should be > 0
        assertGt(sTslaOut, 0);
        assertEq(sTSLA.balanceOf(alice), sTslaOut);

        // Vault should have the USDC
        assertEq(usdc.balanceOf(address(vault)), usdcAmount);
    }

    function test_mint_amount_calculation() public {
        // $10,000 USDC at $392.60/TSLA, 120% collateral ratio, 0.5% fee
        uint256 usdcAmount = 10_000 * 10**6;
        uint256 fee = (usdcAmount * 50) / 10000; // 50 bps = $50
        uint256 netUsdc = usdcAmount - fee;       // $9,950
        // sTSLA = netUsdc * 1e18 * BPS / (price * collateralRatio)
        //       = 9950000000 * 1e18 * 10000 / (392600000 * 12000)
        uint256 expected = (netUsdc * 1e18 * 10000) / (INITIAL_PRICE * 12000);

        vm.startPrank(alice);
        usdc.approve(address(vault), usdcAmount);
        uint256 sTslaOut = vault.mint(usdcAmount);
        vm.stopPrank();

        assertEq(sTslaOut, expected);
    }

    function test_revert_mint_zero() public {
        vm.prank(alice);
        vm.expectRevert(SyntheticVault.ZeroAmount.selector);
        vault.mint(0);
    }

    function test_revert_mint_no_approval() public {
        vm.prank(alice);
        vm.expectRevert();
        vault.mint(1000 * 10**6);
    }

    // ─── Burn Tests ───────────────────────────────────────────────

    function test_burn_basic() public {
        // First mint
        uint256 usdcAmount = 10_000 * 10**6;
        vm.startPrank(alice);
        usdc.approve(address(vault), usdcAmount);
        uint256 sTslaOut = vault.mint(usdcAmount);

        // Then burn all
        sTSLA.approve(address(vault), sTslaOut); // not needed but safe
        uint256 usdcBack = vault.burn(sTslaOut);
        vm.stopPrank();

        assertGt(usdcBack, 0);
        assertLt(usdcBack, usdcAmount); // Less due to fees
        assertEq(sTSLA.balanceOf(alice), 0);
    }

    function test_revert_burn_zero() public {
        vm.prank(alice);
        vm.expectRevert(SyntheticVault.ZeroAmount.selector);
        vault.burn(0);
    }

    function test_mint_and_burn_round_trip() public {
        uint256 usdcAmount = 10_000 * 10**6;
        uint256 aliceInitialUsdc = usdc.balanceOf(alice);

        vm.startPrank(alice);
        usdc.approve(address(vault), usdcAmount);
        uint256 sTslaOut = vault.mint(usdcAmount);

        uint256 usdcBack = vault.burn(sTslaOut);
        vm.stopPrank();

        // Alice should have lost some USDC due to mint + burn fees
        uint256 aliceFinalUsdc = usdc.balanceOf(alice);
        assertLt(aliceFinalUsdc, aliceInitialUsdc);
        assertGt(aliceFinalUsdc, aliceInitialUsdc - usdcAmount); // Should have most back

        // Fees should be in the vault
        assertGt(vault.protocolFees(), 0);
    }

    // ─── View Tests ───────────────────────────────────────────────

    function test_preview_mint() public {
        uint256 usdcAmount = 10_000 * 10**6;
        uint256 preview = vault.previewMint(usdcAmount);

        vm.startPrank(alice);
        usdc.approve(address(vault), usdcAmount);
        uint256 actual = vault.mint(usdcAmount);
        vm.stopPrank();

        assertEq(preview, actual);
    }

    function test_preview_burn() public {
        uint256 usdcAmount = 10_000 * 10**6;

        vm.startPrank(alice);
        usdc.approve(address(vault), usdcAmount);
        uint256 sTslaOut = vault.mint(usdcAmount);

        uint256 preview = vault.previewBurn(sTslaOut);
        uint256 actual = vault.burn(sTslaOut);
        vm.stopPrank();

        assertEq(preview, actual);
    }

    // ─── Admin Tests ──────────────────────────────────────────────

    function test_collect_fees() public {
        // Mint to generate fees
        uint256 usdcAmount = 10_000 * 10**6;
        vm.startPrank(alice);
        usdc.approve(address(vault), usdcAmount);
        vault.mint(usdcAmount);
        vm.stopPrank();

        uint256 fees = vault.protocolFees();
        assertGt(fees, 0);

        // Collect as owner
        uint256 ownerBalBefore = usdc.balanceOf(owner);
        vm.prank(owner);
        vault.collectFees();
        assertEq(usdc.balanceOf(owner), ownerBalBefore + fees);
        assertEq(vault.protocolFees(), 0);
    }

    function test_set_collateral_ratio() public {
        vm.prank(owner);
        vault.setCollateralRatio(15000); // 150%
        assertEq(vault.collateralRatio(), 15000);
    }

    function test_revert_set_collateral_ratio_below_100() public {
        vm.prank(owner);
        vm.expectRevert("ratio must be >= 100%");
        vault.setCollateralRatio(9999);
    }

    function test_deposit_collateral() public {
        uint256 amount = 50_000 * 10**6;
        uint256 vaultBalBefore = usdc.balanceOf(address(vault));

        // Fund owner with USDC
        usdc.mint(owner, amount);

        vm.prank(owner);
        usdc.approve(address(vault), amount);

        vm.prank(owner);
        vault.depositCollateral(amount);

        assertEq(usdc.balanceOf(address(vault)), vaultBalBefore + amount);
    }
}

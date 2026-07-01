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
        vm.warp(block.timestamp + 25 hours); // past MAX_STALENESS (24 hours)
        vm.expectRevert();
        oracle.getPrice();
    }

    function test_isFresh() public {
        assertTrue(oracle.isFresh());
        vm.warp(block.timestamp + 25 hours); // past MAX_STALENESS (24 hours)
        assertFalse(oracle.isFresh());
    }

    function test_revert_setPrice_zero() public {
        vm.prank(owner);
        vm.expectRevert(PriceOracle.ZeroPrice.selector);
        oracle.setPrice(0);
    }

    function test_revert_setPrice_deviation_too_large() public {
        // +25% jump vs prior price — beyond the 20% (2000 bps) default bound
        uint256 jumped = (INITIAL_PRICE * 125) / 100;
        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(PriceOracle.PriceDeviationTooLarge.selector, INITIAL_PRICE, jumped, 2000)
        );
        oracle.setPrice(jumped);
    }

    function test_revert_setPrice_deviation_too_large_downward() public {
        // -25% drop is equally out of bounds
        uint256 dropped = (INITIAL_PRICE * 75) / 100;
        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(PriceOracle.PriceDeviationTooLarge.selector, INITIAL_PRICE, dropped, 2000)
        );
        oracle.setPrice(dropped);
    }

    function test_setPrice_within_deviation_bound() public {
        // +19% stays inside the 20% default bound
        uint256 within = (INITIAL_PRICE * 119) / 100;
        vm.prank(owner);
        oracle.setPrice(within);
        assertEq(oracle.price(), within);
    }

    function test_forceSetPrice_bypasses_deviation_bound() public {
        // Owner escape hatch for a legitimately gapped market: +50% accepted
        uint256 gapped = (INITIAL_PRICE * 150) / 100;
        vm.prank(owner);
        oracle.forceSetPrice(gapped);
        assertEq(oracle.price(), gapped);
    }

    function test_revert_forceSetPrice_zero() public {
        vm.prank(owner);
        vm.expectRevert(PriceOracle.ZeroPrice.selector);
        oracle.forceSetPrice(0);
    }

    /// @dev audit 2026-06-14 B5: forceSetPrice is now bounded by FORCE_MAX_DEVIATION_BPS
    ///      (900% = 10×). A >10× single-call move reverts PriceDeviationTooLarge even
    ///      for the owner — a compromised key cannot zero-out or overflow the oracle.
    function test_revert_forceSetPrice_exceeds_hard_cap() public {
        // +11× is beyond the 900% (10×) hard cap
        uint256 elevenX = INITIAL_PRICE * 11;
        // Read the constant BEFORE the prank so vm.prank applies to the
        // forceSetPrice call itself, not the FORCE_MAX_DEVIATION_BPS() read
        // (a stray view call would consume the single-shot prank and the
        // forceSetPrice would then run as this test contract → OwnableUnauthorizedAccount).
        uint256 hardCap = oracle.FORCE_MAX_DEVIATION_BPS();
        vm.expectRevert(
            abi.encodeWithSelector(
                PriceOracle.PriceDeviationTooLarge.selector,
                INITIAL_PRICE,
                elevenX,
                hardCap
            )
        );
        vm.prank(owner);
        oracle.forceSetPrice(elevenX);
    }

    /// @dev Exactly 10× is within the 900% cap (diff == 9×old, 9*10000 <= 90000).
    function test_forceSetPrice_at_hard_cap_boundary() public {
        uint256 tenX = INITIAL_PRICE * 10;
        vm.prank(owner);
        oracle.forceSetPrice(tenX);
        assertEq(oracle.price(), tenX);
    }

    /// @dev forceSetPrice with no prior price (price == 0) skips the bound check
    ///      so the oracle can always be bootstrapped.
    function test_forceSetPrice_no_prior_price_skips_bound() public {
        PriceOracle freshOracle = new PriceOracle("FRESH", 0, owner);
        uint256 anyPrice = 1e30; // extreme — fine when oldPrice == 0
        vm.prank(owner);
        freshOracle.forceSetPrice(anyPrice);
        assertEq(freshOracle.price(), anyPrice);
    }

    function test_revert_forceSetPrice_non_owner() public {
        vm.prank(alice);
        vm.expectRevert();
        oracle.forceSetPrice(INITIAL_PRICE);
    }

    function test_setMaxDeviationBps() public {
        vm.prank(owner);
        oracle.setMaxDeviationBps(5000);
        assertEq(oracle.maxDeviationBps(), 5000);

        // A +40% move now passes under the widened 50% bound
        uint256 jumped = (INITIAL_PRICE * 140) / 100;
        vm.prank(owner);
        oracle.setPrice(jumped);
        assertEq(oracle.price(), jumped);
    }

    function test_revert_setMaxDeviationBps_invalid_bounds() public {
        vm.startPrank(owner);
        vm.expectRevert(PriceOracle.InvalidDeviationBound.selector);
        oracle.setMaxDeviationBps(0);
        vm.expectRevert(PriceOracle.InvalidDeviationBound.selector);
        oracle.setMaxDeviationBps(10_001);
        vm.stopPrank();
    }

    function test_revert_setMaxDeviationBps_non_owner() public {
        vm.prank(alice);
        vm.expectRevert();
        oracle.setMaxDeviationBps(5000);
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

    /// @dev audit 2026-06-14: integer division can make synthAmount round to 0
    ///      (dust deposit at a very high price). The user must not pay USDC +
    ///      mint fee and receive zero synth — mint now reverts ZeroAmount.
    ///      Reproduce with an extreme-price oracle so 1 USDC-unit yields 0 synth:
    ///      synthAmount = netUsdc*1e18*BPS / (assetPrice*collateralRatio) == 0.
    function test_revert_mint_dust_rounds_to_zero_synth() public {
        // assetPrice * collateralRatio must exceed netUsdc * 1e18 * 1e4 for a
        // 1-unit deposit. With collateralRatio 12000, price > ~8.3e17 suffices.
        uint256 hugePrice = 1e21; // 1e21 * 12000 = 1.2e25 > 1*1e22
        SyntheticToken dustSynth = new SyntheticToken("Dust", "DUST", owner);
        PriceOracle dustOracle = new PriceOracle("DUST", hugePrice, owner);
        SyntheticVault dustVault = new SyntheticVault(
            address(usdc),
            address(dustSynth),
            address(dustOracle),
            owner
        );
        vm.prank(owner);
        dustSynth.setVault(address(dustVault));

        vm.startPrank(alice);
        usdc.approve(address(dustVault), 1);
        vm.expectRevert(SyntheticVault.ZeroAmount.selector);
        dustVault.mint(1); // 1 unit (1e-6 USDC) → rounds to 0 synth → revert
        vm.stopPrank();
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

    // ─── Solvency / Pro-Rata Redemption Tests (issue #509, audit #14) ──

    /// @dev Total issued synth is redeemed across multiple holders after an adverse
    ///      (upward) price move that makes the vault under-collateralized. Pre-fix:
    ///      alice (first) extracted full current-price value and bob's burn reverted
    ///      with InsufficientCollateral. Post-fix: both succeed and, holding equal
    ///      synth, receive equal pro-rata payouts regardless of redemption order.
    function test_burn_total_supply_pro_rata_after_price_spike() public {
        uint256 usdcAmount = 10_000 * 10**6;

        // Alice and bob mint identical amounts at the initial price.
        vm.startPrank(alice);
        usdc.approve(address(vault), usdcAmount);
        uint256 aliceSynth = vault.mint(usdcAmount);
        vm.stopPrank();

        vm.startPrank(bob);
        usdc.approve(address(vault), usdcAmount);
        uint256 bobSynth = vault.mint(usdcAmount);
        vm.stopPrank();

        // Price spikes +50% — beyond the 20% mint-side buffer, so total liability
        // (totalSupply * price) now exceeds vault collateral.
        vm.prank(owner);
        oracle.forceSetPrice((INITIAL_PRICE * 150) / 100);

        uint256 collateralBefore = usdc.balanceOf(address(vault)) - vault.protocolFees();
        uint256 liability = (sTSLA.totalSupply() * oracle.getPrice()) / 1e18;
        assertGt(liability, collateralBefore, "scenario must be under-collateralized");

        // Alice redeems everything first...
        vm.prank(alice);
        uint256 alicePayout = vault.burn(aliceSynth);

        // ...and bob redeems the entire remaining supply. Pre-fix this reverted.
        vm.prank(bob);
        uint256 bobPayout = vault.burn(bobSynth);

        assertEq(sTSLA.totalSupply(), 0, "all issued synth redeemed");

        // Equal holders get equal value: redemption order must not redistribute.
        // (1 wei tolerance for integer-division dust; dust favors the vault.)
        assertApproxEqAbs(alicePayout, bobPayout, 1, "order-independent pro-rata payout");

        // Early redeemer capped at her pro-rata share of available collateral
        // (alice held half the supply -> at most half the collateral, gross of fee).
        assertLe(alicePayout, collateralBefore / 2, "no more than pro-rata share");

        // Vault stays solvent: balance still covers accrued protocol fees.
        assertGe(usdc.balanceOf(address(vault)), vault.protocolFees());
    }

    /// @dev Unequal holders, full-supply redemption under stress: per-synth payout
    ///      rate is the same for both, and total payouts never exceed collateral.
    function test_burn_pro_rata_unequal_holders() public {
        vm.startPrank(alice);
        usdc.approve(address(vault), 30_000 * 10**6);
        uint256 aliceSynth = vault.mint(30_000 * 10**6); // 3x bob's position
        vm.stopPrank();

        vm.startPrank(bob);
        usdc.approve(address(vault), 10_000 * 10**6);
        uint256 bobSynth = vault.mint(10_000 * 10**6);
        vm.stopPrank();

        vm.prank(owner);
        oracle.forceSetPrice(INITIAL_PRICE * 2); // +100%

        uint256 collateralBefore = usdc.balanceOf(address(vault)) - vault.protocolFees();

        // Bob (small holder) redeems first this time.
        vm.prank(bob);
        uint256 bobPayout = vault.burn(bobSynth);

        vm.prank(alice);
        uint256 alicePayout = vault.burn(aliceSynth);

        assertEq(sTSLA.totalSupply(), 0);

        // Same per-synth rate (scaled to 1e18 synth units; tolerate division dust).
        uint256 bobRate = (bobPayout * 1e18) / bobSynth;
        uint256 aliceRate = (alicePayout * 1e18) / aliceSynth;
        assertApproxEqRel(bobRate, aliceRate, 1e12, "equal per-synth payout rate"); // 0.0001%

        // Vault never pays out more than it had.
        assertLe(alicePayout + bobPayout, collateralBefore, "payouts bounded by collateral");
        assertGe(usdc.balanceOf(address(vault)), vault.protocolFees());
    }

    /// @dev When the vault is healthy (collateral >= liability), the pro-rata cap is
    ///      inactive and burn pays full current-price value minus fee, as before.
    function test_burn_no_haircut_when_healthy() public {
        uint256 usdcAmount = 10_000 * 10**6;
        vm.startPrank(alice);
        usdc.approve(address(vault), usdcAmount);
        uint256 synthOut = vault.mint(usdcAmount);

        // Price unchanged: 120% mint collateral comfortably covers 100% redemption.
        uint256 usdcValue = (synthOut * oracle.getPrice()) / 1e18;
        uint256 expected = usdcValue - (usdcValue * 50) / 10000; // minus 0.5% burn fee

        uint256 payout = vault.burn(synthOut);
        vm.stopPrank();

        assertEq(payout, expected, "full price value when solvent");
    }

    /// @dev Under stress the pro-rata cap binds, so burn() emits RedemptionHaircut with
    ///      the redeemer indexed and grossValue > cappedValue (the haircut magnitude).
    function test_burn_emits_RedemptionHaircut_under_stress() public {
        uint256 usdcAmount = 10_000 * 10**6;
        vm.startPrank(alice);
        usdc.approve(address(vault), usdcAmount);
        uint256 synthOut = vault.mint(usdcAmount);
        vm.stopPrank();

        vm.prank(owner);
        oracle.forceSetPrice((INITIAL_PRICE * 150) / 100); // +50% -> C < L

        vm.recordLogs();
        vm.prank(alice);
        vault.burn(synthOut);

        Vm.Log[] memory logs = vm.getRecordedLogs();
        bytes32 sig = keccak256("RedemptionHaircut(address,uint256,uint256)");
        bool found;
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].topics[0] == sig) {
                found = true;
                assertEq(address(uint160(uint256(logs[i].topics[1]))), alice, "redeemer indexed");
                (uint256 gross, uint256 capped) = abi.decode(logs[i].data, (uint256, uint256));
                assertGt(gross, capped, "haircut reduces the payout");
            }
        }
        assertTrue(found, "RedemptionHaircut emitted when the cap binds");
    }

    /// @dev When healthy (cap inactive) burn() must NOT emit RedemptionHaircut.
    function test_burn_no_RedemptionHaircut_when_healthy() public {
        uint256 usdcAmount = 10_000 * 10**6;
        vm.startPrank(alice);
        usdc.approve(address(vault), usdcAmount);
        uint256 synthOut = vault.mint(usdcAmount);

        vm.recordLogs();
        vault.burn(synthOut);
        vm.stopPrank();

        Vm.Log[] memory logs = vm.getRecordedLogs();
        bytes32 sig = keccak256("RedemptionHaircut(address,uint256,uint256)");
        for (uint256 i; i < logs.length; i++) {
            assertTrue(logs[i].topics[0] != sig, "no haircut event when solvent");
        }
    }

    /// @dev previewBurn must mirror burn exactly while the haircut is active.
    function test_preview_burn_matches_under_stress() public {
        uint256 usdcAmount = 10_000 * 10**6;
        vm.startPrank(alice);
        usdc.approve(address(vault), usdcAmount);
        uint256 synthOut = vault.mint(usdcAmount);
        vm.stopPrank();

        vm.startPrank(bob);
        usdc.approve(address(vault), usdcAmount);
        vault.mint(usdcAmount);
        vm.stopPrank();

        vm.prank(owner);
        oracle.forceSetPrice((INITIAL_PRICE * 150) / 100);

        uint256 preview = vault.previewBurn(synthOut);
        vm.prank(alice);
        uint256 actual = vault.burn(synthOut);

        assertEq(preview, actual, "preview == actual under haircut");
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

    /// @dev audit 2026-06-14 B7: previewMint now mirrors mint()'s zero-input guard.
    function test_revert_preview_mint_zero() public {
        vm.expectRevert(SyntheticVault.ZeroAmount.selector);
        vault.previewMint(0);
    }

    /// @dev audit 2026-06-14 B7: previewMint now mirrors mint()'s dust-rounds-to-zero guard.
    function test_revert_preview_mint_dust_rounds_to_zero() public {
        uint256 hugePrice = 1e21;
        SyntheticToken dustSynth = new SyntheticToken("Dust", "DUST", owner);
        PriceOracle dustOracle = new PriceOracle("DUST", hugePrice, owner);
        SyntheticVault dustVault = new SyntheticVault(
            address(usdc),
            address(dustSynth),
            address(dustOracle),
            owner
        );
        vm.prank(owner);
        dustSynth.setVault(address(dustVault));

        vm.expectRevert(SyntheticVault.ZeroAmount.selector);
        dustVault.previewMint(1); // 1 unit at extreme price rounds to 0 synth
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

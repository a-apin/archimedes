// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/Vault.sol";
import "../src/VaultFactory.sol";
import "../src/AMMRouter.sol";
import "../src/AMMPool.sol";
import "../src/AssetRegistry.sol";
import "../src/ReasoningTraceRegistry.sol";

/// @dev Mock ERC-20 USDC for testing
contract MockUSDC is ERC20 {
    constructor() ERC20("Mock USDC", "USDC") {
        _mint(msg.sender, 10_000_000 * 10**6);
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }
}

contract MockToken is ERC20 {
    constructor(string memory name, string memory symbol) ERC20(name, symbol) {
        _mint(msg.sender, 1_000_000 * 1e18);
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

/// @dev A non-18-decimal token (8 decimals) for the #942 NAV-pricing tests.
contract MockToken8 is ERC20 {
    constructor(string memory name, string memory symbol) ERC20(name, symbol) {
        _mint(msg.sender, 1_000_000 * 10**8);
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function decimals() public pure override returns (uint8) {
        return 8;
    }
}

contract VaultTest is Test {
    MockUSDC public usdc;
    AMMRouter public router;
    VaultFactory public factory;
    Vault public vault;
    ReasoningTraceRegistry public traceRegistry;

    MockToken public sTSLA;
    PriceOracle public tslaOracle;

    /// @dev sTSLA oracle price: 2 USDC per token (6-decimal USDC units per 1e18 synth)
    uint256 public constant TSLA_PRICE = 2 * 10**6;

    address public owner = address(0x1);
    address public alice = address(0x2);
    address public bob = address(0x3);
    address public agent = address(0x4);
    address public platformRecipient = address(0x5);

    function setUp() public {
        usdc = new MockUSDC();

        vm.prank(owner);
        router = new AMMRouter(owner);

        vm.prank(owner);
        traceRegistry = new ReasoningTraceRegistry(owner);

        vm.prank(owner);
        factory = new VaultFactory(
            agent,
            address(router),
            address(usdc),
            address(traceRegistry),
            platformRecipient,
            owner
        );

        // Fund test users
        usdc.mint(alice, 1_000_000 * 10**6);
        usdc.mint(bob, 1_000_000 * 10**6);

        // Deploy sTSLA token for rebalance testing
        sTSLA = new MockToken("Synthetic TSLA", "sTSLA");

        // Create AMM pool for USDC/sTSLA
        router.createPool(address(usdc), address(sTSLA));

        // Create a vault as agent (Tier 1)
        vm.prank(agent);
        address vaultAddr = factory.createVault(
            "Momentum Alpha",
            "vMOM",
            150,   // 1.5% management fee
            2000,  // 20% performance fee
            true   // agent assisted
        );
        vault = Vault(payable(vaultAddr));

        // Seed AMM pool with liquidity at the oracle price (2 USDC per sTSLA).
        // Deep pool so a 10k USDC rebalance has price impact well below the
        // vault's default 1% slippage tolerance (impact ~0.1% + 0.3% AMM fee).
        uint256 poolUsdc = 10_000_000 * 10**6;
        uint256 poolTsla = 5_000_000 * 1e18;

        usdc.mint(address(this), poolUsdc);
        sTSLA.mint(address(this), poolTsla);

        usdc.approve(address(router), poolUsdc);
        sTSLA.approve(address(router), poolTsla);
        router.addLiquidity(address(usdc), address(sTSLA), poolUsdc, poolTsla, 0);

        // Deploy oracle for sTSLA and register it on the vault — swaps now
        // require an oracle-derived minAmountOut (issue #506).
        tslaOracle = new PriceOracle("sTSLA", TSLA_PRICE, owner);
        address[] memory oracleTokens = new address[](1);
        oracleTokens[0] = address(sTSLA);
        address[] memory oracles = new address[](1);
        oracles[0] = address(tslaOracle);
        vm.prank(agent);
        vault.setTokenOracles(oracleTokens, oracles);
    }

    // ─── Helpers ─────────────────────────────────────────────────────

    /// @dev Rebalance the vault: buy `amount` USDC worth of sTSLA.
    function _rebalanceBuyTsla(uint256 amount) internal {
        address[] memory tokensIn = new address[](1);
        tokensIn[0] = address(sTSLA);
        uint256[] memory amountsIn = new uint256[](1);
        amountsIn[0] = amount;
        address[] memory tokensOut = new address[](0);
        uint256[] memory amountsOut = new uint256[](0);

        _commitFor(tokensIn, amountsIn, tokensOut, amountsOut);
        vm.prank(agent);
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);
    }

    /// @dev Rebalance the vault: sell `amount` sTSLA back to USDC.
    function _rebalanceSellTsla(uint256 amount) internal {
        address[] memory tokensIn = new address[](0);
        uint256[] memory amountsIn = new uint256[](0);
        address[] memory tokensOut = new address[](1);
        tokensOut[0] = address(sTSLA);
        uint256[] memory amountsOut = new uint256[](1);
        amountsOut[0] = amount;

        _commitFor(tokensIn, amountsIn, tokensOut, amountsOut);
        vm.prank(agent);
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);
    }

    /// @dev #589: commit a matching reasoning trace one block before a rebalance, so the
    ///      vault's commit-before-trade enforcement is satisfied. tradeId MUST equal what
    ///      Vault.rebalance() recomputes from the same swap arrays.
    function _commitFor(
        address[] memory tokensIn,
        uint256[] memory amountsIn,
        address[] memory tokensOut,
        uint256[] memory amountsOut
    ) internal {
        bytes32 tradeId = keccak256(abi.encode(tokensIn, amountsIn, tokensOut, amountsOut));
        vm.prank(agent);
        traceRegistry.commit(address(vault), keccak256("trace"), uint64(block.timestamp + 1), tradeId, "");
        vm.roll(block.number + 1); // commit must strictly precede the trade block
    }

    /// @dev Deposit USDC into the vault as alice.
    function _depositAsAlice(uint256 amount) internal {
        vm.startPrank(alice);
        usdc.approve(address(vault), amount);
        vault.deposit(amount, alice);
        vm.stopPrank();
    }

    // ─── Deposit Tests ───────────────────────────────────────────────

    function test_deposit_first() public {
        uint256 amount = 10_000 * 10**6;

        vm.startPrank(alice);
        usdc.approve(address(vault), amount);
        uint256 shares = vault.deposit(amount, alice);
        vm.stopPrank();

        // First deposit: 1:1 rate minus MIN_LIQUIDITY dead shares (inflation guard, #507)
        assertEq(shares, amount - vault.MIN_LIQUIDITY());
        assertEq(vault.balanceOf(alice), shares);
        assertEq(vault.balanceOf(vault.DEAD_SHARES_SINK()), vault.MIN_LIQUIDITY());
        assertEq(vault.totalSupply(), amount); // 1:1 share:asset scale preserved
        assertEq(usdc.balanceOf(address(vault)), amount);
    }

    function test_deposit_second_user() public {
        uint256 amount = 10_000 * 10**6;

        // Alice deposits first
        vm.startPrank(alice);
        usdc.approve(address(vault), amount);
        vault.deposit(amount, alice);
        vm.stopPrank();

        // Bob deposits same amount
        vm.startPrank(bob);
        usdc.approve(address(vault), amount);
        uint256 bobShares = vault.deposit(amount, bob);
        vm.stopPrank();

        // Same deposit amount should get same shares (no gains yet)
        assertEq(bobShares, amount);
        assertEq(vault.balanceOf(bob), bobShares);
    }

    function test_revert_deposit_zero() public {
        vm.prank(alice);
        vm.expectRevert(Vault.ZeroAmount.selector);
        vault.deposit(0, alice);
    }

    /// @dev #932: the first-depositor inflation floor must be large enough that a
    ///      minimal seed + a large NAV donation can't round a later small deposit
    ///      down to zero shares (a griefable ZeroShares revert). The attacker seeds
    ///      with the smallest valid first deposit (MIN_LIQUIDITY + 1) and donates
    ///      10k USDC to inflate NAV-per-share; a victim's 1 USDC deposit must still
    ///      mint > 0 shares. Because the seed scales with MIN_LIQUIDITY, this reverts
    ///      at the old 1e3 floor (seed 1001 → victim rounds to 0) and passes at 1e6.
    function test_min_liquidity_blocks_first_depositor_inflation_grief() public {
        assertEq(vault.MIN_LIQUIDITY(), 1e6);

        uint256 seed = vault.MIN_LIQUIDITY() + 1;
        vm.startPrank(alice);
        usdc.approve(address(vault), seed);
        vault.deposit(seed, alice);
        vm.stopPrank();

        // Attacker donates USDC straight to the vault to inflate NAV-per-share.
        usdc.mint(address(vault), 10_000 * 10**6);

        // Victim's 1 USDC deposit must still mint a positive share amount.
        vm.startPrank(bob);
        usdc.approve(address(vault), 1 * 10**6);
        uint256 bobShares = vault.deposit(1 * 10**6, bob);
        vm.stopPrank();

        assertGt(bobShares, 0);
    }

    // ─── Withdraw Tests ──────────────────────────────────────────────

    function test_withdraw() public {
        uint256 amount = 10_000 * 10**6;

        vm.startPrank(alice);
        usdc.approve(address(vault), amount);
        vault.deposit(amount, alice);

        // First depositor's max withdraw is amount - MIN_LIQUIDITY: the dead shares
        // (inflation guard, #507) keep their pro-rata sliver of assets locked.
        uint256 withdrawable = amount - vault.MIN_LIQUIDITY();
        uint256 aliceUsdcBefore = usdc.balanceOf(alice);
        vault.withdraw(withdrawable, alice, alice);
        vm.stopPrank();

        assertEq(usdc.balanceOf(alice) - aliceUsdcBefore, withdrawable);
        assertEq(vault.balanceOf(alice), 0);
    }

    function test_redeem() public {
        uint256 amount = 10_000 * 10**6;

        vm.startPrank(alice);
        usdc.approve(address(vault), amount);
        uint256 shares = vault.deposit(amount, alice);

        uint256 aliceUsdcBefore = usdc.balanceOf(alice);
        vault.redeem(shares, alice, alice);
        vm.stopPrank();

        // shares = amount - MIN_LIQUIDITY (dead shares, #507); redeeming them all
        // returns the matching pro-rata assets at the unchanged 1:1 rate.
        assertEq(usdc.balanceOf(alice) - aliceUsdcBefore, amount - vault.MIN_LIQUIDITY());
        assertEq(vault.balanceOf(alice), 0);
    }

    function test_revert_withdraw_zero() public {
        vm.prank(alice);
        vm.expectRevert(Vault.ZeroAmount.selector);
        vault.withdraw(0, alice, alice);
    }

    // ─── Allowance Enforcement (audit 2026-06-13 CRITICAL: share theft) ──

    /// @dev An attacker must NOT be able to burn a victim's shares by passing the
    ///      victim as `owner_` and itself as `receiver`. Without the allowance check
    ///      this drains the victim's deposit; with it, the call reverts.
    function test_revert_redeem_steal_without_allowance() public {
        uint256 amount = 10_000 * 10**6;

        vm.startPrank(alice);
        usdc.approve(address(vault), amount);
        uint256 shares = vault.deposit(amount, alice);
        vm.stopPrank();

        // Bob (attacker) tries to redeem Alice's shares into his own wallet.
        vm.prank(bob);
        vm.expectRevert(); // ERC20InsufficientAllowance — Bob has no allowance from Alice
        vault.redeem(shares, bob, alice);

        // Alice's shares and the vault's USDC are untouched.
        assertEq(vault.balanceOf(alice), shares);
        assertEq(usdc.balanceOf(address(vault)), amount);
    }

    function test_revert_withdraw_steal_without_allowance() public {
        uint256 amount = 10_000 * 10**6;

        vm.startPrank(alice);
        usdc.approve(address(vault), amount);
        vault.deposit(amount, alice);
        vm.stopPrank();

        uint256 withdrawable = amount - vault.MIN_LIQUIDITY();
        vm.prank(bob);
        vm.expectRevert(); // no allowance from Alice
        vault.withdraw(withdrawable, bob, alice);

        assertEq(usdc.balanceOf(address(vault)), amount);
    }

    /// @dev With an explicit share allowance, a delegate CAN redeem on the owner's
    ///      behalf, and the allowance is decremented (canonical ERC-4626 path).
    function test_redeem_with_allowance_succeeds() public {
        uint256 amount = 10_000 * 10**6;

        vm.startPrank(alice);
        usdc.approve(address(vault), amount);
        uint256 shares = vault.deposit(amount, alice);
        vault.approve(bob, shares); // Alice grants Bob a share allowance
        vm.stopPrank();

        uint256 aliceUsdcBefore = usdc.balanceOf(alice);
        vm.prank(bob);
        vault.redeem(shares, alice, alice); // receiver = owner (Alice)

        assertEq(usdc.balanceOf(alice) - aliceUsdcBefore, amount - vault.MIN_LIQUIDITY());
        assertEq(vault.balanceOf(alice), 0);
        assertEq(vault.allowance(alice, bob), 0); // allowance consumed
    }

    // ─── Preview Functions ───────────────────────────────────────────

    function test_previewDeposit() public {
        uint256 amount = 10_000 * 10**6;
        uint256 preview = vault.previewDeposit(amount);

        vm.startPrank(alice);
        usdc.approve(address(vault), amount);
        uint256 actual = vault.deposit(amount, alice);
        vm.stopPrank();

        assertEq(preview, actual);
    }

    function test_previewRedeem() public {
        uint256 amount = 10_000 * 10**6;

        vm.startPrank(alice);
        usdc.approve(address(vault), amount);
        uint256 shares = vault.deposit(amount, alice);

        uint256 preview = vault.previewRedeem(shares);
        uint256 actual = vault.redeem(shares, alice, alice);
        vm.stopPrank();

        assertEq(preview, actual);
    }

    // ─── Inflation-Attack Tests (audit 2026-06-10 #4 / issue #507) ───

    /// @dev Classic first-depositor inflation attack: deposit 1 wei, donate USDC
    ///      directly to the vault to inflate NAV, and let the victim's deposit
    ///      round to zero shares. With MIN_LIQUIDITY dead shares the 1-wei seed
    ///      deposit is impossible and the donation is absorbed by the dead shares,
    ///      so the victim keeps ~full value and the attacker takes a massive loss.
    function test_inflation_attack_fails() public {
        uint256 minLiq = vault.MIN_LIQUIDITY();

        // Step 1: the canonical 1-wei first deposit now reverts —
        // previewDeposit(1) == 0 because MIN_LIQUIDITY is subtracted.
        vm.startPrank(bob); // bob = attacker
        usdc.approve(address(vault), type(uint256).max);
        vm.expectRevert(Vault.ZeroShares.selector);
        vault.deposit(1, bob);

        // Step 2: attacker falls back to the smallest viable first deposit
        // (MIN_LIQUIDITY + 1 wei → exactly 1 share; dead sink holds MIN_LIQUIDITY).
        uint256 attackerCost = minLiq + 1;
        uint256 attackerShares = vault.deposit(attackerCost, bob);
        assertEq(attackerShares, 1);

        // Step 3: attacker donates USDC directly to the vault to inflate NAV
        uint256 donation = 10_000 * 10**6;
        usdc.transfer(address(vault), donation);
        attackerCost += donation;
        vm.stopPrank();

        // Step 4: victim deposits
        uint256 victimDeposit = 10_000 * 10**6;
        vm.startPrank(alice); // alice = victim
        usdc.approve(address(vault), victimDeposit);
        uint256 victimShares = vault.deposit(victimDeposit, alice);
        vm.stopPrank();

        // Victim's shares MUST NOT round to zero
        assertGt(victimShares, 0);

        // Victim's redeemable value ≈ deposit (within 0.1% rounding tolerance)
        uint256 victimValue = vault.previewRedeem(victimShares);
        assertApproxEqRel(victimValue, victimDeposit, 0.001e18);

        // Attack is strictly unprofitable: the dead shares absorb
        // MIN_LIQUIDITY/(MIN_LIQUIDITY+1) of the donation, so the attacker
        // recovers only a dust fraction of what they spent.
        uint256 attackerValue = vault.previewRedeem(attackerShares);
        assertLt(attackerValue, attackerCost / 100); // lost > 99% of outlay
    }

    /// @dev Donation alone (no zero-share rounding) must not let any depositor
    ///      capture more than their pro-rata claim from a later depositor.
    function test_donation_does_not_dilute_later_depositor() public {
        uint256 amount = 10_000 * 10**6;

        // Honest first depositor
        vm.startPrank(alice);
        usdc.approve(address(vault), amount);
        vault.deposit(amount, alice);
        vm.stopPrank();

        // Third party donates to the vault
        usdc.mint(address(this), amount);
        usdc.transfer(address(vault), amount);

        // Bob deposits after the donation: his shares price in the doubled NAV,
        // and his redeemable value still ≈ his deposit.
        vm.startPrank(bob);
        usdc.approve(address(vault), amount);
        uint256 bobShares = vault.deposit(amount, bob);
        vm.stopPrank();

        assertGt(bobShares, 0);
        assertApproxEqRel(vault.previewRedeem(bobShares), amount, 0.001e18);
    }

    // ─── Rebalance Tests ─────────────────────────────────────────────

    function test_rebalance_buy_synth() public {
        uint256 depositAmount = 50_000 * 10**6;

        // Deposit USDC
        vm.prank(alice);
        usdc.approve(address(vault), depositAmount);
        vm.prank(alice);
        vault.deposit(depositAmount, alice);

        // Agent rebalances: buy sTSLA with 10,000 USDC
        uint256 swapAmount = 10_000 * 10**6;
        address[] memory tokensIn = new address[](1);
        tokensIn[0] = address(sTSLA);
        uint256[] memory amountsIn = new uint256[](1);
        amountsIn[0] = swapAmount;
        address[] memory tokensOut = new address[](0);
        uint256[] memory amountsOut = new uint256[](0);

        _commitFor(tokensIn, amountsIn, tokensOut, amountsOut);
        vm.prank(agent);
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);

        // Vault should now hold sTSLA
        assertGt(sTSLA.balanceOf(address(vault)), 0);
        // Vault USDC should be reduced
        assertLt(usdc.balanceOf(address(vault)), depositAmount);
    }

    function test_revert_rebalance_unauthorized() public {
        uint256 depositAmount = 50_000 * 10**6;

        vm.prank(alice);
        usdc.approve(address(vault), depositAmount);
        vm.prank(alice);
        vault.deposit(depositAmount, alice);

        address[] memory tokensIn = new address[](0);
        uint256[] memory amountsIn = new uint256[](0);
        address[] memory tokensOut = new address[](0);
        uint256[] memory amountsOut = new uint256[](0);

        // Bob can't rebalance — he's not the creator or agent
        vm.prank(bob);
        vm.expectRevert(Vault.Unauthorized.selector);
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);
    }

    function test_rebalance_creator_can_also_rebalance() public {
        uint256 depositAmount = 50_000 * 10**6;

        vm.prank(alice);
        usdc.approve(address(vault), depositAmount);
        vm.prank(alice);
        vault.deposit(depositAmount, alice);

        address[] memory tokensIn = new address[](0);
        uint256[] memory amountsIn = new uint256[](0);
        address[] memory tokensOut = new address[](0);
        uint256[] memory amountsOut = new uint256[](0);

        // Creator (agent in this case) can rebalance
        _commitFor(tokensIn, amountsIn, tokensOut, amountsOut);
        vm.prank(agent);
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);
        // No tokens swapped, but no revert — success
    }

    // ─── Swap Slippage Protection (issue #506) ───────────────────────

    function test_maxSlippageBps_default() public view {
        assertEq(vault.maxSlippageBps(), 100);
        assertEq(vault.MAX_SLIPPAGE_CAP_BPS(), 500);
    }

    function test_setMaxSlippageBps() public {
        // Vault owner is the creator (agent in setUp)
        vm.prank(agent);
        vault.setMaxSlippageBps(50);
        assertEq(vault.maxSlippageBps(), 50);

        // Boundary: exactly the cap is allowed
        vm.prank(agent);
        vault.setMaxSlippageBps(500);
        assertEq(vault.maxSlippageBps(), 500);
    }

    function test_revert_setMaxSlippageBps_above_cap() public {
        vm.prank(agent);
        vm.expectRevert(Vault.SlippageBpsTooHigh.selector);
        vault.setMaxSlippageBps(501);
    }

    // ─── setTokenOracles is owner-only (audit 2026-06-14) ────────────
    /// @dev The agent must not be able to redefine the oracles that feed the
    ///      rebalance slippage floor (_oracleMinOut); otherwise a compromised
    ///      agent could point a token at a self-serving oracle and route swaps
    ///      that leak vault value. This test builds a vault whose agent is
    ///      DISTINCT from the creator/owner so it actually distinguishes
    ///      onlyOwner from the old onlyManager (under which the agent could set
    ///      oracles). In the shared setUp, agent == creator == owner.
    function test_revert_setTokenOracles_agent_cannot_set() public {
        address creator = address(0xC0FFEE);
        address distinctAgent = address(0xA9E27);

        vm.prank(creator);
        address vaultAddr = factory.createVault("T", "T", 0, 0, true);
        Vault v = Vault(payable(vaultAddr));

        vm.prank(creator); // creator == Ownable owner
        v.setAgent(distinctAgent);

        address[] memory tokens = new address[](1);
        tokens[0] = address(sTSLA);
        address[] memory oracles = new address[](1);
        oracles[0] = address(tslaOracle);

        // Under the old onlyManager this SUCCEEDED (agent is a manager); under
        // onlyOwner it must revert.
        vm.prank(distinctAgent);
        vm.expectRevert();
        v.setTokenOracles(tokens, oracles);

        // The creator/owner can still set oracles.
        vm.prank(creator);
        v.setTokenOracles(tokens, oracles);
    }

    // ─── Registry-allowlisted oracle wiring (issue #620) ─────────────
    //
    // The agent (onlyManager, not onlyOwner) must be able to wire oracles for a
    // new synth, but ONLY from an owner-curated allowlist — so it can price new
    // assets autonomously yet can never point a token at an arbitrary attacker
    // oracle. These tests build a vault whose agent is DISTINCT from the
    // creator/owner so onlyManager and onlyOwner are actually distinguishable
    // (in the shared setUp, agent == creator == owner).

    address internal constant REG_CREATOR = address(0xC0FFEE);
    address internal constant REG_AGENT = address(0xA9E27);

    /// @dev Deploy a vault with a distinct creator/owner and agent, returning it.
    function _distinctAgentVault() internal returns (Vault v) {
        vm.prank(REG_CREATOR);
        address vaultAddr = factory.createVault("Reg", "REG", 0, 0, true);
        v = Vault(payable(vaultAddr));
        vm.prank(REG_CREATOR); // creator == Ownable owner
        v.setAgent(REG_AGENT);
    }

    function _singleton(address a) internal pure returns (address[] memory arr) {
        arr = new address[](1);
        arr[0] = a;
    }

    /// @dev Owner registers an oracle in AssetRegistry and points the vault at
    ///      the registry; the AGENT (onlyManager) wires it via the allowlist and
    ///      the vault's tokenOracle now equals the registered oracle.
    function test_setTokenOraclesFromRegistry_agent_wires_allowlisted() public {
        Vault v = _distinctAgentVault();

        AssetRegistry registry = new AssetRegistry(REG_CREATOR);
        vm.prank(REG_CREATOR);
        registry.setRegisteredOracle(address(sTSLA), address(tslaOracle));

        vm.prank(REG_CREATOR);
        v.setAssetRegistry(address(registry));

        // The agent — which is NOT the owner — wires the allowlisted oracle.
        vm.prank(REG_AGENT);
        v.setTokenOraclesFromRegistry(_singleton(address(sTSLA)));

        assertEq(v.tokenOracle(address(sTSLA)), address(tslaOracle));
    }

    /// @dev A token with no entry in the allowlist reverts OracleNotRegistered —
    ///      the agent cannot wire an oracle the owner never approved.
    function test_revert_setTokenOraclesFromRegistry_unregistered_token() public {
        Vault v = _distinctAgentVault();

        AssetRegistry registry = new AssetRegistry(REG_CREATOR);
        vm.prank(REG_CREATOR);
        v.setAssetRegistry(address(registry));

        vm.prank(REG_AGENT);
        vm.expectRevert(abi.encodeWithSelector(Vault.OracleNotRegistered.selector, address(sTSLA)));
        v.setTokenOraclesFromRegistry(_singleton(address(sTSLA)));
    }

    /// @dev With no registry set, the allowlist path reverts AssetRegistryNotSet.
    function test_revert_setTokenOraclesFromRegistry_registry_unset() public {
        Vault v = _distinctAgentVault();

        vm.prank(REG_AGENT);
        vm.expectRevert(Vault.AssetRegistryNotSet.selector);
        v.setTokenOraclesFromRegistry(_singleton(address(sTSLA)));
    }

    /// @dev A caller that is neither manager nor owner cannot use the allowlist
    ///      path — reverts Unauthorized (onlyManager).
    function test_revert_setTokenOraclesFromRegistry_non_manager() public {
        Vault v = _distinctAgentVault();

        AssetRegistry registry = new AssetRegistry(REG_CREATOR);
        vm.prank(REG_CREATOR);
        registry.setRegisteredOracle(address(sTSLA), address(tslaOracle));
        vm.prank(REG_CREATOR);
        v.setAssetRegistry(address(registry));

        // bob is neither the creator (owner) nor the agent.
        vm.prank(bob);
        vm.expectRevert(Vault.Unauthorized.selector);
        v.setTokenOraclesFromRegistry(_singleton(address(sTSLA)));
    }

    /// @dev setAssetRegistry is owner-only: a non-owner call reverts Ownable.
    function test_revert_setAssetRegistry_non_owner() public {
        Vault v = _distinctAgentVault();

        AssetRegistry registry = new AssetRegistry(REG_CREATOR);
        vm.prank(REG_AGENT); // agent is not the owner
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, REG_AGENT));
        v.setAssetRegistry(address(registry));
    }

    /// @dev #609 property preserved: the agent calling the ARBITRARY-override
    ///      setTokenOracles still reverts (onlyOwner). Combined with the
    ///      allowlist tests above, the agent's ONLY oracle path is the
    ///      owner-curated registry — it can never inject an unregistered oracle.
    function test_revert_setTokenOracles_agent_still_owner_only() public {
        Vault v = _distinctAgentVault();

        address[] memory tokens = _singleton(address(sTSLA));
        address[] memory oracles = _singleton(address(tslaOracle));

        vm.prank(REG_AGENT);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, REG_AGENT));
        v.setTokenOracles(tokens, oracles);
    }

    function test_revert_setMaxSlippageBps_unauthorized() public {
        vm.prank(bob);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, bob));
        vault.setMaxSlippageBps(50);
    }

    function test_setAgent_emits_event() public {
        // `agent` state starts unset (address(0)); creator (agent addr) is the owner.
        vm.expectEmit(true, true, false, false, address(vault));
        emit Vault.AgentSet(address(0), bob);
        vm.prank(agent);
        vault.setAgent(bob);
        assertEq(vault.agent(), bob);
    }

    function test_setPlatformFeeRecipient_emits_event() public {
        vm.expectEmit(true, true, false, false, address(vault));
        emit Vault.PlatformFeeRecipientSet(platformRecipient, bob);
        vm.prank(agent);
        vault.setPlatformFeeRecipient(bob);
        assertEq(vault.platformFeeRecipient(), bob);
    }

    /// @dev Decimal scaling, buy direction: USDC(6) in -> synth(18) out.
    ///      10,000 USDC at 2 USDC/sTSLA → fair output 5,000e18; the floor at
    ///      1% tolerance is 4,950e18. The aligned pool fills at ~4,983e18
    ///      (0.3% fee + ~0.1% impact), which must clear the floor.
    function test_rebalance_buy_min_out_decimal_scaling() public {
        _depositAsAlice(50_000 * 10**6);
        _rebalanceBuyTsla(10_000 * 10**6);

        uint256 received = sTSLA.balanceOf(address(vault));
        uint256 fairOut = (10_000 * 10**6 * 1e18) / TSLA_PRICE; // 5_000e18
        assertGe(received, (fairOut * 9900) / 10000); // >= oracle floor
        assertLe(received, fairOut); // can't beat fair price in an aligned pool
    }

    /// @dev Decimal scaling, sell direction: synth(18) in -> USDC(6) out.
    ///      1,000 sTSLA at 2 USDC/sTSLA → fair output 2,000e6; floor 1,980e6.
    function test_rebalance_sell_min_out_decimal_scaling() public {
        _depositAsAlice(50_000 * 10**6);
        _rebalanceBuyTsla(10_000 * 10**6);

        uint256 usdcBefore = usdc.balanceOf(address(vault));
        _rebalanceSellTsla(1_000 * 1e18);
        uint256 received = usdc.balanceOf(address(vault)) - usdcBefore;

        uint256 fairOut = (1_000 * 1e18 * TSLA_PRICE) / 1e18; // 2_000e6
        assertGe(received, (fairOut * 9900) / 10000); // >= oracle floor
        assertLe(received, fairOut);
    }

    /// @dev With tolerance 0 the floor equals the oracle-fair output; the
    ///      30 bps AMM fee alone must trip it — proves maxSlippageBps is
    ///      actually enforced in the min-out computation.
    function test_maxSlippageBps_enforced_zero_tolerance() public {
        _depositAsAlice(50_000 * 10**6);

        vm.prank(agent);
        vault.setMaxSlippageBps(0);

        address[] memory tokensIn = new address[](1);
        tokensIn[0] = address(sTSLA);
        uint256[] memory amountsIn = new uint256[](1);
        amountsIn[0] = 10_000 * 10**6;
        address[] memory tokensOut = new address[](0);
        uint256[] memory amountsOut = new uint256[](0);

        _commitFor(tokensIn, amountsIn, tokensOut, amountsOut);
        vm.prank(agent);
        vm.expectRevert(AMMRouter.SlippageExceeded.selector);
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);
    }

    /// @dev Attack scenario (audit finding #3): attacker skews the pool ahead
    ///      of a rebalance BUY (front-run buys sTSLA, raising its pool price).
    ///      The vault must revert instead of buying at the manipulated price.
    function test_revert_rebalance_buy_manipulated_pool() public {
        _depositAsAlice(50_000 * 10**6);

        // Attacker front-runs: dumps 5M USDC into the pool, ~halving the
        // sTSLA reserve and pushing the pool price far above the oracle.
        usdc.mint(bob, 5_000_000 * 10**6);
        vm.startPrank(bob);
        usdc.approve(address(router), 5_000_000 * 10**6);
        router.swap(address(usdc), address(sTSLA), 5_000_000 * 10**6, 0);
        vm.stopPrank();

        address[] memory tokensIn = new address[](1);
        tokensIn[0] = address(sTSLA);
        uint256[] memory amountsIn = new uint256[](1);
        amountsIn[0] = 10_000 * 10**6;
        address[] memory tokensOut = new address[](0);
        uint256[] memory amountsOut = new uint256[](0);

        _commitFor(tokensIn, amountsIn, tokensOut, amountsOut);
        vm.prank(agent);
        vm.expectRevert(AMMRouter.SlippageExceeded.selector);
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);
    }

    /// @dev Attack scenario, sell direction: attacker dumps sTSLA into the
    ///      pool (crashing its pool price) before the vault sells. The vault
    ///      must revert instead of selling at the manipulated price.
    function test_revert_rebalance_sell_manipulated_pool() public {
        _depositAsAlice(50_000 * 10**6);
        _rebalanceBuyTsla(10_000 * 10**6);

        // Attacker front-runs: dumps 5M sTSLA into the pool.
        sTSLA.mint(bob, 5_000_000 * 1e18);
        vm.startPrank(bob);
        sTSLA.approve(address(router), 5_000_000 * 1e18);
        router.swap(address(sTSLA), address(usdc), 5_000_000 * 1e18, 0);
        vm.stopPrank();

        address[] memory tokensIn = new address[](0);
        uint256[] memory amountsIn = new uint256[](0);
        address[] memory tokensOut = new address[](1);
        tokensOut[0] = address(sTSLA);
        uint256[] memory amountsOut = new uint256[](1);
        amountsOut[0] = 1_000 * 1e18;

        _commitFor(tokensIn, amountsIn, tokensOut, amountsOut);
        vm.prank(agent);
        vm.expectRevert(AMMRouter.SlippageExceeded.selector);
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);
    }

    /// @dev A swap on a token with no registered oracle is unprotected and
    ///      must revert rather than fall back to minAmountOut = 0.
    function test_revert_rebalance_without_oracle() public {
        MockToken sNVDA = new MockToken("Synthetic NVDA", "sNVDA");
        router.createPool(address(usdc), address(sNVDA));

        uint256 poolUsdc = 1_000_000 * 10**6;
        uint256 poolNvda = 1_000_000 * 1e18;
        usdc.mint(address(this), poolUsdc);
        sNVDA.mint(address(this), poolNvda);
        usdc.approve(address(router), poolUsdc);
        sNVDA.approve(address(router), poolNvda);
        router.addLiquidity(address(usdc), address(sNVDA), poolUsdc, poolNvda, 0);

        _depositAsAlice(50_000 * 10**6);

        address[] memory tokensIn = new address[](1);
        tokensIn[0] = address(sNVDA);
        uint256[] memory amountsIn = new uint256[](1);
        amountsIn[0] = 10_000 * 10**6;
        address[] memory tokensOut = new address[](0);
        uint256[] memory amountsOut = new uint256[](0);

        _commitFor(tokensIn, amountsIn, tokensOut, amountsOut);
        vm.prank(agent);
        vm.expectRevert(Vault.OracleNotSet.selector);
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);
    }

    /// @dev Withdrawal-driven liquidation (_liquidateToUsdc) succeeds against
    ///      an oracle-aligned pool — the min-out floor doesn't block honest flow.
    function test_withdraw_liquidation_with_min_out() public {
        _depositAsAlice(50_000 * 10**6);
        _rebalanceBuyTsla(40_000 * 10**6);

        // Vault now holds ~10k USDC; withdrawing 30k forces a synth liquidation.
        vm.prank(alice);
        vault.withdraw(30_000 * 10**6, alice, alice);

        assertGe(usdc.balanceOf(alice), 950_000 * 10**6 + 30_000 * 10**6 - 50_000 * 10**6);
    }

    /// @dev Audit finding 1a (2026-06-14): with the old fixed-0.5% liquidation
    ///      buffer (`liquidationTarget = shortfall + shortfall/200`), a
    ///      redemption that needs to liquidate a meaningful slice of an
    ///      oracle-aligned pool reverted `InsufficientLiquidity()` even though
    ///      the vault's non-USDC holdings were comfortably worth more than the
    ///      shortfall — because the realized swap output (oracle-fair price
    ///      minus the 30bps AMM fee and constant-product price impact) landed
    ///      a few bps below `shortfall`, while the buffer only covered ~0.5%.
    ///      The fixed liquidationTarget = ceil(shortfall * BPS / (BPS -
    ///      maxSlippageBps)) ties the buffer to the same oracle-floor
    ///      `_oracleMinOut` enforces, so `usdcAfter >= shortfall` holds by
    ///      construction. This test reproduces the exact pre-fix failure mode
    ///      at maxSlippageBps = 100 (the vault's default, > the ~50bps
    ///      breakeven for the old formula) and asserts the redemption now
    ///      succeeds.
    function test_withdraw_liquidation_slippage_buffer_covers_oracle_floor() public {
        // Fresh synth + oracle + AMM pool, oracle-aligned at 2 USDC / token
        // (10,000,000 USDC : 5,000,000 token, same ratio as TSLA_PRICE).
        MockToken sSYNTH = new MockToken("Synthetic SYNTH", "sSYNTH");
        PriceOracle synthOracle = new PriceOracle("sSYNTH", TSLA_PRICE, owner);

        router.createPool(address(usdc), address(sSYNTH));
        uint256 poolUsdc = 10_000_000 * 10**6;
        uint256 poolSynth = 5_000_000 * 1e18;
        usdc.mint(address(this), poolUsdc);
        sSYNTH.mint(address(this), poolSynth);
        usdc.approve(address(router), poolUsdc);
        sSYNTH.approve(address(router), poolSynth);
        router.addLiquidity(address(usdc), address(sSYNTH), poolUsdc, poolSynth, 0);

        address[] memory oracleTokens = new address[](1);
        oracleTokens[0] = address(sSYNTH);
        address[] memory oracles = new address[](1);
        oracles[0] = address(synthOracle);
        vm.prank(agent);
        vault.setTokenOracles(oracleTokens, oracles);

        // maxSlippageBps = 100 (1%) — the vault's default, and > the ~50bps
        // breakeven where the old fixed-0.5% buffer stopped covering the
        // oracle floor (set explicitly so the scenario is self-documenting).
        vm.prank(agent);
        vault.setMaxSlippageBps(100);

        _depositAsAlice(50_000 * 10**6);

        // Negligible rebalance-buy (1 USDC) just to register sSYNTH in
        // heldTokens — disturbs the fresh pool by < 1e-7 of its depth.
        address[] memory tokensIn = new address[](1);
        tokensIn[0] = address(sSYNTH);
        uint256[] memory amountsIn = new uint256[](1);
        amountsIn[0] = 1 * 10**6;
        address[] memory tokensOut = new address[](0);
        uint256[] memory amountsOut = new uint256[](0);
        _commitFor(tokensIn, amountsIn, tokensOut, amountsOut);
        vm.prank(agent);
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);

        // Top up the vault's sSYNTH holding directly so the position is
        // large enough that liquidating ~30k USDC worth doesn't get capped
        // by totalNonUsdcValue, while the AMM pool itself stays essentially
        // at its oracle-aligned starting ratio.
        sSYNTH.mint(address(vault), 100_000 * 1e18);

        // Withdraw enough to force liquidating ~30,000 USDC of sSYNTH against
        // the oracle-aligned pool. At maxSlippageBps = 100 the realized output
        // (~0.997 * shortfall * 100/99, fee + price impact) lands just below
        // `shortfall` under the old fixed-0.5% buffer — InsufficientLiquidity()
        // — but at/above `shortfall` under the new oracle-floor-tied buffer.
        uint256 usdcOnHand = usdc.balanceOf(address(vault));
        uint256 withdrawAmount = usdcOnHand + 30_000 * 10**6;

        uint256 aliceBefore = usdc.balanceOf(alice);
        vm.prank(alice);
        vault.withdraw(withdrawAmount, alice, alice);

        assertEq(usdc.balanceOf(alice), aliceBefore + withdrawAmount);
    }

    /// @dev Withdrawal-driven liquidation against a manipulated pool must
    ///      revert — rebalance/liquidation authority cannot be converted into
    ///      a bad-price drain.
    function test_revert_withdraw_liquidation_manipulated_pool() public {
        _depositAsAlice(50_000 * 10**6);
        _rebalanceBuyTsla(40_000 * 10**6);

        // Attacker crashes the sTSLA pool price before the liquidation sell.
        sTSLA.mint(bob, 5_000_000 * 1e18);
        vm.startPrank(bob);
        sTSLA.approve(address(router), 5_000_000 * 1e18);
        router.swap(address(sTSLA), address(usdc), 5_000_000 * 1e18, 0);
        vm.stopPrank();

        vm.prank(alice);
        vm.expectRevert(AMMRouter.SlippageExceeded.selector);
        vault.withdraw(30_000 * 10**6, alice, alice);
    }

    /// @dev Stale oracle blocks swaps entirely (PriceOracle.getPrice reverts).
    function test_revert_rebalance_stale_oracle() public {
        _depositAsAlice(50_000 * 10**6);

        vm.warp(block.timestamp + 25 hours);

        address[] memory tokensIn = new address[](1);
        tokensIn[0] = address(sTSLA);
        uint256[] memory amountsIn = new uint256[](1);
        amountsIn[0] = 10_000 * 10**6;
        address[] memory tokensOut = new address[](0);
        uint256[] memory amountsOut = new uint256[](0);

        _commitFor(tokensIn, amountsIn, tokensOut, amountsOut);
        vm.prank(agent);
        vm.expectRevert(PriceOracle.StalePrice.selector);
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);
    }

    // ─── Target Allocations ──────────────────────────────────────────

    function test_setTargetAllocations() public {
        address[] memory tokens = new address[](2);
        tokens[0] = address(usdc);
        tokens[1] = address(sTSLA);
        uint256[] memory weights = new uint256[](2);
        weights[0] = 6000; // 60%
        weights[1] = 4000; // 40%

        vm.prank(agent);
        vault.setTargetAllocations(tokens, weights);

        (address[] memory retTokens, uint256[] memory retWeights) = vault.getTargetAllocations();
        assertEq(retTokens.length, 2);
        assertEq(retWeights[0], 6000);
        assertEq(retWeights[1], 4000);
    }

    function test_revert_setTargetAllocations_invalid_sum() public {
        address[] memory tokens = new address[](1);
        tokens[0] = address(usdc);
        uint256[] memory weights = new uint256[](1);
        weights[0] = 5000; // 50% — doesn't sum to 100%

        vm.prank(agent);
        vm.expectRevert(Vault.InvalidAllocations.selector);
        vault.setTargetAllocations(tokens, weights);
    }

    function test_revert_setTargetAllocations_unauthorized() public {
        address[] memory tokens = new address[](1);
        tokens[0] = address(usdc);
        uint256[] memory weights = new uint256[](1);
        weights[0] = 10000;

        vm.prank(bob);
        vm.expectRevert(Vault.Unauthorized.selector);
        vault.setTargetAllocations(tokens, weights);
    }

    // ─── Vault Properties ────────────────────────────────────────────

    function test_vault_tier1() public view {
        assertEq(vault.tier(), 1);
        assertTrue(vault.isAgentAssisted());
        assertEq(vault.managementFeeBps(), 150);
        assertEq(vault.performanceFeeBps(), 2000);
    }

    function test_vault_creator() public view {
        assertEq(vault.creator(), agent);
    }

    function test_vault_asset() public view {
        assertEq(vault.asset(), address(usdc));
    }

    function test_totalAssets() public {
        uint256 amount = 10_000 * 10**6;

        vm.prank(alice);
        usdc.approve(address(vault), amount);
        vm.prank(alice);
        vault.deposit(amount, alice);

        assertEq(vault.totalAssets(), amount);
    }

    // ─── #942: decimal-aware NAV pricing ──────────────────────────────

    /// @dev Wiring an oracle snapshots 10**decimals for the token, so NAV/swap
    ///      math scales by the token's real precision. sTSLA (18 dec, wired in
    ///      setUp) caches 1e18; an 8-decimal token caches 1e8; an unwired token
    ///      falls back to 1e18 via the getter default (but the public mapping
    ///      itself is still 0).
    function test_tokenUnit_cached_from_decimals() public {
        // sTSLA is 18-decimal and was wired in setUp.
        assertEq(vault.tokenUnit(address(sTSLA)), 1e18);

        MockToken8 eightDec = new MockToken8("Eight", "E8");
        PriceOracle eightOracle = new PriceOracle("E8", 2 * 10**6, owner);
        address[] memory tokens = new address[](1);
        tokens[0] = address(eightDec);
        address[] memory oracles = new address[](1);
        oracles[0] = address(eightOracle);
        vm.prank(agent);
        vault.setTokenOracles(tokens, oracles);

        assertEq(vault.tokenUnit(address(eightDec)), 10**8);
    }

    /// @dev End-to-end regression: a rebalance-buy into an 8-decimal token
    ///      conserves NAV. Under the old hardcoded 1e18 divisor the 8-decimal
    ///      holding would be priced at ~1e-10 of its true value, so totalAssets
    ///      would collapse to just the unspent USDC. With the cached 1e8 unit,
    ///      totalAssets stays ~= the deposit (fee + price impact aside).
    function test_totalAssets_prices_non18_decimal_token() public {
        // Fresh 8-decimal synth + oracle + AMM pool, aligned at 2 USDC / token
        // (10,000,000 USDC : 5,000,000 token, same value ratio as TSLA_PRICE).
        MockToken8 s8 = new MockToken8("Synthetic8", "S8");
        PriceOracle o8 = new PriceOracle("S8", TSLA_PRICE, owner);

        router.createPool(address(usdc), address(s8));
        uint256 poolUsdc = 10_000_000 * 10**6;
        uint256 poolS8 = 5_000_000 * 10**8; // 8-decimal reserves
        usdc.mint(address(this), poolUsdc);
        s8.mint(address(this), poolS8);
        usdc.approve(address(router), poolUsdc);
        s8.approve(address(router), poolS8);
        router.addLiquidity(address(usdc), address(s8), poolUsdc, poolS8, 0);

        address[] memory oracleTokens = new address[](1);
        oracleTokens[0] = address(s8);
        address[] memory oracles = new address[](1);
        oracles[0] = address(o8);
        vm.prank(agent);
        vault.setTokenOracles(oracleTokens, oracles);

        uint256 deposit = 50_000 * 10**6;
        _depositAsAlice(deposit);

        // Buy ~40k USDC of the 8-decimal token so most of the NAV sits in the
        // non-USDC holding — the slice the old 1e18 scaling mispriced to dust.
        address[] memory tokensIn = new address[](1);
        tokensIn[0] = address(s8);
        uint256[] memory amountsIn = new uint256[](1);
        amountsIn[0] = 40_000 * 10**6;
        address[] memory tokensOut = new address[](0);
        uint256[] memory amountsOut = new uint256[](0);
        _commitFor(tokensIn, amountsIn, tokensOut, amountsOut);
        vm.prank(agent);
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);

        // Vault holds ~40k of the 8-decimal token plus ~10k USDC. NAV must stay
        // within ~1% of the deposit (30bps AMM fee + bounded price impact).
        assertApproxEqRel(vault.totalAssets(), deposit, 0.01e18);
        // And the 8-decimal holding must contribute the bulk of NAV, not dust:
        // strictly more than the leftover USDC alone would (old-code failure).
        assertGt(vault.totalAssets(), 20_000 * 10**6);
    }

    // ─── Fee Tests ───────────────────────────────────────────────────

    function test_management_fee_accrues() public {
        uint256 amount = 100_000 * 10**6;

        vm.prank(alice);
        usdc.approve(address(vault), amount);
        vm.prank(alice);
        vault.deposit(amount, alice);

        uint256 supplyBefore = vault.totalSupply();

        // Advance time by 365 days (1 year)
        vm.warp(block.timestamp + 365 days);

        // Trigger fee accrual via another deposit
        uint256 smallDeposit = 1000 * 10**6;
        vm.prank(bob);
        usdc.approve(address(vault), smallDeposit);
        vm.prank(bob);
        vault.deposit(smallDeposit, bob);

        // Total supply should have increased due to management fee shares
        uint256 supplyAfter = vault.totalSupply();
        assertGt(supplyAfter, supplyBefore + smallDeposit);
    }

    function test_highWaterMark_initial() public view {
        assertEq(vault.highWaterMark(), 1e18);
    }

    // ─── VaultFactory Tests ──────────────────────────────────────────

    function test_factory_create_tier1() public {
        vm.prank(agent);
        address v = factory.createVault("Tier1 Vault", "vT1", 100, 1000, true);

        assertEq(Vault(payable(v)).tier(), 1);
        assertEq(Vault(payable(v)).creator(), agent);
    }

    function test_factory_create_tier2() public {
        vm.prank(alice);
        address v = factory.createVault("Community Vault", "vCOM", 200, 1500, false);

        assertEq(Vault(payable(v)).tier(), 2);
        assertEq(Vault(payable(v)).creator(), alice);
    }

    function test_factory_getVaults() public {
        vm.prank(agent);
        factory.createVault("V1", "v1", 100, 1000, true);

        vm.prank(alice);
        factory.createVault("V2", "v2", 200, 1500, false);

        address[] memory vaults = factory.getVaults();
        assertEq(vaults.length, 3); // 1 from setUp + 2 new
    }

    function test_factory_getVaultsByCreator() public {
        vm.prank(alice);
        factory.createVault("Alice Vault", "vAL", 200, 1500, false);

        address[] memory aliceVaults = factory.getVaultsByCreator(alice);
        assertEq(aliceVaults.length, 1);

        address[] memory agentVaults = factory.getVaultsByCreator(agent);
        assertEq(agentVaults.length, 1); // from setUp
    }

    function test_factory_vaultCount() public {
        assertEq(factory.vaultCount(), 1); // from setUp

        vm.prank(alice);
        factory.createVault("V2", "v2", 200, 1500, false);
        assertEq(factory.vaultCount(), 2);
    }

    function test_factory_agentAddress() public view {
        assertEq(factory.agentAddress(), agent);
    }

    function test_factory_ammRouter() public view {
        assertEq(factory.ammRouter(), address(router));
    }

    function test_factory_usdc() public view {
        assertEq(factory.usdc(), address(usdc));
    }

    // ─── Edge Cases ──────────────────────────────────────────────────

    function test_deposit_withdraw_round_trip() public {
        uint256 amount = 10_000 * 10**6;
        uint256 aliceUsdcBefore = usdc.balanceOf(alice);

        vm.startPrank(alice);
        usdc.approve(address(vault), amount);
        vault.deposit(amount, alice);
        vault.withdraw(amount - vault.MIN_LIQUIDITY(), alice, alice);
        vm.stopPrank();

        // Alice gets her USDC back minus the one-time MIN_LIQUIDITY dead-share cost
        // (1e3 share-wei = 0.001 USDC, inflation guard #507; no fees — no time passed)
        assertEq(usdc.balanceOf(alice), aliceUsdcBefore - vault.MIN_LIQUIDITY());
    }

    function test_multiple_deposits_withdraws() public {
        uint256 amount1 = 10_000 * 10**6;
        uint256 amount2 = 20_000 * 10**6;

        // Alice deposits
        vm.startPrank(alice);
        usdc.approve(address(vault), amount1);
        vault.deposit(amount1, alice);

        // Bob deposits
        vm.startPrank(bob);
        usdc.approve(address(vault), amount2);
        vault.deposit(amount2, bob);

        // Alice withdraws her full entitlement: amount1 minus the MIN_LIQUIDITY
        // dead shares locked by her first deposit (inflation guard, #507)
        vm.startPrank(alice);
        vault.withdraw(amount1 - vault.MIN_LIQUIDITY(), alice, alice);

        // Vault retains amount2 plus the dead shares' pro-rata assets
        assertEq(vault.totalAssets(), amount2 + vault.MIN_LIQUIDITY());
        assertEq(vault.balanceOf(alice), 0);
        assertEq(vault.balanceOf(bob), amount2);
    }

    // ─── T0.2: non-custodial owner≠agent + drain-vector ──────────────
    //
    // Context (re-land of the intent behind PR #646, which #650 reverted).
    // #646 tried to separate the Vault's Ownable owner from its creator/agent by
    // adding a 6th `address _vaultOwner` arg to createVault. That changed the
    // on-chain selector to createVault(string,string,uint16,uint16,bool,address),
    // but the VaultFactory deployed on Arc testnet only has the 5-arg selector —
    // so every createVault reverted on-chain and #650 reverted #646 in full to
    // un-break the live demo (see PR #650).
    //
    // The owner≠agent SEPARATION is re-landed here WITHOUT touching the deployed
    // createVault selector or the Vault constructor: the backend now creates the
    // vault (becoming creator+owner), calls setAgent(backendAgent), then
    // transferOwnership(userWallet) — both already in the live ABI. The contract
    // surface below proves that handoff yields the intended non-custodial posture:
    // the agent has rebalance authority ONLY, and a compromised agent key cannot
    // drain the vault, re-point oracles, pause, or otherwise touch owner-only
    // sensitive setters.
    //
    // These tests build vaults with a DISTINCT owner and agent (in the shared
    // setUp, agent == creator == owner, which would mask the separation).

    /// @dev Mirror of the runtime handoff the backend performs after createVault:
    ///      creator creates the vault (becomes Ownable owner), sets a distinct
    ///      agent, then transfers ownership to the end user's wallet. Returns the
    ///      vault now owned by `user` with `compromisedAgent` as the rebalancer.
    function _handoffVault(address creator_, address user, address compromisedAgent)
        internal
        returns (Vault v)
    {
        vm.prank(creator_);
        address vaultAddr = factory.createVault("NonCustodial", "vNC", 0, 0, true);
        v = Vault(payable(vaultAddr));

        // Backend wires the agent (rebalance authority) while it is still owner.
        vm.prank(creator_);
        v.setAgent(compromisedAgent);

        // Backend hands ownership to the depositing user — owner != agent now.
        vm.prank(creator_);
        v.transferOwnership(user);

        assertEq(v.owner(), user, "owner must be the user wallet after handoff");
        assertEq(v.agent(), compromisedAgent, "agent must be the backend signer");
        assertTrue(v.owner() != v.agent(), "owner and agent must differ (non-custodial)");
    }

    /// @dev transferOwnership (inherited from OZ Ownable) moves ALL owner-only
    ///      rights to the new owner and strips them from the previous owner. This
    ///      is the exact primitive the backend relies on to make vaults
    ///      user-owned, so we assert both halves: the user gains owner rights and
    ///      the previous owner (the backend creator) loses them.
    function test_transferOwnership_moves_owner_rights_to_user() public {
        address creator_ = address(0xC0FFEE);
        address user = address(0xA11CE);
        address backendAgent = address(0xA9E27);

        Vault v = _handoffVault(creator_, user, backendAgent);

        // The previous owner (creator/backend) can no longer call owner-only setters.
        vm.prank(creator_);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, creator_));
        v.setMaxSlippageBps(50);

        // The new owner (user) can.
        vm.prank(user);
        v.setMaxSlippageBps(50);
        assertEq(v.maxSlippageBps(), 50);
    }

    /// @dev DRAIN VECTOR — the headline non-custodial invariant.
    ///      A fully compromised agent key (it can call every onlyManager function:
    ///      rebalance, setTargetAllocations, setTokenOraclesFromRegistry) must NOT
    ///      be able to move user funds out of the vault. It has NO path to:
    ///        (a) withdraw/redeem someone else's shares,
    ///        (b) re-point the NAV/slippage oracle to a self-serving one,
    ///        (c) pause the vault to grief users,
    ///        (d) seize ownership.
    ///      The only thing it can do is rebalance WITHIN the oracle-enforced
    ///      slippage floor — which conserves vault value and cannot exfiltrate it.
    function test_drain_vector_compromised_agent_cannot_drain() public {
        address creator_ = address(0xC0FFEE);
        address user = address(0xA11CE);
        address attacker = address(0xBADBAD); // the compromised agent key

        Vault v = _handoffVault(creator_, user, attacker);

        // Owner (user) wires a legitimate oracle so the vault can price/rebalance.
        address[] memory tokens = _singleton(address(sTSLA));
        address[] memory oracles = _singleton(address(tslaOracle));
        vm.prank(user);
        v.setTokenOracles(tokens, oracles);

        // A real user deposits real funds.
        uint256 deposit = 50_000 * 10**6;
        usdc.mint(user, deposit);
        vm.startPrank(user);
        usdc.approve(address(v), deposit);
        v.deposit(deposit, user);
        vm.stopPrank();

        uint256 vaultUsdcBefore = usdc.balanceOf(address(v));
        assertEq(vaultUsdcBefore, deposit, "vault should hold the user's USDC");

        // (a) The attacker cannot withdraw or redeem the user's shares — it owns
        //     none and has no allowance, so the ERC-4626 allowance check reverts.
        //     Read the view values BEFORE expectRevert so the cheatcode targets
        //     the redeem/withdraw call, not an inlined view (#expectRevert pitfall).
        uint256 userShares = v.balanceOf(user);
        uint256 withdrawable = deposit - v.MIN_LIQUIDITY();
        vm.prank(attacker);
        vm.expectRevert(); // ERC20InsufficientAllowance (no allowance granted)
        v.redeem(userShares, attacker, user);

        vm.prank(attacker);
        vm.expectRevert();
        v.withdraw(withdrawable, attacker, user);

        // (b) The attacker cannot re-point the oracle that feeds the slippage
        //     floor — setTokenOracles is onlyOwner. Without this, the classic
        //     drain is: point sTSLA at an attacker oracle returning a price of 1,
        //     set minAmountOut≈0, and route a swap that leaks value.
        PriceOracle evilOracle = new PriceOracle("evil", 1, attacker);
        address[] memory evilOracles = _singleton(address(evilOracle));
        vm.prank(attacker);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, attacker));
        v.setTokenOracles(tokens, evilOracles);

        // (c) The attacker cannot widen slippage past the cap (it cannot call the
        //     setter at all — onlyOwner) to weaken swap protection. Read the cap
        //     view before expectRevert so the cheatcode targets the setter call.
        uint256 cap = v.MAX_SLIPPAGE_CAP_BPS();
        vm.prank(attacker);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, attacker));
        v.setMaxSlippageBps(cap);

        // (d) The attacker cannot pause (grief) or seize ownership.
        vm.prank(attacker);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, attacker));
        v.pause();

        vm.prank(attacker);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, attacker));
        v.transferOwnership(attacker);

        // (e) Sanity: the attacker CAN still rebalance (its legitimate authority),
        //     but the swap is bounded by the owner-set oracle floor, so vault NAV
        //     is conserved — value moves between USDC and sTSLA, never OUT to the
        //     attacker. The vault's total NAV (priced via the owner's oracle) is
        //     held to within the bounded slippage; funds never leave the vault.
        uint256 navBefore = v.totalAssets();
        address[] memory tIn = _singleton(address(sTSLA));
        uint256[] memory aIn = _singletonUint(10_000 * 10**6);
        address[] memory tOut = new address[](0);
        uint256[] memory aOut = new uint256[](0);
        // #589: even the compromised agent must commit-before-trade — but this does NOT
        // help it drain, the swap stays bounded by the owner's oracle floor.
        bytes32 drainTradeId = keccak256(abi.encode(tIn, aIn, tOut, aOut));
        vm.prank(attacker);
        traceRegistry.commit(address(v), keccak256("trace"), uint64(block.timestamp + 1), drainTradeId, "");
        vm.roll(block.number + 1);
        vm.prank(attacker);
        v.rebalance(tIn, aIn, tOut, aOut); // buys sTSLA within the oracle floor

        // The vault now holds sTSLA + remaining USDC; the attacker's own USDC
        // balance is unchanged — nothing was exfiltrated to the attacker.
        assertEq(usdc.balanceOf(attacker), 0, "attacker must not have received any vault USDC");
        assertGt(sTSLA.balanceOf(address(v)), 0, "vault holds the bought synth, not the attacker");

        // NAV is conserved within the vault's slippage tolerance (default 1%) —
        // the rebalance reshuffled USDC↔sTSLA but did not bleed value out. Any
        // delta is AMM fee + bounded impact, NOT an exfiltration to the attacker.
        uint256 navAfter = v.totalAssets();
        assertGe(navAfter, (navBefore * 9900) / 10000, "NAV must stay within slippage; value not drained");

        // The user — and ONLY the user — can redeem their funds; custody never
        // left them. We redeem 90% of the shares: a full-share redeem would have
        // to liquidate the bought sTSLA back through the AMM (a second fee-bearing
        // leg), so the very last sliver of NAV isn't payable in USDC without
        // incurring round-trip friction — that residual is a normal ERC-4626
        // property, not a custody loss. Redeeming 90% comfortably clears the
        // available USDC + liquidation headroom and proves the user controls the
        // funds. Read the share balance before prank so the cheatcode applies to
        // redeem(), not the balanceOf view (single-call pitfall).
        uint256 redeemable = (v.balanceOf(user) * 90) / 100;
        uint256 userUsdcBefore = usdc.balanceOf(user);
        vm.prank(user);
        v.redeem(redeemable, user, user);
        uint256 recovered = usdc.balanceOf(user) - userUsdcBefore;
        // 90% of a ~50k deposit, minus bounded slippage → comfortably > 44k.
        assertGt(recovered, (deposit * 88) / 100, "user recovers their funds; attacker got nothing");
    }

    // ─── Commit-before-trade enforcement (#589) ──────────────────────

    function test_revert_rebalance_without_commit() public {
        _depositAsAlice(50_000 * 10**6);

        address[] memory tokensIn = _singleton(address(sTSLA));
        uint256[] memory amountsIn = _singletonUint(10_000 * 10**6);
        address[] memory tokensOut = new address[](0);
        uint256[] memory amountsOut = new uint256[](0);

        // No prior commit — a trade cannot settle without a matching commitment.
        vm.prank(agent);
        vm.expectRevert("No matching commitment");
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);
    }

    function test_rebalance_commit_is_single_use() public {
        _depositAsAlice(50_000 * 10**6);

        address[] memory tokensIn = _singleton(address(sTSLA));
        uint256[] memory amountsIn = _singletonUint(10_000 * 10**6);
        address[] memory tokensOut = new address[](0);
        uint256[] memory amountsOut = new uint256[](0);

        // Commit, then trade — succeeds.
        _commitFor(tokensIn, amountsIn, tokensOut, amountsOut);
        vm.prank(agent);
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);

        // The commitment is consumed — repeating the identical trade without a fresh
        // commit reverts, so one trace cannot authorize many trades.
        vm.prank(agent);
        vm.expectRevert("No matching commitment");
        vault.rebalance(tokensIn, amountsIn, tokensOut, amountsOut);
    }

    function test_revert_rebalance_commit_for_different_trade() public {
        _depositAsAlice(50_000 * 10**6);

        // The agent commits a SMALL trade (1,000 USDC) ...
        address[] memory tIn = _singleton(address(sTSLA));
        uint256[] memory aInCommitted = _singletonUint(1_000 * 10**6);
        address[] memory tOut = new address[](0);
        uint256[] memory aOut = new uint256[](0);
        _commitFor(tIn, aInCommitted, tOut, aOut);

        // ... but tries to execute a LARGER trade (10,000 USDC). Different tradeId =>
        // no matching commitment => the trade is blocked. (No bait-and-switch.)
        uint256[] memory aInActual = _singletonUint(10_000 * 10**6);
        vm.prank(agent);
        vm.expectRevert("No matching commitment");
        vault.rebalance(tIn, aInActual, tOut, aOut);
    }

    function _singletonUint(uint256 x) internal pure returns (uint256[] memory arr) {
        arr = new uint256[](1);
        arr[0] = x;
    }

    // ─── Redemption Liquidation-Slippage Fairness (issue #913) ───────

    /// @dev Deposit alice + bob equally, then rebalance most USDC into sTSLA so a full-exit
    ///      redemption has to liquidate at a loss.
    function _twoUserVaultMostlyInSynth() internal returns (uint256 aliceShares, uint256 bobShares) {
        // Give the swaps room: raise the oracle-floor tolerance to the 5% cap so the later
        // liquidation isn't rejected. The realized slippage the test measures is still just
        // the AMM fee + price impact — the tolerance only gates whether the swap is allowed.
        vm.prank(agent);
        vault.setMaxSlippageBps(500);

        // Alice + bob each own ~half the vault.
        _depositAsAlice(5_000 * 10**6);
        vm.startPrank(bob);
        usdc.approve(address(vault), 5_000 * 10**6);
        vault.deposit(5_000 * 10**6, bob);
        vm.stopPrank();

        // Register sTSLA in heldTokens with a negligible 1-USDC buy (keeps the pool at its
        // oracle-aligned starting ratio), then seed a large synth position directly — same
        // fixture shape as test_withdraw_liquidation_slippage_buffer_covers_oracle_floor.
        // The vault now holds ~10k USDC + ~90k sTSLA NAV, so a half-vault exit must liquidate
        // ~40k of synth into an oracle-aligned pool and eat the fee + impact (a real loss vs
        // oracle NAV, unlike selling back into a pool a prior buy already skewed upward).
        _rebalanceBuyTsla(1 * 10**6);
        sTSLA.mint(address(vault), 45_000 * 1e18); // 45k tokens * 2 USDC = 90k NAV

        aliceShares = vault.balanceOf(alice);
        bobShares = vault.balanceOf(bob);
    }

    /// @dev The core #913 invariant: a redemption that forces a liquidation must not lower
    ///      the per-share NAV of the holders who stay. Pre-fix, alice got the full pre-trade
    ///      claim and bob silently ate the liquidation slippage.
    function test_redeem_liquidation_slippage_not_socialized() public {
        (uint256 aliceShares,) = _twoUserVaultMostlyInSynth();

        uint256 navPerShareBefore = (vault.totalAssets() * 1e18) / vault.totalSupply();

        vm.prank(alice);
        vault.redeem(aliceShares, alice, alice);

        uint256 navPerShareAfter = (vault.totalAssets() * 1e18) / vault.totalSupply();

        // Bob's per-share NAV must not fall. It can tick up a hair (integer-division dust
        // rounds in the vault's favor), so assert non-decreasing rather than exact.
        assertGe(navPerShareAfter, navPerShareBefore, "remaining holders' NAV must not drop");
    }

    /// @dev The flip side: the redeemer actually absorbs the slippage — her payout is below
    ///      the pre-trade claim, and RedemptionLiquidationCharge is emitted.
    function test_redeem_charges_redeemer_the_slippage() public {
        (uint256 aliceShares,) = _twoUserVaultMostlyInSynth();

        uint256 claim = vault.previewRedeem(aliceShares); // pre-trade (uncharged) value
        uint256 aliceBefore = usdc.balanceOf(alice);

        vm.recordLogs();
        vm.prank(alice);
        uint256 paid = vault.redeem(aliceShares, alice, alice);

        assertEq(usdc.balanceOf(alice) - aliceBefore, paid, "payout matches return value");
        assertLt(paid, claim, "redeemer bears the liquidation slippage");

        // RedemptionLiquidationCharge(owner, slippageLoss) was emitted, and the charge
        // equals the gap between the pre-trade claim and what was actually paid.
        Vm.Log[] memory logs = vm.getRecordedLogs();
        bytes32 sig = keccak256("RedemptionLiquidationCharge(address,uint256)");
        bool found;
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].topics[0] == sig) {
                found = true;
                assertEq(address(uint160(uint256(logs[i].topics[1]))), alice, "owner indexed");
                uint256 loss = abi.decode(logs[i].data, (uint256));
                assertEq(loss, claim - paid, "charge equals claim - payout");
            }
        }
        assertTrue(found, "RedemptionLiquidationCharge emitted");
    }

    /// @dev withdraw() must still deliver EXACTLY the requested USDC, charging the caller
    ///      the slippage in extra burned shares — so remaining holders are protected the
    ///      same way, without changing the exact-assets-out contract.
    function test_withdraw_liquidation_burns_extra_shares_from_withdrawer() public {
        (uint256 aliceSharesBefore,) = _twoUserVaultMostlyInSynth();

        uint256 navPerShareBefore = (vault.totalAssets() * 1e18) / vault.totalSupply();

        // Base shares withdraw() would burn with no slippage (mirrors _convertToShares:
        // ceil(assets * totalSupply / totalAssets)), for comparison.
        uint256 baseShares =
            (30_000 * 10**6 * vault.totalSupply() + vault.totalAssets() - 1) / vault.totalAssets();

        uint256 aliceUsdcBefore = usdc.balanceOf(alice);
        vm.prank(alice);
        uint256 sharesBurned = vault.withdraw(30_000 * 10**6, alice, alice);

        // Exactly the requested USDC is delivered.
        assertEq(usdc.balanceOf(alice) - aliceUsdcBefore, 30_000 * 10**6, "exact assets out");
        // More shares were burned than the no-slippage base — that's the slippage charge.
        assertGt(sharesBurned, baseShares, "extra shares cover the slippage");
        assertEq(aliceSharesBefore - vault.balanceOf(alice), sharesBurned, "burned from the withdrawer");

        // Remaining holders' NAV/share is preserved.
        uint256 navPerShareAfter = (vault.totalAssets() * 1e18) / vault.totalSupply();
        assertGe(navPerShareAfter, navPerShareBefore, "remaining holders' NAV must not drop");
    }

    /// @dev Regression: when the vault holds enough USDC on hand, no liquidation runs, so
    ///      no charge is applied and the redeemer gets the full pre-trade claim.
    function test_redeem_no_charge_when_usdc_on_hand() public {
        _depositAsAlice(50_000 * 10**6);
        vm.startPrank(bob);
        usdc.approve(address(vault), 50_000 * 10**6);
        vault.deposit(50_000 * 10**6, bob);
        vm.stopPrank();
        // No rebalance — the vault is all USDC, so redeem is fully covered.

        uint256 aliceShares = vault.balanceOf(alice);
        uint256 claim = vault.previewRedeem(aliceShares);

        vm.recordLogs();
        vm.prank(alice);
        uint256 paid = vault.redeem(aliceShares, alice, alice);

        assertEq(paid, claim, "full claim when no liquidation needed");

        Vm.Log[] memory logs = vm.getRecordedLogs();
        bytes32 sig = keccak256("RedemptionLiquidationCharge(address,uint256)");
        for (uint256 i; i < logs.length; i++) {
            assertTrue(logs[i].topics[0] != sig, "no charge event when USDC covers the redemption");
        }
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/QuorumPriceOracle.sol";

/// @dev Settable mock AggregatorV3 feed — answer, decimals, round metadata all drivable so
///      we can exercise the quorum deterministically (fresh/stale, agree/diverge, scaling).
contract MockFeed is AggregatorV3Interface {
    uint8 internal _decimals;
    int256 internal _answer;
    uint80 internal _roundId = 1;
    uint256 internal _updatedAt;
    uint80 internal _answeredInRound = 1;
    bool internal _revert;

    constructor(uint8 d, int256 answer, uint256 updatedAt) {
        _decimals = d;
        _answer = answer;
        _updatedAt = updatedAt;
    }

    function decimals() external view override returns (uint8) {
        return _decimals;
    }

    function latestRoundData() external view override returns (uint80, int256, uint256, uint256, uint80) {
        if (_revert) revert("feed down");
        return (_roundId, _answer, _updatedAt, _updatedAt, _answeredInRound);
    }

    function setAnswer(int256 a) external {
        _answer = a;
    }

    function setUpdatedAt(uint256 t) external {
        _updatedAt = t;
    }

    function setRevert(bool r) external {
        _revert = r;
    }
}

/// @dev Quorum read-path tests for QuorumPriceOracle. Pins the load-bearing safety
///      properties: median of agreeing feeds (tier QUORUM), fail-closed on divergence,
///      N-aware tier transitions (QUORUM → SINGLE → ADMIN), 8→6 decimal scaling inside the
///      quorum, and the provenance surface. The no-arg getPrice() is the exact path
///      Vault/SyntheticVault read.
contract QuorumPriceOracleTest is Test {
    QuorumPriceOracle public oracle;

    address public owner = address(0x1);
    address public alice = address(0x2);

    // Admin-fed reference, 6-dec USDC: $500.00.
    uint256 constant ADMIN_PRICE = 500_000000;

    // Chainlink-style 8-dec USD answers; _tryReadFeed scales /100 → 6-dec.
    uint8 constant D8 = 8;

    function _8dec(uint256 dollars6) internal pure returns (int256) {
        // 6-dec USDC value → 8-dec feed answer (×100).
        return int256(dollars6 * 100);
    }

    function setUp() public {
        vm.warp(1_000_000); // baseline well above any staleness window
        vm.prank(owner);
        oracle = new QuorumPriceOracle("sSPY", ADMIN_PRICE, owner);
    }

    function _add(address feed) internal {
        vm.prank(owner);
        oracle.addFeed(feed);
    }

    // ── Tier ADMIN (no feeds) ────────────────────────────────────────────

    function test_no_feeds_uses_admin() public view {
        assertEq(oracle.getPrice(), ADMIN_PRICE);
        (uint256 p, uint8 tier, uint256 n, bool usable) = oracle.priceWithProvenance();
        assertEq(p, ADMIN_PRICE);
        assertEq(tier, oracle.TIER_ADMIN());
        assertEq(n, 0);
        assertTrue(usable);
        assertEq(oracle.feedCount(), 0);
    }

    function test_no_feeds_admin_stale_reverts() public {
        vm.warp(block.timestamp + 25 hours); // past admin MAX_STALENESS
        vm.expectRevert(PriceOracle.StalePrice.selector);
        oracle.getPrice();
        assertFalse(oracle.isFresh());
        (,,, bool usable) = oracle.priceWithProvenance();
        assertFalse(usable);
    }

    // ── Tier SINGLE (1 fresh feed) ───────────────────────────────────────

    function test_single_feed_in_band() public {
        MockFeed f = new MockFeed(D8, _8dec(500_000000), block.timestamp);
        _add(address(f));
        assertEq(oracle.getPrice(), 500_000000);
        (uint256 p, uint8 tier, uint256 n,) = oracle.priceWithProvenance();
        assertEq(p, 500_000000);
        assertEq(tier, oracle.TIER_SINGLE());
        assertEq(n, 1);
    }

    function test_single_feed_out_of_band_degrades_to_admin() public {
        // One feed 10× the admin reference → out of band → degrade to admin (admin fresh).
        MockFeed f = new MockFeed(D8, _8dec(5_000_000000), block.timestamp);
        _add(address(f));
        assertEq(oracle.getPrice(), ADMIN_PRICE);
        (, uint8 tier,,) = oracle.priceWithProvenance();
        assertEq(tier, oracle.TIER_ADMIN());
    }

    function test_single_feed_trusted_when_admin_stale() public {
        // With no fresh admin reference, the single feed cannot be cross-checked → trust it.
        MockFeed f = new MockFeed(D8, _8dec(600_000000), block.timestamp);
        _add(address(f));
        vm.warp(block.timestamp + 25 hours); // admin now stale
        f.setUpdatedAt(block.timestamp); // keep the feed fresh
        assertEq(oracle.getPrice(), 600_000000);
        (, uint8 tier,,) = oracle.priceWithProvenance();
        assertEq(tier, oracle.TIER_SINGLE());
    }

    // ── Tier QUORUM (≥2 fresh feeds) ─────────────────────────────────────

    function test_quorum_two_feeds_agree_returns_median() public {
        MockFeed a = new MockFeed(D8, _8dec(500_000000), block.timestamp);
        MockFeed b = new MockFeed(D8, _8dec(501_000000), block.timestamp);
        _add(address(a));
        _add(address(b));
        // median of {500.00, 501.00} = 500.50, within the 2% band.
        assertEq(oracle.getPrice(), 500_500000);
        (uint256 p, uint8 tier, uint256 n,) = oracle.priceWithProvenance();
        assertEq(p, 500_500000);
        assertEq(tier, oracle.TIER_QUORUM());
        assertEq(n, 2);
    }

    function test_quorum_three_feeds_returns_middle() public {
        _add(address(new MockFeed(D8, _8dec(501_000000), block.timestamp)));
        _add(address(new MockFeed(D8, _8dec(499_000000), block.timestamp)));
        _add(address(new MockFeed(D8, _8dec(500_000000), block.timestamp)));
        assertEq(oracle.getPrice(), 500_000000); // middle of {499,500,501}
        (, uint8 tier, uint256 n,) = oracle.priceWithProvenance();
        assertEq(tier, oracle.TIER_QUORUM());
        assertEq(n, 3);
    }

    function test_quorum_divergence_fails_closed_to_admin() public {
        // {500, 600} → 20% spread ≫ 2% band → fail-closed; admin fresh → degrade to admin.
        _add(address(new MockFeed(D8, _8dec(500_000000), block.timestamp)));
        _add(address(new MockFeed(D8, _8dec(600_000000), block.timestamp)));
        assertEq(oracle.getPrice(), ADMIN_PRICE);
        (, uint8 tier, uint256 n,) = oracle.priceWithProvenance();
        assertEq(tier, oracle.TIER_ADMIN()); // never priced off the divergent feeds
        assertEq(n, 2);
    }

    function test_quorum_divergence_admin_stale_reverts() public {
        MockFeed a = new MockFeed(D8, _8dec(500_000000), block.timestamp);
        MockFeed b = new MockFeed(D8, _8dec(600_000000), block.timestamp);
        _add(address(a));
        _add(address(b));
        vm.warp(block.timestamp + 25 hours); // admin stale
        a.setUpdatedAt(block.timestamp);
        b.setUpdatedAt(block.timestamp);
        vm.expectRevert(PriceOracle.StalePrice.selector); // divergent + no fresh fallback → fail-closed
        oracle.getPrice();
        assertFalse(oracle.isFresh());
    }

    function test_stale_feed_drops_quorum_to_single() public {
        MockFeed a = new MockFeed(D8, _8dec(500_000000), block.timestamp);
        MockFeed b = new MockFeed(D8, _8dec(501_000000), block.timestamp);
        _add(address(a));
        _add(address(b));
        // Age b past the 1h feed heartbeat → only a is fresh → tier SINGLE.
        b.setUpdatedAt(block.timestamp - 2 hours);
        assertEq(oracle.getPrice(), 500_000000);
        (, uint8 tier, uint256 n,) = oracle.priceWithProvenance();
        assertEq(tier, oracle.TIER_SINGLE());
        assertEq(n, 1);
    }

    function test_reverting_feed_is_skipped() public {
        MockFeed a = new MockFeed(D8, _8dec(500_000000), block.timestamp);
        MockFeed b = new MockFeed(D8, _8dec(501_000000), block.timestamp);
        _add(address(a));
        _add(address(b));
        a.setRevert(true); // a bricks → only b fresh → SINGLE on b
        assertEq(oracle.getPrice(), 501_000000);
        (, uint8 tier, uint256 n,) = oracle.priceWithProvenance();
        assertEq(tier, oracle.TIER_SINGLE());
        assertEq(n, 1);
    }

    // ── Agreement band tuning ────────────────────────────────────────────

    function test_band_zero_requires_exact_agreement() public {
        vm.prank(owner);
        oracle.setQuorumBandBps(0);
        MockFeed a = new MockFeed(D8, _8dec(500_000000), block.timestamp);
        MockFeed b = new MockFeed(D8, _8dec(500_010000), block.timestamp); // 1 cent off
        _add(address(a));
        _add(address(b));
        // Not exactly equal → disagree → degrade to admin.
        assertEq(oracle.getPrice(), ADMIN_PRICE);
        // Now make them exactly equal → quorum.
        b.setAnswer(_8dec(500_000000));
        assertEq(oracle.getPrice(), 500_000000);
        (, uint8 tier,,) = oracle.priceWithProvenance();
        assertEq(tier, oracle.TIER_QUORUM());
    }

    // ── Feed-set management ──────────────────────────────────────────────

    function test_addFeed_rejects_zero_dup_and_bad_decimals() public {
        vm.prank(owner);
        vm.expectRevert(QuorumPriceOracle.ZeroFeed.selector);
        oracle.addFeed(address(0));

        MockFeed f = new MockFeed(D8, _8dec(500_000000), block.timestamp);
        _add(address(f));
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(QuorumPriceOracle.FeedAlreadyAdded.selector, address(f)));
        oracle.addFeed(address(f));

        MockFeed bad = new MockFeed(37, _8dec(500_000000), block.timestamp); // decimals > 36
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(PriceOracle.InvalidFeedDecimals.selector, uint8(37)));
        oracle.addFeed(address(bad));
    }

    function test_removeFeed_swap_pop() public {
        MockFeed a = new MockFeed(D8, _8dec(500_000000), block.timestamp);
        MockFeed b = new MockFeed(D8, _8dec(501_000000), block.timestamp);
        _add(address(a));
        _add(address(b));
        assertEq(oracle.feedCount(), 2);
        vm.prank(owner);
        oracle.removeFeed(0);
        assertEq(oracle.feedCount(), 1);
        // Remaining feed (b, swapped into slot 0) is the only fresh source → SINGLE.
        (, uint8 tier, uint256 n,) = oracle.priceWithProvenance();
        assertEq(tier, oracle.TIER_SINGLE());
        assertEq(n, 1);

        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(QuorumPriceOracle.IndexOutOfBounds.selector, uint256(5), uint256(1)));
        oracle.removeFeed(5);
    }

    function test_onlyOwner_guards() public {
        MockFeed f = new MockFeed(D8, _8dec(500_000000), block.timestamp);
        vm.prank(alice);
        vm.expectRevert();
        oracle.addFeed(address(f));
        vm.prank(alice);
        vm.expectRevert();
        oracle.setQuorumBandBps(100);
    }

    // ── Inherited single priceFeed is ignored by the quorum override ─────

    function test_inherited_priceFeed_ignored() public {
        // setPriceFeed wires the inherited single feed — the quorum override must NOT read it
        // (feeds are managed via addFeed). With no addFeed call, we stay tier ADMIN.
        MockFeed f = new MockFeed(D8, _8dec(700_000000), block.timestamp);
        vm.prank(owner);
        oracle.setPriceFeed(address(f));
        assertEq(oracle.getPrice(), ADMIN_PRICE); // priceFeed ignored
        (, uint8 tier, uint256 n,) = oracle.priceWithProvenance();
        assertEq(tier, oracle.TIER_ADMIN());
        assertEq(n, 0);
    }

    // ── Inherited admin write-path still works (the bounded fallback leg) ─

    function test_inherited_admin_setPrice_still_works() public {
        vm.warp(block.timestamp + 60); // clear the update cooldown
        vm.prank(owner);
        oracle.setPrice(505_000000); // within 20% deviation bound
        assertEq(oracle.getPrice(), 505_000000); // no feeds → admin tier reflects the new price
    }
}

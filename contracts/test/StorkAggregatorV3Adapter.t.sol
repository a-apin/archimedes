// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/StorkAggregatorV3Adapter.sol";
import "../src/PriceOracle.sol";

/// @dev Minimal mock of the Stork on-chain getter — settable per-id value + timestamp.
contract MockStork is IStorkTemporalNumericValueUnsafeGetter {
    mapping(bytes32 => TemporalNumericValue) internal _values;

    function set(bytes32 id, uint64 timestampNs, int192 quantizedValue) external {
        _values[id] = TemporalNumericValue(timestampNs, quantizedValue);
    }

    function getTemporalNumericValueUnsafeV1(bytes32 id) external view returns (TemporalNumericValue memory) {
        return _values[id];
    }
}

contract StorkAggregatorV3AdapterTest is Test {
    MockStork internal stork;
    StorkAggregatorV3Adapter internal adapter;

    bytes32 constant FEED_ID = keccak256("SPY/USD");
    // $392.60 at Stork's 18-decimal quantization (3.926e20) and PriceOracle's 6 (392_600_000).
    int192 constant STORK_392_60 = 392_600_000_000_000_000_000;
    uint256 constant EXPECTED_6DEC = 392_600_000;

    address constant OWNER = address(0x1);
    address constant ALICE = address(0x2);

    function setUp() public {
        vm.warp(1_000_000);
        stork = new MockStork();
        adapter = new StorkAggregatorV3Adapter(address(stork), FEED_ID);
    }

    function _setFresh(int192 value) internal {
        stork.set(FEED_ID, uint64(block.timestamp * 1e9), value);
    }

    // ── Adapter unit ──────────────────────────────────────────────

    function test_decimals_is_18() public view {
        assertEq(adapter.decimals(), 18);
    }

    function test_immutables() public view {
        assertEq(address(adapter.stork()), address(stork));
        assertEq(adapter.priceId(), FEED_ID);
    }

    function test_constructor_reverts_on_zero_stork() public {
        vm.expectRevert(StorkAggregatorV3Adapter.ZeroStork.selector);
        new StorkAggregatorV3Adapter(address(0), FEED_ID);
    }

    function test_constructor_reverts_on_zero_id() public {
        vm.expectRevert(StorkAggregatorV3Adapter.ZeroPriceId.selector);
        new StorkAggregatorV3Adapter(address(stork), bytes32(0));
    }

    function test_latestRoundData_maps_stork_value() public {
        uint64 tsNs = uint64(block.timestamp * 1e9);
        stork.set(FEED_ID, tsNs, STORK_392_60);
        (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound) =
            adapter.latestRoundData();
        assertEq(answer, int256(STORK_392_60));
        assertEq(updatedAt, block.timestamp); // ns / 1e9
        assertEq(startedAt, block.timestamp);
        assertEq(roundId, uint80(block.timestamp));
        assertEq(answeredInRound, roundId); // carry-over guard always satisfied
    }

    function test_latestRoundData_uninitialized_feed_is_incomplete_round() public view {
        // No value set → timestampNs 0 → updatedAt 0 (PriceOracle reads as incomplete round).
        (, int256 answer,, uint256 updatedAt,) = adapter.latestRoundData();
        assertEq(answer, 0);
        assertEq(updatedAt, 0);
    }

    // ── Integration with PriceOracle (#724 read path) ─────────────

    function _oracle(uint256 adminPrice) internal returns (PriceOracle o) {
        vm.prank(OWNER);
        o = new PriceOracle("SPY", adminPrice, OWNER);
        vm.prank(OWNER);
        o.setPriceFeed(address(adapter));
    }

    function test_setPriceFeed_caches_18_decimals() public {
        PriceOracle o = _oracle(EXPECTED_6DEC);
        assertEq(o.feedDecimals(), 18);
        assertEq(address(o.priceFeed()), address(adapter));
    }

    function test_oracle_reads_stork_via_adapter_scaled_to_6dec() public {
        PriceOracle o = _oracle(EXPECTED_6DEC);
        _setFresh(STORK_392_60);
        // 18-dec Stork $392.60 → 6-dec 392_600_000 through the adapter + PriceOracle.
        assertEq(o.getPrice(), EXPECTED_6DEC);
        assertTrue(o.isFresh());
    }

    function test_oracle_feed_precedence_in_band() public {
        // Admin $392.60; Stork reads $450 (in the 50% band) → feed wins.
        PriceOracle o = _oracle(EXPECTED_6DEC);
        _setFresh(450_000_000_000_000_000_000); // $450 @ 18 dec
        assertEq(o.getPrice(), 450_000_000);
    }

    function test_oracle_degrades_to_admin_on_stale_stork() public {
        PriceOracle o = _oracle(EXPECTED_6DEC);
        // Stork value observed now, then time advances past the feed heartbeat while the
        // admin reference stays fresh → PriceOracle degrades to admin (fail-soft).
        _setFresh(450_000_000_000_000_000_000);
        vm.warp(block.timestamp + o.feedStaleness() + 1);
        assertEq(o.getPrice(), EXPECTED_6DEC); // admin, not the stale feed
        assertTrue(o.isFresh());
    }

    function test_oracle_degrades_to_admin_on_negative_stork() public {
        PriceOracle o = _oracle(EXPECTED_6DEC);
        _setFresh(-1);
        assertEq(o.getPrice(), EXPECTED_6DEC);
    }

    function test_oracle_out_of_band_stork_degrades_to_admin() public {
        // Stork $5000 vs admin $392.60 — way outside the 50% band → degrade to admin.
        PriceOracle o = _oracle(EXPECTED_6DEC);
        _setFresh(5_000_000_000_000_000_000_000); // $5000 @ 18 dec
        assertEq(o.getPrice(), EXPECTED_6DEC);
    }
}

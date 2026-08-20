// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/adapters/PythAdapter.sol";

contract MockPyth is IPyth {
    int64 internal _price;
    int32 internal _expo;
    uint256 internal _publishTime;

    constructor(int64 price_, int32 expo_, uint256 publishTime_) {
        _price = price_;
        _expo = expo_;
        _publishTime = publishTime_;
    }

    function setPrice(int64 price_, int32 expo_, uint256 publishTime_) external {
        _price = price_;
        _expo = expo_;
        _publishTime = publishTime_;
    }

    function getPriceUnsafe(bytes32) external view override returns (Price memory) {
        return Price({
            price: _price,
            conf: 100,
            expo: _expo,
            publishTime: _publishTime
        });
    }
}

contract PythAdapterTest is Test {
    MockPyth public pyth;
    PythAdapter public adapter;
    bytes32 public constant FEED_ID = bytes32(uint256(1));

    function setUp() public {
        pyth = new MockPyth(50000000000, -8, block.timestamp); // $500.00 at -8 decimals
        adapter = new PythAdapter(address(pyth), FEED_ID);
    }

    function test_decimals() public view {
        assertEq(adapter.decimals(), 8);
    }

    // Constructor zero-guards — parity with the sibling StorkAggregatorV3Adapter
    // (#1153 review): a zero pyth address or feed id can only ever be a
    // deploy-script bug; revert at construction, not at first read.
    function test_revert_zero_pyth_address() public {
        vm.expectRevert(PythAdapter.ZeroPyth.selector);
        new PythAdapter(address(0), FEED_ID);
    }

    function test_revert_zero_feed_id() public {
        vm.expectRevert(PythAdapter.ZeroFeedId.selector);
        new PythAdapter(address(pyth), bytes32(0));
    }

    function test_latestRoundData_exponent_minus_8() public view {
        (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound) = adapter.latestRoundData();
        assertEq(roundId, 1);
        assertEq(answer, 50000000000); // 500 * 1e8 = 50,000,000,000
        assertEq(startedAt, block.timestamp);
        assertEq(updatedAt, block.timestamp);
        assertEq(answeredInRound, 1);
    }

    function test_latestRoundData_exponent_minus_5() public {
        pyth.setPrice(500000, -5, block.timestamp); // 5.00000 at -5 decimals
        (, int256 answer, , , ) = adapter.latestRoundData();
        assertEq(answer, 500000000); // 5 * 1e8 = 500,000,000
    }

    function test_latestRoundData_exponent_minus_10() public {
        pyth.setPrice(5000000000000, -10, block.timestamp); // 500.0000000000
        (, int256 answer, , , ) = adapter.latestRoundData();
        assertEq(answer, 50000000000); // 500 * 1e8
    }

    function test_latestRoundData_exponent_positive() public {
        pyth.setPrice(500, 2, block.timestamp); // 500 * 10^2 = 50,000
        (, int256 answer, , , ) = adapter.latestRoundData();
        assertEq(answer, 5000000000000); // 50,000 * 1e8 = 5,000,000,000,000
    }

    function test_latestRoundData_negative_price() public {
        pyth.setPrice(-500, 2, block.timestamp); // -500 * 10^2 = -50,000
        (, int256 answer, , , ) = adapter.latestRoundData();
        assertEq(answer, -5000000000000); // -50,000 * 1e8 = -5,000,000,000,000
    }

    /// @notice THE regression case (#1153 review): expo = 69 was the one SILENT
    ///         failure. 10**77 fits uint256 but exceeds int256.max, so the cast
    ///         reinterpreted the multiplier negative and (price=-1, expo=69)
    ///         returned large POSITIVE garbage — defeating PriceOracle's
    ///         answer <= 0 guard in the setMaxFeedDeviationBps(0) config.
    ///         Every larger exponent merely panicked; 69 lied. It must revert
    ///         with the typed error, never return.
    function test_revert_expo69_negative_price_no_positive_garbage() public {
        pyth.setPrice(-1, 69, block.timestamp);
        vm.expectRevert(abi.encodeWithSelector(PythAdapter.PriceExponentOutOfRange.selector, int32(69)));
        adapter.latestRoundData();
    }

    function test_expo_at_upper_bound_scales_and_preserves_sign() public {
        // expo = 49 is the provably-safe maximum: |int64| * 10**57 < int256.max.
        pyth.setPrice(-3, 49, block.timestamp);
        (, int256 answer,,,) = adapter.latestRoundData();
        assertEq(answer, int256(-3) * int256(10 ** 57));
        assertLt(answer, 0); // a negative price must never come out positive
    }

    function test_revert_just_above_upper_bound() public {
        pyth.setPrice(1, 50, block.timestamp);
        vm.expectRevert(abi.encodeWithSelector(PythAdapter.PriceExponentOutOfRange.selector, int32(50)));
        adapter.latestRoundData();
    }

    function test_expo_at_lower_bound_truncates_toward_zero() public {
        // expo = -77: divisor 10**69 dwarfs any int64 price — truncates to 0,
        // which downstream answer <= 0 guards reject HONESTLY (no fabrication).
        pyth.setPrice(type(int64).max, -77, block.timestamp);
        (, int256 answer,,,) = adapter.latestRoundData();
        assertEq(answer, 0);
    }

    function test_revert_just_below_lower_bound() public {
        pyth.setPrice(1, -78, block.timestamp);
        vm.expectRevert(abi.encodeWithSelector(PythAdapter.PriceExponentOutOfRange.selector, int32(-78)));
        adapter.latestRoundData();
    }
}

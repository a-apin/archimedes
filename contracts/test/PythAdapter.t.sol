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
}

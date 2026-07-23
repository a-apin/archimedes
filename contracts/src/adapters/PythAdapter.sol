// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "../interfaces/IAggregatorV3.sol";

interface IPyth {
    struct Price {
        int64 price;
        uint64 conf;
        int32 expo;
        uint256 publishTime;
    }

    function getPriceUnsafe(bytes32 id) external view returns (Price memory price);
}

/// @title PythAdapter
/// @notice AggregatorV3Interface adapter for Pyth Network price feeds on Arc testnet (#794).
contract PythAdapter is AggregatorV3Interface {
    IPyth public immutable pyth;
    bytes32 public immutable feedId;

    constructor(address _pyth, bytes32 _feedId) {
        pyth = IPyth(_pyth);
        feedId = _feedId;
    }

    function decimals() external pure override returns (uint8) {
        return 8;
    }

    function latestRoundData()
        external
        view
        override
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        IPyth.Price memory p = pyth.getPriceUnsafe(feedId);
        int256 scaledPrice;
        if (p.expo < -8) {
            uint256 divisor = 10 ** uint256(int256(-p.expo) - 8);
            scaledPrice = int256(p.price) / int256(divisor);
        } else {
            uint256 multiplier = 10 ** uint256(int256(8 + p.expo));
            scaledPrice = int256(p.price) * int256(multiplier);
        }
        return (1, scaledPrice, p.publishTime, p.publishTime, 1);
    }
}

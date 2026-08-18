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

    /// @notice The feed reported an exponent outside the provably-safe scaling
    ///         window. Real Pyth feeds publish expo near -8; anything outside
    ///         [-77, 49] cannot be scaled here without either a silent int256
    ///         sign reinterpretation or a checked-arithmetic panic, so the
    ///         adapter refuses loudly instead.
    error PriceExponentOutOfRange(int32 expo);

    /// @dev Bounds for the scaling arithmetic below, derived — not vibes:
    ///      UPPER (49): the multiply path computes price * 10**(8+expo).
    ///        |price| <= int64.max ≈ 9.22e18, and int256.max ≈ 5.78e76, so the
    ///        product provably fits iff 10**(8+expo) <= 5.78e76 / 9.22e18
    ///        ≈ 6.27e57, i.e. 8+expo <= 57 → expo <= 49. The previously
    ///        unguarded landmine sat at exactly expo = 69: 10**77 FITS uint256
    ///        (max ≈ 1.16e77) but exceeds int256.max, so int256(multiplier)
    ///        silently reinterpreted NEGATIVE — and (price = -1, expo = 69)
    ///        produced large POSITIVE garbage that defeats PriceOracle's
    ///        answer <= 0 guard in the documented setMaxFeedDeviationBps(0)
    ///        config. Every exponent above 69 merely panicked; 69 lied.
    ///      LOWER (-77): the divide path computes 10**(-expo-8), which must
    ///        itself fit int256: -expo-8 <= 76 → expo >= -84; -77 adds margin
    ///        and division then only truncates toward zero (a vanishingly
    ///        small price reads 0, which the downstream answer <= 0 guard
    ///        rejects honestly).
    int32 internal constant MIN_EXPO = -77;
    int32 internal constant MAX_EXPO = 49;

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
        if (p.expo < MIN_EXPO || p.expo > MAX_EXPO) {
            revert PriceExponentOutOfRange(p.expo);
        }
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

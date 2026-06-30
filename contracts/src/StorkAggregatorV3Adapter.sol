// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal read interface for the Stork on-chain price contract (live on Arc
///         testnet at 0xacC0a0cF13571d30B4b8637996F5D6D774d4fd62, verified #794). Stork
///         stores a per-asset temporal numeric value keyed by a bytes32 feed id; the
///         "Unsafe" getter returns the stored value WITHOUT reverting on staleness —
///         staleness is enforced downstream by the consuming PriceOracle, which degrades
///         to the admin-fed price on a stale/invalid feed (#724 design).
interface IStorkTemporalNumericValueUnsafeGetter {
    struct TemporalNumericValue {
        uint64 timestampNs; // observation time, NANOSECONDS
        int192 quantizedValue; // value scaled to 18 decimals (Stork convention)
    }

    function getTemporalNumericValueUnsafeV1(bytes32 id) external view returns (TemporalNumericValue memory value);
}

/// @notice Chainlink AggregatorV3 signature — function selectors are byte-identical to the
///         `AggregatorV3Interface` PriceOracle.sol declares locally, so this adapter is a
///         drop-in `setPriceFeed` target. Named `IAggregatorV3` (not `AggregatorV3Interface`)
///         only to avoid an identifier clash when a consumer imports both files.
/// @dev    ⚠️ DUPLICATE (tracked): this restates PriceOracle.sol's local `AggregatorV3Interface`.
///         The clean fix is one shared `src/interfaces/IAggregatorV3.sol` imported by BOTH —
///         deferred to the #588 redeploy because it edits the deployed PriceOracle.sol
///         (bytecode-neutral, but contract-review-grade). See the consolidation follow-up issue.
interface IAggregatorV3 {
    function decimals() external view returns (uint8);

    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound);
}

/// @title StorkAggregatorV3Adapter
/// @notice Adapts ONE Stork price feed to Chainlink's AggregatorV3Interface so the existing
///         #724 PriceOracle (Chainlink-first, degrade-to-admin) can consume Stork on Arc —
///         where Chainlink has no Data Feeds (#794). One adapter instance per asset (one
///         Stork feed id), pointed at by `PriceOracle.setPriceFeed(adapter)`.
/// @dev    Thin, view-only, holds no funds. Stork quantizes to 18 decimals; the adapter
///         reports `decimals() = 18` and PriceOracle rescales to its 6-decimal convention.
///         The adapter does NOT enforce staleness itself (it uses the Unsafe getter) —
///         PriceOracle owns staleness / round-completeness / sanity-band validation and
///         degrades to the admin price on ANY feed failure, so a frozen or garbled Stork
///         feed can never brick a vault read. It is a real decentralized primary sitting in
///         front of the admin safety net, not a replacement for it.
///
///         ⚠️ Funds-adjacent (feeds vault collateral math via PriceOracle). Contract work →
///         Dan owns + deploys; Bogdan reviews (#794).
contract StorkAggregatorV3Adapter is IAggregatorV3 {
    /// @notice The Stork on-chain contract this adapter reads from.
    IStorkTemporalNumericValueUnsafeGetter public immutable stork;

    /// @notice The Stork feed id (asset) this adapter exposes.
    bytes32 public immutable priceId;

    /// @notice Stork quantizes values to 18 decimals; PriceOracle rescales to 6.
    uint8 public constant STORK_DECIMALS = 18;

    error ZeroStork();
    error ZeroPriceId();

    constructor(address _stork, bytes32 _priceId) {
        if (_stork == address(0)) revert ZeroStork();
        if (_priceId == bytes32(0)) revert ZeroPriceId();
        stork = IStorkTemporalNumericValueUnsafeGetter(_stork);
        priceId = _priceId;
    }

    /// @inheritdoc IAggregatorV3
    function decimals() external pure returns (uint8) {
        return STORK_DECIMALS;
    }

    /// @inheritdoc IAggregatorV3
    /// @dev Maps Stork's (timestampNs, quantizedValue) → Chainlink round data:
    ///        answer              = quantizedValue (18-dec; PriceOracle rescales to 6)
    ///        updatedAt/startedAt = timestampNs / 1e9 (ns → s; PriceOracle's staleness key)
    ///        roundId = answeredInRound = the second-resolution timestamp. Stork has no round
    ///          concept, so a monotonic-by-time id keeps PriceOracle's carry-over guard
    ///          (`answeredInRound >= roundId`) satisfied while advancing on each update.
    ///      An uninitialized feed (timestampNs == 0) yields updatedAt == 0, which PriceOracle
    ///      reads as an incomplete round and degrades to admin — the intended fail-soft.
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        IStorkTemporalNumericValueUnsafeGetter.TemporalNumericValue memory v =
            stork.getTemporalNumericValueUnsafeV1(priceId);
        uint256 tsSeconds = uint256(v.timestampNs) / 1e9;
        answer = int256(v.quantizedValue);
        startedAt = tsSeconds;
        updatedAt = tsSeconds;
        roundId = uint80(tsSeconds);
        answeredInRound = uint80(tsSeconds);
    }
}

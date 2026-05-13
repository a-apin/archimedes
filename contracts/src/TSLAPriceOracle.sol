// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/Ownable.sol";

/// @title TSLAPriceOracle
/// @notice Admin-updatable price oracle for TSLA on Arc testnet.
///         In production, this would be replaced by a Chainlink feed or
///         Ondo's SyntheticSharesOracle. For the hackathon, the backend
///         agent pushes price updates periodically.
contract TSLAPriceOracle is Ownable {
    /// @notice Price of 1 TSLA share in USDC (6 decimals).
    ///         e.g. $392.60 → 392600000
    uint256 public price;

    /// @notice Timestamp of the last price update
    uint256 public lastUpdated;

    /// @notice Maximum age of a price before it's considered stale (1 hour)
    uint256 public constant MAX_STALENESS = 1 hours;

    /// @notice Emitted when price is updated
    event PriceUpdated(uint256 oldPrice, uint256 newPrice, uint256 timestamp);

    error StalePrice();

    constructor(uint256 _initialPrice) Ownable(msg.sender) {
        price = _initialPrice;
        lastUpdated = block.timestamp;
        emit PriceUpdated(0, _initialPrice, block.timestamp);
    }

    /// @notice Update the TSLA price (owner only — the backend agent)
    function setPrice(uint256 _newPrice) external onlyOwner {
        uint256 oldPrice = price;
        price = _newPrice;
        lastUpdated = block.timestamp;
        emit PriceUpdated(oldPrice, _newPrice, block.timestamp);
    }

    /// @notice Get the current price, reverting if stale
    function getPrice() external view returns (uint256) {
        if (block.timestamp > lastUpdated + MAX_STALENESS) {
            revert StalePrice();
        }
        return price;
    }

    /// @notice Check if the price is fresh without reverting
    function isFresh() external view returns (bool) {
        return block.timestamp <= lastUpdated + MAX_STALENESS;
    }
}

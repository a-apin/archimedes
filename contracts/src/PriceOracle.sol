// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/Ownable.sol";

/// @title PriceOracle
/// @notice Admin-updatable price oracle for any asset on Arc testnet.
///         In production, replace with Chainlink feed. For the hackathon,
///         the backend agent pushes price updates periodically.
contract PriceOracle is Ownable {
    /// @notice Asset price in USDC (6 decimals). e.g. $392.60 → 392600000
    uint256 public price;

    /// @notice Human-readable label (e.g. "TSLA", "NVDA", "SPY")
    string public symbol;

    /// @notice Timestamp of the last price update
    uint256 public lastUpdated;

    /// @notice Maximum age before stale (1 hour)
    uint256 public constant MAX_STALENESS = 1 hours;

    event PriceUpdated(uint256 oldPrice, uint256 newPrice, uint256 timestamp);

    error StalePrice();

    constructor(string memory _symbol, uint256 _initialPrice, address _owner) Ownable(_owner) {
        symbol = _symbol;
        price = _initialPrice;
        lastUpdated = block.timestamp;
        emit PriceUpdated(0, _initialPrice, block.timestamp);
    }

    function setPrice(uint256 _newPrice) external onlyOwner {
        uint256 oldPrice = price;
        price = _newPrice;
        lastUpdated = block.timestamp;
        emit PriceUpdated(oldPrice, _newPrice, block.timestamp);
    }

    function getPrice() external view returns (uint256) {
        if (block.timestamp > lastUpdated + MAX_STALENESS) revert StalePrice();
        return price;
    }

    function isFresh() external view returns (bool) {
        return block.timestamp <= lastUpdated + MAX_STALENESS;
    }
}

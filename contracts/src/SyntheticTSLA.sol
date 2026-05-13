// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// @title SyntheticTSLA
/// @notice ERC-20 token representing synthetic TSLA exposure on Arc.
///         Each sTSLA is backed by USDC collateral in the vault at oracle price.
///         Minting and burning is restricted to the vault contract.
contract SyntheticTSLA is ERC20, Ownable {
    /// @notice The vault contract allowed to mint/burn
    address public vault;

    error NotVault();

    modifier onlyVault() {
        if (msg.sender != vault) revert NotVault();
        _;
    }

    constructor(address _owner) ERC20("Synthetic TSLA", "sTSLA") Ownable(_owner) {}

    /// @notice Set the vault address (owner only, once)
    function setVault(address _vault) external onlyOwner {
        vault = _vault;
    }

    /// @notice Mint sTSLA to a user (vault only)
    function mint(address to, uint256 amount) external onlyVault {
        _mint(to, amount);
    }

    /// @notice Burn sTSLA from a user (vault only)
    function burn(address from, uint256 amount) external onlyVault {
        _burn(from, amount);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// @title SyntheticToken
/// @notice Generic ERC-20 synthetic asset token.
///         Minting and burning is restricted to the vault contract.
contract SyntheticToken is ERC20, Ownable {
    address public vault;

    error NotVault();
    error VaultAlreadySet();
    error ZeroVault();

    modifier onlyVault() {
        if (msg.sender != vault) revert NotVault();
        _;
    }

    constructor(string memory _name, string memory _symbol, address _owner)
        ERC20(_name, _symbol)
        Ownable(_owner)
    {}

    /// @notice Set the vault address (owner only). Called once after the vault
    ///         deploys; immutable afterwards. A mutable pointer would let the
    ///         owner re-point `vault` at a contract they control, mint unbacked
    ///         synth, and burn it against the real vault to drain user USDC —
    ///         breaking the non-custodial guarantee (issue #904). Vault
    ///         evolution requires deploying a new token+vault pair via the
    ///         factory, never re-pointing a live token.
    function setVault(address _vault) external onlyOwner {
        if (vault != address(0)) revert VaultAlreadySet();
        if (_vault == address(0)) revert ZeroVault();
        vault = _vault;
    }

    function mint(address to, uint256 amount) external onlyVault {
        _mint(to, amount);
    }

    function burn(address from, uint256 amount) external onlyVault {
        _burn(from, amount);
    }
}

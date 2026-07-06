// SPDX-License-Identifier: Unlicense
pragma solidity ^0.8.24;

/// @title IAssetRegistry
/// @notice On-chain registry of all assets and vaults in the Archimedes ecosystem.
///         Evolves from StrategyRegistry in original design.md.
/// @dev Owner: Chuan (implements). Consumers: Daniel (frontend reads leaderboard),
///      Marten (registers new assets/vaults after deployment).
interface IAssetRegistry {
    // ── Events ───────────────────────────────────────────────
    event SyntheticRegistered(address indexed token, string symbol);
    event BridgedRegistered(address indexed token, uint256 sourceChainId);
    event VaultRegistered(address indexed vault, uint8 tier, address creator);
    event VaultMetricsUpdated(address indexed vault, uint256 aum, uint256 timestamp);
    event OracleRegistered(address indexed token, address indexed oracle);

    // ── Synthetic Assets ─────────────────────────────────────
    /// @notice Register a synthetic asset.
    /// @param token Synthetic token address
    /// @param oracleId Identifier linking to the oracle (e.g. hash of "TSLA/USD")
    /// @param metadata ABI-encoded metadata (name, symbol, decimals, etc.)
    function registerSynthetic(
        address token,
        bytes32 oracleId,
        bytes calldata metadata
    ) external;

    /// @notice Get metadata for a synthetic asset.
    function getSynthetic(address token) external view returns (bytes memory metadata);

    /// @notice List all registered synthetic token addresses.
    function getAllSynthetics() external view returns (address[] memory);

    // ── Oracle Allowlist ─────────────────────────────────────
    /// @notice Register (or de-register) the canonical oracle for a token.
    ///         Owner-curated allowlist consumed by Vault.setTokenOraclesFromRegistry,
    ///         so a manager can wire only known-good oracles for new assets.
    /// @param token Token whose NAV-pricing oracle is being set
    /// @param oracle Oracle address; address(0) de-registers the token
    function setRegisteredOracle(address token, address oracle) external;

    /// @notice Get the registered oracle for a token (address(0) if unregistered).
    function registeredOracle(address token) external view returns (address);

    // ── Bridged Assets ───────────────────────────────────────
    /// @notice Register a bridged asset from another chain.
    function registerBridged(
        address token,
        uint256 sourceChainId,
        address sourceToken
    ) external;

    // ── Vaults ───────────────────────────────────────────────
    /// @notice Register a vault in the ecosystem.
    /// @param vault Vault contract address
    /// @param tier 1 = Archimedes verified, 2 = community
    /// @param metadata ABI-encoded metadata (name, symbol, fees, etc.)
    function registerVault(
        address vault,
        uint8 tier,
        bytes calldata metadata
    ) external;

    /// @notice Update vault performance metrics (called periodically by backend).
    /// @param vault Vault address
    /// @param metrics ABI-encoded metrics (AUM, returns, Sharpe, etc.)
    function updateVaultMetrics(
        address vault,
        bytes calldata metrics
    ) external;

    /// @notice Get the top vaults by AUM for a given tier, paginated (#927).
    /// @param tier 1 or 2 (0 = all tiers)
    /// @param offset Number of top-ranked vaults to skip (page start)
    /// @param limit Max results to return; 0 = all from offset onward
    /// @return vaults Array of vault addresses sorted by AUM descending
    function getLeaderboard(
        uint8 tier,
        uint256 offset,
        uint256 limit
    ) external view returns (address[] memory vaults);

    /// @notice Total number of registered vaults.
    function vaultCount() external view returns (uint256);
}

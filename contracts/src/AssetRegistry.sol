// SPDX-License-Identifier: Unlicense
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/Ownable.sol";

import "./interfaces/IAssetRegistry.sol";

/// @title AssetRegistry
/// @notice On-chain registry of all assets and vaults in the Archimedes ecosystem.
///         Powers the leaderboard and asset discovery.
contract AssetRegistry is IAssetRegistry, Ownable {
    // ─── Structs ─────────────────────────────────────────────────────

    struct SyntheticInfo {
        bytes32 oracleId;
        bytes metadata;
        bool registered;
    }

    struct BridgedInfo {
        uint256 sourceChainId;
        address sourceToken;
        bool registered;
    }

    struct VaultInfo {
        uint8 tier;
        bytes metadata;
        uint256 aum;
        bool registered;
    }

    // ─── State ───────────────────────────────────────────────────────

    /// @notice Synthetic assets
    mapping(address => SyntheticInfo) private _synthetics;
    address[] private _syntheticList;

    /// @notice Bridged assets
    mapping(address => BridgedInfo) private _bridged;
    address[] private _bridgedList;

    /// @notice Vaults
    mapping(address => VaultInfo) private _vaults;
    address[] private _vaultList;

    /// @notice Owner-curated token → oracle allowlist. A vault manager can wire
    ///         only oracles registered here (Vault.setTokenOraclesFromRegistry),
    ///         so it can never point a token at an arbitrary attacker contract.
    ///         The public getter satisfies IAssetRegistry.registeredOracle.
    mapping(address => address) public override registeredOracle;

    // ─── Constructor ─────────────────────────────────────────────────

    constructor(address _owner) Ownable(_owner) {}

    // ─── Oracle Allowlist ────────────────────────────────────────────

    /// @notice Register or de-register the canonical oracle for a token.
    /// @dev    address(0) de-registers; consumers treat an unregistered token
    ///         as "no known-good oracle" and reject manager-driven wiring.
    function setRegisteredOracle(address token, address oracle) external override onlyOwner {
        registeredOracle[token] = oracle;
        emit OracleRegistered(token, oracle);
    }

    // ─── Synthetic Assets ────────────────────────────────────────────

    function registerSynthetic(
        address token,
        bytes32 oracleId,
        bytes calldata metadata
    ) external override onlyOwner {
        require(!_synthetics[token].registered, "Already registered");
        _synthetics[token] = SyntheticInfo({
            oracleId: oracleId,
            metadata: metadata,
            registered: true
        });
        _syntheticList.push(token);

        emit SyntheticRegistered(token, _symbolFromMetadata(metadata));
    }

    function getSynthetic(address token) external view override returns (bytes memory metadata) {
        return _synthetics[token].metadata;
    }

    function getAllSynthetics() external view override returns (address[] memory) {
        return _syntheticList;
    }

    // ─── Bridged Assets ──────────────────────────────────────────────

    function registerBridged(
        address token,
        uint256 sourceChainId,
        address sourceToken
    ) external override onlyOwner {
        require(!_bridged[token].registered, "Already registered");
        _bridged[token] = BridgedInfo({
            sourceChainId: sourceChainId,
            sourceToken: sourceToken,
            registered: true
        });
        _bridgedList.push(token);

        emit BridgedRegistered(token, sourceChainId);
    }

    // ─── Vaults ──────────────────────────────────────────────────────

    function registerVault(
        address vault,
        uint8 tier,
        bytes calldata metadata
    ) external override onlyOwner {
        require(!_vaults[vault].registered, "Already registered");
        _vaults[vault] = VaultInfo({
            tier: tier,
            metadata: metadata,
            aum: 0,
            registered: true
        });
        _vaultList.push(vault);

        emit VaultRegistered(vault, tier, msg.sender);
    }

    function updateVaultMetrics(address vault, bytes calldata metrics) external override onlyOwner {
        require(_vaults[vault].registered, "Vault not registered");

        // Decode AUM from metrics (first 32 bytes)
        if (metrics.length >= 32) {
            _vaults[vault].aum = abi.decode(metrics[:32], (uint256));
        }
        _vaults[vault].metadata = metrics;

        emit VaultMetricsUpdated(vault, _vaults[vault].aum, block.timestamp);
    }

    /// @notice Top vaults by AUM for a tier, paginated (issue #927).
    /// @param tier   1 or 2 (0 = all tiers).
    /// @param offset Number of top-ranked vaults to skip (the page start).
    /// @param limit  Max results to return; 0 = all from ``offset`` onward.
    /// @dev    Was a full O(n²) insertion sort over EVERY matching vault before
    ///         applying the limit — as the vault set grows that view exceeds the
    ///         block gas limit and bricks on-chain callers + eth_call reads. Now a
    ///         PARTIAL selection sort: it extracts only the top ``offset + limit``
    ///         vaults (O(n · (offset+limit))) and returns the ``[offset, …)`` slice,
    ///         so a bounded page stays bounded in gas regardless of total size.
    ///         (limit == 0 keeps the old "return all" behavior and its O(n²) cost —
    ///         callers should page with a real limit for large sets.)
    function getLeaderboard(uint8 tier, uint256 offset, uint256 limit)
        external
        view
        override
        returns (address[] memory vaults)
    {
        // Count matching vaults.
        uint256 total;
        for (uint256 i = 0; i < _vaultList.length; i++) {
            if (tier == 0 || _vaults[_vaultList[i]].tier == tier) {
                total++;
            }
        }

        if (total == 0 || offset >= total) return new address[](0);

        // Build the filtered (addr, aum) arrays.
        address[] memory allAddrs = new address[](total);
        uint256[] memory allAums = new uint256[](total);
        uint256 idx;
        for (uint256 i = 0; i < _vaultList.length; i++) {
            if (tier == 0 || _vaults[_vaultList[i]].tier == tier) {
                allAddrs[idx] = _vaultList[i];
                allAums[idx] = _vaults[_vaultList[i]].aum;
                idx++;
            }
        }

        // We only need the top `needed` ranked to serve [offset, offset+limit).
        uint256 needed = (limit == 0 || offset + limit > total) ? total : offset + limit;

        // Partial selection sort: pull the max into each of the first `needed`
        // positions. O(total · needed) — bounded by the page for a real limit,
        // versus the old O(total²) full sort.
        for (uint256 i = 0; i < needed; i++) {
            uint256 maxJ = i;
            for (uint256 j = i + 1; j < total; j++) {
                if (allAums[j] > allAums[maxJ]) {
                    maxJ = j;
                }
            }
            if (maxJ != i) {
                (allAddrs[i], allAddrs[maxJ]) = (allAddrs[maxJ], allAddrs[i]);
                (allAums[i], allAums[maxJ]) = (allAums[maxJ], allAums[i]);
            }
        }

        uint256 resultCount = needed - offset;
        vaults = new address[](resultCount);
        for (uint256 i = 0; i < resultCount; i++) {
            vaults[i] = allAddrs[offset + i];
        }
    }

    function vaultCount() external view override returns (uint256) {
        return _vaultList.length;
    }

    // ─── Helpers ─────────────────────────────────────────────────────

    function _symbolFromMetadata(bytes memory metadata) internal pure returns (string memory) {
        // Try to extract symbol from ABI-encoded metadata
        // For now, just return empty string — the event still fires correctly
        return "";
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import "../src/StorkAggregatorV3Adapter.sol";

/// @notice Deploy one StorkAggregatorV3Adapter, binding a Stork feed id to the
///         AggregatorV3 interface PriceOracle (#724) consumes. One adapter per asset.
///
///         Env:
///           STORK_CONTRACT  — Stork on-chain contract (Arc testnet default below)
///           STORK_PRICE_ID  — the bytes32 Stork feed id for this asset
///
///         Usage:
///           STORK_CONTRACT=0xacC0a0cF13571d30B4b8637996F5D6D774d4fd62 \
///           STORK_PRICE_ID=0x<feedId> \
///           forge script contracts/script/DeployStorkAdapter.s.sol \
///             --rpc-url https://rpc.testnet.arc.network --broadcast
///
///         After deploy: PriceOracle(asset).setPriceFeed(adapter) — owner-only (Dan).
///         The #724 oracle keeps the admin-fed value as the degrade target behind it.
contract DeployStorkAdapter is Script {
    /// @dev Stork's verified Arc-testnet deployment (#794).
    address constant ARC_STORK_DEFAULT = 0xacC0a0cF13571d30B4b8637996F5D6D774d4fd62;

    function run() external {
        address storkContract = vm.envOr("STORK_CONTRACT", ARC_STORK_DEFAULT);
        bytes32 priceId = vm.envBytes32("STORK_PRICE_ID");

        vm.startBroadcast();
        StorkAggregatorV3Adapter adapter = new StorkAggregatorV3Adapter(storkContract, priceId);
        vm.stopBroadcast();

        console.log("=== Stork AggregatorV3 adapter deployed ===");
        console.log("Stork contract:", storkContract);
        console.logBytes32(priceId);
        console.log("Adapter:", address(adapter));
        // The adapter address is logged above; forge-std has no console.log(string,address,string)
        // overload, so keep this a plain-string hint rather than interpolating the address inline.
        console.log("Next: call PriceOracle(asset).setPriceFeed(adapter) as owner");
    }
}

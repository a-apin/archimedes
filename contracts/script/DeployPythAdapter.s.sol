// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import "../src/adapters/PythAdapter.sol";

/// @notice Deploy one PythAdapter, binding a Pyth price-feed id to the
///         AggregatorV3 interface PriceOracle (#724) consumes. One adapter per asset.
///         Mirrors DeployStorkAdapter.s.sol (#1153 review parity).
///
///         Env:
///           PYTH_CONTRACT — the Pyth contract on the target chain. REQUIRED: unlike
///                           Stork (verified on Arc testnet on-chain, #794), no Pyth
///                           deployment on Arc has been verified yet, so there is no
///                           default to bake in — never guess a funds-adjacent address.
///           PYTH_PRICE_ID — the bytes32 Pyth price-feed id for this asset
///
///         Usage:
///           PYTH_CONTRACT=0x<pyth> PYTH_PRICE_ID=0x<feedId> \
///           forge script contracts/script/DeployPythAdapter.s.sol \
///             --rpc-url https://rpc.testnet.arc.network --broadcast
///
///         After deploy: PriceOracle(asset).setPriceFeed(adapter) — owner-only (Dan).
///         The #724 oracle keeps the admin-fed value as the degrade target behind it.
contract DeployPythAdapter is Script {
    function run() external {
        address pythContract = vm.envAddress("PYTH_CONTRACT");
        bytes32 priceId = vm.envBytes32("PYTH_PRICE_ID");

        vm.startBroadcast();
        PythAdapter adapter = new PythAdapter(pythContract, priceId);
        vm.stopBroadcast();

        console.log("=== Pyth AggregatorV3 adapter deployed ===");
        console.log("Pyth contract:", pythContract);
        console.logBytes32(priceId);
        console.log("Adapter:", address(adapter));
        console.log("Next: call PriceOracle(asset).setPriceFeed(adapter) as owner");
    }
}

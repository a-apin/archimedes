// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import "../src/PriceOracle.sol";
import "../src/SyntheticToken.sol";
import "../src/SyntheticVault.sol";

/// @notice Deploy all contracts to Arc testnet.
///         Usage:
///           forge script script/Deploy.s.sol --rpc-url arc-testnet --broadcast
///
///         Required env vars:
///           OWNER_ADDRESS    — address that will own the contracts
///           DEPLOYER_KEY     — private key of the deployer
///
///         Arc Testnet USDC: 0x3600000000000000000000000000000000000000
contract DeployScript is Script {
    // Arc Testnet USDC (ERC-20 interface)
    address constant USDC_ARC_TESTNET = 0x3600000000000000000000000000000000000000;

    // Initial TSLA price: $392.60 (6 decimals)
    uint256 constant INITIAL_TSLA_PRICE = 392_600_000;

    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_KEY");
        address owner = vm.envAddress("OWNER_ADDRESS");

        vm.startBroadcast(deployerKey);

        // 1. Deploy oracle with initial price
        PriceOracle oracle = new PriceOracle("TSLA", INITIAL_TSLA_PRICE, msg.sender);
        console.log("PriceOracle deployed:", address(oracle));

        // 2. Deploy synthetic TSLA token
        SyntheticToken sTSLA = new SyntheticToken("Synthetic TSLA", "sTSLA", owner);
        console.log("SyntheticToken (sTSLA) deployed:", address(sTSLA));

        // 3. Deploy vault
        SyntheticVault vault = new SyntheticVault(
            USDC_ARC_TESTNET,
            address(sTSLA),
            address(oracle),
            owner
        );
        console.log("SyntheticVault deployed:", address(vault));

        // 4. Set vault as the only minter/burner of sTSLA
        if (owner == vm.addr(deployerKey)) {
            sTSLA.setVault(address(vault));
            console.log("sTSLA vault set to:", address(vault));
        } else {
            console.log("!!! Call sTSLA.setVault(%s) as owner %s !!!", address(vault), owner);
        }

        vm.stopBroadcast();

        // Summary
        console.log("");
        console.log("=== Deployment Summary ===");
        console.log("USDC (existing):   ", USDC_ARC_TESTNET);
        console.log("PriceOracle:       ", address(oracle));
        console.log("SyntheticToken:    ", address(sTSLA));
        console.log("SyntheticVault:    ", address(vault));
        console.log("Owner:             ", owner);
        console.log("Initial Price:     ", INITIAL_TSLA_PRICE);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import "../src/RevenueSplit.sol";

/// @notice Deploy RevenueSplit.sol to Arc testnet.
///         Usage:
///           forge script script/DeployRevenueSplit.s.sol --rpc-url arc-testnet --broadcast
///
///         Required env vars:
///           DEPLOYER_KEY      — private key of the deployer
///           USDC_ADDRESS      — USDC token address on Arc (settlement asset)
///           CREATOR_ADDRESS   — address that receives the 90% creator share
///           PLATFORM_ADDRESS  — address that receives the 10% platform share
///
///         After deploy, copy the printed address into .env as REVENUE_SPLIT_ADDRESS
///         so the x402 middleware names it in the PAYMENT-REQUIRED header.
contract DeployRevenueSplitScript is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_KEY");
        address usdc = vm.envAddress("USDC_ADDRESS");
        address creator = vm.envAddress("CREATOR_ADDRESS");
        address platform = vm.envAddress("PLATFORM_ADDRESS");

        vm.startBroadcast(deployerKey);

        RevenueSplit splitter = new RevenueSplit(usdc, creator, platform);
        console.log("RevenueSplit deployed at:", address(splitter));

        vm.stopBroadcast();

        console.log("");
        console.log("=== Deployment Summary ===");
        console.log("RevenueSplit:", address(splitter));
        console.log("USDC:        ", usdc);
        console.log("Creator (90%):", creator);
        console.log("Platform (10%):", platform);
        console.log("");
        console.log("Add to .env:");
        console.log("  REVENUE_SPLIT_ADDRESS=", address(splitter));
    }
}

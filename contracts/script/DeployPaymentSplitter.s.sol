// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import {PaymentSplitter} from "../src/PaymentSplitter.sol";

/// @notice Deploy PaymentSplitter (Solidity) for the marketplace.
///
///         Subscriptions are off-chain as of P7 — PaymentSplitter is the
///         only marketplace contract that runs on-chain.
///
///         Usage:
///           forge script contracts/script/DeployPaymentSplitter.s.sol \
///             --rpc-url <RPC> --broadcast
contract DeployPaymentSplitter is Script {
    function run() external {
        address usdc = vm.envAddress("USDC_ADDRESS");
        // Note: PLATFORM_WALLET and FLAT_FEE_PER_ACTION are NOT constructor
        // parameters. The platform wallet is set per-pool via createPool() at
        // publish time — there is no global contract-level config for it.

        vm.startBroadcast();

        console.log("=== Deploying PaymentSplitter ===");
        console.log("USDC:", usdc);

        // Deploy PaymentSplitter via native Solidity constructor.
        PaymentSplitter splitter = new PaymentSplitter(usdc);
        require(address(splitter.usdc()) == usdc, "usdc mismatch");
        console.log("PaymentSplitter:", address(splitter));

        vm.stopBroadcast();

        console.log("");
        console.log("=== Deployment Complete ===");
        console.log("ARC_PAYMENT_SPLITTER_ADDRESS=", address(splitter));
    }
}


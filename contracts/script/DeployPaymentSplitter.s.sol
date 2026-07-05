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
        address platformWallet = vm.envAddress("PLATFORM_WALLET");
        uint256 flatFee = vm.envUint("FLAT_FEE_PER_ACTION");

        vm.startBroadcast();

        console.log("=== Deploying PaymentSplitter ===");
        console.log("USDC:", usdc);
        console.log("Platform Wallet:", platformWallet);
        console.log("Flat Fee Per Action:", flatFee);

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


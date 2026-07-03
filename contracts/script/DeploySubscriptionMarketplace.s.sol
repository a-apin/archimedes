// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";

/// @notice Deploy PaymentSplitter (Vyper contract) for the marketplace.
///
///         Subscriptions are off-chain as of P7 — PaymentSplitter is the
///         only marketplace contract that runs on-chain.
///
///         Prerequisites:
///         1. Compile Vyper contract to get bytecode:
///            vyper contracts/vyper/PaymentSplitter.vy -f bytecode > contracts/abis/PaymentSplitter.bin
///         2. Set env vars:
///            USDC_ADDRESS, PLATFORM_WALLET, FLAT_FEE_PER_ACTION
///
///         Usage:
///           source <(python3 -c "
///             import os; bs = open('contracts/abis/PaymentSplitter.bin').read().strip();
///             print(f'export PAYMENT_SPLITTER_BYTECODE={bs}')
///           ")
///           forge script contracts/script/DeploySubscriptionMarketplace.s.sol \
///             --rpc-url <RPC> --broadcast
contract DeploySubscriptionMarketplace is Script {
    function run() external {
        address usdc = vm.envAddress("USDC_ADDRESS");
        address platformWallet = vm.envAddress("PLATFORM_WALLET");
        uint256 flatFee = vm.envUint("FLAT_FEE_PER_ACTION");

        bytes memory paymentSplitterCode = vm.envBytes("PAYMENT_SPLITTER_BYTECODE");

        vm.startBroadcast();

        console.log("=== Deploying PaymentSplitter ===");
        console.log("USDC:", usdc);
        console.log("Platform Wallet:", platformWallet);
        console.log("Flat Fee Per Action:", flatFee);

        // Deploy PaymentSplitter
        address splitter;
        assembly {
            splitter := create(0, add(paymentSplitterCode, 0x20), mload(paymentSplitterCode))
        }
        require(splitter != address(0), "PaymentSplitter deploy failed");
        console.log("PaymentSplitter:", splitter);

        vm.stopBroadcast();

        console.log("");
        console.log("=== Deployment Complete ===");
        console.log("ARC_PAYMENT_SPLITTER_ADDRESS=", splitter);
    }
}


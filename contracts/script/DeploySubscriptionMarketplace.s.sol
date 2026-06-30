// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";

/// @notice Deploy PaymentSplitter and SubscriptionManager (Vyper contracts).
///
///         Prerequisites:
///         1. Compile Vyper contracts to get bytecode:
///            vyper contracts/vyper/PaymentSplitter.vy -f bytecode > contracts/abis/PaymentSplitter.bin
///            vyper contracts/vyper/SubscriptionManager.vy -f bytecode > contracts/abis/SubscriptionManager.bin
///         2. Set env vars:
///            USDC_ADDRESS, PLATFORM_WALLET, FLAT_FEE_PER_ACTION
///
///         Usage:
///           source <(python3 -c "
///             import os; bs = open('contracts/abis/PaymentSplitter.bin').read().strip();
///             print(f'export PAYMENT_SPLITTER_BYTECODE={bs}')
///           ")
///           source <(python3 -c "
///             import os; bs = open('contracts/abis/SubscriptionManager.bin').read().strip();
///             print(f'export SUBSCRIPTION_MANAGER_BYTECODE={bs}')
///           ")
///           forge script contracts/script/DeploySubscriptionMarketplace.s.sol \
///             --rpc-url <RPC> --broadcast
contract DeploySubscriptionMarketplace is Script {
    function run() external {
        address usdc = vm.envAddress("USDC_ADDRESS");
        address platformWallet = vm.envAddress("PLATFORM_WALLET");
        uint256 flatFee = vm.envUint("FLAT_FEE_PER_ACTION");

        bytes memory paymentSplitterCode = vm.envBytes("PAYMENT_SPLITTER_BYTECODE");
        bytes memory subscriptionManagerCode = vm.envBytes("SUBSCRIPTION_MANAGER_BYTECODE");

        vm.startBroadcast();

        console.log("=== Deploying Subscription Marketplace ===");
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

        // Deploy SubscriptionManager
        address manager;
        assembly {
            manager := create(0, add(subscriptionManagerCode, 0x20), mload(subscriptionManagerCode))
        }
        require(manager != address(0), "SubscriptionManager deploy failed");
        console.log("SubscriptionManager:", manager);

        // Access-control wiring (security review #823): the splitter must accept
        // split() ONLY from the manager. Without this, split() reverts
        // ("only subscription manager") and chargeActions can never settle —
        // fail-closed by design. The broadcaster is the splitter's owner here.
        (bool wired, ) = splitter.call(
            abi.encodeWithSignature("setSubscriptionManager(address)", manager)
        );
        require(wired, "setSubscriptionManager failed");
        console.log("Wired PaymentSplitter.subscription_manager ->", manager);

        // NOTE: SubscriptionManager.authorized_charger defaults to the deployer
        // (owner). If the off-chain charger runs under a separate agent wallet,
        // call SubscriptionManager.setAuthorizedCharger(<agentWallet>) post-deploy.

        vm.stopBroadcast();

        console.log("");
        console.log("=== Deployment Complete ===");
        console.log("ARC_PAYMENT_SPLITTER_ADDRESS=", splitter);
        console.log("ARC_SUBSCRIPTION_MANAGER_ADDRESS=", manager);
    }
}


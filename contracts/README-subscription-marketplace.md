# Subscription Marketplace — Deployment & Operations

## Overview

Two Vyper contracts power the publisher/subscriber marketplace:

- **PaymentSplitter** — Receives USDC from subscriptions and splits 90/10 (creator/platform).
- **SubscriptionManager** — Manages subscriber registrations, ephemeral wallets, and per-action charging.

## Compilation

Install Vyper 0.4.0:

```bash
pip install vyper==0.4.0
```

Compile both contracts:

```bash
# ABI (for Python backend)
vyper contracts/vyper/PaymentSplitter.vy -f abi > contracts/abis/PaymentSplitter.json
vyper contracts/vyper/SubscriptionManager.vy -f abi > contracts/abis/SubscriptionManager.json

# Bytecode (for Foundry deployment)
vyper contracts/vyper/PaymentSplitter.vy -f bytecode > contracts/abis/PaymentSplitter.bin
vyper contracts/vyper/SubscriptionManager.vy -f bytecode > contracts/abis/SubscriptionManager.bin
```

## Deployment

### Using Foundry + Vyper Bytecode

```bash
# 1. Compile bytecode (see above)

# 2. Export bytecode as env vars
PAYMENT_SPLITTER_CODE=$(cat contracts/abis/PaymentSplitter.bin)
SUBSCRIPTION_MANAGER_CODE=$(cat contracts/abis/SubscriptionManager.bin)

# 3. Deploy via Forge script
forge script contracts/script/DeploySubscriptionMarketplace.s.sol \
  --rpc-url https://rpc.testnet.arc.network \
  --broadcast \
  --env-vars USDC_ADDRESS,PLATFORM_WALLET,FLAT_FEE_PER_ACTION,PAYMENT_SPLITTER_BYTECODE,SUBSCRIPTION_MANAGER_BYTECODE
```

### Required Environment Variables

| Variable | Description | Example |
|---|---|---|
| `USDC_ADDRESS` | USDC token contract address | `0x3600000000000000000000000000000000000000` |
| `PLATFORM_WALLET` | Platform fee recipient (10%) | `0x...` |
| `FLAT_FEE_PER_ACTION` | Flat fee in USDC raw (6 decimals) | `100` (= $0.0001) |
| `PAYMENT_SPLITTER_BYTECODE` | Init bytecode from `vyper -f bytecode` | `0x60...` |
| `SUBSCRIPTION_MANAGER_BYTECODE` | Init bytecode from `vyper -f bytecode` | `0x60...` |

## Operational Instructions

### When a Creator Publishes a Strategy

The platform backend calls `PaymentSplitter.createPool()`:

```solidity
// pool_id = keccak256(abi.encode(strategy_id, creator_address))
bytes32 poolId = keccak256(abi.encode("strategy_abc_123", 0xCreatorAddress));

// Create the pool
PaymentSplitter(paymentSplitterAddress).createPool(
    poolId,
    0xCreatorAddress,      // 90% recipient
    0xPlatformWallet       // 10% recipient
);
```

This is invoked automatically by the publisher agent container on startup
(see `strategy_runner_publisher.py`).

### When a User Subscribes

The user (or subscriber container) calls `SubscriptionManager.subscribe()`:

```solidity
// 1. User must first approve USDC spending
USDC.approve(subscriptionManagerAddress, depositAmount);

// 2. Subscribe
bytes32 subId = SubscriptionManager(subscriptionManagerAddress).subscribe(
    0xPoolId,                   // pool_id from PaymentSplitter.createPool
    "https://subscriber.example:8081/events",  // webhook_url
    10_000_000                  // initial_deposit (10 USDC raw, 6 decimals)
);

// 3. Platform backend picks up the Subscribed event
//    and registers the subscriber webhook with the publisher agent.
```

### Charging Actions (Publisher Side)

When a publisher broadcasts a rebalance, it charges each subscriber:

```solidity
// Called by publisher agent before sending rebalance payload
uint256 actionCount = 3;  // number of trades
SubscriptionManager(subscriptionManagerAddress).chargeActions(subId, actionCount);
```

If the subscriber's ephemeral wallet has insufficient balance, the call reverts
and the publisher marks that subscriber as inactive.

### Unsubscribing

```solidity
SubscriptionManager(subscriptionManagerAddress).unsubscribe(subId);
// Remaining ephemeral wallet balance is refunded to subscriber.
```

## Contract Architecture

```
┌──────────────┐    subscribe()/chargeActions()     ┌──────────────────┐
│              │ ───────────────────────────────────▶│                  │
│  Subscriber  │                                     │ SubscriptionMgr │
│  (User)      │ ◀───────────────────────────────────│                  │
│              │    unsubscribe()/renewEphemeral()    │    (Vyper)      │
└──────────────┘                                     └────────┬─────────┘
                                                              │ split()
                                                              ▼
                                                     ┌──────────────────┐
                                                     │                  │
                                                     │ PaymentSplitter  │
                                                     │                  │
                                                     │    (Vyper)       │
                                                     │                  │
                                                     ├────────┬─────────┤
                                                     │ 90%    │  10%    │
                                                     │        │         │
                                                     ▼        ▼         │
                                                 Creator   Platform     │
                                                 Wallet     Wallet      │
                                                     └──────────────────┘
```

## Event Flow

1. Subscriber calls `subscribe()` → `Subscribed` event emitted
2. Platform registers subscriber webhook with publisher agent
3. Publisher evaluates strategy → sends `evaluation_step` events (no charge)
4. Publisher detects rebalance needed → calls `chargeActions()` for each subscriber
5. `chargeActions` transfers USDC from ephemeral wallet → `PaymentSplitter.split()`
6. `split()` sends 90% to creator, 10% to platform
7. Publisher sends rebalance payload to subscriber webhook
8. Subscriber executes trades on its vault
9. Subscriber calls `unsubscribe()` → remaining balance refunded

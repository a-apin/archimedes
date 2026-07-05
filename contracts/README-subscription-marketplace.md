# Subscription Marketplace — Deployment & Operations

## Overview

**Subscriptions are off-chain (as of P7).** The subscription registry is
Postgres-only — no on-chain SubscriptionManager contract is deployed or needed.

The only marketplace contract on-chain is **PaymentSplitter** — receives USDC
via the settlement sweep and splits 90/10 (creator/platform).

## The Only Contract: PaymentSplitter

| Contract | File | Purpose |
|---|---|---|
| PaymentSplitter | `contracts/vyper/PaymentSplitter.vy` | Receives USDC deposits from the settlement sweep, tracks creator/platform balances per pool, supports withdrawal and pool deactivation |

## Compilation

Install Vyper 0.4.0:

```bash
pip install vyper==0.4.0
```

Compile:

```bash
# ABI (for Python backend)
vyper contracts/vyper/PaymentSplitter.vy -f abi > contracts/abis/PaymentSplitter.json

# Bytecode (for Foundry deployment)
vyper contracts/vyper/PaymentSplitter.vy -f bytecode > contracts/abis/PaymentSplitter.bin
```

## Deployment

### Using Foundry + Vyper Bytecode

```bash
# 1. Compile bytecode (see above)

# 2. Export bytecode as env vars
PAYMENT_SPLITTER_CODE=$(cat contracts/abis/PaymentSplitter.bin)

# 3. Deploy via Forge script
forge script contracts/script/DeployPaymentSplitter.s.sol \
  --rpc-url https://rpc.testnet.arc.network \
  --broadcast \
  --env-vars USDC_ADDRESS,PLATFORM_WALLET,FLAT_FEE_PER_ACTION,PAYMENT_SPLITTER_BYTECODE
```

### Required Environment Variables

| Variable | Description | Example |
|---|---|---|
| `USDC_ADDRESS` | USDC token contract address | `0x3600000000000000000000000000000000000000` |
| `PLATFORM_WALLET` | Platform fee recipient (10%) | `0x...` |
| `FLAT_FEE_PER_ACTION` | Flat fee in USDC raw (6 decimals) | `100` (= $0.0001) |
| `PAYMENT_SPLITTER_BYTECODE` | Init bytecode from `vyper -f bytecode` | `0x60...` |

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

This is invoked automatically by the marketplace monolith on publish.

### Subscriptions (Off-Chain)

Subscriptions are managed entirely in Postgres via the marketplace API:
`POST /api/marketplace/subscribe`. A Circle Developer-Controlled Wallet is
provisioned per subscriber for x402 micropayment signing and balance tracking.

### Settlement Sweep (Publisher Side)

The settlement sweeper runs each tick and handles two cadences:

1. **Gateway → agent wallet** (threshold-based): Withdraws available Gateway
   balance when it exceeds `SWEEP_WITHDRAW_THRESHOLD_USDC` (default 10 USDC).
2. **Agent wallet → PaymentSplitter.depositToPool** (per tick): Approves and
   deposits USDC into the pool when the wallet balance exceeds
   `SWEEP_MIN_DEPOSIT_RAW` (default 1 USDC).

See `backend/archimedes/marketplace/settlement.py` for details.

### Withdrawing

Creators withdraw their share from PaymentSplitter via the standard
`withdraw(poolId)` call (same selector as `PaymentSplitter.vy`).

## Contract Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        Off-Chain (Postgres)                      │
│                                                                  │
│  ┌──────────────┐    ┌────────────────────┐   ┌───────────────┐  │
│  │  Subscriber   │───▶│ Marketplace Routes │──▶│  Publisher    │  │
│  │  (Circle Wal) │    │ (FastAPI + Redis)  │   │  Engine       │  │
│  └──────────────┘    └─────────┬──────────┘   └───────┬───────┘  │
│                                │                       │          │
│                                │  settle()             │ sweep    │
│                                ▼                       ▼          │
│                        ┌──────────────┐       ┌──────────────┐   │
│                        │ x402 Gateway │       │ Settlement   │   │
│                        │  (Circle)    │       │ Sweeper      │   │
│                        └──────┬───────┘       └──────┬───────┘   │
│                               │ withdraw()          │ deposit   │
└───────────────────────────────┼─────────────────────┼───────────┘
                                ▼                     ▼
                        ┌──────────────────────────────────┐
                        │         PaymentSplitter          │
                        │          (Vyper)                 │
                        ├────────────────┬─────────────────┤
                        │     90%        │     10%         │
                        │   Creator      │   Platform      │
                        │   Wallet       │   Wallet        │
                        └────────────────┴─────────────────┘
```

## Event Flow

1. Subscriber POSTs to `/api/marketplace/subscribe` → Circle wallet provisioned
2. Publisher tick evaluates the strategy and charges subscribers via x402
3. Settlement sweeper withdraws Gateway balance → agent wallet (threshold-based)
4. Settlement sweeper deposits agent wallet USDC → PaymentSplitter.depositToPool
5. Creators call `PaymentSplitter.withdraw()` to claim their 90% share

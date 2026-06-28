---
name: vyper-agentic-payments__contracts
description: Six Vyper smart contracts for agentic payment workflows — escrow, spending limits, subscriptions, splitter, vault, payment channel
triggers: [vyper-agentic-payments contracts, Vyper, vyper-agentic-payments solidity, vyper-agentic-payments smart contracts, AgentEscrow, SpendingLimiter, PaymentSplitter, SubscriptionManager]
---

# Contracts — Vyper Agentic Payments

**Source**: `vyper-agentic-payments`
**Category**: Core

## When to use this skill
Working on the Vyper smart contracts — deploying, modifying, testing, or understanding the contract architecture. Covers all 6 contracts and 3 interfaces.

## Key files and folders
- `/home/ricardo/github/vyper-agentic-payments/contracts/` — All Vyper source files:
  - `Vault.vy` — Minimal USDC deposit/withdraw (Track A2)
  - `AgentEscrow.vy` — Escrow with task lifecycle, dispute resolution, agent identity verification
  - `SpendingLimiter.vy` — Per-tx/daily/total spending limits for authorized agents
  - `SubscriptionManager.vy` — Recurring USDC payments with pull-payment, grace period, pause/resume
  - `PaymentSplitter.vy` — Revenue distribution (up to 100 recipients, basis-point shares)
  - `PaymentChannel.vy` — Bidirectional USDC payment channel (stub/scaffold only)
  - `interfaces/IERC20.vy` — Standard ERC-20 interface
  - `interfaces/IERC721.vy` — Standard ERC-721 interface
  - `interfaces/IERC721Receiver.vy` — ERC-721 receiver interface
- `/home/ricardo/github/vyper-agentic-payments/moccasin.toml` — Moccasin config (contracts source dir, dependencies: erc-8004-vyper, snekmate)
- `/home/ricardo/github/vyper-agentic-payments/contracts/interfaces/` — ERC standards in Vyper

## Key concepts
- **Vyper ^0.4.0** — all contracts written in Vyper, compiled via Moccasin or titanoboa
- **USDC (6 decimals)** — all contracts use IERC20 for USDC transfers
- **Arc testnet (Chain ID 5042002)** — primary deployment target
- **Contract lifecycle patterns**: Ownable (admin), pull-based payments, time-based limits
- **AgentEscrow**: Task lifecycle `OPEN → CLAIMED → COMPLETED/DISPUTED/CANCELLED`, uses `IIdentityRegistry` for agent verification
- **SpendingLimiter**: Three-tier limits (per-tx, daily via `block.timestamp // 86400`, total), zero = no limit
- **SubscriptionManager**: Price-locked at subscription time, min 1hr / max 1yr interval, 7-day grace period
- **PaymentSplitter**: Basis-point shares summing to 10000, pull-based claiming, owner-managed recipients
- **PaymentChannel**: Bidirectional, challenge period — **scaffold only, not implemented**
- **Local execution**: Titanoboa (`boa`) compiles and runs Vyper in-process — no real chain needed

## Constraints and rules
- Vyper ^0.4.0 — do not use Solidity or older Vyper syntax
- All contracts accept USDC via `IERC20` — 6 decimal precision
- Run `moccasin.toml` config for production builds (Vyper dependencies via Moccasin)
- For local testing, use `boa.load()` (titanoboa) — no real chain required
- PaymentChannel is a stub — do not deploy to production

## Related skills
- See `.agents/skills/vyper-agentic-payments__tests` (titanoboa test patterns for these contracts)
- See `.agents/skills/vyper-agentic-payments__scripts-deploy` (deployment scripts)
- See `.agents/skills/shared__arc-blockchain` (Arc testnet config, USDC)

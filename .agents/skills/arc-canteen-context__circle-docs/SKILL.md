---
name: arc-canteen-context__circle-docs
description: Circle developer platform documentation — USDC, CCTP, Developer-Controlled Wallets, Gateway, and Agent Stack
triggers: [circle docs, circle usdc, circle cctp, circle wallets, circle agent stack, circle gateway]
---

# ARC Canteen — Circle Developer Docs

**Source**: arc-canteen-context
**Category**: Reference

## When to use this skill
Looking up Circle developer platform documentation: USDC integration, CCTP (Cross-Chain Transfer Protocol), Developer-Controlled Wallets, Gateway API, or Agent Stack.

## Key files and folders
- **Circle main developer docs**: `/home/ricardo/.arc-canteen/context/docs/developers.circle.com/`
  - **USDC**: `/home/ricardo/.arc-canteen/context/docs/developers.circle.com/stablecoins/`
  - **CCTP**: `/home/ricardo/.arc-canteen/context/docs/developers.circle.com/cctp/` — subdirs: `concepts/`, `references/`, `quickstarts/`
  - **Wallets**: `/home/ricardo/.arc-canteen/context/docs/developers.circle.com/wallets/` — root files: `dev-controlled.md`, `user-controlled.md`, `modular.md`, `gas-station.md`, etc.
  - **Gateway**: `/home/ricardo/.arc-canteen/context/docs/developers.circle.com/gateway/` — subdirs: `nanopayments/`, `quickstarts/`, `references/`
  - **Agent Stack**: `/home/ricardo/.arc-canteen/context/docs/developers.circle.com/agent-stack/`
  - **Paymaster**: `/home/ricardo/.arc-canteen/context/docs/developers.circle.com/paymaster/`
- **Circle integration guides**: `/home/ricardo/.arc-canteen/context/docs/circlefin-skills/`
  - **Use USDC**: `/home/ricardo/.arc-canteen/context/docs/circlefin-skills/use-usdc.md`
  - **Use Developer-Controlled Wallets**: `/home/ricardo/.arc-canteen/context/docs/circlefin-skills/use-developer-controlled-wallets.md`
  - **Use Gateway**: `/home/ricardo/.arc-canteen/context/docs/circlefin-skills/use-gateway.md`
  - **Use CCTP**: `/home/ricardo/.arc-canteen/context/docs/circlefin-skills/bridge-stablecoin.md`
  - **Use Arc**: `/home/ricardo/.arc-canteen/context/docs/circlefin-skills/use-arc.md`

## Key concepts
- USDC: native gas token on Arc, standard ERC-20 with 6 decimals
- Developer-Controlled Wallets: Circle manages key infrastructure, dev controls signing
- CCTP: cross-chain USDC transfers between supported networks
- Gateway: unified API for balance, payout, and settlement operations
- Agent Stack: SDK for creating AI agents with wallet capabilities

## Related skills
- See `.agents/skills/arc-canteen-context__arc-docs` — Arc chain docs complement Circle docs
- See `.agents/skills/shared__arc-blockchain` — consolidated blockchain config
- See `.agents/skills/archimedes__payments` — Circle wallet usage in Archimedes
- See `.agents/skills/arc-nanopayments__x402-gateway` — x402 on top of Circle Gateway

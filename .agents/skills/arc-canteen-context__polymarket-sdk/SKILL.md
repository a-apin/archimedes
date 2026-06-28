---
name: arc-canteen-context__polymarket-sdk
description: Polymarket SDK, CTF exchange v2, and conditional token framework integration
triggers: [polymarket sdk, polymarket api, ctf exchange, conditional tokens, polymarket integration]
---

# ARC Canteen — Polymarket SDK

**Source**: arc-canteen-context
**Category**: Reference

## When to use this skill
Integrating with the Polymarket SDK, working with the CTF exchange, or using conditional token logic.

## Key files and folders
- **Polymarket SDK**: `/home/ricardo/.arc-canteen/context/polymarket-sdk/`
- **CTF exchange v2**: `/home/ricardo/.arc-canteen/context/ctf-exchange-v2/`

## Key concepts
- Conditional tokens: ERC-1155 tokens representing outcome-dependent positions
- CTF exchange: on-chain settlement layer integrated with CLOB
- SDK provides TypeScript bindings for market queries, order placement, and position management
- Works with USDC as collateral

## Related skills
- See `.agents/skills/arc-canteen-context__clob-client` — order book interaction
- See `.agents/skills/arc-canteen-context__real-time-data` — market data feeds
- See `.agents/skills/shared__arc-blockchain` — chain config for Polymarket deployment

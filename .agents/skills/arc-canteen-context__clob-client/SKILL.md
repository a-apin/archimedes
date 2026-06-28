---
name: arc-canteen-context__clob-client
description: CLOB client v2 (TypeScript and Rust) for the Polymarket CLOB API, plus order utilities
triggers: [clob client, clob api, polymarket clob, order utils, clob v2]
---

# ARC Canteen — CLOB Client

**Source**: arc-canteen-context
**Category**: Reference

## When to use this skill
Working with the Polymarket CLOB (Central Limit Order Book) API, placing orders, managing order signatures, or using the order utilities.

## Key files and folders
- **CLOB client v2 (TS)**: `/home/ricardo/.arc-canteen/context/clob-client-v2/`
- **CLOB client v2 (Rust)**: `/home/ricardo/.arc-canteen/context/rs-clob-client-v2/`
- **Order utils**: `/home/ricardo/.arc-canteen/context/clob-order-utils/`

## Key concepts
- CLOB v2: on-chain settlement with off-chain order book matching
- Order types: GTC, FOK, IOC
- EIP-712 typed signatures for order authorization
- Rust client is an alternative implementation alongside the TypeScript reference

## Related skills
- See `.agents/skills/arc-canteen-context__polymarket-sdk` — higher-level Polymarket SDK
- See `.agents/skills/arc-canteen-context__real-time-data` — real-time market data feeds
- See `.agents/skills/arc-canteen-context__builder-relayer` — off-chain builder/relayer for order routing

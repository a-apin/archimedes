---
name: arc-canteen-context__builder-relayer
description: Python client for the Polymarket builder/relayer — order signing and submission
triggers: [builder relayer, polymarket builder, polymarket relayer, py builder relayer]
---

# ARC Canteen — Builder/Relayer Client

**Source**: arc-canteen-context
**Category**: Reference

## When to use this skill
Using the Python builder/relayer client for off-chain order submission to Polymarket CLOB.

## Key files and folders
- **Python client**: `/home/ricardo/.arc-canteen/context/py-builder-relayer-client/`
  - **Source**: `/home/ricardo/.arc-canteen/context/py-builder-relayer-client/py_builder_relayer_client/`
  - **Examples**: `/home/ricardo/.arc-canteen/context/py-builder-relayer-client/examples/`
  - **Tests**: `/home/ricardo/.arc-canteen/context/py-builder-relayer-client/tests/`

## Key concepts
- Builder relays signed orders to the CLOB matching engine
- Python client wraps the REST API for order submission
- Supports both EIP-712 and raw signature schemes

## Related skills
- See `.agents/skills/arc-canteen-context__clob-client` — the TypeScript/Rust CLOB clients
- See `.agents/skills/arc-canteen-context__polymarket-sdk` — higher-level SDK

---
name: arc-canteen-context__real-time-data
description: Real-time data client for Polymarket market feeds — prices, volumes, and market state
triggers: [real time data, polymarket data feed, market data stream, polymarket prices]
---

# ARC Canteen — Real-Time Data Client

**Source**: arc-canteen-context
**Category**: Reference

## When to use this skill
Working with real-time Polymarket data feeds: price streams, volume data, or market state updates.

## Key files and folders
- **Real-time data client**: `/home/ricardo/.arc-canteen/context/real-time-data-client/`
- **Examples**: explored via the source directory

## Key concepts
- WebSocket-based streaming of market data
- Feeds include: order book deltas, ticker updates, trade history, market state changes
- Client handles reconnection and sequence gap recovery

## Related skills
- See `.agents/skills/arc-canteen-context__clob-client` — CLOB interaction uses real-time data
- See `.agents/skills/arc-canteen-context__polymarket-sdk` — SDK wraps data client

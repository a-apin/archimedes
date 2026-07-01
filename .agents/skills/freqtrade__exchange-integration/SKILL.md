---
name: freqtrade__exchange-integration
description: CCXT-based exchange abstraction, WebSocket support, spot and futures connectors for 20+ exchanges
triggers: [exchange, ccxt, connector, exchange config]
---

# Exchange Integration

**Source**: freqtrade
**Category**: Integration

## When to use this skill
Adding or modifying exchange connectors, understanding CCXT integration, working with WebSocket feeds, or configuring spot/futures trading pairs.

## Key files and folders
- `/home/ricardo/github/freqtrade/freqtrade/exchange/exchange.py` — Base `Exchange` class wrapping CCXT
- `/home/ricardo/github/freqtrade/freqtrade/exchange/exchange_types.py` — Exchange-specific type definitions
- `/home/ricardo/github/freqtrade/freqtrade/exchange/exchange_utils.py` — Exchange utility functions
- `/home/ricardo/github/freqtrade/freqtrade/exchange/exchange_utils_timeframe.py` — Timeframe utility functions
- `/home/ricardo/github/freqtrade/freqtrade/exchange/exchange_ws.py` — WebSocket support
- `/home/ricardo/github/freqtrade/freqtrade/exchange/check_exchange.py` — Exchange configuration validation
- `/home/ricardo/github/freqtrade/freqtrade/exchange/common.py` — Common exchange helpers
- `/home/ricardo/github/freqtrade/freqtrade/exchange/*.py` — Per-exchange modules: `binance.py`, `kraken.py`, `bybit.py`, `okx.py`, `hyperliquid.py`, `coinbase.py`, `gate.py`, `kucoin.py`, `bitget.py`, `bingx.py`, etc.
- `/home/ricardo/github/freqtrade/freqtrade/enums/` — Enums for trading modes (spot, futures)

## Key concepts
- **CCXT**: Unified API for 100+ exchanges. Freqtrade wraps it in the base `Exchange` class with additional safety checks, rate limiting, and retry logic.
- **Per-exchange modules**: override base class methods for exchange-specific behavior (fees, order types, trading rules, leverage tiers).
- **WebSocket**: `exchange_ws.py` provides real-time order book/trade feeds.
- **Futures**: leverage, margin modes, funding rate tracking via exchange-specific implementations.

## Related skills
- See `.agents/skills/freqtrade__trading-engine` — the bot that uses the exchange layer
- See `.agents/skills/freqtrade__data-management` — OHLCV data download via exchange
- See `.agents/skills/freqtrade__risk-management` — position sizing and leverage constraints

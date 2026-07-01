---
name: hummingbot__market-data
description: Real-time order book data, candlestick feeds, liquidation feeds, CoinCap/CoinGecko integration
triggers: [market data, order book, candles, data feed]
---

# Market Data

**Source**: hummingbot
**Category**: Infrastructure

## When to use this skill
Working with real-time market data: order book streams, candlestick feeds, liquidation data, or external data sources like CoinCap and CoinGecko.

## Key files and folders
- `/home/ricardo/github/hummingbot/hummingbot/core/data_type/order_book.pyx` — Order book data type (C++ backed)
- `/home/ricardo/github/hummingbot/hummingbot/data_feed/candles_feed/` — Per-exchange candlestick data sources (~25 exchanges)
- `/home/ricardo/github/hummingbot/hummingbot/data_feed/coin_cap_data_feed/` — CoinCap price feed
- `/home/ricardo/github/hummingbot/hummingbot/data_feed/coin_gecko_data_feed/` — CoinGecko price feed
- `/home/ricardo/github/hummingbot/hummingbot/data_feed/liquidations_feed/` — Liquidation data feeds
- `/home/ricardo/github/hummingbot/hummingbot/data_feed/data_feed_base.py` — Data feed base class
- `/home/ricardo/github/hummingbot/hummingbot/data_feed/amm_gateway_data_feed.py` — AMM DEX data feed
- `/home/ricardo/github/hummingbot/hummingbot/data_feed/custom_api_data_feed.py` — Custom API data feed
- `/home/ricardo/github/hummingbot/hummingbot/data_feed/market_data_provider.py` — Market data provider abstraction
- `/home/ricardo/github/hummingbot/hummingbot/data_feed/wallet_tracker_data_feed.py` — Wallet balance tracking

## Key concepts
- **Order book**: C++-backed `OrderBook` class in `core/data_type/`. Used by all connectors and strategies.
- **CandlesFeed**: per-exchange candle data sources, accessible via `CandlesFactory`. Supports multiple timeframes.
- **External feeds**: CoinCap and CoinGecko feeds for asset prices when exchange data is not needed.
- **Liquidation feeds**: real-time liquidation event streams for perpetual markets.

## Related skills
- See `.agents/skills/hummingbot__core-engine` — the engine that drives data feed polling
- See `.agents/skills/hummingbot__exchange-connectors` — connectors provide order book data

---
name: hummingbot__exchange-connectors
description: Connector framework and 100+ exchange implementations (CEX, CLOB DEX, AMM DEX)
triggers: [connector, exchange connector, exchange integration]
---

# Exchange Connectors

**Source**: hummingbot
**Category**: Integration

## When to use this skill
Adding or modifying exchange connectors, understanding the connector framework (ExchangeBase/ExchangePyBase), working with CEX spot, perpetual, or DEX connectors.

## Key files and folders
- `/home/ricardo/github/hummingbot/hummingbot/connector/exchange_base.pyx` — `ExchangeBase` Cython ABC for all CEX connectors
- `/home/ricardo/github/hummingbot/hummingbot/connector/exchange_py_base.py` — `ExchangePyBase`: pure-Python base with built-in polling, order tracking, and WebSocket support
- `/home/ricardo/github/hummingbot/hummingbot/connector/derivative_base.py` — Base class for perpetual/derivative connectors
- `/home/ricardo/github/hummingbot/hummingbot/connector/perpetual_derivative_py_base.py` — Pure-Python perpetual derivative base
- `/home/ricardo/github/hummingbot/hummingbot/connector/connector_base.pxd` / `connector_base.pyx` — Core connector base
- `/home/ricardo/github/hummingbot/hummingbot/connector/exchange/` — 28 CEX spot connectors (binance, coinbase, kraken, bybit, okx, gate, etc.)
- `/home/ricardo/github/hummingbot/hummingbot/connector/derivative/` — 21 perpetual connectors (binance_perpetual, dydx_perpetual, etc.)
- `/home/ricardo/github/hummingbot/hummingbot/connector/gateway/` — DEX connector base via Gateway middleware
- `/home/ricardo/github/hummingbot/hummingbot/connector/test_support/` — Mock exchanges and network mocking for connector tests

## Key concepts
- **ExchangeBase**: Cython ABC defining the interface all connectors must implement (`ticker`, `get_order_book`, `place_order`, `cancel_order`, etc.)
- **ExchangePyBase**: pure-Python alternative to ExchangeBase, easier to extend, with built-in polling loop and order tracking.
- **ConnectorManager**: creates connector instances dynamically based on config.
- **3 connector categories**: CLOB CEX (centralized order book), CLOB DEX (on-chain order books like dYdX), AMM DEX (via Gateway: Uniswap, Curve, Balancer).

## Related skills
- See `.agents/skills/hummingbot__dex-gateway` — DEX-specific connector patterns
- See `.agents/skills/hummingbot__core-engine` — the engine that uses connectors
- See `.agents/skills/hummingbot__v1-strategies` — strategies that trade through connectors

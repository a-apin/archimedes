---
name: hummingbot__core-engine
description: Clock, event system, trading core, network iterator, C++ order book data types, API throttler
---

# Core Engine

**Source**: hummingbot
**Category**: Core

## When to use this skill
Understanding the Hummingbot engine backbone: the Clock-driven tick system, event pub/sub, TradingCore orchestration, network iterator, C++ performance layer, rate limiting, or Web assistant framework.

## Key files and folders
- `/home/ricardo/github/hummingbot/hummingbot/core/clock.pyx` — `Clock`: time keeper, pulses all registered components on each tick
- `/home/ricardo/github/hummingbot/hummingbot/core/trading_core.py` — `TradingCore`: orchestrates everything
- `/home/ricardo/github/hummingbot/hummingbot/core/connector_manager.py` — `ConnectorManager`: creates connectors dynamically
- `/home/ricardo/github/hummingbot/hummingbot/core/event/` — Event pub/sub system (`EventReporter`, `EventListener`, `EventForwarder`)
- `/home/ricardo/github/hummingbot/hummingbot/core/network_iterator.pyx` — `NetworkIterator`: async network polling base
- `/home/ricardo/github/hummingbot/hummingbot/core/cpp/` — C++ order book data types (`.cpp`/`.h`): `LimitOrder`, `OrderBookEntry`, `OrderExpirationEntry`
- `/home/ricardo/github/hummingbot/hummingbot/core/data_type/` — Order book, in-flight orders, trade fees, etc.
- `/home/ricardo/github/hummingbot/hummingbot/core/api_throttler/` — Rate limiting
- `/home/ricardo/github/hummingbot/hummingbot/core/web_assistant/` — REST/WebSocket abstraction (`WebAssistantsFactory`)
- `/home/ricardo/github/hummingbot/hummingbot/core/rate_oracle/` — Exchange rate feeds
- `/home/ricardo/github/hummingbot/hummingbot/core/pubsub.pyx` — PubSub system
- `/home/ricardo/github/hummingbot/hummingbot/core/gateway/` — Gateway monitor

## Key concepts
- **Clock-driven architecture**: `Clock` advances in configurable ticks; each registered component gets a `c_tick()` call for time-sliced processing.
- **Event system**: decoupled pub/sub — producers emit events, consumers subscribe via `EventListener`.
- **C++ layer**: ultra-fast order book operations (sorted entry lists, linked lists) via Cython-wrapped C++ code.
- **NetworkIterator**: async base class for all network-connected components (connectors, data feeds).

## Related skills
- See `.agents/skills/hummingbot__exchange-connectors` — connectors registered with the engine
- See `.agents/skills/hummingbot__market-data` — data feeds driven by the engine
- See `.agents/skills/hummingbot__v1-strategies` — strategies driven by Clock ticks

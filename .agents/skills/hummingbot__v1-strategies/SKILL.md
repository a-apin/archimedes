---
name: hummingbot__v1-strategies
description: V1 monolithic strategy implementations — pure market making, XEMM, AMM arb, liquidity mining, hedge, avellaneda
triggers: [v1 strategy, pure market making, amm arb, xemm, liquidity mining]
---

# V1 Strategies

**Source**: hummingbot
**Category**: Domain

## When to use this skill
Writing, modifying, or debugging a V1 (legacy) strategy: pure market making, cross-exchange market making, AMM arbitrage, liquidity mining, perpetual market making, spot-perp arbitrage, hedge, or Avellaneda market making.

## Key files and folders
- `/home/ricardo/github/hummingbot/hummingbot/strategy/strategy_base.pxd` / `strategy_base.pyx` — `StrategyBase` Cython ABC
- `/home/ricardo/github/hummingbot/hummingbot/strategy/strategy_py_base.pxd` / `strategy_py_base.pyx` — Pure-Python strategy base
- `/home/ricardo/github/hummingbot/hummingbot/strategy/pure_market_making/` — Pure market making strategy
- `/home/ricardo/github/hummingbot/hummingbot/strategy/cross_exchange_market_making/` — XEMM strategy
- `/home/ricardo/github/hummingbot/hummingbot/strategy/amm_arb/` — AMM arbitrage strategy
- `/home/ricardo/github/hummingbot/hummingbot/strategy/liquidity_mining/` — Liquidity mining strategy
- `/home/ricardo/github/hummingbot/hummingbot/strategy/perpetual_market_making/` — Perpetual market making
- `/home/ricardo/github/hummingbot/hummingbot/strategy/spot_perpetual_arbitrage/` — Spot-perp arbitrage
- `/home/ricardo/github/hummingbot/hummingbot/strategy/hedge/` — Hedge strategy
- `/home/ricardo/github/hummingbot/hummingbot/strategy/avellaneda_market_making/` — Avellaneda-Stoikov market making
- `/home/ricardo/github/hummingbot/hummingbot/strategy_v2_base.py` — Bridge between V1 and V2 frameworks

## Key concepts
- **StrategyBase**: Cython ABC all V1 strategies inherit from. Provides clock-driven `c_tick()` method.
- **Monolithic structure**: each V1 strategy is a single class with its own config, logic, and order management.
- **Script strategies**: user-defined strategies in `/home/ricardo/github/hummingbot/scripts/` can be hot-loaded at runtime.
- **DataTypes**: `MarketTradingPairTuple`, `TradingPairTuple` — key data structures passed to strategies.

## Decision points
- Use V1 strategies for stable, well-known patterns (PMM, XEMM, AMM arb).
- Use V2 framework for modular, composable strategies (controllers + executors).

## Related skills
- See `.agents/skills/hummingbot__v2-framework` — V2 modular alternative
- See `.agents/skills/hummingbot__exchange-connectors` — connectors used by all strategies
- See `.agents/skills/hummingbot__core-engine` — the engine driving strategy ticks

---
name: hummingbot__v2-framework
description: V2 modular strategy framework — controllers, executors, pydantic models, backtesting
triggers: [v2 strategy, controller, executor, v2 backtesting]
---

# V2 Framework

**Source**: hummingbot
**Category**: Domain

## When to use this skill
Building V2 modular strategies using the Controller/Executor pattern, configuring strategies with pydantic models, or running V2 backtesting.

## Key files and folders
- `/home/ricardo/github/hummingbot/hummingbot/strategy_v2/controllers/` — Controller base classes and implementations
- `/home/ricardo/github/hummingbot/hummingbot/strategy_v2/executors/` — Executor implementations: `ArbitrageExecutor`, `DCAExecutor`, `GridExecutor`, `LpExecutor`, `OrderExecutor`, `PositionExecutor`, `TwapExecutor`, `XemmExecutor`
- `/home/ricardo/github/hummingbot/hummingbot/strategy_v2/models/` — Pydantic config models and executor action types
- `/home/ricardo/github/hummingbot/hummingbot/strategy_v2/backtesting/` — V2 backtesting engine
- `/home/ricardo/github/hummingbot/hummingbot/strategy_v2/utils/` — V2-specific utilities
- `/home/ricardo/github/hummingbot/hummingbot/strategy_v2/runnable_base.py` — Runnable base class
- `/home/ricardo/github/hummingbot/controllers/` — Deployable V2 controller scripts (top-level)

## Key concepts
- **Controller**: defines the trading logic (when to enter/exit, position sizing). Stateless logic.
- **Executor**: handles order execution for a specific position/task. Stateful.
- **Separation of concerns**: Controller decides WHAT and WHEN, Executor decides HOW.
- **Pydantic configs**: all V2 configs use `BaseClientModel` subclasses for type-safe, validated configuration.
- **V2 backtesting**: dedicated backtesting engine separate from the V1 backtesting approach.

## Decision points
- Use V2 for new strategies — it's more modular, testable, and composable than V1.
- Mix and match controllers with executors for different behavior combinations.

## Related skills
- See `.agents/skills/hummingbot__v1-strategies` — V1 alternative
- See `.agents/skills/hummingbot__exchange-connectors` — connectors used by executors
- See `.agents/skills/hummingbot__core-engine` — the engine that drives V2 strategies

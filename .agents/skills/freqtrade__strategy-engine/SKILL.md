---
name: freqtrade__strategy-engine
description: IStrategy interface, strategy parameters, informative pairs, data provider, strategy validation
triggers: [strategy, IStrategy, strategy interface, custom strategy]
---

# Strategy Engine

**Source**: freqtrade
**Category**: Domain

## When to use this skill
Writing, modifying, or debugging a trading strategy; understanding the `IStrategy` interface, informative pairs, the DataProvider, or strategy parameter definitions.

## Key files and folders
- `/home/ricardo/github/freqtrade/freqtrade/strategy/interface.py` — `IStrategy` abstract base class (all strategies inherit from this)
- `/home/ricardo/github/freqtrade/freqtrade/strategy/parameters.py` — Strategy parameter framework (`IntParameter`, `FloatParameter`, `CategoricalParameter`, `BooleanParameter`)
- `/home/ricardo/github/freqtrade/freqtrade/strategy/informative_decorator.py` — `@informative()` decorator for loading additional pair data
- `/home/ricardo/github/freqtrade/freqtrade/strategy/hyper.py` — Hyperoptable strategy parameter mixin
- `/home/ricardo/github/freqtrade/freqtrade/strategy/strategy_validation.py` — Strategy configuration validation
- `/home/ricardo/github/freqtrade/freqtrade/data/dataprovider.py` — `DataProvider` class that provides candle data to strategies
- `/home/ricardo/github/freqtrade/freqtrade/resolvers/strategy_resolver.py` — Dynamic strategy loading
- `/home/ricardo/github/freqtrade/freqtrade/templates/` — Strategy template files (`strategy.py.jinja2`)

## Key concepts
- **IStrategy methods**: `populate_indicators()`, `populate_entry_trend()`, `populate_exit_trend()`, `custom_exit()`, `custom_stake_amount()`, `check_buy_timeout()`, etc.
- **Parameters**: `IntParameter(low, high, default, space="buy")` — automatically optimized during hyperopt.
- **Informative pairs**: load higher-timeframe or different-pair data via the `@informative()` decorator.
- **DataProvider**: strategy-facing API for accessing candle data, ticker info, whitelist pairs, and running/timeframe info.

## Decision points
- Use `populate_*_trend()` for signal generation (entry/exit conditions based on indicators).
- Use `custom_exit()` for dynamic exit conditions (e.g., trailing stop, ROI table override).
- Use `custom_stake_amount()` for dynamic position sizing.

## Related skills
- See `.agents/skills/freqtrade__backtesting` — backtesting strategies
- See `.agents/skills/freqtrade__hyperopt` — optimizing strategy parameters
- See `.agents/skills/freqtrade__risk-management` — protections and pairlists that constrain strategies

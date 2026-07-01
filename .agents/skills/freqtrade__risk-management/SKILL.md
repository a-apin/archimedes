---
name: freqtrade__risk-management
description: Pairlists, protections, stop-loss, position sizing, leverage, and budget management
triggers: [pairlist, protection, stoploss, risk management, position sizing]
---

# Risk Management

**Source**: freqtrade
**Category**: Domain

## When to use this skill
Configuring or modifying pairlists (trade pair filters), protections (cool-down, stoploss guard, max drawdown), stop-loss strategies, position sizing, or leverage settings.

## Key files and folders
- `/home/ricardo/github/freqtrade/freqtrade/plugins/pairlistmanager.py` — `PairListManager`: chains pairlist filters
- `/home/ricardo/github/freqtrade/freqtrade/plugins/pairlist/` — 20+ pairlist filters: `StaticPairList`, `VolumePairList`, `AgeFilter`, `VolatilityFilter`, `PriceFilter`, `SpreadFilter`, `RangeStabilityFilter`, etc.
- `/home/ricardo/github/freqtrade/freqtrade/plugins/protectionmanager.py` — `ProtectionManager`: evaluates trade protections
- `/home/ricardo/github/freqtrade/freqtrade/plugins/protections/` — Protection implementations: `StoplossGuard`, `CooldownPeriod`, `LowProfitPairs`, `MaxDrawdown`
- `/home/ricardo/github/freqtrade/freqtrade/leverage/` — Leverage and liquidation price calculation
- `/home/ricardo/github/freqtrade/freqtrade/wallets.py` — Wallet balance tracking and position sizing

## Key concepts
- **PairList chain**: filters are applied sequentially. Order matters — put expensive API-call filters last.
- **Protections**: time-based or trade-based guards that pause trading when conditions are met (e.g., too many stoplosses in a period).
- **Position sizing**: controlled by `stake_amount` config, `max_open_trades`, and `custom_stake_amount()` strategy method.
- **Leverage**: `leverage/` module handles interest calculation and liquidation price formulas for futures trading.

## Related skills
- See `.agents/skills/freqtrade__strategy-engine` — strategy interacts with risk management via DataProvider
- See `.agents/skills/freqtrade__exchange-integration` — exchange provides leverage and position data

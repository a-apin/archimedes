---
name: archimedes__analytics-engine
description: Backtrader-based quantitative backtesting engine with 35+ published strategies and PBO/DSR rigor gate
triggers: [archimedes backtest, archimedes strategy, archimedes analytics, archimedes pbo, archimedes rigor]
---

# Archimedes Analytics Engine

**Source**: archimedes
**Category**: Core

## When to use this skill
Implementing or modifying quantitative trading strategies, running backtests, working with the PBO (Probability of Backtest Overfitting) or DSR (Deflated Sharpe Ratio) rigor gates, or analyzing strategy performance.

## Key files and folders
- **Engine root**: `/home/ricardo/github/archimedes/analytics-engine/`
- **Strategy implementations**: `/home/ricardo/github/archimedes/analytics-engine/strategies/`
  - 35+ strategies (e.g., `jegadeesh_titman_1993_cross_sectional_momentum.py`, `moskowitz_ooi_pedersen_2012_tsmom.py`)
- **Source library**: `/home/ricardo/github/archimedes/analytics-engine/src/`
- **Test suite**: `/home/ricardo/github/archimedes/analytics-engine/tests/`
- **Scripts**: `/home/ricardo/github/archimedes/analytics-engine/scripts/`
- **Project config**: `/home/ricardo/github/archimedes/analytics-engine/pyproject.toml`
- **Backtest fixtures**: `/home/ricardo/github/archimedes/analytics-engine/strategies/backtest_fixtures.json`

## Key concepts
- **Backtrader-based**: each strategy subclasses `bt.Strategy`
- **Rigor gate**: two-stage validation — PBO (combinatorial symmetric cross-validation) then DSR (accounting for multiple testing)
- **Strategy DSL**: strategies defined via a domain-specific language in `services/strategy_dsl.py`
- **Walk-forward analysis**: out-of-sample testing with `test_walk_forward.py`
- **Pairs trading**: cointegration-based (Engle-Granger), distance-based (Gatev), and Kalman filter pairs

## Constraints and rules
- **Dependency management**: uses `uv` — run `uv sync` after changing dependencies
- **Strategy naming**: `{author}_{year}_{descriptive_name}.py` in lowercase with underscores
- **Data format**: Backtrader standard OHLCV; fixtures in `strategies/backtest_fixtures.json`

## Related skills
- See `.agents/skills/archimedes__backend` — backtest results stored via `services/backtest_repository.py`
- See `.agents/skills/shared__arc-blockchain` — strategies are deployed as on-chain vault strategies

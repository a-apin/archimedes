---
name: archimedes-fork-1__analytics-engine
description: Backtrader-based quantitative backtesting engine with 30+ academic strategies, walk-forward, PBO/DSR rigor
triggers: [archimedes-fork-1 analytics, backtesting, backtrader, quant, strategies, archimedes-fork-1 pbo, walk-forward]
---

# Analytics Engine — Archimedes Fork

**Source**: `archimedes-fork-1`
**Category**: Core

## When to use this skill
Working on quantitative backtesting — the standalone `analytics-engine/` sub-project: running backtests, adding/modifying strategies, computing PBO, walk-forward analysis, or interpreting backtest results.

## Key files and folders
- `/home/ricardo/github/archimedes-fork-1/analytics-engine/pyproject.toml` — hatchling project, minimal deps (backtrader, pandas, yfinance)
- `/home/ricardo/github/archimedes-fork-1/analytics-engine/src/archimedes_analytics_engine/engine.py` — Core engine (599 lines): `run_backtest()`, `run_pairs_backtest()`, `BuyAndHoldStrategy`, analyzers
- `/home/ricardo/github/archimedes-fork-1/analytics-engine/src/archimedes_analytics_engine/pbo.py` — Probability of Backtest Overfitting (CSCV method, 99 lines)
- `/home/ricardo/github/archimedes-fork-1/analytics-engine/src/archimedes_analytics_engine/walk_forward.py` — Walk-forward parameter selection (188 lines)
- `/home/ricardo/github/archimedes-fork-1/analytics-engine/src/archimedes_analytics_engine/costs.py` — Cost model + turnover analyzer
- `/home/ricardo/github/archimedes-fork-1/analytics-engine/src/archimedes_analytics_engine/data.py` — OHLCV data loading
- `/home/ricardo/github/archimedes-fork-1/analytics-engine/strategies/` — 30+ academic strategy implementations
- `/home/ricardo/github/archimedes-fork-1/analytics-engine/tests/` — 19 test files
- `/home/ricardo/github/archimedes-fork-1/analytics-engine/artifacts/` — 7 JSON backtest fixture artifacts

## Key concepts
- **30+ strategies** from academic literature: Dual Momentum (Antonacci 2014), Low Idio Vol (Ang & Hodrick 2006), Value Factor (Asness 2013), PCA Stat Arb (Avellaneda & Lee 2010), Residual Momentum (Blitz 2010), TSMOM (Moskowitz 2012), Quality Minus Junk (Novy-Marx 2013), Risk Parity (Maillard 2010), etc.
- **CLI**: `archimedes-analytics-engine` entrypoint via `cli.py`
- **Managed via uv**: `uv.lock` present, uses hatchling build system
- **Standalone project**: imported by backend via `services/backtest_mapper.py` and `services/dsl_to_backtrader.py`

## Constraints and rules
- Minimal runtime deps: backtrader, pandas, yfinance only
- Dev dep: pytest with `pythonpath = ["src"]` in pyproject.toml
- Strategies follow a consistent interface pattern — check existing strategies before adding new ones

## Related skills
- See `.agents/skills/archimedes-fork-1__backend` (backend services that consume analytics results)

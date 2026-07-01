---
name: freqtrade__backtesting
description: Historical backtesting engine with realistic order matching, backtest data management, and analysis tools
triggers: [backtesting, backtest, backtest analysis]
---

# Backtesting

**Source**: freqtrade
**Category**: Domain

## When to use this skill
Running or analyzing historical backtests, understanding order matching logic, generating backtest reports, or using lookahead bias and recursive analysis tools.

## Key files and folders
- `/home/ricardo/github/freqtrade/freqtrade/optimize/backtesting.py` — Core backtesting engine
- `/home/ricardo/github/freqtrade/freqtrade/optimize/backtest_caching.py` — Backtest result caching
- `/home/ricardo/github/freqtrade/freqtrade/optimize/optimize_reports/` — Backtest report generation
- `/home/ricardo/github/freqtrade/freqtrade/optimize/analysis/` — Lookahead bias and recursive analysis tools
- `/home/ricardo/github/freqtrade/freqtrade/data/btanalysis.py` — Backtest trade analysis
- `/home/ricardo/github/freqtrade/freqtrade/data/entryexitanalysis.py` — Entry/exit signal analysis
- `/home/ricardo/github/freqtrade/freqtrade/commands/cli_options.py` — Backtesting CLI options (`--timeframe`, `--timerange`, `--max-open-trades`, etc.)

## Key concepts
- **Order matching**: simulates fills based on OHLCV data with realistic slippage and fee models.
- **Caching**: backtest results are cached to disk for fast re-runs with identical parameters.
- **Analysis tools**: lookahead bias detection prevents future-leaking indicators; recursive analysis checks strategy consistency across rolling time windows.

## Related skills
- See `.agents/skills/freqtrade__strategy-engine` — the strategies being backtested
- See `.agents/skills/freqtrade__hyperopt` — optimization driven by backtest results
- See `.agents/skills/freqtrade__data-management` — historical data used by backtesting

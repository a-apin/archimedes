---
name: freqtrade__data-management
description: OHLCV data download, history management, data conversion, metrics, and data provider
triggers: [data download, ohlcv, data provider, history, data converter]
---

# Data Management

**Source**: freqtrade
**Category**: Infrastructure

## When to use this skill
Downloading OHLCV market data, managing the local data history store, converting between data formats, computing data metrics, or working with the DataProvider interface.

## Key files and folders
- `/home/ricardo/github/freqtrade/freqtrade/data/history/` — History management (download, load, store OHLCV data)
- `/home/ricardo/github/freqtrade/freqtrade/data/converter/` — Data format converters (trade data to OHLCV, timeframe resampling)
- `/home/ricardo/github/freqtrade/freqtrade/data/dataprovider.py` — `DataProvider`: strategy-facing data API
- `/home/ricardo/github/freqtrade/freqtrade/data/metrics.py` — Data quality metrics (missing data, anomalies)
- `/home/ricardo/github/freqtrade/freqtrade/data/btanalysis.py` — Backtest trade analysis data functions
- `/home/ricardo/github/freqtrade/freqtrade/data/entryexitanalysis.py` — Entry/exit signal analysis

## Key concepts
- **Data storage**: Parquet/feather/JSONGZ formats. Configurable data directory via `user_data_dir`.
- **Download**: `freqtrade download-data` CLI command, supports multiple exchanges and timeframes.
- **DataProvider**: strategy-facing object injected into every strategy, providing `ohlcv()`, `ticker()`, `orderbook()`, and utility methods.
- **Metrics**: `data/metrics.py` provides functions for detecting data quality issues.

## Related skills
- See `.agents/skills/freqtrade__exchange-integration` — the exchange that provides raw data
- See `.agents/skills/freqtrade__backtesting` — backtesting consumes historical data
- See `.agents/skills/freqtrade__strategy-engine` — the DataProvider is injected into strategies

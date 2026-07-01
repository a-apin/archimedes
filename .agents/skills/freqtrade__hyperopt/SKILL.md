---
name: freqtrade__hyperopt
description: Optuna-based hyperparameter optimization, loss functions, and parameter spaces
triggers: [hyperopt, hyperparameter optimization, optuna]
---

# Hyperparameter Optimization

**Source**: freqtrade
**Category**: Domain

## When to use this skill
Running hyperparameter optimization to tune strategy parameters, defining custom loss functions, configuring search spaces, or using epoch filters.

## Key files and folders
- `/home/ricardo/github/freqtrade/freqtrade/optimize/hyperopt/` — Hyperopt engine and interfaces
- `/home/ricardo/github/freqtrade/freqtrade/optimize/hyperopt_loss/` — Built-in loss functions (Sharpe, Sortino, Calmar, DrawDown, etc.)
- `/home/ricardo/github/freqtrade/freqtrade/optimize/hyperopt_tools.py` — Hyperopt utilities
- `/home/ricardo/github/freqtrade/freqtrade/optimize/hyperopt_epoch_filters.py` — Epoch filtering (custom filters for trial results)
- `/home/ricardo/github/freqtrade/freqtrade/optimize/space/` — Search space definitions

## Key concepts
- **Optuna**: underlying optimization framework. Freqtrade wraps it with trading-specific sampling and pruning.
- **Loss functions**: `HyperOptLoss` (Sharpe), `SortinoHyperOptLoss`, `CalmarHyperOptLoss`, `MaxDrawDownHyperOptLoss`, `ProfitDrawDownHyperOptLoss`, etc.
- **Spaces**: `buy_space`, `sell_space`, `roi_space`, `stoploss_space`, `trailing_space` — each corresponds to a group of strategy parameters.
- **Epoch filters**: skip or retry epochs based on intermediate results to avoid wasting compute.

## Decision points
- Use `--hyperopt-loss SharpeHyperOptLoss` for risk-adjusted returns; use `SortinoHyperOptLoss` if downside volatility matters most.
- Use `--spaces all` for full optimization; use `--spaces buy sell` to optimize only entry/exit signals.

## Related skills
- See `.agents/skills/freqtrade__strategy-engine` — the strategy parameters being optimized
- See `.agents/skills/freqtrade__backtesting` — backtesting engine that evaluates each epoch

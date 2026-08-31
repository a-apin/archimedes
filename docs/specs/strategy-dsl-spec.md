# Strategy DSL Specification

Closed-enum JSON schema for machine-readable strategy definitions produced by
the Archimedes fusion pipeline. A valid `strategy_spec` can be interpreted into
a `backtrader.Strategy` subclass and backtested without human intervention.

## Schema

```json
{
  "name": "string — unique identifier (snake_case)",
  "entry": {
    "indicator": "<indicator>",
    "operator": "<operator>",
    "threshold": "float | null — if null, cross-over between two data series",
    "secondary_indicator": "<indicator> | null"
  },
  "exit": {
    "indicator": "<indicator>",
    "operator": "<operator>",
    "threshold": "float | null",
    "secondary_indicator": "<indicator> | null"
  },
  "position_sizing": "<position_sizing>",
  "params": {
    "period": "int — primary lookback window (default 20)",
    "exit_period": "int — exit lookback window (default = period)",
    "stop_loss_pct": "float — decimal, e.g. 0.05 for 5% (default 0.0 = disabled)",
    "take_profit_pct": "float — decimal (default 0.0 = disabled)"
  }
}
```

## Closed-enum vocabulary

### Indicators

| Key              | Computation                     |
| ---------------- | ------------------------------- |
| `sma`            | Simple moving average           |
| `ema`            | Exponential moving average      |
| `rsi`            | Relative Strength Index         |
| `macd_line`      | MACD line (12/26 EMA diff)     |
| `macd_signal`    | MACD signal line (9-EMA of MACD)|
| `bb_upper`       | Bollinger Band upper rail       |
| `bb_lower`       | Bollinger Band lower rail       |
| `bb_middle`      | Bollinger Band middle (SMA)     |
| `atr`            | Average True Range              |
| `close`          | Raw close price                 |
| `volume`         | Raw volume                      |

### Operators

| Key    | Meaning                          |
| ------ | -------------------------------- |
| `gt`   | series > threshold / secondary   |
| `lt`   | series < threshold / secondary   |
| `crossover`  | series crosses above secondary |
| `crossunder` | series crosses below secondary |

### Position sizing

| Key              | Behavior                                |
| ---------------- | --------------------------------------- |
| `full_capital`   | All-in on each signal (default)         |
| `fixed_fraction` | Fixed % of portfolio per trade          |
| `kelly`          | Kelly Criterion fraction                |
| `atr_sized`      | Size inversely proportional to ATR      |

## Validation rules

1. `name` is required, non-empty, snake_case (`^[a-z][a-z0-9_]*$`).
2. `entry.indicator` and `entry.operator` are required.
3. When `operator` is `crossover` or `crossunder`, `secondary_indicator` must
   be provided (two-series comparison); `threshold` is ignored.
4. When `operator` is `gt` or `lt`, either `threshold` (float) or
   `secondary_indicator` must be provided, but not both.
5. `exit` follows the same rules as `entry`.
6. `params.period` defaults to 20 if absent; must be >= 2.
7. Unknown keys in any closed-enum field are rejected.

## Example — Faber 2007 SMA200

```json
{
  "name": "faber_sma200",
  "entry": {
    "indicator": "sma",
    "operator": "crossover",
    "threshold": null,
    "secondary_indicator": "close"
  },
  "exit": {
    "indicator": "close",
    "operator": "crossunder",
    "threshold": null,
    "secondary_indicator": "sma"
  },
  "position_sizing": "full_capital",
  "params": {
    "period": 200,
    "exit_period": 200
  }
}
```

## Pipeline integration

1. **Fusion LLM** emits a `strategy_spec` alongside the human-readable thesis.
2. `strategy_dsl.validate_strategy_spec()` checks the spec against these rules.
3. `dsl_to_backtrader.interpret_spec()` produces a `bt.Strategy` subclass.
4. `fusion_evaluator.evaluate_fusion_spec()` runs the full validate → backtest →
   rigor-gate pipeline.
5. The result feeds back into the strategy library and the generation job
   response, attaching backtest metrics and a rigor verdict.

## Verification

DSL primitives are validated against hand-written counterparts via fixture-based
comparison on real SPY OHLCV data (2004-01-02 through 2026-02-06, 5560 daily
bars). The test suite confirms that a DSL-interpreted Faber 2007 SMA200 strategy
produces Sharpe and max-drawdown metrics directionally consistent with the
hand-curated seed strategy within defined tolerances.

The verification tests live in
`backend/tests/services/test_fusion_evaluator_real_spy.py` and assert:

- DSL Faber Sharpe within 0.10 of the seed Sharpe (0.6335).
- DSL Faber max drawdown within 0.10 of the seed max drawdown (0.246).

The fixture CSV is generated from yfinance SPY data and stored at
`backend/tests/fixtures/spy_ohlcv_2004_2026.csv` — no network calls at test
time. Metrics are computed using backtrader's `SharpeRatio` and `DrawDown`
analyzers with `riskfreerate=0.0` (matching the analytics-engine runner).

## Parameter variants

The optional `parameter_variants` field enables CSCV-based overfitting detection
by specifying a small grid of alternative parameter values for one or more
indicators. When present, the fusion evaluator runs backtests for each
cartesian-product point and computes a real Probability of Backtest Overfitting
(PBO) via the Combinatorially Symmetric Cross-Validation (CSCV) algorithm
(Bailey, Borwein, Lopez de Prado, Zhu 2014).

### Schema

```json
{
  ...standard strategy_spec fields...,
  "parameter_variants": {
    "<indicator_alias>": [<value_1>, <value_2>, ..., <value_N>]
  }
}
```

### Rules

1. Keys must reference indicator aliases already present in `entry`/`exit`
   conditions (e.g., `"sma_200"` for a condition using `sma_200`).
2. Values must be a list of 2 to 8 numeric entries (int or float).
3. Unknown keys (not found in the spec's indicator set) are rejected at
   validation time.
4. Empty lists or lists with fewer than 2 entries are rejected.
5. The field is optional; when absent, PBO is reported as `None`.

### Example

```json
{
  "name": "SMA-200 Tactical Allocation",
  "asset_universe": ["SPY"],
  "rebalance_frequency": "monthly",
  "entry": {"gt": ["close", "sma_200"]},
  "exit": {"lt": ["close", "sma_200"]},
  "position_sizing": {"type": "full_invested_when_in_market"},
  "source_arxiv_ids": ["0706.1497"],
  "parameter_variants": {
    "sma_200": [150, 175, 200, 225, 250]
  }
}
```

### CSCV-PBO connection

When `parameter_variants` is provided with >= 2 variants, the fusion evaluator:

1. Expands the cartesian product of all variant dimensions.
2. Runs a backtest for each combination via `dsl_to_backtrader.interpret_variant`.
3. Extracts daily returns from each variant's equity curve.
4. Calls `rigor_evaluator.compute_pbo` with the variant returns matrix.
5. Attaches the resulting PBO score to the `RigorVerdict`.

The PBO score is a library-level metric (identical across all variants in the
grid). A PBO >= 0.5 indicates the in-sample-optimal strategy underperforms the
out-of-sample median in at least half of the CSCV partitions, suggesting
overfitting. The rigor gate fails strategies with PBO >= 0.5.

When fewer than 2 variants are available (or `parameter_variants` is absent),
PBO is honestly reported as `None` rather than the misleading `0.0`.

---
status: current
owner: Dan
updated: 2026-08-30
---

# Strategy DSL Specification

Closed-enum JSON schema for machine-readable strategy definitions. A valid
`strategy_spec` is interpreted into a `backtrader.Strategy` subclass and
backtested with no human in the loop, and the SAME spec drives the live signal
evaluator — so the language is deliberately small.

**This document describes the language that exists.** Source of truth, in order:
`backend/archimedes/services/strategy_dsl.py` (the grammar and the validator),
`backend/archimedes/services/dsl_to_backtrader.py` (the interpreter),
`backend/archimedes/services/strategy_signal_evaluator.py` (the live twin).
Anything not listed under "Vocabulary" below is not in the language; unbuilt
ideas live in [Not implemented — roadmap](#not-implemented--roadmap) and are
marked as such rather than described in the present tense.

> Rewritten 2026-08-30. The previous revision described a different, older
> design — a flat `{indicator, operator, threshold, secondary_indicator}` shape
> with MACD, Bollinger Bands, ATR, `crossover`/`crossunder`, Kelly sizing and
> stop-loss / take-profit parameters. None of that was ever built. It is kept
> below, correctly labelled, instead of being silently deleted.

## Schema

```json
{
  "name": "string — required, non-empty",
  "asset_universe": ["TICKER", "..."],
  "rebalance_frequency": "daily | weekly | monthly",
  "entry": { "<condition tree>": "..." },
  "exit": { "<condition tree>": "..." },
  "position_sizing": { "type": "<sizing type>" },
  "source_arxiv_ids": ["arxiv id", "..."],
  "look_ahead_safe": true,
  "parameter_variants": { "<indicator alias>": [1, 2, "…2-8 numbers"] }
}
```

All fields except `parameter_variants` are **required**. `indicators` is derived
by the validator from the operands actually referenced in `entry` / `exit` — a
spec may supply it, but the validator's own derivation wins.

## Vocabulary

### Indicators

An indicator operand is written `<stem>_<period>`, e.g. `sma_200`, `rsi_14`.
`period` is an integer in `[1, 10000]`.

| Stem | Computation | Backtest | Live |
| --- | --- | --- | --- |
| `sma` | Simple moving average of close | `bt.indicators.SimpleMovingAverage` | `rolling(N).mean()` |
| `ema` | Exponential moving average of close | `bt.indicators.ExponentialMovingAverage` | `ewm(span=N)` |
| `rsi` | Wilder RSI (SMMA smoothing, **not** Cutler's) | `bt.indicators.RSI` | hand-rolled Wilder |
| `momentum` | **Trailing N-bar return, centred on 0.0** — `close[0]/close[-N] - 1` | line arithmetic | `iloc[-1]/iloc[-N-1] - 1` |
| `realized_vol` | Annualized std of the last N daily returns, **ddof=1**, × √252 | `RealizedVolAnnualized` | `pct_change().tail(N).std() * sqrt(252)` |

Two conventions are load-bearing and have burned us before:

- **`momentum` is a return, not a price ratio.** `{"gt": ["momentum_20", 0]}`
  means "the trailing 20-bar return is positive". Under the ratio convention
  that condition is a tautology (prices are positive), and the backtest entered
  long on a 10% decline. Audit finding F1.
- **`rsi` is Wilder, both sides.** Cutler's RSI is a different indicator — 7+
  points apart on short windows. Audit finding F5.
- **`realized_vol` uses the sample std (ddof=1).** `bt.indicators.StandardDeviation`
  is a population std; using it would put the backtest and the live signal
  √(N/(N−1)) apart on every bar. Audit finding F6, closed 2026-08-30.

`backend/tests/test_interpreter_parity.py` pins every stem in this table
per-bar across both implementations.

### Price operands

`close`, `open`, `high`, `low`, `volume`. Usable anywhere an indicator alias is.

### Condition trees

`entry` and `exit` are recursive one-key dicts.

| Key | Shape | Meaning |
| --- | --- | --- |
| `gt` / `lt` / `gte` / `lte` | `[left, right]` — each a number, a price operand, or an indicator alias | comparison |
| `and` / `or` | list of ≥ 2 condition trees | logical |
| `not` | one condition tree | negation |

There is no `crossover` / `crossunder`. Express a crossover as a comparison of
the two series (`{"gt": ["close", "sma_200"]}`); the interpreter is a
state machine over positions, so "crossed above" and "is above" produce the same
entry once you account for the flat/held state.

### Position sizing

`position_sizing` is a dict with a required `type`.

| `type` | Behaviour | Extra keys |
| --- | --- | --- |
| `full_invested_when_in_market` | All available cash (× the 0.99 exposure buffer) into the instrument. | — |
| `equal_weight` | One equal slot of the declared universe: `order_target_percent(1/N × 0.99)`, where N is `universe_slots` (default `len(asset_universe)`). | — |
| `inverse_vol` | The `equal_weight` slot, scaled by `reference_vol_annual / realized vol`, capped at 2.0×, clamped to ≤ 1.0 of the account. | `reference_vol_annual` (optional, > 0, default `0.15`) |
| `volatility_target` | Full-cash size scaled by `annual_pct / realized vol`, capped at 2.0×. | `annual_pct` (**required**, > 0) |

Sizing happens **on entry only**. The interpreter enters when flat and the entry
tree is true, and closes when held and the exit tree is true; it does not
re-target an existing position on subsequent rebalance bars. A spec that needs
continuous re-weighting is not expressible today (see roadmap).

#### The single-feed seam — read this before reasoning about `equal_weight`

The interpreted strategy reads exactly ONE instrument (`self.data`). There is no
cross-sectional book, so "equal weight across N names" cannot be N simultaneous
target weights. It is a per-slot weight, controlled by the `universe_slots`
strategy parameter:

- **Single-feed runs** (`run_dsl_backtest` with one feed): the strategy holds the
  whole account, so one feed is one of N equal slots. `equal_weight` targets
  `1/N` and leaves `(N−1)/N` in cash. That is the honest reading — the run only
  ever observed one of the N names, so it must not claim the exposure of all N.
- **Sleeve runs** (`run_dsl_backtest_portfolio`, `paper_trading.replay_spec`):
  the runner capitalizes each ticker at `cash/N` and runs this same strategy once
  per ticker. The equal split happens OUTSIDE, so those callers pass
  `universe_slots=1` and each sleeve is fully invested in its own share.

Aggregate exposure is 1.0 either way. Applying `1/N` on both sides would size at
`1/N²`; that is why the parameter exists and why sleeve callers must set it.

`full_invested_when_in_market` and `volatility_target` ignore `universe_slots` by
definition — "full invested" means all-in on the account it was given, and a vol
target is an account-level target.

### Rebalance frequency

`daily` / `weekly` / `monthly`, as trading-day proxies: **1 / 5 / 21 bars**. Not
calendar weeks or months. The live evaluator replays the same table
(`dsl_to_backtrader.rebalance_period_bars`) so the two interpreters cannot drift
on cadence (audit F3).

### `look_ahead_safe`

Must be `true`. A spec with `look_ahead_safe: false` is rejected by the
validator, not merely flagged.

## Validation rules

1. All required fields present; unknown enum members rejected.
2. `name` non-empty. There is no snake_case constraint — the field carries the
   LLM's working title.
3. `asset_universe` a non-empty list of non-empty strings.
4. `rebalance_frequency` ∈ {`daily`, `weekly`, `monthly`}.
5. Condition trees: exactly one key per node; comparison ops take exactly 2
   arguments; `and`/`or` take a list of ≥ 2; unknown operands and unknown
   operators are rejected.
6. Indicator periods ∈ `[1, 10000]`.
7. `position_sizing.type` ∈ the four types above. `volatility_target` requires
   `annual_pct` > 0. `inverse_vol` rejects a `reference_vol_annual` that is
   present but not a positive number.
8. `source_arxiv_ids` a list of non-empty strings.
9. `look_ahead_safe` must be boolean **and** `true`.
10. `parameter_variants` keys must reference an alias used in `entry`/`exit`;
    values must be lists of 2–8 numbers.

## Example — Faber 2007 SMA200

The reference spec, verbatim from `strategy_dsl.FABER_2007_SPEC`:

```json
{
  "name": "SMA-200 Tactical Allocation",
  "asset_universe": ["SPY"],
  "rebalance_frequency": "monthly",
  "entry": {"gt": ["close", "sma_200"]},
  "exit": {"lt": ["close", "sma_200"]},
  "position_sizing": {"type": "full_invested_when_in_market"},
  "source_arxiv_ids": ["0706.1497"],
  "look_ahead_safe": true
}
```

## Example — inverse-vol over a calm-regime filter

```json
{
  "name": "Calm-regime inverse-vol allocation",
  "asset_universe": ["SPY", "QQQ", "IWM", "EFA"],
  "rebalance_frequency": "weekly",
  "entry": {"and": [
    {"gt": ["close", "sma_200"]},
    {"lt": ["realized_vol_20", 0.25]}
  ]},
  "exit": {"or": [
    {"lt": ["close", "sma_200"]},
    {"gt": ["realized_vol_20", 0.35]}
  ]},
  "position_sizing": {"type": "inverse_vol", "reference_vol_annual": 0.15},
  "source_arxiv_ids": ["0706.1497", "1704.03022"],
  "look_ahead_safe": true
}
```

## Pipeline integration

1. **Generation** emits a `strategy_spec` alongside the human-readable thesis.
   The prompt contract is `strategy_fusion._SPEC_CONTRACT` and must list exactly
   the vocabulary above.
2. `debate_engine._dsl_conformance_ok` drops any spec whose indicator stems fall
   outside `dsl_to_backtrader.SUPPORTED_INDICATORS`, before C-rigor — a
   `DSLError` inside the leaderboard build would take the whole build down.
3. `strategy_dsl.validate_strategy_spec()` checks the spec against these rules.
4. `dsl_to_backtrader.interpret_spec()` produces the `bt.Strategy` subclass.
5. `fusion_evaluator.evaluate_fusion_spec()` runs validate → backtest →
   rigor gate.
6. The result feeds the strategy library and the generation job response, with
   backtest metrics and a rigor verdict attached.

`INDICATOR_NAMES` (what validates) and `SUPPORTED_INDICATORS` (what the
interpreter can build) must be equal; a test asserts it, because a name that is
legal to write and fatal to run is precisely the `realized_vol` defect.

## Verification

DSL primitives are validated against hand-written counterparts via fixture-based
comparison on real SPY OHLCV data (2004-01-02 through 2026-02-06, 5560 daily
bars). The tests live in
`backend/tests/services/test_fusion_evaluator_real_spy.py` and assert:

- DSL Faber Sharpe within 0.10 of the seed Sharpe (0.6335).
- DSL Faber max drawdown within 0.10 of the seed max drawdown (0.246).

The fixture CSV is at `backend/tests/fixtures/spy_ohlcv_2004_2026.csv` — no
network at test time.

Related suites: `test_interpreter_parity.py` (backtest ↔ live, per bar),
`test_dsl_sizing_and_indicators.py` (sizing behaviour, `realized_vol`, rejected
orders), `test_dsl_momentum_convention.py`, `test_dsl_rsi_parity.py`.

## Parameter variants

The optional `parameter_variants` field enables CSCV-based overfitting detection
by specifying a small grid of alternative parameter values for one or more
indicators. When present, the fusion evaluator runs backtests for each
cartesian-product point and computes a real Probability of Backtest Overfitting
(PBO) via Combinatorially Symmetric Cross-Validation (Bailey, Borwein,
Lopez de Prado, Zhu 2014).

```json
{
  "…standard strategy_spec fields…": "…",
  "parameter_variants": { "sma_200": [150, 175, 200, 225, 250] }
}
```

Rules:

1. Keys must reference indicator aliases already present in `entry`/`exit`.
2. Values must be 2–8 numeric entries.
3. Unknown keys are rejected at validation time.
4. The field is optional; when absent, PBO is reported as `None` — never `0.0`.

When ≥ 2 variants are present the evaluator expands the cartesian product, runs
one backtest per combination via `interpret_variant`, extracts each variant's
daily returns, and calls `rigor_evaluator.compute_pbo`. PBO is a library-level
metric, identical across the grid. PBO ≥ 0.5 means the in-sample-optimal
parameterization underperforms the out-of-sample median in at least half the
CSCV partitions; the rigor gate fails those strategies.

## Known limitations

Real, currently-true constraints — distinct from the roadmap below.

- **One instrument per interpreted strategy.** Multi-asset specs are evaluated
  as independent equal-cash sleeves and summed. There is no cross-sectional
  ranking, no cross-sleeve rebalancing, and no pairs/spread construction.
- **Sizing is entry-only.** A held position is not re-weighted as its target
  weight drifts; only entry and exit are decided per rebalance bar.
- **Long-only, no shorting.** `entry` opens a long; `exit` closes it.
- **`volatility_target` can request unfundable leverage.** Its scale is capped at
  2.0×, and the backtest broker has no margin, so on a calm series every order is
  margin-rejected and the strategy stays flat for the whole run. As of
  2026-08-30 that is at least *logged* (`notify_order` emits a WARNING naming the
  strategy) instead of silently producing an all-cash equity curve. The sizing
  itself is unchanged — clamping it would move published numbers.
- **The two vol estimators differ by design.** The `realized_vol` *indicator* is
  a ddof=1 sample std, because it has a live twin it must equal. The *sizing*
  estimator inside `volatility_target` / `inverse_vol` is a 20-bar RMS of returns
  about zero, kept byte-for-byte because published `volatility_target` numbers
  came out of it. Immaterial for daily returns; documented so neither gets
  "reconciled" into the other by accident.

## Not implemented — roadmap

**None of the following exists.** They are listed because earlier revisions of
this document described them in the present tense, and because they are the
obvious next asks. Do not write a spec that uses them; the validator rejects
them and the interpreter cannot build them.

| Feature | Status | Note |
| --- | --- | --- |
| `macd_line`, `macd_signal` | not implemented | Expressible today as a difference of two `ema_N` only if the DSL grows arithmetic operands, which it has not. |
| `bb_upper`, `bb_lower`, `bb_middle` (Bollinger) | not implemented | Needs a rolling-std band around an SMA; the `RealizedVolAnnualized` machinery is the closest existing piece. |
| `atr` | not implemented | Needs high/low/close true range; only close-based indicators exist. |
| `crossover` / `crossunder` operators | not implemented | Use a `gt`/`lt` comparison of the two series. |
| `stop_loss_pct` / `take_profit_pct` | not implemented | Requires intra-position order management; the interpreter only decides at rebalance bars. |
| `kelly` position sizing | not implemented | A `kelly_fraction` is computed on the *passport* (`StrategyPassport.kelly_fraction`) as a reported statistic; it is not a DSL sizing type and does not size any backtest. |
| `risk_parity` position sizing | not implemented | Present on the passport-level `models.strategy.PositionSizing` enum, which is a **different** enum from the DSL's `POSITION_SIZING_TYPES`. Do not confuse the two. |
| `atr_sized` / `fixed_fraction` sizing | not implemented | — |
| Shorting | not implemented | — |
| Continuous re-weighting of a held position | not implemented | See "Sizing is entry-only" above. |
| Cross-sectional (rank-based) selection | not implemented | The interpreter is single-feed; see the seam note. |

Two enums with overlapping names are a standing trap: `models/strategy.py`'s
`PositionSizing` (`equal_weight`, `risk_parity`, `kelly`, `inverse_vol`) is
passport metadata describing intent. `strategy_dsl.POSITION_SIZING_TYPES`
(`full_invested_when_in_market`, `equal_weight`, `inverse_vol`,
`volatility_target`) is the executable set. Only the latter changes what a
backtest does.

# Transaction-cost + turnover model (analytics-engine)

> **Status:** Shipped 2026-06-11 (third wave, item 1 of
> [`third-wave-handover.md`](../handovers/third-wave-handover.md) §2 / Priority 2.2 of
> [`quant-roadmap.md`](../plans/quant-roadmap.md)). Author: Önder (quant lane).
> Code: `analytics-engine/src/archimedes_analytics_engine/costs.py` + wiring in
> `engine.py`. Tests: `analytics-engine/tests/test_costs.py`.

## Why

The Kalman pairs strategy cost-bled to −1.47 Sharpe on 1174 trades — execution
realism is decisive, and until now the engine reported nothing about *how much*
of a strategy's edge costs consume. This model makes cost sensitivity a
first-class, reported quantity so re-tests of the 20 `CANDIDATE`s (and every
future strategy) are credible.

## What it adds

### 1. `CostModel` — per-side costs in bps, per-symbol overrides

```python
from archimedes_analytics_engine.costs import CostModel

model = CostModel(default_bps=10.0, slippage_bps=5.0, per_symbol={"EEM": 25.0})
run_multi_backtest(frames, names=["SPY", "EEM"], cost_model=model, ...)
```

All three runners (`run_backtest`, `run_pairs_backtest`, `run_multi_backtest`)
accept an optional `cost_model=`. When given, it supersedes the flat
`transaction_cost_bps` / `slippage_bps` arguments; per-symbol overrides are
matched against the feed names. When omitted, behaviour is byte-identical to
before (flat bps) — fully backward compatible.

### 2. Turnover + cost metrics on every `BacktestResult`

| Field | Definition |
| --- | --- |
| `turnover_annualized` | One-way annualized turnover: `(two_way_notional / 2) / mean(equity) / years`. A strategy replacing its whole book once a year scores 1.0. |
| `traded_notional` | Total two-way traded notional over the backtest (sum of \|size\| × execution price). |
| `total_commission_paid` | Commission the broker actually charged. |
| `cost_drag_annual_pct` | Annualized commission as % of average equity — the headline "how much did costs eat" number. |
| `break_even_cost_bps` | The per-side cost level at which the *gross* CAGR is fully consumed: `gross_cagr / (2 × one-way turnover) × 10⁴`. If a strategy's break-even is below its realistic cost, the alpha is not implementable — the Kalman failure mode, now quantified. Floored at 0; `None` when there is no turnover. |
| `gross_sharpe_ratio` | Sharpe with commissions added back to the return series, computed under the *same convention* as the net `sharpe_ratio` (bt SharpeRatio: geometric daily risk-free at 5%, population stddev, √252 annualization) so gross-vs-net is apples-to-apples. |

Measurement is done by a `TurnoverAnalyzer` (`bt.Analyzer`) that records every
completed execution's notional and commission, plus a per-bar commission series
positionally aligned with the TimeReturn series — gross daily returns are
reconstructed as `net_r + commission / prior_equity`. If the alignment ever
breaks, gross metrics are reported as `None` rather than misreported.

### 3. `no_trade_band` — the turnover penalty strategies opt into

```python
from archimedes_analytics_engine.costs import no_trade_band, position_weight

w_now = position_weight(self, d)
w_target = no_trade_band(w_now, w_model, band=0.01)  # hold unless |Δw| ≥ 1%
if w_target != w_now:
    self.order_target_percent(data=d, target=w_target)
```

Suppresses rebalances smaller than a weight band — the standard, simple
turnover-control device. No existing strategy was modified in this PR (re-tests
through the band are roadmap step 4, a separate change per the add-only /
one-change-per-PR discipline).

## Conventions and caveats (read before citing the numbers)

- **Costs are per side.** 10 bps means 0.10% on the buy and 0.10% on the
  sell — identical to the legacy flat `transaction_cost_bps` semantics.
- **"Gross" adds back commissions only, not slippage.** Slippage is embedded in
  execution prices by the broker and cannot be recovered from accounting; when
  `slippage_bps > 0`, gross metrics are a *lower bound* on frictionless
  performance. Disclosed here so nobody over-reads the gross Sharpe.
- **`break_even_cost_bps` assumes cost scales linearly with turnover** (no
  market-impact convexity). It is a screening number, not a capacity model.
- **No per-symbol default cost table is shipped.** Per the honesty discipline,
  we don't hand-assert spread estimates; callers supply their own assumptions
  explicitly via `per_symbol`, and those assumptions are visible at the call
  site.
- Fixture schema is unchanged — `backtest_fixtures.json` is untouched (add-only
  law). Surfacing turnover fields in *future* fixture entries is a follow-up
  decision for the fixture/backend join, not made unilaterally here.

## Verify

```bash
cd analytics-engine && uv run pytest                  # 59 passed (11 new in test_costs.py)
uv run --with ruff ruff format --check . && uv run --with ruff ruff check --select E9,F63,F7,F40 .
git diff main -- analytics-engine/strategies/backtest_fixtures.json   # empty
```

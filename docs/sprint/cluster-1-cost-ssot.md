# Cluster 1 — cost SSOT (A1)

**Pair with [cluster-3](cluster-3-backtest-models.md) in one session.** Both are small files;
together they are the best value-per-token in the sprint.

Read [README](README.md) session rules first.

## Why

Three engines write to one `backtest_results` table, graded by one gate that cannot tell them
apart. `grep -rn "set_slippage" --include="*.py" .` returns **exactly two hits, both Engine A**
— re-verified 2026-08-16. Engine C, the engine that grades the *generated* strategies we want
to sell, charges commission only and systematically flatters its own output against the curated
library it is ranked beside. **This blocks the re-run**: re-running before closing it publishes
a fresh set of biased numbers with more authority attached.

Literal cost parity is not reachable in two weeks (Almgren impact needs ADV inside a custom
`bt.CommInfoBase`). The target is an **identical cost floor** everywhere — per-side linear bps +
proportional slippage from one SSOT — with Engine B's Almgren term retained as an additional
*disclosed* haircut, which makes B stricter and never looser.

## Files (all small — read whole)

| File | Lines | Edit |
|---|---|---|
| `analytics-engine/src/archimedes_analytics_engine/costs.py` | 155 | add `DEFAULT_COST_MODEL` + `cost_model_fingerprint()` |
| `analytics-engine/.../cli.py` | — | `run_command`: build a `CostModel`, pass `cost_model=` to all three runners |
| `backend/archimedes/services/portfolio_backtester.py` | 1180 | **window-read only** — `_simulate_portfolio` + `:509` |

Engine C's two sites live in [cluster-2](cluster-2-fusion-engine.md) — same file, one open.

## Edits

1. **`costs.py`** — `CostModel.apply_to_broker` (`:65-78`) *already* does commission **and**
   `set_slippage_perc`. Add `DEFAULT_COST_MODEL` as the single source and
   `cost_model_fingerprint()` returning a stable id that gets stamped on every row.
2. **`cli.py run_command`** — build a `CostModel`, pass `cost_model=` to all three runners.
   This also revives the dead per-symbol override path; `_configure_broker` already honours it.
3. **`portfolio_backtester._simulate_portfolio`** — add an explicit slippage term on
   `sum(|Δw|)` so the floor matches. Fix the misleading `"slippage_bps": tx_cost_bps` at
   `portfolio_backtester.py:509`.

## Test — `test_cost_parity.py`, both suites

- Same buy-and-hold-equivalent run on a **deterministic ramp** through all three engines lands
  within tolerance and **strictly below** the zero-cost run.
- A spy asserts `set_slippage_perc` is called on the Engine C cerebro.
- A regression pins that `tx=0, slip=0` reproduces Engine C's pre-change numbers **exactly**.

```bash
pytest backend/tests/test_cost_parity.py -q
cd analytics-engine && uv run pytest tests/test_cost_parity.py -q
```

## Anti-goals

- Do **not** attempt Almgren impact inside backtrader. Not this sprint.
- Do **not** change any rigor threshold.
- Do not touch `engine.py:399` — Engine A is already correct; it is the reference.

## Gate

**This test passing is what authorises the re-run** ([a6-rerun](a6-rerun.md)). If it does not
exist and pass, skip the re-run entirely and leave #1226's banner up. Do not run it anyway.

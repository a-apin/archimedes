# Cluster 3 — backtest models + mapper (A7-surgical · A2-lite · A3/A4 mapper)

**Highest value-per-token in the sprint.** Four doc items, three small files, ~25 lines for the
headline fix. Pair with [cluster-1](cluster-1-cost-ssot.md) in one session.

Read [README](README.md) session rules first.

## Files — all small, read whole

| File | Lines |
|---|---|
| `backend/archimedes/models/backtest.py` | 202 |
| `backend/archimedes/services/backtest_mapper.py` | 202 |
| `backend/archimedes/models/backtest_store.py` | — |

## 1. A7-surgical — delete the second gate ⭐

> *"The highest-value single action in this workstream, and it is surgical."*

`BacktestResult.passes_rigor_gate` uses **entirely different thresholds** from the canonical
gate (`sharpe>0.5`, `dsr_p>0.95`, `pbo<0.5`, `oos/is>=0.5`, `sharpe_vs_paper>=0.5`,
`max_dd<0.5`) — and it is the one `generation_pipeline.py:1573` uses. **So generated and curated
strategies are graded by different gates today.** The comment above that call claims parity with
`_to_strategy_response`; verified false — that route uses `verdict.passes` from `live_rigor_gate`.

Verified present 2026-08-16:
- [`models/backtest.py:126`](../../backend/archimedes/models/backtest.py#L126) `passes_validation`
- [`models/backtest.py:144`](../../backend/archimedes/models/backtest.py#L144) `passes_rigor_gate`
- `:159` `if not self.passes_validation` — internal caller inside `passes_rigor_gate`, so the
  two delete together cleanly.

**Do:** delete both properties → re-point `generation_pipeline.py:1573` at `live_rigor_gate`'s
`verdict.passes` → **retract the false comment above it in the same diff.**

**Targeted equivalence test** (not the full golden-vector harness — that is buffer work): freeze
the return series for the 3 passing strategies + 2 zero-Sharpe + 1 degenerate, assert the
generated and curated paths now produce identical verdicts.

```bash
grep -rn "passes_rigor_gate" backend/archimedes/models/backtest.py   # must return nothing
pytest backend/tests/test_gate_equivalence.py -q
```

## 2. A2-lite — provenance

`backtest_engine` is **already a column** (`models/backtest.py:91`, `backtest_store.py:70`) and
all three engines already write their tag — it just appears in **zero API schemas and zero UI
files**. This is plumbing, not new capability.

- Add `cost_model_id` (from cluster-1's `cost_model_fingerprint()`).
- Declare `backtest_engine` + `cost_model_id` on `backtest_mapper.py:35 EngineMetricsModel` —
  its `extra="ignore"` is what silently eats them at the boundary.
- Carry through `models/backtest.py`, `backtest_store.py`, `api/schemas.py`,
  `leaderboard_schemas.py`.
- **Fail closed:** `insert_backtest_if_missing` **raises** on `backtest_engine is None`. No
  unattributed row can ever be written again.

**Deferred to buffer** (A2-full): `slippage_bps`, `turnover_annualized`, `traded_notional`,
`total_commission_paid`, `cost_drag_annual_pct`, `break_even_cost_bps`, `gross_sharpe_ratio`,
`portfolio_construction`, `daily_return_dates`.

## 3. A3-part — the look-ahead OR

`backtest_mapper.py:165-167` — `look_ahead_audit_passed` is the **OR** of a real AST audit and a
broker-config check whose own docstring admits it is *"unconditionally True for every run"*
(`engine.py:103-114`). An OR with an always-true operand is always true. Drop the broker-config
leg from the OR; keep the AST audit as the only signal.

## 4. A4-part — stop coercing correlations

`backtest_mapper.py:180` — stop coercing `correlation_to_spy` / `correlation_to_btc` to `0.0`;
serve `null`. Populating them needs a benchmark feed in every run: **not this sprint.** A null
is honest; a fabricated `0.0` is not.

## Anti-goals

- Do **not** build `cohort_results` or the 7-way golden-vector harness — buffer.
- Do **not** delete the persisted rigor columns yet (A3/cluster-4 switches the *read* path;
  column drop is next sprint).
- Never weaken a threshold.

## Done when

`grep passes_rigor_gate models/backtest.py` is empty · every new row carries a non-null
`backtest_engine` + `cost_model_id` or the insert raises · equivalence test green.

# Cluster 2 — fusion_evaluator.py (A1c · A4 · A8-label · A7-adapter)

**Four doc items, one file open.** `fusion_evaluator.py` is 864 lines — **never read whole**
(~10k tokens). Grep to each anchor, window-read ±40 lines, edit, move on.

Read [README](README.md) session rules first.

```bash
grep -n "setcommission\|adddata\|data_feed\|apply_rigor_gate\|run_dsl_backtest_portfolio" \
  backend/archimedes/services/fusion_evaluator.py
```

## 1. A1c — Engine C slippage (depends on [cluster-1](cluster-1-cost-ssot.md))

`:222-223` and `:379-380` — replace the bare `setcommission` with
`CostModel.apply_to_broker(cerebro)` using cluster-1's `DEFAULT_COST_MODEL`. One line per site.
`apply_to_broker` (`costs.py:65-78`) already does commission **and** `set_slippage_perc`.

Stamp `cost_model_id` on the result so the row carries its fingerprint.

## 2. A4 — the dead condition that nulls every DSL start date

`:285` — the condition is **dead**: `data_feed` is assigned at `:216-219` so it is never `None`.
That is why DSL rows always get `backtest_start=None`, while `_run_variant_backtest:438` uses a
*different* condition for the same job. Thread the real panel dates through both.

This one matters for the re-run: a null `backtest_start` makes a row un-auditable.

## 3. A8-label — stop the sleeve fiction being silent

`run_dsl_backtest_portfolio` (`:516`) runs the **same single-asset spec** once per asset with
`initial_cash/N` sleeves and averages the results — precisely the pattern #1203 ripped out of
Engine A as wrong. `run_dsl_backtest` does exactly one `cerebro.adddata(...)` (`:221`, same at
`:378`). An "inverse-vol 5-asset" generated strategy is really **five independent 100%-long
single-asset backtests, equal-weighted**.

The full multi-feed interpreter is ~3.75d and is **cut**. What ships is the label:

- Stamp `backtest_engine="dsl-fusion-sleeves"` and
  `portfolio_construction="n_independent_sleeves_equal_weight"` on rows from
  `run_dsl_backtest_portfolio`.
- Surface both on the passport and leaderboard (rides cluster-3's A2-lite plumbing).

That converts a silent lie into a disclosed limitation. It is a real improvement over today and
it is the truth-first answer.

## 4. A7-adapter — re-point thresholds only

`:609 apply_rigor_gate` — **keep it as an adapter**, re-point its thresholds at
`rigor_profiles.py`'s ladder. Do not delete it: it owns the variant-grid → `num_trials` / CSCV-
matrix assembly, which is genuinely fusion-specific and is exactly what `run_rigor_gate` wants
as input.

If #1223 (num_trials by provenance) is in flight, **coordinate before touching this** — it
collides here.

## Also in this file, do NOT fix now

`fusion_market_data.py:53 _MAX_ASSETS = 6` caps the fan-out. Leave it — but the Generate-page
copy must state the cap (see [cluster-7](cluster-7-ui-surface.md)): the picker offers 281 assets,
Engine C caps at 6 per spec and grades them as independent sleeves.

## Test

```bash
pytest backend/tests/test_fusion_evaluator.py -q
```

Add: a spy asserting `set_slippage_perc` is called on the Engine C cerebro (shared with
cluster-1's parity test); a case asserting a DSL row now carries non-null `backtest_start`; a
case asserting sleeve rows carry the `portfolio_construction` label.

## Anti-goals

- **Do not build the multi-asset interpreter.** Cut. Next sprint's design is in the source doc.
- Do not touch `strategy_dsl.py` validation or `debate_engine.py:151 _dsl_conformance_ok` —
  that is A8-contract-tests, buffer work.
- Do not raise `_MAX_ASSETS`.

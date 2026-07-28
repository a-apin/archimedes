# Handover: Building the Second Wave of Multi-Asset Strategies

> **Audience:** the agent (or teammate) implementing all three phases of
> [`second-wave-multi-asset-strategies.md`](../plans/second-wave-multi-asset-strategies.md).
> **Author:** Önder, 2026-06-11. **Prereq read:** that roadmap + the repo
> `CLAUDE.md`. This doc is the *how* and *where*; the roadmap is the *what* and *why*.

## 1. What wave 1 already shipped (your foundation)

Merged in [PR #528](https://github.com/a-apin/archimedes/pull/528) (2026-06-11):

- **Engine:** `analytics-engine/src/archimedes_analytics_engine/engine.py` now has
  **`run_pairs_backtest(prices_a, prices_b, *, strategy_cls, initial_cash, name_a, name_b, transaction_cost_bps, slippage_bps)`** — two inner-joined, bar-aligned feeds in one
  `cerebro` run. It shares `_add_analyzers(cerebro)` and `_extract_result(...)` with the
  single-asset `run_backtest`, so all three eventual runners (single / pair / N) produce
  identical metric shapes. **Reuse these helpers — do not re-derive metric extraction.**
- **Strategies (7 new):** `gatev_2006_pairs_distance.py` (the pairs flagship + your
  template for multi-asset metadata) plus 6 single-asset ones in
  `analytics-engine/strategies/`.
- **Fixture generator:** `analytics-engine/scripts/regen_fixtures.py` — computes real
  DSR/PBO/OOS/Kelly from live backtests and writes `backtest_fixtures.json`. **Add-only.**
- **Instruments:** `instruments.py` `OPERATION_TO_SYMBOL` gained `GLD`, `GDX`, `IVV`.

## 2. The engine work you must do first (Phase 2+ blocker)

Generalize the 2-feed runner to N feeds. Concretely, add to `engine.py`:

```python
def run_multi_backtest(prices_list, *, strategy_cls, initial_cash,
                       names=None, transaction_cost_bps=10, slippage_bps=0):
    # inner-join all frames on common index; add N PandasData feeds;
    # reuse _add_analyzers + _extract_result exactly as run_pairs_backtest does.
```

Mirror `run_pairs_backtest` line-for-line; the only change is the loop over N frames
and an N-way index intersection. Write `analytics-engine/tests/test_multi_engine.py`
mirroring `test_pairs_engine.py` (runs, aligns on common dates, raises on disjoint).
Ship as its own PR. **Phase 1 needs none of this — do Phase 1 first to bank wins.**

## 3. How a strategy becomes visible in the product (the full path)

The backend is **directory-driven** — no registry to edit:

1. Write `analytics-engine/strategies/<name>.py` (a `bt.Strategy` subclass + module-level
   metadata constants). Copy the metadata block from `gatev_2006_pairs_distance.py`
   verbatim and edit values. **`PAPER_TITLE` and `REGIME_TAG` are required** or the
   backend skips / rejects the strategy.
2. Add its fixture entry by running `scripts/regen_fixtures.py` (see §4). Fixture key =
   the file stem (filename without `.py`).
3. On backend restart, `backend/archimedes/services/strategy_provider.py`
   (`LocalStrategyProvider.refresh()`) scans the strategies dir, reads metadata via AST,
   joins the fixture, and serves it at `GET /api/strategies/`. **Nothing else to wire.**

Metadata keys the loader reads (it lowercases them): `PAPER_TITLE`, `PAPER_AUTHORS`,
`PAPER_VENUE`, `PAPER_YEAR`, `PAPER_DOI`, `PAPER_ARXIV_ID`, `PAPER_CITATION_COUNT`,
`METHODOLOGY_SUMMARY`, `METHODOLOGY_TEXT`, `PAPER_CLAIMED_SHARPE/CAGR/MAX_DD`,
`ASSET_UNIVERSE`, `POSITION_SIZING`, `REBALANCE_FREQUENCY`, `RISK_PROFILES`, `REGIME_TAG`,
`STATUS`, `CURATOR_NOTE`, `EXTRACTION_LLM`.

## 4. The fixture-generation pattern (honesty-critical — read carefully)

`scripts/regen_fixtures.py` is your template. To add new strategies:
- Append to `NEW_SINGLE_SPECS` (single-asset: `{stem, symbol, tx_cost_bps}`) or
  `NEW_PAIR_SPECS` (`{stem, pair: (A, B), tx_cost_bps}`). For N-asset strategies you'll
  add a `NEW_MULTI_SPECS` block that calls your new `run_multi_backtest`.
- Run **dry-run first**: `cd analytics-engine && uv run python scripts/regen_fixtures.py`
  — eyeball the printed Sharpe/DSR/PBO/gate table. Then `--write`.
- **Requires network** (yfinance). If unavailable, **STOP and report** — never hand-type
  metrics.
- **Add-only law:** the six legacy entries do NOT reproduce on current data (data drift;
  `capital_preservation_tbill` models a T-bill yield, not a TLT buy-hold). The script
  refuses to overwrite existing keys. Keep it that way.
- DSR/OOS/Kelly are imported from `regen_buy_hold_fixture.py` (single source for the
  formulas — Önder's lane; don't fork them). PBO mirrors backend
  `archimedes/services/rigor_evaluator.py::compute_pbo`. `num_trials_in_selection` =
  full library size (conservative DSR penalty); PBO is cohort-level (documented in-script).

## 5. Provenance discipline (this is the product, not a chore)

- **Verify each paper's claimed numbers via web search before writing `paper_claimed_*`.**
  Wave 1 verified Gatev's ~11% figure and left Sharpe null because the paper has no clean
  single-pair Sharpe. Do the same. **Never invent a number.**
- **If you can't implement a paper faithfully, drop it.** Don't mis-attribute. Wave 1
  rejected Jegadeesh 1990 (cross-sectional, engine couldn't do it) and Lo-Mamaysky-Wang
  2000 (pattern-recognition, not the MA-crossover it's often mis-cited as). Document
  rejections — that *is* the rigor signal.
- For folklore rules with a named inventor but no peer-reviewed origin, anchor to the
  academic test (e.g. Brock, Lakonishok & LeBaron 1992) and note the Sullivan-Timmermann-White
  1999 data-snooping caveat in `METHODOLOGY_TEXT`.
- **Expect many strategies to fail the gate — that's correct.** Admit as CANDIDATE; never
  weaken thresholds to force a pass. Parameter-tuning to clear the gate is a separate,
  later task, not part of adding a strategy.

## 6. Testing & verification (must be green before any PR)

```bash
cd analytics-engine && uv run pytest          # analytics-engine suite (incl. your new engine/strategy tests)
# from repo root, in the `archimedes` conda env:
pytest backend/tests/test_strategy_endpoints.py backend/tests/services/test_strategy_provider.py
ruff format --check . && ruff check --select E9,F63,F7,F40 .   # the CI hard-block gate
```

Tests must be **hermetic** (synthetic data, no network) — see `test_pairs_engine.py` for
the synthetic-OHLCV pattern. The fixture *script* uses network; the *tests* must not.
Extend `backend/tests/test_strategy_endpoints.py` with presence/shape assertions for each
new strategy (see the wave-1 additions there for the pattern).

## 7. Conventions (from CLAUDE.md — don't relearn the hard way)

- Branch `onder/<short-name>` (or your handle) off `main`; PR → **merge commit only**
  (`gh pr merge <n> --merge`). One logical change per PR. Phase 1 / engine / Phase 2 /
  Phase 3 should be separate PRs.
- Title marker: new strategies / new capability → end title with `!minor`. Bug-fixes/docs
  → no marker.
- Never force-push `main`; never commit secrets/`.env`.
- `main` moves fast — rebase late, merge quickly.
- Ruff: `line-length = 120`. Run `ruff format .` before committing or install pre-commit.

## 8. Suggested order of operations

1. **Phase 1.3** (extra economic pairs) — pure data + fixture, smallest possible PR, proves
   your loop end-to-end.
2. **Phase 1.1 / 1.2** (cointegration, Kalman) — real new logic on the existing 2-feed engine.
3. **Engine milestone** (`run_multi_backtest` + tests) — its own PR.
4. **Phase 2** (cross-sectional momentum, dual momentum, risk parity).
5. **Phase 3** (PCA stat-arb).

When in doubt on a quant/provenance call, leave it CANDIDATE, document the uncertainty in
the PR, and flag Önder for review — don't guess your way past the rigor gate.

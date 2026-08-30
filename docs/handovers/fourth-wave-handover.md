# Handover: Fourth Wave (the "gate-as-product" wave) — executable brief for the next agent

> **Audience:** the agent (or teammate) picking up the quant lane after the third
> wave. **Author:** Önder, 2026-06-11. **Prereq read:** the repo `CLAUDE.md`,
> [`quant-roadmap.md`](../plans/quant-roadmap.md) (the what/why),
> [`third-wave-handover.md`](third-wave-handover.md) (§5-§10 conventions all still
> apply — this doc does not repeat them, it extends them), and
> [`third-wave-retest.md`](../quant/third-wave-retest.md) (the evidence base you inherit).
>
> **Operating context:** assume the team may be offline. Solo boundaries are
> task-specific this wave — each task below states exactly where you must stop.
> The blunt summary: **task 1 is buildable solo, task 2 is "prepare the package,
> Dan decides," task 3 is buildable solo in `services/` but anything touching
> `chain/` or contracts stops for a human.**

## 0. What the third wave left you (your new toolbox)

All merged to `main` 2026-06-11 (PRs #540-#543), all deployed:

- **Cost model** (`analytics-engine/src/.../costs.py`): `CostModel` (per-symbol
  per-side bps), `TurnoverAnalyzer`, `no_trade_band`/`position_weight`.
  `BacktestResult` now carries `turnover_annualized`, `traded_notional`,
  `total_commission_paid`, `cost_drag_annual_pct`, `break_even_cost_bps`,
  `gross_sharpe_ratio`. Conventions in
  [`transaction-cost-turnover-model.md`](../specs/transaction-cost-turnover-model.md).
- **Walk-forward harness** (`walk_forward.py`): train-only param selection,
  OOS-tail evaluation, `n_param_combos` exposed. All three engine runners now
  take `strategy_params=` and `cost_model=`.
- **Library: 23 strategies, 2 pass the gate** (unchanged: Moreira-Muir,
  MOP TSMOM). The faithful-scale Gatev portfolio is an honest CANDIDATE
  (Sharpe −1.59 — the distance alpha is decayed at the paper's own scale).
- **The re-test findings you must not re-litigate:** failures are alpha-absent,
  not cost-bled (every negative-net CANDIDATE is negative *gross*); walk-forward
  rescues nothing (best CANDIDATE OOS +0.05); break-even headroom separates the
  shelf (passers ≥ 100 bps, failures ≤ 50). Reproduce any number with
  `uv run python scripts/retest_candidates.py` — do not re-derive by hand.

## 1. The three tasks (recommended order, one PR each)

### Task 1 — Library-level PBO via a daily-returns store (roadmap Priority 3.3)

**The gap:** PBO (Bailey et al. 2014 CSCV) is a *library-level* metric — it needs
every strategy's daily-return series simultaneously. Fixtures store only summary
metrics, so today PBO is computed per-cohort at fixture-generation time
(`scripts/regen_fixtures.py::compute_pbo`, mirroring backend
`archimedes/services/rigor_evaluator.py::compute_pbo`, which takes
`returns_matrix: {strategy_id: [daily_returns]}`). Cohort PBO is disclosed as a
limitation in both files' docstrings.

**Build:**

1. A daily-returns store under `analytics-engine/strategies/` (suggest
   `daily_returns/<stem>.json`, one file per strategy: `{"backtest_start": ...,
   "backtest_end": ..., "data_vintage": "<run date>", "daily_returns": [...]}`).
   Generate from the same spec catalog `regen_fixtures.py` uses (import
   `NEW_SINGLE_SPECS`/`NEW_PAIR_SPECS`/`NEW_MULTI_SPECS`; copy the in-process
   price cache pattern from `scripts/retest_candidates.py`). Make generation
   **add-only and idempotent** exactly like `regen_fixtures.py` — skip stems
   whose file exists.
2. A `scripts/compute_library_pbo.py` that loads the store, builds the returns
   matrix, runs the CSCV PBO over the full library, and prints per-strategy and
   library-level results.
3. A findings note [`quant/library-pbo.md`](../quant/library-pbo.md) reporting the honest
   number.

**The constraint that shapes everything (do not fight it):** the legacy
strategies' fixture-era return series **cannot be reproduced** — current
yfinance data has drifted (this is why fixtures are add-only; see
`regen_fixtures.py` docstring). So the store's series are a *fresh measurement
on current data*, and the library PBO you compute is a **new, parallel
diagnostic — NOT a backfill** of the `pbo_score` values already in
`backtest_fixtures.json`.

**Hard boundary:** do **NOT** overwrite any `pbo_score` (or any other field) in
`backtest_fixtures.json`. That is simultaneously a fixture overwrite (forbidden,
add-only law) and a gate-affecting change (PBO < 0.5 is criterion 4 — touching
stored values can flip verdicts). Compute, report, and propose; promoting
library-PBO into the served fixtures is a team decision — file an issue with
your findings and leave it open.

**Acceptance sketch:** `uv run pytest` green with new hermetic tests (synthetic
returns matrix with a planted overfit strategy → high PBO; independent
strategies → low); store generation idempotent (second run = no-op);
`git diff -- strategies/backtest_fixtures.json` empty; the PBO math validated
against `rigor_evaluator.compute_pbo` on the same matrix (they share the
algorithm — assert equal outputs, don't fork the formula).

### Task 2 — The #537 `num_trials` decision package (roadmap Priority 1)

**What it is:** [issue #537](https://github.com/a-apin/archimedes/issues/537) —
should the DSR penalty's `num_trials_in_selection` be the full library size (23)
for *individually-specified, paper-grounded* strategies? Today every strategy is
penalized as if cherry-picked best-of-23. It blocks exactly one strategy: risk
parity (+0.35 Sharpe, passes DSR at N ≤ 13, fails at N = 22+).

**Your job is to assemble and post the decision package, not to decide.** The
issue already contains the N-sweep table. New evidence from the third wave to
fold in (from [`third-wave-retest.md`](../quant/third-wave-retest.md)):

- *For* the provenance split: risk parity's honest search space was 3 lookback
  combos (`n_param_combos = 3` in the walk-forward run), nowhere near 22. The
  walk-forward harness now gives every future strategy an *auditable* trial
  count — the provenance-based N is measurable, not asserted.
- *Against / tempering:* walk-forward OOS for risk parity is +0.14 vs +0.35 for
  the fixed default — the default config benefits from some configuration luck.
  If promoted, it should be Kelly-sized conservatively (ties into task 3).
- Break-even cost 1221 bps and 0.35×/yr turnover — implementability is not in
  question.

**Deliverable:** one comprehensive comment on #537 with a concrete proposal
(recommendation already on record: provenance-based split — paper-grounded
strategies penalized by variants actually tried, fusion/library-selected by full
count; **never** lower the p ≥ 0.95 bar). Optionally a prototype implementation
in `rigor_evaluator.py` on a branch, **clearly marked DO-NOT-MERGE**, showing
the diff is small. Then stop.

**Hard boundary:** Dan signs off on any `rigor_evaluator.py` change (shared
module; the gate governs real-USDC promotion). Do not merge a gate change solo
under any circumstances — this is the explicit §9 carve-out from the third-wave
handover and it has not moved.

### Task 3 — Kelly-sized allocation wired into vault construction (roadmap Priority 3.1)

**Read this first — the roadmap's one-liner is stale.** "We compute Kelly
fractions but do not yet size vault allocations from them" undersells what
exists. Before writing code, map the current surface (≈1 hour, do it in the PR
description):

- `backend/archimedes/services/portfolio_optimizer.py` — **already** has
  substantial Kelly machinery: `kelly_optimize_from_prices`, a γ-mapped
  `RISK_AVERSION` table (γ=2 ≈ half-Kelly per Bell & Cover; risk profile → γ),
  `kelly_risk_decomposition`, Ledoit-Wolf shrinkage. Docstring says
  "Owner: Önder (math lane)" — this is squarely your lane.
- `backend/archimedes/api/strategies_routes.py` (~lines 559-693) — allocation
  endpoints already emit `kelly_fraction`-labelled weights from optimizer
  output.
- The *strategy passport's* `kelly_fraction` (per-strategy, from the rigor
  metrics via `scripts/regen_buy_hold_fixture.py::compute_kelly`, served through
  `models/strategy.py` → `api/schemas.py`) — this is the piece that is computed
  but, as far as the roadmap claims, **not consumed by vault construction**.
- `backend/archimedes/services/vault_service.py` — vault allocations
  (`_get_target_allocations` reads on-chain state).

**The actual gap to close** (verify during your mapping): strategy-level sizing —
when a vault deploys a strategy, the capital fraction it receives should be
`min(fractional_kelly × passport_kelly_fraction, risk_profile_cap)`, not an
ad-hoc constant. Where that multiplication belongs (optimizer vs. a thin sizing
service consumed by the deploy path) is your design call; spec it in the PR.
Use **fractional** Kelly (½ or less) — full Kelly on backtest-estimated edges is
overbetting by construction; cite Bell & Cover / the γ table already in
`portfolio_optimizer.py` and keep one source for the fraction.

**Solo boundary for this task:** pure-python sizing logic in `services/` +
`api/` + tests is fine to build and merge solo (flag Daniel R. for backend
review in the PR description, per the lanes-aren't-gates norm). **Stop and leave
for a human** anything that (a) touches `backend/archimedes/chain/` or the
oracle runner, (b) changes what transactions get sent to vault contracts, or
(c) modifies contracts. Sizing *computation* is quant lane; sizing *execution
on-chain* is Chuan-review territory.

**Acceptance sketch:** hermetic backend tests (copy boundary-mock patterns from
`backend/tests/test_api_routes.py`); a VALIDATED strategy with
`kelly_fraction = 0.3` under a moderate profile gets a deterministic, capped
allocation; a CANDIDATE gets zero (only gate-passers are sizeable — do not let
sizing become a side-door past the gate); `PYTHONPATH=backend
/tmp/abe/bin/python -m pytest backend/tests/ -q` green.

## 2. Boundaries that have not moved (recap, binding)

- **Rigor gate** (`rigor_evaluator.py`, thresholds, `num_trials` policy): propose,
  prototype flagged, never merge solo. (#537 is Dan's sign-off.)
- **Fixtures** (`backtest_fixtures.json`): add-only, never overwrite a key. Same
  law extends to the new daily-returns store once it exists.
- **Contracts / `chain/` / infra / CI / new spend:** human required.
- **Never tune to pass; expect CANDIDATE outcomes; real data only** — if a
  yfinance fetch genuinely fails, STOP and report; never hand-type metrics.
- Commits authored as **Önder only** — no Claude author/co-author/footer.
  Branch `onder/<name>` off `main`, merge-commit (`gh pr merge <n> --merge`),
  ruff gate before commit, `!minor` only for new user-facing capability.

## 3. Environment & verification (unchanged from third wave §7-8, abbreviated)

```bash
# analytics engine (uv-managed, NO conda):
cd analytics-engine && uv run pytest                      # 74 tests green on main
uv run --with ruff ruff format . && uv run --with ruff ruff check --select E9,F63,F7,F40 .

# backend tests (venv recipe — rebuild if /tmp/abe is gone):
uv venv /tmp/abe --python 3.12
uv pip install --python /tmp/abe/bin/python -r backend/requirements.txt pytest pytest-asyncio
PYTHONPATH=backend /tmp/abe/bin/python -m pytest backend/tests/test_strategy_endpoints.py backend/tests/services/test_strategy_provider.py -q   # 71 green on main
```

## 4. Gotchas — third-wave additions (the older list in third-wave §6 all still applies)

1. **`gh pr create` needs `--repo a-apin/archimedes --head <branch>`** — origin
   points at the redirecting `hackagora/archimedes-arcadia` name and bare
   `gh pr create` fails on head-ref resolution.
2. **`gh pr checks <n> --watch` races check spawn** — run immediately after PR
   creation it reports "no checks reported" and exits 0, which looks like
   success. Guard: `until gh pr checks <n> | grep -q .; do sleep 15; done` first.
3. **`order_target_percent(target=1.0)` is margin-rejected** once commission is
   added (needs 100% of equity + commission). Use ≤ 0.95 in tests and strategies.
4. **Gross metrics are `None` when equity goes ≤ 0** (PCA does this: −$46k
   minimum). That is deliberate — reconstruction is undefined, and `None` beats
   a fabricated number. Don't "fix" it.
5. **`total_trades` is blind to resize-style strategies** — Moreira-Muir shows
   0 closed trades but 1.07×/yr turnover. Use `turnover_annualized` as the
   activity metric anywhere it matters (it's on every `BacktestResult` now).
6. **Engine Sharpe convention:** `_sharpe_bt_convention` in `engine.py`
   replicates the bt analyzer exactly (geometric daily rf at 5%, population
   stddev, √252). Reuse it for any series-level Sharpe (walk-forward does);
   never write a fourth Sharpe variant.
7. **pytest from the repo root picks up the root `pytest.ini`** (backend suite,
   system python). For engine tests, run from `analytics-engine/` or use
   `uv run --directory analytics-engine pytest`.

## 5. Suggested order of operations

1. **Task 1 (library PBO)** — cleanest solo build, produces a number the team
   has never seen, and its store is infrastructure task 2's evidence can cite.
2. **Task 2 (#537 package)** — post it early so Dan can be deciding while you
   build task 3; it consumes task 1's output if available but does not block on it.
3. **Task 3 (Kelly sizing)** — biggest scope; do the surface-mapping hour first
   and put the map in the PR description before writing code.

When in doubt: leave it CANDIDATE, document the uncertainty in the PR, flag
Önder. The honesty *is* the product — that rule has survived three waves and it
is why the library is trusted.

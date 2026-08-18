# Handover: Third Wave (the "fidelity wave") — executable brief for the next agent

> **Audience:** the agent (or teammate) building the next strategies / engine work
> solo. **Author:** Önder, 2026-06-11. **Prereq read:** the repo `CLAUDE.md`, plus
> [`quant-roadmap.md`](../plans/quant-roadmap.md) (the *what/why*) and
> [`second-wave-universe-experiment.md`](../quant/second-wave-universe-experiment.md) (why a
> bigger universe alone won't save anything). This doc is the *how/where*.
>
> **Operating context (important):** the rest of the team is offline. You are
> working autonomously. That changes the rules about *what* you may do alone —
> see [§9 Solo boundaries](#9-solo-boundaries-what-you-may-and-may-not-do-alone).
> The short version: ship quant-lane analytics-engine work freely; do **not**
> unilaterally change the rigor gate, the contracts, or infra.

## 1. What is already shipped (your foundation)

As of 2026-06-11, all merged to `main`:

- **Library: 22 strategies**, in `analytics-engine/strategies/*.py`. **Only 2 pass
  the rigor gate** (`moreira_muir_2017_volatility_managed`,
  `moskowitz_ooi_pedersen_2012_tsmom`). The other 20 are honest `CANDIDATE`s — that
  is correct, not debt. Do not delete them and do not tune them to pass.
- **Engine:** `analytics-engine/src/archimedes_analytics_engine/engine.py` has three
  runners that all share `_add_analyzers` + `_extract_result` (so metric shapes are
  identical across them — **reuse these, never re-derive metric extraction**):
  - `run_backtest(prices, *, strategy_cls, initial_cash, transaction_cost_bps, slippage_bps)` — 1 feed.
  - `run_pairs_backtest(prices_a, prices_b, *, strategy_cls, initial_cash, name_a, name_b, …)` — 2 bar-aligned feeds.
  - `run_multi_backtest(prices_list, *, strategy_cls, initial_cash, names=None, …)` — **N feeds** (inner-joins all on the common index; `self.datas[i]` for `i in range(N)`). This is the second wave's headline engine addition.
- **Universe:** `instruments.py::OPERATION_TO_SYMBOL` — the 5 operations
  (SPY/NIKKEI/GOLD/TREASURY/OIL) + pairs legs (GLD/GDX/IVV/KO/PEP/EWA/EWC/SLV) + 16
  liquid ETFs added 2026-06 (QQQ/IWM/EFA/EEM, IEF/DBC/VNQ, and the 9 SPDR sectors
  XLB…XLY). Plenty to compose universes from.
- **Fixture generator:** `analytics-engine/scripts/regen_fixtures.py` — computes real
  DSR/PBO/OOS/Kelly + the gate verdict from live backtests. Now **add-only AND
  idempotent**: it skips any stem already in `backtest_fixtures.json`, so re-running
  is a no-op. `num_trials` = post-add library size.
- **Rigor formulas (single source):** `scripts/regen_buy_hold_fixture.py` —
  `compute_dsr`, `compute_oos_sharpe`, `compute_kelly`. **Önder's lane; don't fork
  these.** PBO mirrors backend `archimedes/services/rigor_evaluator.py::compute_pbo`.

## 2. What to build next (from the roadmap — pick in this order)

The roadmap's thesis: **count is a vanity metric.** Don't add more single-instance
toy strategies — the second wave proved they fail honestly. Build *fidelity* and
*infrastructure* instead. Each is its own PR.

1. **Transaction-cost + turnover model (engine).** Highest leverage, reusable. The
   Kalman strategy cost-bled to −1.47 Sharpe on 1174 trades — execution realism is
   decisive. Add a turnover-aware cost model + a turnover penalty usable by every
   strategy. Pure analytics-engine; safe to do solo.
2. **Faithful-scale Gatev portfolio-of-pairs.** The cleanest fidelity win: Gatev's
   ~11% is a *diversified portfolio of ~20 pairs*, but we only shipped single pairs.
   Build a strategy that forms/trades a basket of N pairs over the expanded universe
   via `run_multi_backtest`. This directly tests "is the alpha real at the paper's
   scale?" — and may genuinely pass the gate.
3. **Walk-forward parameter selection harness.** Choose params out-of-sample on a
   rolling window. Makes any future "pass" credible rather than lucky; feeds the PBO
   story. Pure analytics-engine.

(Deprioritized by evidence: naive cross-sectional momentum and PCA — the universe
experiment showed sector momentum at −0.89 and PCA worse at scale. Don't grind on
these; if you touch them, expect honest failure.)

## 3. How a strategy becomes visible (the full path — directory-driven, no registry)

1. Write `analytics-engine/strategies/<name>.py`: a `bt.Strategy` subclass **plus
   module-level metadata constants**. Copy a metadata block from an existing file
   (e.g. `gatev_2006_pairs_distance.py` for pairs, `maillard_2010_risk_parity.py`
   for N-asset) and edit values.
2. Generate its fixture entry with `scripts/regen_fixtures.py` (see §4). Fixture key
   = the file stem.
3. On backend restart, `backend/archimedes/services/strategy_provider.py`
   (`LocalStrategyProvider.refresh()`) AST-scans the dir, reads metadata, joins the
   fixture, serves it at `GET /api/strategies/`. **Nothing else to wire.**

**Metadata the loader reads** (it lowercases keys): `PAPER_TITLE` (**required**),
`PAPER_AUTHORS`, `PAPER_VENUE`, `PAPER_YEAR`, `PAPER_DOI`, `PAPER_ARXIV_ID`,
`PAPER_CITATION_COUNT`, `METHODOLOGY_SUMMARY`, `METHODOLOGY_TEXT`,
`PAPER_CLAIMED_SHARPE/CAGR/MAX_DD`, `ASSET_UNIVERSE`, `POSITION_SIZING`,
`REBALANCE_FREQUENCY`, `RISK_PROFILES`, `REGIME_TAG` (**required**), `STATUS`,
`CURATOR_NOTE`, `EXTRACTION_LLM`.

## 4. Fixture generation (honesty-critical — read carefully)

- Append your strategy to `NEW_SINGLE_SPECS` (`{stem, symbol, tx_cost_bps}`),
  `NEW_PAIR_SPECS` (`{stem, pair: (A,B), tx_cost_bps}`), or `NEW_MULTI_SPECS`
  (`{stem, symbols: [...], tx_cost_bps}`). The lists are a *cumulative catalog*;
  the script only backtests stems not already in the fixture file.
- **Dry-run first:** `cd analytics-engine && uv run python scripts/regen_fixtures.py`
  — eyeball the printed Sharpe/DSR/PBO/gate table. Then `--write`.
- **Requires network (yfinance).** A raw `curl` to Yahoo may return HTTP 429, but the
  yfinance library works with retries — network IS available here. If a fetch
  genuinely fails, **STOP and report; never hand-type metrics.**
- **Add-only law:** never overwrite existing keys (legacy entries don't reproduce on
  current data). After `--write`, confirm the JSON diff is **insertions only**
  (`git diff --stat` + grep for `^-` lines = none).
- For N-asset specs, `symbols` doubles as the per-feed `names` passed to the engine
  (e.g. `DualMomentum` finds its `"TLT"` defensive leg by feed name).

## 5. Provenance & honesty discipline (this is the product)

- **Verify any claimed number before populating `paper_claimed_*`; else leave null.**
  Most of these papers report no clean single-config Sharpe → null is correct.
- **Expect failures. Admit `CANDIDATE`; never weaken thresholds to force a pass.**
  Parameter-tuning to clear the gate is a separate, later task — and even then it is
  walk-forward/out-of-sample tuning, never in-sample curve-fitting.
- **If you can't implement a paper faithfully, drop it and document why.** That *is*
  the rigor signal (wave 1 dropped Jegadeesh 1990 + Lo-Mamaysky-Wang 2000).
- Disclose every simplification in `METHODOLOGY_TEXT` (e.g. "numpy-only AR(1)
  half-life proxy for the ADF test, no statsmodels added").

## 6. Gotchas this session burned time on (do not relearn the hard way)

1. **ID collisions on a shared paper anchor.** The strategy ID =
   `hash(arxiv_id|DOI|title) + methodology_hash`. If two files share a paper AND
   identical methodology text, they collide and only one loads. Fix: make each
   file's `METHODOLOGY_TEXT` genuinely distinct. **Verify by loading the provider and
   asserting unique IDs.**
2. **`POSITION_SIZING` must be a valid enum value** — `equal_weight`, `risk_parity`,
   `kelly`, or `inverse_vol`. Anything else (e.g. `"concentrated"`) silently defaults
   to `equal_weight`. There is no warning at info level.
3. **`REGIME_TAG` is required and must be `bull` / `bear` / `regime_neutral`** — the
   provider **raises** (strategy won't load) otherwise.
4. **One `bt.Strategy` candidate per file.** To reuse a class, `from x import TheClass`
   and use it directly — do NOT also subclass it (that yields two candidates and the
   loader errors "Multiple strategy classes").
5. **Universe composition > count** (the experiment). Risk parity wants low-correlation
   cross-asset names; cross-sectional momentum wants a homogeneous cross-section
   (sector ETFs). Bigger ≠ better — a mostly-equity 10-ETF set is *less* diversified.
6. **`num_trials` calibration** (issue #537) is what blocks the one good near-miss
   (risk parity, +0.35 Sharpe). **Do NOT change the gate to fix this solo** — see §9.

## 7. Environment & how to run things (no conda here)

- **Analytics-engine:** `cd analytics-engine && uv run pytest` (uv manages its venv).
  Run a strategy ad hoc with `uv run python -c "..."`.
- **Backend tests need a venv** (system python lacks fastapi/pydantic/etc.):
  ```bash
  uv venv /tmp/abe --python 3.12
  uv pip install --python /tmp/abe/bin/python -r backend/requirements.txt
  uv pip install --python /tmp/abe/bin/python pytest pytest-asyncio
  PYTHONPATH=backend /tmp/abe/bin/python -m pytest backend/tests/test_strategy_endpoints.py backend/tests/services/test_strategy_provider.py -q
  ```
  Backend tests are **hermetic** — they degrade gracefully without DB/Redis/network
  (the provider logs a warning and carries on). Keep them that way.
- **Ruff hard-block gate** (run before every commit):
  ```bash
  uv run --with ruff ruff format .                       # apply (line-length 120)
  uv run --with ruff ruff check --select E9,F63,F7,F40 . # critical-lint subset (hard block)
  ```
  Also clear the broader informational lint on *your new files*
  (`ruff check <files>`) — RUF059 (unused unpacked var) and SLF001 (`d._name`
  access; use `getattr(d, "_name", None)`) are the two that bit this session.

## 8. Testing & verification (must be green before any PR)

```bash
cd analytics-engine && uv run pytest                     # engine + strategy + fixture-load tests
# backend (via the venv above):
PYTHONPATH=backend /tmp/abe/bin/python -m pytest backend/tests/test_strategy_endpoints.py backend/tests/services/test_strategy_provider.py -q
```

For each new strategy add: (a) an analytics-engine test that loads the file via
`strategy_loader.load_strategy` and runs it on **synthetic/seeded** data (hermetic —
mirror `test_pairs_engine.py` / `test_cross_sectional_strategies.py`), exercising the
trade path; (b) a backend `test_strategy_endpoints.py` assertion for presence /
universe / regime / honest-CANDIDATE.

## 9. Solo boundaries (what you may and may not do alone)

**Safe to build + merge solo (quant lane, no human-gated surface):** new strategy
files, the transaction-cost/turnover model, the walk-forward harness, `instruments.py`
additions, fixtures (add-only), tests, docs. All of it lives in `analytics-engine/`
and `backend/tests/`.

**Do NOT do alone — leave for a human:**
- **The rigor gate logic** (`rigor_evaluator.py`, the DSR `num_trials` policy / issue
  #537). It governs promotion of strategies that hold real USDC. Propose in the
  issue; don't merge a gate change. (You may *recommend* and prototype, flagged.)
- **Smart contracts** (`contracts/`, `ReasoningTraceRegistry`, vaults) — Chuan's
  explicit consent required (live funds).
- **Infra / CI / docker-compose / new AWS spend** — needs a human ack.
- Anything in `CLAUDE.md` § "When to ask before acting" (`.env.example`,
  `environment.yml`, etc.).

## 10. Conventions (from CLAUDE.md — non-negotiable)

- Branch `onder/<short-name>` off `main`; one logical change per PR; phases = separate PRs.
- **Author commits as Önder ONLY.** Never list Claude/an agent as author or
  co-author; no `Co-Authored-By`, no "Generated with" footer. (This is a hard rule.)
- **Merge commits only:** `gh pr merge <n> --merge`. (Squash/rebase-merge disabled.)
- PR title marker: new strategy / new capability → end title with `!minor`;
  fixes/docs/refactors → no marker.
- **Never force-push `main`; never commit secrets/`.env`.** `main` is build-on-deploy
  — every merge deploys to the live EC2 stack, so smoke-test locally first.
- **Stacking sequential PRs (if you don't merge between them):** branch each phase on
  the previous branch, set the PR base to that branch. To rebase after the parent
  changes, use `git rebase --onto <new-parent> <old-parent> <branch>`. On merge,
  GitHub may not auto-retarget (it can't delete a branch other open PRs depend on) —
  **manually `gh pr edit <n> --base main`** before merging the next one, in order.
- **Repo note:** `origin` currently redirects (`hackagora/archimedes-arcadia`). `gh`
  prints "This repository moved" but operations still work.

## 11. Suggested order of operations

1. **Transaction-cost + turnover model** (engine; unblocks credible re-tests).
2. **Faithful-scale Gatev portfolio-of-pairs** (the fidelity proof-of-concept).
3. **Walk-forward harness** (credibility upgrade; pairs well with PBO).
4. Re-test the existing CANDIDATEs through (1)+(3) — see which pass *honestly*.

When in doubt on a quant/provenance call, leave it `CANDIDATE`, document the
uncertainty in the PR, and (if a human is back) flag Önder — do not guess your way
past the rigor gate.

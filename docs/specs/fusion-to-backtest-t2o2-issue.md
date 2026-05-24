# Issue spec for t2o2 — Fusion-output → Backtestable Strategy DSL

> **Status:** ✓ filed + closed as [#128](https://github.com/a-apin/archimedes-arcadia/issues/128) on 2026-05-23. Foundation shipped in `bd6935b` (DSL + interpreter + evaluator + 37 passing tests, using canonical `services/rigor_evaluator.py`); wiring + DSL spec doc landed via follow-on [#133](https://github.com/a-apin/archimedes-arcadia/issues/133) — closed, shipped on `main` (commit `2f7f871`).
>
> **This is a ready-to-file issue body, written to the standard in CLAUDE.md's
> "agentic issue pipeline" section.** Copy/paste into a GitHub issue,
> `gh issue edit <n> --add-assignee t2o2`, and the t2o2 agent will pick it up.
>
> **Why this exists:** today the fusion endpoint produces a JSON hypothesis
> with thesis, source_arxiv_ids, fusion_reasoning, novelty_rationale, risk_notes
> — all *text*. It cannot be backtested, cannot enter the rigor gate, cannot
> produce a DSR/PBO/OOS-Sharpe verdict. This makes the Generate(fusion) path's
> result strictly weaker than the Generate(architect) path, which picks from
> the curated library and gets real numbers. The product wedge ("fusion
> synthesizes novel hypotheses that survive the rigor gate") is not yet true.
>
> This issue closes that gap by making fusion output a structured spec that
> the existing analytics engine can execute.
>
> **2026-05-22 update notes (between draft + filing):** spec aligned with
> Phase 0–2 work that landed since this was first drafted:
>   - The canonical rigor module is now **`backend/archimedes/services/rigor_evaluator.py`**
>     (Önder's lane). The interpreter must integrate with that module's
>     `compute_dsr` / `compute_pbo` / `compute_oos_sharpe` primitives (NOT the
>     older `selection_bias.py`, which is queued for retirement under the
>     Phase 7 dedup pass).
>   - The streaming Generate pipeline now lives at
>     `backend/archimedes/services/generation_pipeline.py`. Fusion-backtested
>     output should land in the same `StrategyRecord` table as architect-path
>     output (via `models/strategy_store.py::upsert_strategy`), so Library
>     can render both side by side under the existing Generated tab.
>   - The strategy lifecycle (Generated → Validated → Deployed → … per
>     `docs/specs/strategy-lifecycle-spec.md`) applies: fusion-backtested
>     strategies that pass rigor enter `Validated`; failures enter `Rejected`
>     (NOT silently dropped). The user wedge depends on rejected-but-honest
>     reporting.

---

## APIN — Backend — Fusion-output → backtestable strategy DSL + interpreter + rigor gate

## Summary

The fusion endpoint (`POST /api/strategies/generate?mode=fusion`) returns a
JSON proposal with prose fields (`thesis`, `fusion_reasoning`,
`novelty_rationale`, `risk_notes`) and a list of source `arxiv_id`s. **None of
this is executable** — there is no backtest, no DSR/PBO computation, no
rigor-gate verdict, and the result cannot be promoted to the library where
`StrategyArchitect` could pick it.

Close the gap by:

1. **Define a strategy DSL** that is rich enough to express the bulk of q-fin
   strategies in our seed library (momentum, vol-managed, trend-following,
   tactical asset allocation) and constrained enough that we never `exec()`
   arbitrary LLM-generated Python.
2. **Extend the fusion LLM prompt** to also emit a `strategy_spec` field that
   is valid against the DSL JSON schema (in addition to the prose fields).
3. **Build a DSL → backtrader interpreter** that translates a validated
   `strategy_spec` into a `backtrader.Strategy` subclass at runtime.
4. **Wire the interpreter into the existing analytics-engine backtest path**
   so fusion outputs flow through the same backtest → rigor-gate (DSR / PBO /
   walk-forward OOS / look-ahead audit) pipeline as the curated seeds.
5. **Persist the fusion result + backtest result + rigor verdict** in the
   strategy library so the user can see their new strategy alongside the
   seeds — same passport format, same metrics, same rigor badge.

The user wedge after this lands: **"Generate a strategy → see it survive
the rigor gate → deploy it"** becomes literally true for fusion outputs, not
just curated picks.

## Scope (do exactly this, nothing more)

Files to create:
- `backend/archimedes/services/strategy_dsl.py` — DSL schema + validator
- `backend/archimedes/services/dsl_to_backtrader.py` — interpreter
- `backend/archimedes/services/fusion_evaluator.py` — orchestrator:
  spec → interpreter → backtrader → rigor gate → library upsert
- `tests/services/test_strategy_dsl.py`
- `tests/services/test_dsl_to_backtrader.py`
- `tests/services/test_fusion_evaluator.py`
- `docs/specs/strategy-dsl-spec.md` — DSL reference (the contract docs)

Files to modify:
- `backend/archimedes/services/strategy_fusion.py` — extend `_SYSTEM_PROMPT`
  to require `strategy_spec` in the output JSON; extend `FusionProposal` to
  carry it; do NOT change the existing fields (back-compat).
- `backend/archimedes/api/routes.py` `_run_fusion_job` — after the fusion call
  returns a proposal with a valid `strategy_spec`, call `fusion_evaluator`
  to backtest + rigor-gate + library-upsert. If `strategy_spec` is missing or
  invalid, fall back to today's pre-backtest hypothesis path with the existing
  honest "rigor pending" framing.

Files to NOT touch:
- `analytics-engine/strategies/*.py` — the hand-written seed strategies are
  the comparison baseline. Leave them alone.
- `backend/archimedes/services/strategy_architect.py` — out of scope.
- The on-chain contract layer — strategies don't get on-chain artifacts in
  this issue, just library entries.
- `pytest.ini`, thresholds in `tests/` — don't weaken any existing test
  config.

## DSL design constraints

The DSL must:
- Be **a JSON schema with closed enums** (no free-form expression strings, no
  Python imports, no shell-out). The interpreter validates against the schema
  before instantiating any backtrader object.
- Express **entry condition, exit condition, position sizing, asset universe,
  and rebalance frequency** at minimum.
- Support a small library of **named primitives** that map to backtrader
  indicators: `sma(period)`, `ema(period)`, `rsi(period)`, `realized_vol(window)`,
  `momentum(lookback)`, `volatility_target(annual_pct)`, `equal_weight`,
  `min_variance` (small set — favor coverage of the seed strategies over
  expressive power).
- Allow **boolean combinations** of conditions via a JSON tree:
  `{"and": [{"gt": ["price", "sma_200"]}, {"lt": ["realized_vol_20", 0.20]}]}`.
  Recursive structure, finite operator set.
- Carry **provenance** — the spec must reference the `arxiv_id`s that
  inspired each primitive choice, so the rigor verdict + the citation chain
  stay coupled (matters for the trust story).

The DSL must NOT:
- Allow arbitrary code strings, lambdas, eval, exec, or import.
- Allow new primitives outside the closed enum without updating the schema +
  interpreter together (one PR per primitive addition).
- Allow look-ahead (the interpreter must guarantee primitives reference only
  data available at-or-before the decision bar; `look-ahead audit` becomes a
  static check on the spec tree, not a runtime hack).

A reference DSL example that should validate, interpret, and backtest to
within 5% of the Faber 2007 seed's numbers:

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

## Acceptance criteria

Each is a runnable command + expected output. The agent should not mark this
issue closed until every command passes on a clean clone with `pip install -r
backend/requirements.txt`, no Docker, no env vars beyond the test defaults.

- [ ] **DSL schema validates the reference example:**
  `pytest tests/services/test_strategy_dsl.py::test_validates_reference_examples -q`
  → exit 0, all examples pass schema validation.

- [ ] **Interpreter produces a backtrader.Strategy subclass:**
  `pytest tests/services/test_dsl_to_backtrader.py::test_interprets_reference_examples -q`
  → exit 0, no exceptions; type checks confirm subclass.

- [ ] **Fusion-DSL-interpreted Faber strategy reproduces the seed's Sharpe
  within ±0.10 absolute on the same data window:**
  `pytest tests/services/test_fusion_evaluator.py::test_faber_dsl_matches_seed -q`
  → exit 0. Seed Faber Sharpe is the canonical value in
  `backend/archimedes/services/backtest_mapper.py` fixture; the DSL run
  should land within ±0.10 of that. This is the contract that says the DSL
  + interpreter aren't a different strategy under the hood.

- [ ] **Rigor gate runs on DSL-interpreted strategies:** invoking
  `passes_rigor_gate(metrics)` on the DSL-interpreted backtest output
  returns `True` for Faber (a known-passing seed) and produces all four
  fields (`dsr`, `pbo`, `oos_sharpe`, `look_ahead_clean`). Test:
  `pytest tests/services/test_fusion_evaluator.py::test_rigor_gate_applies_to_dsl_output -q`
  → exit 0.

- [ ] **End-to-end fusion → backtest path works without LLM credentials**
  (using a fixture-based fusion proposal that already contains a valid
  `strategy_spec`):
  `pytest tests/services/test_fusion_evaluator.py::test_fixture_fusion_to_library -q`
  → exit 0. After the test runs, the library has a new strategy with
  `generation_method="fusion"`, real backtest metrics, and a rigor verdict.

- [ ] **Schema enforcement: a DSL spec with `look_ahead_safe: false` is
  rejected by the interpreter** (we do not run unsafe specs):
  `pytest tests/services/test_dsl_to_backtrader.py::test_rejects_look_ahead_unsafe -q`
  → exit 0 (the test confirms the rejection).

- [ ] **Existing tests still green** — no regressions:
  `pytest -q tests/services/ tests/api/` → 0 failed.

## Verify (the literal commands a reviewer runs)

After clone + `pip install -r backend/requirements.txt`:

```bash
pytest tests/services/test_strategy_dsl.py -q
pytest tests/services/test_dsl_to_backtrader.py -q
pytest tests/services/test_fusion_evaluator.py -q
pytest tests/services/ tests/api/ -q
```

Each must exit 0. Combined runtime should be under 90 seconds (rigor gate
on a Faber-equivalent backtest is ~5-10 seconds; everything else is
fixture-based).

## Anti-goals (what NOT to do)

- **Do not** add support for arbitrary Python expression strings, lambdas, or
  any form of dynamic code execution. The DSL is closed-enum on purpose; if
  a strategy can't be expressed in the current enum, the answer is "add the
  primitive in a new PR with its own schema + interpreter test", not "let
  the LLM emit Python."
- **Do not** weaken any existing rigor-gate thresholds. The DSL output must
  pass the same gate as hand-written strategies, not a relaxed one.
- **Do not** modify the on-chain contract layer. Library entries for
  fusion-generated strategies don't get an on-chain registration in this
  issue; that's a follow-up.
- **Do not** require a real LLM call in tests. Fixture the fusion proposal.
  Real-LLM-call testing belongs in a separate integration test suite, not
  this PR.
- **Do not** add `eval()`, `exec()`, `compile()`, or `importlib` anywhere
  in the new code paths.
- **Do not** drop the existing fusion text fields (`thesis`,
  `fusion_reasoning`, `novelty_rationale`, `risk_notes`). They remain
  user-facing and are still emitted; `strategy_spec` is additive.

## Cite a precedent (so the agent copies the right shape)

- For the test fixture pattern, copy
  `tests/services/test_strategy_architect.py::test_proposes_with_canned_backend`
  — same shape (canned LLM backend, fixture proposal, asserts on the
  returned structure).
- For the rigor-gate integration, use `backend/archimedes/services/rigor_evaluator.py`
  directly — `compute_dsr(returns, num_trials)` + `compute_pbo({sid: returns})`
  + `compute_oos_sharpe(returns)` are the canonical entry points. See
  `backend/archimedes/services/generation_pipeline.py::_rigor_verdict_for`
  for the working integration shape (the Phase 2 follow-up wired it for
  agent output; fusion_evaluator should mirror that pattern). Do NOT use
  `selection_bias.py` (older parallel impl, queued for retirement).
- For the backtrader integration, copy one of the runnable seed strategies
  in `analytics-engine/strategies/` (e.g., `faber_sma200.py`) — that's the
  shape the interpreter must produce. The DSL→backtrader interpreter
  emits the equivalent of these files at runtime.

## Out of scope (deferred to follow-up issues)

- Promoting a fusion-generated strategy to "live" (i.e., wiring it into a
  vault's `setTargetAllocations`) — that needs the on-chain registration
  + curator review. Stays "validated" in the library after rigor gate
  passes; deploy is manual for now.
- A UI for browsing fusion-generated strategies separately from the seed
  library — they appear in the same Library page; we can add filters
  later.
- IPFS pinning of the strategy_spec — see
  `docs/specs/ipfs-reasoning-traces-design-note.md` for the parallel work
  on trace pinning; same pattern would apply.
- Multi-asset, factor-portfolio strategies (long-short, market-neutral) —
  the DSL is intentionally simple for v1; cross-sectional strategies need
  a more involved DSL extension.

## Owner suggestion

This sits between **Dan** (strategy library / Q-fin literature interpretation
— knows what primitives matter) and **Daniel R.** (backend services — owns
the FastAPI integration). The interpreter glue is mechanical once the DSL
is right; the hard call is what to include in the primitive enum, which is
Dan's call.

The t2o2 agent can crank the implementation autonomously given this spec.
Review will need Dan's eyes on the DSL primitive choices specifically.

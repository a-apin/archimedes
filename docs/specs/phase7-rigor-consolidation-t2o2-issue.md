# Issue spec for t2o2 — Consolidate rigor implementation on `rigor_evaluator.py`

> **Status:** ✓ filed + closed as [#129](https://github.com/a-apin/archimedes-arcadia/issues/129) on 2026-05-23 — shipped on `main` (commit `e030ee4`). This file is the spec source-of-truth; PR archive lives on the issue.
>
> **Ready-to-file issue body.** Copy/paste into a new GitHub issue, run
> `gh issue edit <n> --add-assignee t2o2`, and t2o2 will pick it up.
>
> **Why this exists:** the codebase has TWO implementations of the same
> rigor math — `services/selection_bias.py` (534 lines, older `t2o2`-authored)
> and `services/rigor_evaluator.py` (348 lines, newer Önder-authored). Both
> compute DSR + PBO. Future bug fixes risk landing in the wrong file; a
> Phase 2 follow-up already chose `rigor_evaluator.py` as the path for
> agent-output rigor wiring, locking in the canonical choice. This issue
> retires `selection_bias.py` cleanly.

---

## APIN - Backend - Consolidate rigor implementation on `rigor_evaluator.py`

## Summary

[`services/rigor_evaluator.py`](../../backend/archimedes/services/rigor_evaluator.py)
(Önder, 348 lines, math lane) is the **canonical** rigor module going forward.
[`services/selection_bias.py`](../../backend/archimedes/services/selection_bias.py)
(534 lines, older parallel impl) duplicates the same primitives.

Retire `selection_bias.py` by: (1) finding every call site that imports from
it; (2) re-pointing those imports at `rigor_evaluator.py` (signatures match
for the public primitives); (3) re-exporting any genuinely-unique helpers
from `rigor_evaluator.py` so external import paths keep working through a
single deprecation cycle; (4) deleting `selection_bias.py` and its test
file at the end of the PR.

Do NOT silently change rigor thresholds. Do NOT alter the meaning of any
existing API response field.

## Scope (do exactly this, nothing more)

Files to modify:
- `backend/archimedes/api/selection_bias_routes.py` — repoint imports.
- Any other file in `backend/` that imports from `services.selection_bias` —
  enumerate via `grep -rn "from archimedes.services.selection_bias\|import archimedes.services.selection_bias" backend/`
  before starting; repoint each.
- `backend/archimedes/services/rigor_evaluator.py` — only if a unique helper
  from `selection_bias.py` is needed elsewhere (e.g. `_look_ahead_audit`),
  add the helper here as a public function with a tested-against-baseline
  test. Default expectation: no additions needed.

Files to delete (at the END of the PR, after all callers are repointed):
- `backend/archimedes/services/selection_bias.py`
- `backend/tests/services/test_selection_bias.py` (if it exists as a separate
  file; if its tests already cover behaviors `rigor_evaluator.py`'s tests
  don't, migrate them first).

Files to NOT touch:
- `analytics-engine/strategies/*.py` — strategy code.
- `services/rigor_evaluator.py` semantics — only additions if needed.
- Test thresholds, `pytest.ini`, fixture data.

## Acceptance criteria

Each is a runnable command + expected output.

- [ ] **All `selection_bias` imports gone:**
  `grep -rn "from archimedes.services.selection_bias\|archimedes\.services\.selection_bias" backend/`
  → empty
- [ ] **File is gone:**
  `ls backend/archimedes/services/selection_bias.py 2>&1`
  → `No such file or directory`
- [ ] **All existing tests still green:**
  `pytest -q --deselect backend/tests/test_api_routes.py::TestAgentRoutes::test_agent_status_redis_down_defaults --deselect backend/tests/test_api_routes.py::TestAdvisorRoutes::test_advisor_redis_unavailable`
  → 307+ passed, 0 failed (the two deselects are pre-existing Redis-state flakes documented in CLAUDE.md history)
- [ ] **API response shape unchanged:** every `/api/strategies/rigor*` /
  `/api/selection-bias/*` endpoint returns the same fields with the same
  semantics it returned before this PR. Verify via a `gh pr diff` of the
  schema files — no field added, removed, or renamed.
- [ ] **No behavioral drift on DSR/PBO/OOS numbers:** rerun the architect
  + agent integration tests; numeric outputs (`dsr`, `pbo`, `oos_sharpe`)
  match pre-PR values within 1e-6.

## Verify

```bash
git checkout main && pip install -r backend/requirements.txt
# capture pre-PR numbers:
pytest -q backend/tests/services/test_rigor_evaluator.py -v | tee /tmp/before.txt
# apply PR, then:
pytest -q backend/tests/services/test_rigor_evaluator.py -v | tee /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt  # numeric output diff should be empty
grep -rn "selection_bias" backend/   # should be empty
```

## Anti-goals

- Do NOT broaden the scope to "refactor `rigor_evaluator.py`." Pure migration.
- Do NOT relax any rigor threshold. Do NOT change `passes_rigor_gate` logic.
- Do NOT touch the `IBacktestEvaluator` Protocol in `interfaces/math.py`.
- Do NOT add `selection_bias` as a "compatibility shim" file. Either delete
  cleanly or leave the consolidation incomplete with an explicit follow-up.

## Precedent / shape to copy

- For the integration test shape, copy
  `backend/tests/services/test_generation_pipeline.py::test_rigor_adapter_computes_dsr_and_oos_sharpe_on_synthetic_series`
  — that's the Phase 2 follow-up that already wired `rigor_evaluator.py`
  for live use. Same primitive signatures apply here.
- For the migration shape, this is closest to the
  Phase 1 ownership-comment reframe (`07276d3`) — surgical, no behavior
  change, comprehensive grep-checkable acceptance.

## Out of scope (deferred follow-up issues)

- Refactoring `rigor_evaluator.py` internals (it's clean; don't rewrite).
- Adding new rigor primitives (Sharpe CI is there but underused — separate issue).
- Making the rigor gate user-tunable via env (separate UX issue).

## Owner suggestion

t2o2 executes. Önder reviews (his module is becoming canonical). Dan
coordinates timing — should land BEFORE the fusion-to-backtest DSL issue
(#128) merges, so that PR can rely on the consolidated import path.

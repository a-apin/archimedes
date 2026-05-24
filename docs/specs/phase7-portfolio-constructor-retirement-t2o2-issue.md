# Issue spec for t2o2 — Retire `portfolio_constructor.py` + `kelly_portfolio.py`

> **Status:** ✓ filed + closed as [#131](https://github.com/a-apin/archimedes-arcadia/issues/131) on 2026-05-23 — shipped on `main` (commit `a4a09fb`); deprecated constructors moved to `services/_deprecated/`. This file is the spec source-of-truth; PR archive lives on the issue.
>
> **Ready-to-file issue body.** Per CLAUDE.md's agentic issue pipeline.
>
> **Why this exists:** the Phase 0 decision tree
> ([`docs/specs/portfolio-constructor-decision-tree.md`](portfolio-constructor-decision-tree.md))
> already named the canonical top-level constructor (`portfolio_agent.py`)
> and the math leaf (`portfolio_optimizer.py`). It also named the two
> deprecated modules: `portfolio_constructor.py` and `kelly_portfolio.py`.
> Both have **zero production call sites** today (verified via grep) — they
> only survive because the `IPortfolioConstructor` Protocol still references
> them indirectly. This issue executes the spec: retire them cleanly.

---

## APIN - Backend - Retire deprecated portfolio constructors per decision-tree spec

## Summary

Per [`docs/specs/portfolio-constructor-decision-tree.md`](portfolio-constructor-decision-tree.md):

| File | Status per spec | Production callers as of 2026-05-22 |
|---|---|---|
| `services/portfolio_agent.py` (850 lines) | Canonical top-level | `routes.py:677` (live) |
| `services/portfolio_optimizer.py` (488 lines) | Canonical math leaf | `routes.py:509, 1056, 1259` (live) |
| `services/portfolio_constructor.py` (285 lines) | **Deprecated** | None (test-only) |
| `services/kelly_portfolio.py` (505 lines) | **Deprecated** | None (test-only) |

This issue: retire the two deprecated modules. Lift any unique Kelly math
worth keeping into `portfolio_optimizer.py` BEFORE deletion (the spec
flags `kelly_size(weights, edge_estimates) → scaled_weights` as the kind
of helper that might be worth saving — Önder to confirm).

## Scope (exact files)

Files to modify:
- `backend/archimedes/services/portfolio_optimizer.py` — add any
  Kelly-specific math worth preserving as pure functions (e.g.
  `kelly_size`, `risk_parity_weights`). Each addition has its own unit test.
- `backend/archimedes/interfaces/__init__.py` — keep `IPortfolioConstructor`
  exported (the Protocol still has callers via the LLM-unavailable fallback
  path described in the decision-tree spec). The Protocol itself stays.

Files to move (to `backend/archimedes/services/_deprecated/`):
- `backend/archimedes/services/portfolio_constructor.py`
- `backend/archimedes/services/kelly_portfolio.py`

Files to update:
- `backend/tests/services/test_kelly_portfolio.py` — either migrate the
  tests to exercise the lifted helpers in `portfolio_optimizer.py`, or
  delete if the lifted set covers the math.
- `backend/tests/services/test_portfolio_constructor.py` (if it exists) —
  same migration.

Files to NOT touch:
- `services/portfolio_agent.py`, `services/portfolio_optimizer.py`
  (canonical — only the targeted Kelly additions).
- Any frontend file. Generate/Library UX is unchanged.

After one release cycle confirms no consumers (single PR cycle on the
hackathon timeline — i.e. when t2o2's PR merges + the next test run
passes), the `_deprecated/` directory can be deleted in a follow-up PR.

## Acceptance criteria

- [ ] **Grep-clean:**
  `grep -rn "from archimedes.services.portfolio_constructor\|from archimedes.services.kelly_portfolio" backend/` (excluding `_deprecated/`) → empty
- [ ] **Files relocated, not deleted:**
  `ls backend/archimedes/services/_deprecated/portfolio_constructor.py backend/archimedes/services/_deprecated/kelly_portfolio.py`
  → both present
- [ ] **Unique Kelly math preserved:** any function `compute_*` /
  `kelly_*` / `risk_parity_*` referenced in `kelly_portfolio.py` and NOT
  duplicating `portfolio_optimizer.py` is now in `portfolio_optimizer.py`
  with its own test.
- [ ] **All tests pass:**
  `pytest -q --deselect backend/tests/test_api_routes.py::TestAgentRoutes::test_agent_status_redis_down_defaults --deselect backend/tests/test_api_routes.py::TestAdvisorRoutes::test_advisor_redis_unavailable`
  → 307+ passed.
- [ ] **API responses unchanged:** any endpoint that touches portfolio
  construction (`/api/agent/advisor`, `/api/strategies/frontier*`) returns
  bit-for-bit-identical numeric outputs against the same fixture inputs.
  (Numeric diff <1e-6 acceptable.)

## Verify

```bash
grep -rn "portfolio_constructor\|kelly_portfolio" backend/ \
  | grep -v "_deprecated\|.pyc"
# Expect: only Protocol references in interfaces/, test names, and the
# decision-tree spec doc. No production imports.

pytest -q backend/tests/services/test_portfolio_optimizer.py -v
# Should include any lifted Kelly tests under their new home.
```

## Anti-goals

- Do NOT delete the `_deprecated/` files in this PR — moving them is
  reversible; deleting is not. Deletion is a follow-up PR after CI green.
- Do NOT touch `portfolio_agent.py` semantics. The agent path is the live
  product surface.
- Do NOT change the `IPortfolioConstructor` Protocol shape. Future LLM-
  unavailable-fallback code may want to implement it; keeping the seam
  unchanged is cheap insurance.
- Do NOT alter `_DRIFT_THRESHOLD`. The canonical value (0.05) is documented
  in the decision-tree spec; the move shouldn't change it.

## Precedent / shape to copy

- The decision-tree spec
  ([`docs/specs/portfolio-constructor-decision-tree.md`](portfolio-constructor-decision-tree.md))
  is the authoritative reference. Read it first.
- For the test migration shape, copy how
  `backend/tests/services/test_portfolio_optimizer.py` exercises pure
  math functions with seeded numpy inputs.
- For the "move to `_deprecated/`" precedent, this is the first time we're
  using that pattern — name it carefully. Alternatively, prefix moved
  files with `_deprecated_` (less invasive). t2o2's call.

## Out of scope

- Soft-delete to hard-delete (one release cycle later, separate PR).
- Any LLM-unavailable fallback policy work — the decision-tree spec
  recommends route-handler-side fallback; that's a Phase 5 concern.

## Owner suggestion

t2o2 executes. **Önder reviews** (the Kelly math judgment call about what
to lift is his to make). Dan coordinates with the fusion-to-backtest issue
(#128) — if that issue lands first, fusion may add a Kelly-sizing step
that the lifted helpers need to support.

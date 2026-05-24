# Issue spec for t2o2 — Split `routes.py` monolith by resource

> **Status:** ✓ filed + closed as [#132](https://github.com/a-apin/archimedes-arcadia/issues/132) on 2026-05-23 — shipped on `main` (commit `be9260b`); `routes.py` is now a thin re-export shim with 9 per-resource routers. This file is the spec source-of-truth; PR archive lives on the issue.
>
> **Ready-to-file issue body.** Per CLAUDE.md's agentic issue pipeline.
>
> **Why this exists:** `backend/archimedes/api/routes.py` is **~2315 lines**
> as of 2026-05-22, holding every legacy endpoint that predates the
> "dedicated router file" convention. The new routers we've added in
> recent phases (`chat_routes.py`, `marketplace_routes.py`, `risk_routes.py`,
> `selection_bias_routes.py`, `generate_routes.py` Phase 2, `explore_routes.py`
> Phase 3a, `corpus_routes.py` Phase 3c) prove the per-resource split is
> workable and dramatically improves discoverability. This issue applies
> that pattern retroactively to the legacy monolith.

---

## APIN - Backend - Split `routes.py` monolith into per-resource routers

## Summary

Split `backend/archimedes/api/routes.py` (~2315 lines) into per-resource
router files, mirroring the pattern already used by `chat_routes.py` /
`marketplace_routes.py` / `risk_routes.py` / `selection_bias_routes.py` /
`generate_routes.py` / `explore_routes.py` / `corpus_routes.py`.

Target resource splits:
- `assets_routes.py` — `/api/assets/*`
- `vaults_routes.py` — `/api/vaults/*`
- `strategies_routes.py` — `/api/strategies/*` (including `/construct`,
  `/generated`, `/frontier*`, `/correlations`)
- `traces_routes.py` — `/api/traces/*`
- `regime_routes.py` — `/api/regime/*`
- `swap_routes.py` — `/api/swap/*`
- `config_routes.py` — `/api/config/*`
- `agent_routes.py` — `/api/agent/*` (advisor, stress, status)
- `papers_routes.py` — `/api/papers/*` (excluding /api/corpus/* which already
  has its own router from Phase 3c)

After the split, `routes.py` itself becomes a thin re-export shim during
the transition: it imports the routers from their new files and re-exports
the symbol names `main.py` already binds (`assets_router`, etc.) so the
include-router list in `main.py` doesn't need to change in this PR.

In a follow-up PR (NOT this one): delete the re-export shim and update
`main.py` to import directly from the new files.

## Scope (exact files)

Files to add (9 new):
- `backend/archimedes/api/assets_routes.py`
- `backend/archimedes/api/vaults_routes.py`
- `backend/archimedes/api/strategies_routes.py`
- `backend/archimedes/api/traces_routes.py`
- `backend/archimedes/api/regime_routes.py`
- `backend/archimedes/api/swap_routes.py`
- `backend/archimedes/api/config_routes.py`
- `backend/archimedes/api/agent_routes.py`
- `backend/archimedes/api/papers_routes.py`

Files to modify:
- `backend/archimedes/api/routes.py` — slim down to a thin re-export shim
  importing each `*_router` from the new files. Module-level helpers that
  are used by multiple new routers (e.g. `_category_label`, `_persist_trace_off_chain`)
  move to `api/_route_helpers.py`. No endpoint definitions remain in
  `routes.py`.
- `backend/archimedes/api/_route_helpers.py` (NEW) — shared helper functions
  lifted out of the original monolith.

Files to NOT touch:
- `backend/archimedes/main.py` — the `include_router(*_router)` list stays
  unchanged in this PR. Subsequent PR cleans up after the shim deletion.
- Any frontend file. The URL contracts are unchanged.
- Any router file outside the legacy monolith (`chat_routes.py` etc.).

## Acceptance criteria

- [ ] **`routes.py` is < 100 lines:**
  `wc -l backend/archimedes/api/routes.py` → <100. The file contains only:
  imports of routers from the new files, re-exports of the router names
  `main.py` binds, and a module docstring explaining the transition.
- [ ] **Every URL still exists:** capture the live URL set before+after:
  `python -c "from archimedes.main import app; print('\n'.join(sorted(r.path for r in app.routes)))"`
  Before-PR list = after-PR list (byte-identical).
- [ ] **All tests pass:**
  `pytest -q --deselect backend/tests/test_api_routes.py::TestAgentRoutes::test_agent_status_redis_down_defaults --deselect backend/tests/test_api_routes.py::TestAdvisorRoutes::test_advisor_redis_unavailable`
  → 307+ passed.
- [ ] **OpenAPI schema unchanged:** `curl http://localhost:8000/openapi.json`
  (or equivalent test fixture) produces the same `paths` dict before+after.
- [ ] **No new top-level dependencies:** `pip-tools sync` reports no new
  requirements.

## Verify

```bash
git checkout main && pip install -r backend/requirements.txt
python -c "from archimedes.main import app; import json; \
  print(json.dumps({r.path: sorted(r.methods or []) for r in app.routes}, sort_keys=True, indent=2))" \
  > /tmp/routes_before.json

# Apply PR, then:
python -c "from archimedes.main import app; import json; \
  print(json.dumps({r.path: sorted(r.methods or []) for r in app.routes}, sort_keys=True, indent=2))" \
  > /tmp/routes_after.json
diff /tmp/routes_before.json /tmp/routes_after.json   # must be empty

pytest -q
wc -l backend/archimedes/api/routes.py   # must be < 100
```

## Anti-goals

- Do NOT change any endpoint behavior. Pure refactor.
- Do NOT rename any URL path. The frontend contract is locked.
- Do NOT split into smaller-than-resource granularity (no
  `vault_create_routes.py` + `vault_read_routes.py`). One file per resource.
- Do NOT introduce shared mutable state between routers. Each `*_routes.py`
  is self-contained except for imports from `_route_helpers.py`.
- Do NOT touch the `chat_router` / `marketplace_router` / `risk_router` /
  `selection_bias_router` / `generate_router` / `explore_router` /
  `corpus_router` — already in their own files; orthogonal to this work.
- Do NOT delete `routes.py` in this PR. Shim stays; deletion is the
  follow-up after one CI cycle confirms zero downstream surprises.

## Precedent / shape to copy

The cleanest existing per-resource router is
[`backend/archimedes/api/chat_routes.py`](../../backend/archimedes/api/chat_routes.py)
(145 lines). Same shape — module docstring, `APIRouter(prefix=…, tags=[…])`,
endpoint functions with response_model. The split should produce 9 files
each shaped like that one.

For the shared-helpers-extraction shape, copy how
`backend/archimedes/api/selection_bias_routes.py` factors out
`_synthetic_returns_from_stub` as a private helper. Multi-router helpers
go in `_route_helpers.py`; single-router helpers stay private to that
router file.

## Out of scope (deferred follow-up PRs)

- Deleting `routes.py` (the shim) after CI confirms zero surprises.
- Updating `main.py`'s include-router list to import directly from the
  new files (cosmetic; the shim's re-exports are equally fine).
- Migrating the dedicated routers in `chat_routes.py` etc. into a `routers/`
  subpackage (optional; no functional benefit).

## Owner suggestion

t2o2 executes — high mechanical, low judgment work. **Chuan reviews** (his
lane is the chain integration most touched by `vaults_routes.py` +
`traces_routes.py`). Daniel R. reviews the strategies + advisor splits.

This issue is the LAST of the Phase 7 dedup batch — should land AFTER
rigor consolidation, LLM backend unification, and constructor retirement,
so the splits don't have to re-do work those issues already touched.

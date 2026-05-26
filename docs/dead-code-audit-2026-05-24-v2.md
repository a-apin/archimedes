# Dead-Code Audit v2 — 2026-05-24 (refreshed 2026-05-25 — submission day)

> **Refresh 2 (2026-05-25, submission day):** Pivots the doc from "what's safe
> to delete" to **"what's still stub vs shipped, and where the plan gaps are"** —
> the question that matters for the final submission window. The original audit's
> headline ("zero files safe to delete now") still holds; the new value is the
> **§ "Work Remaining Inventory"** near the bottom, which catalogs every
> currently-stubbed file against the plan that protects it and what would
> constitute "shipped." Baseline: `main` @ `2195417` (Merge PR #263, 2026-05-25)
> — same as Refresh 1; no main commits have landed in the intervening hours
> (the run of 5 dbrowneup PRs + 1 workflow PR Dan's session merged are all on
> origin/main at this SHA already).
>
> **Refresh 1 (earlier 2026-05-25):** main moved by ~80 commits / 90+ files after
> the v2 baseline. Three v2 findings were **resolved** by intermediate merges
> (test_user_profile_privacy moved, source_tracker wired, StressScenarioPanel
> wired); no new dead-code surfaced. See § "Refresh delta — 2026-05-25" mid-doc.

> **Supersedes:** the earlier same-day v1 audit, which was retracted as unsound.
> v1 ran against a static snapshot of `main` and treated "no current importer" as
> "dead." That missed (a) intentional stubs in active plans, (b) files about to
> be re-wired by an in-flight PR, and (c) entrypoints invoked by `docker-compose`
> / `python -m` / `forge script` that have no Python-level import edges.
>
> **Audit baseline:** `main` @ `d6afdca` (Merge PR #225 from
> `dbrowneup/pr-eip6963-wallet-discovery`, 2026-05-24).
> **Methodology v2 (3-phase):**
> 1. Build a **protected-files set** FIRST from every active plan, spec, open PR,
>    and open issue. Treat any file mentioned in any active artifact as off-limits
>    by default. (135 files protected at audit time.)
> 2. Inventory + importer map against current `main` (one-pass: `grep -rEn 'from
>    archimedes' …` cached, then parsed with python to handle both `from … import
>    X` and `import … .X` forms).
> 3. For every zero-importer candidate, MANDATORY cross-check against the
>    protected set + entrypoint heuristics (`if __name__`, docker-compose service,
>    `python -m`, `forge script`) before flagging anything as dead.
>
> **No deletions performed.** This is a manifest. **No file changes anywhere.**
> Delivered in chat + committed (not pushed) on branch
> `dbrowneup/dead-code-audit-v2-wt` inside `.claude/worktrees/audit-v2/`.

---

## TL;DR

| Bucket | Files | Action |
|---|---:|---|
| **Fully dead — safe to delete now** | **0** | nothing |
| Test-only (no runtime, no protection) | 1 | flag for decision, don't act unilaterally |
| Plan-protected zero-importer (intentional stubs) | 3 | leave alone, named in active plans |
| Operator-only entrypoints (docker-compose, `python -m`, forge) | 8 + 3 Forge | leave alone, intended |
| Orphaned test (wrong directory) | 1 | **move** not delete |
| JSX/TSX zero-importer (all protected or entrypoint) | 0 actionable | none |
| Solidity zero-importer (all Forge scripts or protected) | 0 actionable | none |

**Bottom line:** every candidate the v1 audit listed as "fully dead" turns out
to be protected by an active plan, an in-flight PR (`PortfolioAdvisor.jsx`
re-wired by #216), an entrypoint, or a deliberate stub for upcoming work. The
correct number of files to delete right now is **zero**. The audit's value is
making explicit *why* — and identifying which removals become safe **after**
which specific plans land.

---

## Phase 0 — What changed under main since v1 ran (~3 hours ago)

21 commits landed on `main` after v1's baseline (`409cc0f`). The ones that
invalidated v1's findings:

| Merge | What it changed | v1 finding it invalidated |
|---|---|---|
| **PR #215** — prune historical artifacts to `docs/archive/` | Moved active plans into `docs/archive/` (a docs reorg, not a retirement signal — `archive/` here means "moved out of the top-level browsing surface," not "no longer active"). | v1's grep didn't consult `docs/archive/`, so it missed protection signals like the launch plan's `AgentLike` reference. |
| **PR #216** — resurrect `PortfolioAdvisor` on `/generate` (closes #210) | `ui/src/components/Generate.jsx:6` now `import PortfolioAdvisor from './PortfolioAdvisor'`; rendered on `Generate.jsx:297,312`. | v1 flagged `PortfolioAdvisor.jsx` as zero-importer and recommended deletion. It is now LIVE. |
| **PR #220** — kill fake Library Correlation matrix | Removed the `<CorrelationMatrix>` render from `Strategies.jsx` but **explicitly kept the component file** ("kept for potential re-use once we persist real daily series"). Reasoning.jsx still imports it. | Would have been flagged by a naive v2 too without reading PR body. |
| **PR #222** — regime-honest | Touched `RegimePanel.jsx`, `chain/agent_runner.py`, regime routes. | Confirms regime detection is being actively reshaped — both `regime_detector.py` and `statistical_regime.py` are in flux, not "dead." |
| **PR #223** — ruff Tier 1 + autofixes | Touched 105 files mechanically. | v1's importer map ran against pre-autofix code; v2 ran post-autofix. |

---

## Phase 1 — Protected set construction

### Sources (in priority order)

| Source | What we extracted | Count of unique file refs |
|---|---|---|
| `docs/archive/*.md` (15 files) | Every file path / dotted module mentioned | included |
| `docs/specs/*.md` (20 files) | Same | included |
| `docs/*.md` top-level (19 files) | Same | included |
| `gh pr diff` for open PRs #199, #214, #225 | All files those PRs touch (about to land) | 22 files |
| `gh issue view` for open issues #218, #219, #163, #164, #160, #155, #154, #151, #148, #147, #212, #176 | Files named in issue bodies | 46 files |
| **Union, dedupe, filter to existing-on-disk** | | **135 files** |

Stored at `/tmp/audit-v2/protected_existing.txt` (135 lines, all confirmed to
exist on the audit-baseline tree).

### Key protections worth calling out

These are the files v1 wrongly targeted; each is now demonstrably protected:

- **`backend/archimedes/agents/base.py`** (36 lines) — `AgentLike(Protocol)`
  defined as an intentional stub. Named in:
  - `docs/archive/launch-execution-plan-2026-05-23.md:1148` ("Protocol for AgentLike + shared utilities")
  - `docs/archive/launch-execution-plan-2026-05-23.md:1172` (literal acceptance check: `python -c "from archimedes.agents.base import AgentLike"` → succeeds)
  - Closed Issue #173 ("Refactor agentic services into agents/ subpackage with shared base.py") — closed, but Issues #163 + #164 (Strategy Generation Agent + Portfolio Construction Agent) carry the work forward.
- **`backend/archimedes/services/regime_detector.py`** (111 lines) — old v1 detector.
  Named in `docs/specs/component-interfaces-spec.md`, `docs/chuan-architecture-survey.md`,
  `docs/judging-rubric-assessment.md`. Survey marks it as "superseded but coexists"
  pending Önder's read.
- **`backend/archimedes/services/statistical_regime.py`** (466 lines) — v2 GMM detector.
  Named in `docs/chuan-architecture-survey.md` + others. Survey gap #2: regime
  detection consolidation is deferred ("needs Önder's read on which is wired to
  `RegimePanel` before specing").
- **`backend/archimedes/services/_deprecated/portfolio_constructor.py`** (282 lines)
  + **`_deprecated/kelly_portfolio.py`** (523 lines) — named in
  `docs/specs/portfolio-constructor-decision-tree.md` (the canonical decision tree)
  + Issue tracker references. The `_deprecated/` location is the *plan*; deletion
  is pending the retirement step in the decision tree.
- **`backend/archimedes/services/source_tracker.py`** (86 lines) — Xia 2026 § 4.3
  Source Tracking protocol. Named in `docs/specs/xia-2026-protocols.md` and
  `docs/archive/launch-execution-plan-2026-05-23.md`. Issue #219 (Önder driving,
  T3.7 Xia 2026 named protocols) finishes the wiring; deletion would be the wrong
  direction.
- **`backend/archimedes/chain/strategy_publisher.py`** (190 lines) — `StrategyPublisher`
  class. Named in `docs/archive/launch-execution-plan-2026-05-23.md`. The wiring
  into the runtime path is pending (not yet imported anywhere outside tests).
- **`backend/archimedes/chain/agent_runner.py`** (923 lines) — `StrategyRunner`.
  Test-only at the Python-import level, BUT a `docker-compose` service entrypoint
  (`python -m archimedes.chain.agent_runner`). Live.
- **`backend/archimedes/scripts/run_kb_pipeline.py`** (115 lines) — **v1 audit
  error.** Looked orphaned but `services/kb_runner.py:102` does `from
  archimedes.scripts.run_kb_pipeline import run_pipeline` inside its tick loop,
  AND `api/corpus_routes.py:151` instructs users to invoke it directly. v2
  catches this via the "entrypoints + reverse-import" check.
- **`ui/src/components/PortfolioAdvisor.jsx`** (480 lines) — LIVE via PR #216,
  `Generate.jsx:297,312`.
- **`ui/src/components/StressScenarioPanel.jsx`** (131 lines) — named in
  `docs/specs/spine-plus-v2-plan.md`, `docs/archive/phase5-execution-runbook.md`,
  `docs/archive/afternoon-execution-plan-2026-05-24.md`. Planned for re-wire.
- **`ui/src/components/CorrelationMatrix.jsx`** (176 lines) — PR #220's body
  explicitly says "`CorrelationMatrix.jsx` is **not** deleted — kept for
  potential re-use once we persist real daily series." Also imported by
  `Reasoning.jsx` (my JSX importer regex missed the `.jsx` extension; manual
  verification confirmed the live import).
- **`contracts/src/interfaces/IPriceOracle.sol`** (36 lines) — named in
  `docs/specs/component-interfaces-spec.md` and `docs/specs/ecosystem-design-spec.md`.
  Currently no Solidity-level import or cast, but the interface is part of the
  ecosystem spec contract.

---

## Phase 2 — Importer map (current `main` @ `d6afdca`)

Inventory: 116 Python modules under `backend/archimedes/`, 34 JSX/JS under `ui/src/`,
23 Solidity files under `contracts/src` + `contracts/script`.

### Python — ZERO IMPORTERS (12 modules)

After classifying each by entrypoint role and protection:

| File | Lines | Why it's zero-importer | Bucket |
|---|---:|---|---|
| `backend/archimedes/agents/base.py` | 36 | Intentional stub | **PLAN-PROTECTED** |
| `backend/archimedes/services/regime_detector.py` | 111 | v1 detector kept pending Önder's consolidation read | **PLAN-PROTECTED** |
| `backend/archimedes/services/_deprecated/portfolio_constructor.py` | 282 | Deprecated, kept pending decision-tree retirement step | **PLAN-PROTECTED** |
| `backend/archimedes/chain/oracle_runner.py` | 52 | `docker-compose` service via `python -m` | **OPERATOR ENTRYPOINT** |
| `backend/archimedes/services/kb_runner.py` | 115 | `docker-compose` service via `python -m` | **OPERATOR ENTRYPOINT** |
| `backend/archimedes/evaluation/stockbench/__main__.py` | 88 | `python -m archimedes.evaluation.stockbench` entrypoint | **OPERATOR ENTRYPOINT** |
| `backend/archimedes/scripts/bootstrap_vaults.py` | 575 | `python -m …` operator script (in plan) | **OPERATOR ENTRYPOINT** |
| `backend/archimedes/scripts/deploy_contracts.py` | 365 | `python -m …` operator script | **OPERATOR ENTRYPOINT** |
| `backend/archimedes/scripts/hydrate_corpus.py` | 154 | `python -m …` operator script | **OPERATOR ENTRYPOINT** |
| `backend/archimedes/scripts/seed_backtests_from_artifacts.py` | 118 | `python -m …` operator script | **OPERATOR ENTRYPOINT** |
| `backend/archimedes/scripts/verify_arc_e2e.py` | 635 | `python -m …` operator script (in plan) | **OPERATOR ENTRYPOINT** |
| `backend/archimedes/tests/test_user_profile_privacy.py` | 186 | Wrong directory; `pytest.ini` doesn't collect `backend/archimedes/tests/` | **ORPHANED TEST — MOVE** |

**Truly safe-to-delete count after cross-check: 0.**

The orphaned test (`test_user_profile_privacy.py`) should be **moved** to
`backend/tests/test_user_profile_privacy.py` where pytest will collect it (this
restores Issue #181 privacy test coverage on live `email_crypto`/`log_scrubber`
code in `api/user_routes.py`).

### Python — TEST-ONLY (runtime=0, scripts=0, tests>0) (9 modules)

| File | Lines | Test importer | Disposition |
|---|---:|---|---|
| `backend/archimedes/main.py` | 275 | 4 tests | LIVE (uvicorn entrypoint) |
| `backend/archimedes/chain/agent_runner.py` | 923 | 2 tests | LIVE (docker-compose service) |
| `backend/archimedes/chain/strategy_publisher.py` | 190 | 6 tests | **PLAN-PROTECTED** (launch plan) |
| `backend/archimedes/evaluation/stockbench/adapter.py` | 656 | 2 tests | LIVE via `stockbench/__main__.py:13` import |
| `backend/archimedes/scripts/run_backtests.py` | 174 | 1 test | LIVE (`__main__` operator script) |
| `backend/archimedes/services/_deprecated/kelly_portfolio.py` | 523 | 1 test | **PLAN-PROTECTED** (decision tree) |
| `backend/archimedes/services/source_tracker.py` | 86 | 7 tests | **PLAN-PROTECTED** (Issue #219, Xia spec) |
| `backend/archimedes/services/statistical_regime.py` | 466 | 1 test | **PLAN-PROTECTED** (survey gap #2) |
| `backend/archimedes/services/arxiv_corpus.py` | 478 | 1 test | **PLAN-PROTECTED** (named in `docs/specs/spine-plus-v2-plan.md:909` as "#4 Arxiv intake paths" consolidation work) |

All 9 are accounted for. **None recommended for unilateral deletion.**

`arxiv_corpus.py` is the closest thing to a candidate — it's flagged in
spine-plus-v2 as one of three parallel intake paths to consolidate, but the
consolidation itself hasn't shipped. Wait for that consolidation PR before
acting.

### JSX/JS — ZERO IMPORTERS (regex-checked + manually verified)

| File | Lines | True status |
|---|---:|---|
| `ui/src/main.jsx` | 11 | LIVE (Vite entrypoint, not imported) |
| `ui/src/App.jsx` | 234 | LIVE (`main.jsx:5: import App from './App.jsx'` — my regex missed the `.jsx` extension) |
| `ui/src/components/CorrelationMatrix.jsx` | 176 | LIVE (`Reasoning.jsx` imports it) AND **PLAN-PROTECTED** (PR #220 explicitly kept it + `docs/specs/evening-execution-plan-2026-05-24.md`) |
| `ui/src/components/StressScenarioPanel.jsx` | 131 | **PLAN-PROTECTED** (3 plans reference it for re-wire) |

**Truly safe-to-delete count: 0.**

### Solidity — ZERO IMPORTERS

| File | Lines | True status |
|---|---:|---|
| `contracts/script/Deploy.s.sol` | 204 | LIVE (`forge script` entrypoint) |
| `contracts/script/DeployInfra.s.sol` | 53 | LIVE (`forge script` entrypoint; referenced in launch plan) |
| `contracts/script/DeployStrategyRegistry.s.sol` | 34 | LIVE (`forge script` entrypoint; referenced in launch plan) |
| `contracts/src/interfaces/IPriceOracle.sol` | 36 | **PLAN-PROTECTED** (component-interfaces-spec + ecosystem-design-spec) |

**Truly safe-to-delete count: 0.**

---

## Phase 3 — Cross-check methodology (the v1 → v2 fix)

For each candidate, the gate is:

```
candidate is SAFE-TO-DELETE only if:
    candidate not in protected_existing.txt                           AND
    candidate does NOT contain `if __name__ == "__main__"`            AND
    candidate is NOT a docker-compose service                         AND
    candidate is NOT loaded dynamically (importlib, exec, eval)       AND
    candidate is NOT a Forge script (.s.sol, contracts/script/)       AND
    candidate is NOT a Vite entry (main.jsx, App.jsx, vite.config*)   AND
    candidate file is NOT mentioned by name (with or without path)
        in ANY plan/spec/issue/open-PR body
```

Eight conditions, ALL must pass. **No candidate in v2 passed all eight.**

---

## Recommendations

### Do this now

1. **Move (don't delete)** `backend/archimedes/tests/test_user_profile_privacy.py`
   → `backend/tests/test_user_profile_privacy.py`. Restores Issue #181 privacy
   coverage on live `email_crypto`/`log_scrubber` code. This is the only
   actionable item from v2. Even this is a `git mv`, not deletion.

### Do these only when a specific PR lands

| Trigger event | Becomes-safe-to-delete |
|---|---|
| Spine-plus-v2 § "#4 Arxiv intake paths" consolidation PR merges | `services/arxiv_corpus.py` (478 lines) — wait for it. |
| `docs/specs/portfolio-constructor-decision-tree.md` retirement step ships | `services/_deprecated/portfolio_constructor.py` (282) + `_deprecated/kelly_portfolio.py` (523) + the deprecated `__init__.py` |
| Regime consolidation (survey gap #2, Issue not yet filed) ships | one of `regime_detector.py` (111) or `statistical_regime.py` (466) becomes deletable depending on Önder's read |
| Issue #173 follow-on / agentic refactor completes | `agents/base.py` continues to be protected unless the `AgentLike` Protocol is explicitly retired in spec |

### Do this never (well, almost)

- Don't delete `chain/strategy_publisher.py` — it's awaiting wiring per the launch
  plan, not orphaned by mistake.
- Don't delete `IPriceOracle.sol` — interface contract referenced in two specs;
  the absence of Solidity-level imports is intentional (interface for off-chain
  consumers).
- Don't delete any `docker-compose` service entrypoint, `python -m` script, or
  Forge script. Zero Python importers ≠ unused.

---

## Reproducibility

All intermediate files persist under `/tmp/audit-v2/`:

| File | Contents |
|---|---|
| `protected_existing.txt` | 135 files protected by plans/specs/PRs/issues |
| `modules.txt` | inventory of all 116 Python modules + paths |
| `all_imports.txt` | 608 raw import lines from grep |
| `module_importers_py.txt` | per-module importer counts (from python parser) |
| `module_importers.txt` | joined inventory + importer counts |
| `jsx_importers.txt` | 34 JSX files + importer counts |
| `sol_importers.txt` | 23 Solidity files + importer counts |

To regenerate from scratch on a future `main`:

```bash
# Phase 1: protected set
mkdir -p /tmp/audit-v2
grep -rhE '(backend|ui|contracts|scripts|analytics-engine|tests)/[A-Za-z0-9_/.-]+\.(py|jsx|tsx|ts|js|sol)' \
  docs/archive/ docs/specs/ docs/*.md \
  | sort -u > /tmp/audit-v2/protected_from_docs.txt

for n in $(gh pr list --state open --json number --jq '.[].number'); do
  gh pr diff $n --name-only
done | sort -u > /tmp/audit-v2/protected_from_open_prs.txt

# Phase 2: importer map via python (handles both import forms)
grep -rEn --include="*.py" -- '(from|import) archimedes\.[a-zA-Z0-9_.]+' \
  backend/ scripts/ analytics-engine/ tests/ > /tmp/audit-v2/all_imports.txt
# Then run the python parser block in this file

# Phase 3: cross-check
awk -F'|' '$6==0 { print $2 }' /tmp/audit-v2/module_importers.txt \
  | grep -Fvxf /tmp/audit-v2/protected_existing.txt
```

---

## Refresh delta — 2026-05-25 (main @ `2195417`)

Re-ran the audit against latest origin/main after ~80 commits / 90+ files of
merges. Verified each prior finding by `git cat-file -e` against `origin/main`
and `git grep` for new importers. **No deletions. Headline unchanged.**

### Three v2 findings resolved by intermediate merges (no action needed)

| v2 finding | Merge that resolved | New status |
|---|---|---|
| **`backend/archimedes/tests/test_user_profile_privacy.py` → `backend/tests/`** (only actionable in v2) | **PR #229** (`[tests] Move test_user_profile_privacy.py into pytest collection path`) — landed 2026-05-25 03:03 UTC, cites v2 directly | ✅ done; pytest now collects it; Issue #181 privacy coverage restored |
| **`services/source_tracker.py`** was test-only / plan-protected | **PR #235** (`onder/source-tracker-wiring`, `[quant] Wire source_tracker into reasoning trace — Xia § 4.3 runtime`) | ✅ now LIVE: `chain/agent_runner.py:40` imports `build_consulted_hashes`, called at 3 trace-publish sites; closes #219 |
| **`ui/src/components/StressScenarioPanel.jsx`** was plan-protected zero-importer | **PR #258** (`moonshot/244-stress-panel`, `[frontend] Wire StressScenarioPanel into Portfolio`) | ✅ now LIVE: `ui/src/components/Portfolio.jsx:7` imports, `Portfolio.jsx:201` renders |

### Other significant merges that touch the audit surface (status unchanged)

| Merge | Effect on audit |
|---|---|
| **PR #239** (`onder/stockbench-consolidation`, "Option C") | Consolidated two parallel StockBench adapters into the canonical `evaluation/stockbench/adapter.py`; **deleted** `benchmarks/stockbench_adapter.py` (419 LOC) + `scripts/stockbench_run.py` (234 LOC). v2 didn't list those (they were added and deleted between v2 baseline and this refresh — net-zero against `d6afdca`), but worth recording. |
| **PR #214** (`moonshot/147-aws-s3-dynamodb-iam`) | Added `services/dynamodb_paper_index.py` + `services/s3_artifact_store.py` + `api/auth_guard.py` + `services/secrets_service.py` to the tree. See "New zero-importer files" below. |
| **PR #265** (`moonshot/fix-missing-secrets-service`) | Hotfix adding `services/secrets_service.py` (502 fix). Now LIVE: `main.py` imports it. |
| **5× dependabot merges** (#247–#251) | Backend dep bumps; no audit impact. |
| **PR #267, #272** ruff format work | Mechanical autofixes; no audit impact. |

### New zero-importer files since v2 (all PLAN-PROTECTED / intentional foundation)

PR #214 added two boto3-wrapper services as the AWS foundation per
`docs/archive/launch-execution-plan-2026-05-23.md`. They're zero-importer at
runtime today, but explicitly intended as foundation for downstream features
(#148 HTTPS, #151 GPU EC2 + KB pipeline, #176 SSM secrets). 27 unit tests
cover both. **Both are PLAN-PROTECTED** by the same pattern as `agents/base.py`:
named in an active plan as a stub for upcoming work.

| File | Lines (approx) | Test coverage | Why zero-importer is OK |
|---|---:|---:|---|
| `backend/archimedes/services/dynamodb_paper_index.py` | — | ✅ `test_dynamodb_paper_index.py` | Named in launch plan; foundation for #151 KB pipeline |
| `backend/archimedes/services/s3_artifact_store.py` | — | ✅ `test_s3_artifact_store.py` | Named in launch plan; foundation for #151 KB pipeline + #148 HTTPS |

### Refreshed bottom line (still zero deletions recommended)

| File | v2 status | Refresh status |
|---|---|---|
| `agents/base.py` | PLAN-PROTECTED | unchanged |
| `services/regime_detector.py` | PLAN-PROTECTED, zero-importer | unchanged (no consolidation PR yet) |
| `services/_deprecated/portfolio_constructor.py` | PLAN-PROTECTED | unchanged |
| `services/_deprecated/kelly_portfolio.py` | PLAN-PROTECTED (test-only) | unchanged |
| `services/arxiv_corpus.py` | PLAN-PROTECTED (test-only) | unchanged (spine-plus-v2 #4 still open) |
| `services/source_tracker.py` | PLAN-PROTECTED (test-only) | **RESOLVED — now runtime-wired (PR #235)** |
| `services/statistical_regime.py` | PLAN-PROTECTED (test-only) | unchanged |
| `chain/strategy_publisher.py` | PLAN-PROTECTED (test-only) | unchanged |
| `tests/test_user_profile_privacy.py` | ORPHANED → MOVE | **RESOLVED — moved (PR #229)** |
| `StressScenarioPanel.jsx` | PLAN-PROTECTED, zero-importer | **RESOLVED — now runtime-wired (PR #258)** |
| `PortfolioAdvisor.jsx` | LIVE via Generate.jsx | unchanged |
| `CorrelationMatrix.jsx` | LIVE via Reasoning.jsx + PROTECTED | unchanged |
| `IPriceOracle.sol` | PLAN-PROTECTED, zero-importer | unchanged |
| Operator entrypoints (oracle_runner, kb_runner, scripts/*, forge scripts, stockbench/__main__) | LIVE as entrypoints | unchanged |
| `services/dynamodb_paper_index.py` (NEW) | n/a | PLAN-PROTECTED (launch plan), test-covered |
| `services/s3_artifact_store.py` (NEW) | n/a | PLAN-PROTECTED (launch plan), test-covered |

**Files safe-to-delete unilaterally: still 0.** Conditional deletion roadmap
(arxiv_corpus / _deprecated / regime consolidation) is unchanged — the
prerequisite PRs haven't shipped.

### Other useful telemetry from the refresh

- `backend/archimedes/tests/` directory was untouched by PR #229's `git mv`
  (it deleted the file but left the dir). It is now empty on `origin/main`. The
  dir itself can be `rmdir`'d in a tiny follow-up PR; leaving it is harmless.
- Backend test coverage rose materially in this window: 15+ new `backend/tests/services/test_*.py`
  files (PR #214's 60.66% coverage gate work). Several were for previously-untested
  services flagged in the survey (`test_regime_detector.py`, `test_kb_runner.py`,
  `test_amm_bootstrap.py`, etc.). None of those flip an audit verdict — they
  add tests for files that were already LIVE-via-entrypoint or
  PLAN-PROTECTED.

---

---

## Work Remaining Inventory (2026-05-25, submission day)

This section is the audit's primary deliverable for the final-window decision
making. For each currently-stubbed file or planned-but-not-shipped surface,
state precisely: **what exists today, what the plan says is supposed to exist,
what would constitute "shipped," and who owns the gap.** This is *not* a delete
list — it is a build list (and an honest "deferred" list).

The aim of organizing it this way is to make it trivially clear to a reader (or
to a `t2o2` issue author) what work is still on the table vs. what's done — and
to surface the gaps that don't have an owner yet.

### Closed since Refresh 1 (today) — confirmation

These issues / PRs landed today and need to be reflected in any prior reading
of the audit. Most were referenced as protection sources in v2:

| Closed | What | Status of the protected file(s) |
|---|---|---|
| **#147** | AWS S3 + DynamoDB for paper artifacts + IAM | Infrastructure complete; `services/s3_artifact_store.py` + `services/dynamodb_paper_index.py` are foundation code (still zero runtime importers) — see below. |
| **#151** | GPU EC2 + KB pipeline on 10k corpus → S3 / DynamoDB | Infrastructure complete; runtime wiring of pipeline output into the `/api/corpus/*` 503-or-real-data surface still needs to be exercised against the live KB artifacts. |
| **#173** | `agents/` subpackage with shared `base.py` | Subpackage created; `AgentLike` Protocol exists at `agents/base.py` (36 LOC); **no runtime adopter has imported it yet.** Sequel issues #163 + #164 carry the adoption work. |
| **#218** | StockBench harness (Önder) | Adapter consolidated to `evaluation/stockbench/adapter.py` (PR #239 Option C). LIVE via `__main__.py` entrypoint. |
| **#219** | Xia 2026 named protocols | `source_tracker.py` wired into `chain/agent_runner.py:40` (PR #235); `purged_kfold` + other protocols enumerated in [`docs/specs/xia-2026-protocols.md`](specs/xia-2026-protocols.md). |

### Still-stub files (zero runtime importers as of `2195417`)

For each: the runtime importer count (excluding tests + self-import), test
importer count, the plan that protects it, and what "shipped" looks like.

Verified by `grep -rE '(from|import) <module>( |$|\.)' backend/ scripts/
analytics-engine/` on 2026-05-25.

| File | LOC | runtime / test importers | Protecting plan | What "shipped" looks like | Owner |
|---|---:|---:|---|---|---|
| `backend/archimedes/agents/base.py` | 36 | 0 / 0 | Closed #173 (subpackage created), open #163 + #164 (concrete adopters) | A Strategy Generation Agent + Portfolio Construction Agent both declare `AgentLike` and the `services/` callsites switch to importing from `agents/` rather than `services/`. Until then the Protocol is genuinely unreferenced. | Önder (#163), Daniel R. / Önder (#164) |
| `backend/archimedes/services/regime_detector.py` | 111 | 0 / 1 | `docs/chuan-architecture-survey.md` gap #2 (regime consolidation); component-interfaces-spec | Önder reviews v1 (`regime_detector.py`) vs v2 (`statistical_regime.py`), picks one, deletes the other, and the surviving file is imported by `RegimePanel`'s data path. No issue filed yet. | Önder (architecture call) |
| `backend/archimedes/services/statistical_regime.py` | 466 | 0 / 1 | Same as above (gap #2) | Same as above — consolidation picks one or the other. | Önder |
| `backend/archimedes/services/_deprecated/portfolio_constructor.py` | 282 | 0 / 0 | `docs/specs/portfolio-constructor-decision-tree.md` retirement step | The decision tree's "retirement step" gets ticked (the surviving constructor(s) cover all callsites in `services/strategy_fusion.py` + the rebalance path) and the `_deprecated/` directory is deleted. | Önder (decision-tree owner) |
| `backend/archimedes/services/_deprecated/kelly_portfolio.py` | 523 | 0 / 1 | Same retirement step | Same as above. | Önder |
| `backend/archimedes/services/arxiv_corpus.py` | 478 | 0 / 1 | `docs/specs/spine-plus-v2-plan.md:909` (Phase 7 dedup #4) marked **Defer**; corpus-architecture.md as the canonical seed/intake path | Spine-plus-v2's "#4 Arxiv intake paths" consolidation PR ships, picking one of: `arxiv_corpus.py`, `corpus_service.py`, or `scripts/bulk_ingest_arxiv.py` as canonical. Currently deferred pending Dan's KB pipeline stabilization. | Dan |
| `backend/archimedes/chain/strategy_publisher.py` | 190 | 0 / 6 | `docs/archive/launch-execution-plan-2026-05-23.md` (publishes Strategy Passport metadata on-chain via `StrategyRegistry` contract) | `StrategyPublisher` gets called from the strategy-deploy path (post-passport-creation) so passports anchor on-chain alongside reasoning traces. Today only the trace half is wired (`source_tracker` → `ReasoningTraceRegistry`); passport half is stub. | Chuan / Marten |
| `backend/archimedes/services/dynamodb_paper_index.py` | NEW since v2 | 0 / 1 | `docs/archive/launch-execution-plan-2026-05-23.md`; closed #147 + #151 | KB pipeline output (paper-level metadata + cluster ID + embedding pointer) actually writes to DynamoDB on a pipeline run, and `corpus_routes.py` reads from it for the Explorer. Today: code exists, integration tested with mocks, no live KB artifact has been written yet. | Dan (KB pipeline), Chuan (infra glue) |
| `backend/archimedes/services/s3_artifact_store.py` | NEW since v2 | 0 / 1 | Same as above | KB pipeline output (artifact JSON / pickled topic model / SPECTER2 embeddings) actually writes to S3 and `corpus_routes.py` resolves artifact pointers when serving the Explorer. Same status as DynamoDB — code exists, no live KB artifact yet. | Dan + Chuan |
| `contracts/src/interfaces/IPriceOracle.sol` | 36 | n/a (Solidity interface) | `docs/specs/component-interfaces-spec.md`, `docs/specs/ecosystem-design-spec.md` | An off-chain consumer (e.g. a future external indexer or third-party oracle client) imports the interface ABI. **Today the interface is referenced by the spec but not implemented against by any external party** — keeping it is correct (it's a published contract); deleting would break the spec promise. | Chuan |

**Aggregate count:** **10 stub files** carrying ~2,706 LOC, all protected by an
active plan or interface promise. Zero are deletable today. Each row's "what
shipped looks like" answers Dan's question literally — these are the gaps.

### Plan gaps — work spec'd but not built (Phase 4 + Phase 5 + 3c + 9)

The `docs/specs/spine-plus-v2-plan.md` phase status snapshot as of `2195417`:

| Phase | Status | What's missing |
|---|---|---|
| **Phase 0** — Architectural Specs | ✅ LANDED | — |
| **Phase 1** — Junk extermination + UX fixes | ✅ LANDED | — |
| **Phase 2** — Streaming Generate on `portfolio_agent.py` | ✅ LANDED | — |
| **Phase 3a + 3b** — Real Explore + Corpus depth | ✅ LANDED | — |
| **Phase 3c** — KB integration | 🟡 SKELETON ONLY | Production pipeline body deferred pending Dan's Linus-side iteration. `services/kb_runner.py` + `services/kb_artifacts.py` + `scripts/run_kb_pipeline.py` exist as the runtime hooks; the pipeline has not been *run* end-to-end against the 10k corpus on the GPU EC2 host. `/api/corpus/*` returns **503 "kb_artifact_not_found"** until a real artifact lands. **Closed #151 means infra is ready; the artifact run is the remaining step.** |
| **Phase 4** — Vault encapsulation (1:1, time-bound) | 🟠 PARTIAL | `vaults_routes.py` exists (7 endpoints incl. `/create`, `/{addr}/metadata`, `/{addr}/derive-allocations`), `CreateVaultModal.jsx` + `StrategyPassport.jsx` + `DepositFlow.jsx` exist on the frontend. **MISSING:** `services/vault_lifecycle.py` — no PENDING → ACTIVE → COMPLETED state machine; `vault_service.py` has the data layer but no time-bound trade-window enforcement. The flow today is "create vault → deposit → agent rebalances perpetually." The 1:1 time-bound model is unimplemented. **Pending Chuan + Marten alignment on the 5 open questions in the phase spec.** |
| **Phase 5** — Real testnet trade execution | 🟠 CODE-COMPLETE / UNVERIFIED | `DepositFlow.jsx` issues approve + deposit + setTargetAllocations; `chain/executor.py:143` calls `vault.rebalance(tokens_in, amounts_in, tokens_out, amounts_out)`; `chain/agent_runner.py:446` records the rebalance. The path *exists* in code. What's missing is the end-to-end verification: one signed execution from a real wallet that results in (a) USDC deposited, (b) vault holds synth tokens, (c) on-chain trace anchored, (d) Portfolio reflects state. **No runbook documents this has actually happened on Arc testnet from the live UI.** |
| **Phase 6** — Onboarding tour | ✅ LANDED | PR #134; Phase 8 polish (#262) also landed. |
| **Phase 7** — Consolidation & dedup via t2o2 | ✅ LANDED | All 6 issues (#128–#133) closed. |
| **Phase 8** — Landing polish | ✅ LANDED | #262 + #263 today. |
| **Phase 9** — Fusion engine UI surface (third Generate mode toggle) | 🟠 NOT STARTED | `phase8-9-landing-and-fusion-spec.md` spec exists but no Generate-mode toggle UI ships. Backend `POST /api/strategies/generate?mode=fusion` is referenced in the spec; verify whether the endpoint exists and whether the UI exposes it. |

### Open issues with build work remaining

| Issue | Title | What it adds | Status as of `2195417` |
|---|---|---|---|
| **#163** | APIN - Backend - Strategy Generation Agent emits BOTH a bull-tilted AND a bear-tilted candidate per Generate call | Adopts `AgentLike` Protocol (would activate `agents/base.py`); changes Generate UX to surface considered-alternatives panel against current K=1 | OPEN — Önder |
| **#164** | APIN - Backend - Portfolio Construction Agent reads regime + applies bull/bear weight schedule | Second adopter of `AgentLike` Protocol; reads from regime detector output | OPEN — depends on #163's regime read |
| **#160** | APIN - Backend - Unify file-based + StrategyRecord ORM into ONE `strategy_passports` Postgres table | Migration consolidating two strategy persistence layers | OPEN |
| **#155** | APIN - Infra - AWS ALB + CloudFront + ASG: virality-ready backend tier | Production-scale infra; **post-hackathon** | OPEN, low priority for submission |
| **#154** | APIN - Backend+Security - [OPTIONAL] AWS Bedrock as primary LLM with IAM auth | Optional second LLM provider | OPEN, optional |
| **#212** | [security] Supply-chain hardening roadmap | pip-audit promoted to CI gate + SBOM + Dependabot triage | OPEN |
| **#43, #41** | Platform - Registration Page / User Management | Multi-user onboarding | OPEN, not for MVP |
| **#16** | APIN - DEMO - Final delivery (hackathon) | The submission itself | OPEN — closed when submitted |

### Summary — what to brief judges on if they ask "what's not built"

The honest, defensible answer (works because each item has a *named* plan and
owner):

1. **Vault lifecycle states (PENDING / ACTIVE / COMPLETED with a trade window):**
   spec'd in Phase 4; vault contract supports the data, but the off-chain
   state-machine worker (`vault_lifecycle.py`) and the time-bound enforcement
   are not built yet. Today vaults are "always Active."
2. **End-to-end testnet execution verified from the live UI:** code-complete
   (Phase 5), unverified. We have the path; we don't have a recorded runbook
   showing one signed execution from a real wallet results in the full
   USDC→synth→trace anchor cycle. Demo includes the path; production "we ran
   it twice" is the missing bit.
3. **KB pipeline production run:** infrastructure shipped today (#147, #151).
   The first end-to-end artifact run hasn't happened yet — Corpus Explorer
   returns 503 "first artifact pending" until it does.
4. **Two-agent generation (bull + bear candidates):** spec'd via #163; would
   activate the `agents/` subpackage. Current Generate is single-candidate K=1
   per the [`CLAUDE.md` § 5](../CLAUDE.md) architectural decision.
5. **Strategy passport on-chain anchoring (`strategy_publisher.py`):** code
   complete with 6 tests; not yet invoked from the deploy path. The trace half
   of "on-chain provenance" ships today; the passport half is the next hop.
6. **Three regime detectors / portfolio constructors / arxiv intake paths
   coexist** because consolidation decisions need a quiet hour Önder + Dan
   haven't had yet. None of the duplicates hurt correctness; they're style
   debt with a known cleanup path.

None of these are surprises; all six are documented; none affect what judges
see in the live demo. The discipline is in *naming them as gaps* rather than
hiding them behind aggregate scoring.

---

## See also

- v1 retracted: not retained on `main` (lived only in conversation context + an
  uncommitted file that was blown away by a branch switch — itself a lesson
  about not relying on uncommitted artifacts in a multi-agent checkout)
- `docs/chuan-architecture-survey.md` — the survey v2 cross-references for
  redundancy clusters #1 (rigor), #2 (regime), #3 (LLM backends), #4 (arxiv),
  #5 (portfolio constructors)
- `docs/specs/portfolio-constructor-decision-tree.md` — canonical for the
  `_deprecated/` retirement plan
- `docs/specs/xia-2026-protocols.md` — protects `source_tracker.py`
- `docs/specs/spine-plus-v2-plan.md` § "#4 Arxiv intake paths" — protects
  `arxiv_corpus.py` for now
- Closed Issue #173 + open Issues #163/#164 — context for `agents/base.py`

---

## Submission-day execution plan (2026-05-25, T-18h to deadline)

> Authored 2026-05-25 06:15 UTC; **refreshed 2026-05-25 13:40 UTC** by Maestro
> under Dan's steering. Translates the §"Work Remaining Inventory" above into
> specific tonight-overnight execution: live-state snapshot, paste-ready `t2o2`
> issue specs Dan can fire immediately, manual Dan-task checklist, and
> emergency triage notes for the four held specs that have been assigned to
> `t2o2`. **Refresh-1 also adds §"Critical review of t2o2 recent work" per
> Dan's directive — Chuan's bot's recent PRs need scrutiny, not trust.**
> Baseline at refresh: `main` @ `97c9099`. Since the first authoring,
> 15 commits landed (9 PRs), including the full T-PE.3 #160 cutover
> (Phase 1 + Phase 2), T-PE.7 #164 regime-portfolio, a Dan hotfix that
> caught a t2o2 schema-drift bug in production, and several wallet UX fixes.

### Live-state at audit time

**Initial snapshot — 2026-05-25 06:13 UTC (kept for record).** Site completely hung: TCP accepting on 80/443/22 but HTTPS root timing out at 30s. PR #275 (T-PE.3 unified-passport-store Phase 1) had merged ~10 minutes earlier and triggered a deploy cycle that wasn't completing.

**Refresh snapshot — 2026-05-25 13:40 UTC (this is the current state).** Backend has mostly recovered but the picture is mixed and shifting minute-to-minute:

| Path | HTTP code | Latency | Implication |
|---|---|---|---|
| `https://archimedes-arc.app/` (UI root) | **200** | 0.43s ✅ | nginx + UI bundle serving |
| `https://archimedes-arc.app/api/health` | **404** | 0.33s ⚠️ | Endpoint either renamed, never existed at this exact path, or removed during one of the recent cutovers. **Judge or operator running `/api/health` gets a misleading "not found." SPEC-3 below addresses this.** |
| `https://archimedes-arc.app/api/strategies/generated` | **200** | 0.34s ✅ | Strategies endpoint working |
| `https://archimedes-arc.app/api/regime/current` | **200** | 0.41s ✅ | Regime endpoint working |
| `https://archimedes-arc.app/api/traces/?limit=5` | **200** | 0.49s ✅ | Traces endpoint working |
| `https://archimedes-arc.app/api/vaults/` | **PARTIALLY FIXED (post-#297 + #300): 1st call 7.84s, 2nd 0.33s, 3rd 0.33s** | varies | **#288 closed by #297 + #300.** Cache layer added in #300 means cache-warm calls are fast; cache-cold (first call after 30s idle, e.g. judge landing on Portfolio fresh) is still ~8s because the N+1 on-chain RPC reads are unfixed at the root. **See § "Critical review of t2o2's batch fix #297 + #300" below — cache hides the bug, doesn't fix it.** |
| `https://archimedes-arc.app/api/traces/?limit=10` | **200 with 4 real rebalance traces — all with real arc_tx_hash values** | <500ms | **MAJOR REFRAMING.** Agent IS making real on-chain rebalance transactions right now. The "no real on-chain transactions yet" framing from earlier is no longer accurate post-#245 v_check fix + post-#297 vault stability. What remains missing: (a) a real-wallet-driven user deposit → rebalance flow (SPEC-1 still gates this), (b) arcscan-link UX surfaces (SPEC #303 just filed). |

**5 minutes earlier (at 13:35 UTC) every `/api/*` path returned HTTP 502** — the backend was fully unreachable while c0c2d21 + 97c9099 deploys cycled. Steady state is not yet established; expect transient behavior as `deploy.yml` finishes the post-#283 rebuild.

#### P0 recovery procedure (run if `/` itself is back down — DAN or CHUAN, ≤ 15 min)

```bash
ssh -i infra/archimedes-deploy-key.pem ubuntu@13.40.112.220 \
    'cd /opt/archimedes && docker compose ps && docker compose logs --tail=120 backend nginx'
```

Three diagnostic patterns and the fix for each:

1. **Backend container is in a restart loop.** Logs show SQLAlchemy errors like `column strategy_store.is_example does not exist` or similar schema-drift error. This is the recurring t2o2 pattern (see § "Critical review of t2o2 recent work" below). Fix: confirm the latest `init_db()` ALTERs ran; if not, restart with `docker compose down && docker compose up -d --build` (forces re-run).
2. **Backend is up but nginx upstream is unhealthy.** Logs show `connect() failed (111: Connection refused)`. Fix: `docker compose restart backend nginx`.
3. **Deploy is in flight right now.** Multiple deploys may be queued from the last 30 min (97c9099 ruff format + 90b2ad9 #283 cutover both triggered `deploy.yml`). Wait 5 minutes; expect HTTP 502 → 404-or-200 transient.

#### Post-recovery validation gate

Once HTTPS API is responding stably, validate the v_check fix (#245) is in production by checking for at least one successful rebalance trace since 03:00 UTC today:

```bash
curl -s 'https://archimedes-arc.app/api/traces/?limit=20' \
  | jq '.[] | select(.decision_type == "rebalance" and .created_at > "2026-05-25T03:00:00")'
```

If empty: the v_check fix is in code but the agent loop may be paused due to recent backend instability. If at least one rebalance trace is present: fix is live; proceed to SPEC-1.

---

### Critical review of t2o2 recent work (added 2026-05-25 14:00 UTC per Dan's directive)

> Dan: *"We need to review Chuan's bot's work with a critical eye. We've caught a lot of crap that it produced in the past, we need to be rigorous and critical and scan everything with careful judgement. We cannot allow junk or dead code to slip through."*

This section interrogates each t2o2-authored PR that merged in the last ~12 hours. Each entry: **what landed**, **what's solid**, **what's suspicious**, **mitigation**.

#### PR #275 — T-PE.3 #160 Phase 1: unified passport store (`moonshot/160-unified-passport-store`)

**What landed.** 5 files, 840 LOC added, 0 removed. New `StrategyPassportRecord` ORM, new `passport_loader.py` service, `--dry-run` migration script, 12 SQLite tests. Phase 2 cutover deferred to a follow-up.

**Solid.**
- The migration script defaults to `--dry-run`. Safe by default.
- Tests cover the migration script in SQLite memory; the schema shape is exercised.
- Phase 1 deliberately did NOT touch the read path. Conservatism is correct here.

**Suspicious.**
- **Schema drift caused production outage.** Adding `is_example` + `on_chain_registration_tx` + `on_chain_registration_block` + `parent_id` to `StrategyRecord` shipped via `Base.metadata.create_all()` only — which **does not add columns to existing tables**. Every Generate attempt on the live site died with `column strategy_store.is_example does not exist`. Dan caught this via Safari MCP, shipped PR #282 hotfix (idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS`). **This is the second time this exact pattern has hit the project** (the earlier `papers.cluster_id` incident is documented in `db.py`).
- Tests are SQLite-in-memory. They don't catch Postgres-on-EC2 column-drift bugs because SQLite happily creates the schema fresh each time.
- "816 total tests pass" in the PR body — but this is a false-comfort metric for a migration PR. Test count tells you nothing about migration safety in production.

**Mitigation going forward.** Any t2o2 PR touching `backend/archimedes/models/` should be flagged for a mandatory pre-merge check: *"Did this PR add an `ALTER TABLE … ADD COLUMN IF NOT EXISTS` to `db.py::init_db()` for every new column?"* If not, the PR is unsafe to merge against a non-empty Postgres. This is a Dan/Chuan review-gate item, not a CI one — CI can't easily check this without a Postgres fixture matching production.

#### PR #283 — T-PE.3 #160 Phase 2: cutover (`moonshot/160-phase2-cutover`)

**What landed.** 3 files, 90 LOC added, 0 removed. `strategy_provider.refresh()` now write-throughs to the unified table on every reload. `generation_pipeline._persist_candidate()` writes to **both** legacy + unified tables. New `GET /api/strategies/passports` endpoint.

**Solid.**
- Read path stays on the legacy `strategy_provider` file-based code. Cutover is write-through, not read-side switch. Safer.
- `try`/`except` around the sync prevents the unified-table write from crashing the provider.
- 28 new tests added (per PR body).

**Suspicious.**
1. **`force_update=True` on every refresh.** Every call rewrites all 6 curated strategies' rows + their paper_refs FK rows. If `refresh()` is called frequently (e.g. from a `/api/strategies` query path), this is a 6-row update + N paper_refs upsert on every request. The PR body claims "non-blocking" but the writes themselves are synchronous in the request path; only the failure mode is non-blocking. Could be the hidden cause of the `/api/vaults/` 8-second hang seen in the live-state snapshot above, if vault rendering touches strategy lookup.
2. **Per-strategy exceptions logged at `debug` level.** `_sync_to_unified_table` swallows `Exception` per-strategy and logs `logger.debug("passport sync failed for %s: %s", ...)`. **Debug is below the production log threshold.** A schema-shape mismatch on any one strategy would be silently dropped from the unified table with no observable error in production logs. This is the exact failure mode that masked the schema-drift bug for hours pre-#282.
3. **PR body is 4 sentences.** "Completes Issue #160. Write-through to unified table on every refresh() + generation. New /api/strategies/passports read endpoint. 844 tests pass." For a cutover that touches the central data path of the strategy library, this is insufficient documentation. There is no "what could go wrong" / "what to monitor post-deploy" / "rollback plan."

**Mitigation going forward.**
- Raise the swallowed exception log level to `warning` (or `error`) — a single-line PR.
- Add a `/api/health/passport-sync` endpoint that reports last successful sync timestamp + last error, so silent drops become observable.
- Investigate whether `refresh()` is called from a request path (suspected, given the 8-second `/api/vaults/` hang).

#### PR #279 — T-PE.7 #164 regime-aware portfolio (`moonshot/164-regime-portfolio`)

**What landed.** 3 files, 225 LOC added, 1 removed. New `regime_weight_schedule.py` service (134 LOC), wired into the advisor endpoint. 28 new tests.

**Solid.**
- Adds a service in isolation. No ORM changes. No new tables.
- Test coverage is proportional to LOC: 81 LOC of tests for 134 LOC of service.
- Mathematically grounded — implements the Ang-Bekaert 2002 regime-conditional γ scaling pattern that's already in the deck.

**Suspicious.**
- **Touches live trading rebalance flow (per the audit's held-spec list).** The PR body says "Strategies prioritized by regime tilt; API returns regime_breakdown with bull/bear/neutral weights" — but what *deploys* the new weights? If the agent runner's next tick reads from this new schedule and starts rebalancing vaults at new weights, that's a behavior change with on-chain consequences during the demo window. **Not verified in this critical review — recommend Dan or Chuan inspect `agent_runner.py` to confirm what gets consumed from this service in the live runtime.**
- PR body is 2 sentences — same insufficient-documentation pattern as #283. No "what changes for existing live vaults at the next tick" / "what to watch in production."

**Mitigation.** During the demo, watch the Reasoning page for the next rebalance trace. If it shows allocations different from the strategy passport's expected weights, the regime weight schedule fired. That's correct behavior, but a judge looking at a passport's "moderate / 50-50" allocation and seeing a "crisis / 80-20" deployed allocation needs the linkage explained.

#### #297 + #300 — t2o2's batch fix for SPEC-5/#288 (added 2026-05-25 17:00 UTC)

**What landed.** Two PRs across a 20-minute window:
- **PR #297** (`moonshot/batch-fixes-288-289-291`): claims to fix #288 vaults 8s + #289 health 404 + #291 reasoning sort. Touches 4 files, +35/-5 lines. The actual fix for #288 is a one-time `_synced_to_unified` flag in `strategy_provider.py:380` that guards `_sync_to_unified_table` so it runs once at process startup instead of every refresh.
- **PR #300** (`moonshot/fix-vaults-cache`): adds a 30-second TTL cache to the vault list endpoint. PR body says "Root cause: N+1 on-chain RPC reads per vault on every request."

**Solid.**
- #297's one-time-flag pattern is the right shape for the strategy_provider sync issue.
- #297 actually batches THREE filed SPECs in one PR — good throughput for the bot.
- nginx proxy timeout adds in #297 (`proxy_connect_timeout 5s`, `proxy_read_timeout 30s`) match the recommendation from SPEC-3.

**Suspicious.**
1. **PR #297's claim "Fixes 8s latency" is incorrect.** Live re-probe of `/api/vaults/` after the #297 deploy showed first-call still ~8 seconds. The actual fix had to come from #300's cache layer 20 minutes later. **t2o2 (or its PR-body generator) attributed the fix to the wrong root cause.** The actual root cause — N+1 on-chain RPC reads — was diagnosed correctly only in #300's PR body.
2. **#300's cache is a demo-quality fix, not production-grade.** The N+1 on-chain reads are still happening; the cache just hides them from frequent-call paths. After 30 seconds of idle (e.g. judge first lands on Portfolio), the cold call still takes ~8 seconds. **Per Dan's "real, high quality, production grade software" bar, this is tech debt.** The proper fix is a multicall (single RPC reads N vaults' state in one call) or event-driven state sync subscribing to `VaultFactory.VaultCreated` + `Vault.Deposit` events. Both are post-submission work; the cache is acceptable for the demo window.
3. **#297's PR body undersold the cache need.** A self-aware bot would have run the live verification, observed the post-#297 8s, and either flagged "fix incomplete" OR opened the cache PR immediately. Instead the cache came as a separate later PR (#300). Indicates the bot's verification gate is still test-suite-only, not live-state.

**Mitigation.** Comment on the merged #288 referencing the N+1 root cause as remaining tech debt; file post-submission. The fix-claim mismatch (#297 said it fixed 8s, didn't) is a recurring pattern worth surfacing in the next agent-pipeline retro.

#### #304 — t2o2's fix for #298 (paper_refs empty) + #299 (hardcoded name) (added 2026-05-25 17:30 UTC)

**What landed.** PR #304 — 1 file, +31/-10. Adds fuzzy paper-anchor matching + a defensive fallback ("include up to 5 library strategies as sources when agent returns no anchors") to `generation_pipeline._run_live_candidate`. Also derives strategy name from brief intent instead of hardcoded `"Moderate Agent Blend"`.

**Solid (the name fix worked).**
- Live `/api/strategies/generated` confirms #299 is fixed: names are now `"Moderate Blend — low drawdown trend following"` (templated, but derived from user intent).
- Thesis text is now derived from agent reasoning or a descriptive summary of the allocation — no longer hardcoded "Agent-constructed allocation".

**Acceptance-incomplete — #298 fix is NOT working in production (validated 17:30 UTC).**

Live `/api/strategies/generated?limit=2` returns strategies created at 16:23:21 UTC (post-#304 deploy) with `"source_papers": []` — the defensive fallback at `generation_pipeline.py:470-480` did not execute, or executed against an empty `strategies` list, or its result was dropped downstream before persistence. **Filed follow-up: #310.**

This is the **third instance today** of a t2o2 PR claiming acceptance + closing the issue without verifying live behavior:
1. **#297**: claimed "Fixes 8s latency" — the actual fix (cache) came in #300 twenty minutes later
2. **#297**: also closed #289 — but `/api/health/amm` still returns 404 (filed follow-up #309)
3. **#304**: closes #298 but `source_papers` still empty in production (filed follow-up #310)

**Pattern conclusion.** The `CLAUDE.md` § "Working with AI agents on this repo" pre-close verification gate (added 2026-05-24) explicitly says:

> Before closing *any* issue, the agentic system MUST:
> 1. Run every acceptance-criteria command listed in the issue and verify the exact expected output matches.

The bot is consistently NOT honoring this gate. PR bodies claim "848 tests pass" — but tests pass on SQLite in CI, not on live Postgres + actual deploys + actual user-facing endpoint contracts. **CI green is necessary, not sufficient.** Any t2o2-authored close action needs a Dan-or-Maestro live-verification follow-up before being trusted as final.

#### #308 — Dan-session: Fix /verify 504 with O(N) → O(1) (added 2026-05-25 18:00 UTC)

**Model for production-grade work.** Author: Dan-session (`worktree-agent-abcb017b333339ebf`). 3 files, +172/-12 — 110 lines of those are tests. Replaces an O(N) `getTracesByVault → getTraceById` scan in `verify_trace` that 504'd on vaults with 40+ traces, with a single `eth_getTransactionReceipt` call decoded via the existing `registry.events.TracePublished().process_log()` pattern.

**Why this PR is the model t2o2 should match:**

- **PR body has live evidence**: curl 504 reproduction from the live host
- **Names specific line numbers** in the diff
- **Names anti-goals explicitly**: "All other code paths preserved verbatim: the integer-id fallback for traces with no Redis entry, the 'not published on-chain' fallback, and the outer exception handler. The TraceVerifyResponse schema is untouched"
- **Tests verify the O(1) claim explicitly**: `assert_awaited_once_with(...)` proves single RPC roundtrip — the test enforces the performance contract, not just functional correctness
- **Three distinct outcomes** with clear `details` strings (receipt-missing / verified / mismatch) instead of a single boolean
- **Defensive backfill** of `vault` from on-chain when off-chain didn't record one
- **Proportional test coverage**: 110 lines tests for 42 lines production code
- **Acceptance checklist with runnable commands** at the bottom of the PR body

Per Dan's "real, high quality, production grade software" bar — this is exactly the shape. Merged at 17:50 UTC (commit `04602ed`).

**Caveat:** Live re-test of `/api/traces/{trace_id}/verify` at 18:00 UTC returned HTTP 000 (30s timeout). Possible causes: (a) #308's deploy still cycling, (b) #311 deploy stacked on top of #308 also cycling, (c) deploy.yml's hydrate-corpus timeout from earlier (#295 should have fixed that but the interaction with rapid back-to-back deploys is untested). **Backend `/api/health` returns 200 in 1.6s** and `/api/traces/?limit=1` returns 200 in 380ms — only `/verify` hangs. Needs re-verification once main settles.

#### #313 — Dan-session: Portfolio + Marketplace split (added 2026-05-25 19:00 UTC)

**Third model PR from the parallel Safari MCP session.** 312/89 across 5 files. Splits the conflated Portfolio surface (personal AUM + everyone's vaults) into two clean routes: `/portfolio` = strictly personal, `/marketplace` = public browse for every deployed vault.

**Solid.**
- **Live evidence in PR body**: "6 anonymous 'Vault 0xF3eA…d2c4'-style deploy-seed vaults (one with $10.11 AUM, five with $0.00) — exactly the noise that should never have been on the personal Portfolio surface."
- **Full state machine on Marketplace**: loading / error / no-vaults / no-matches / populated — each with honest copy. No fake data, no placeholder cards.
- **Honest empty state on Portfolio**: "You haven't deployed a vault yet" with both `Generate a Strategy` (primary) and `Browse Marketplace` (secondary) CTAs. No more wall-of-zeros.
- **Wallet-gate posture preserved correctly**: Portfolio stays inside `WalletGate` (per #292); Marketplace is explicitly public ("Anyone can browse" claim matches actual code — render case is outside `WalletGate`).
- **Grep-checkable acceptance criteria all pass**: `allVaults`/`loadAllVaults` purged from Portfolio.jsx; App.jsx has `marketplace:` path + `case 'marketplace'` render; Layout.jsx has Marketplace sidebar entry between Portfolio + Reasoning; uno.config.js safelists `i-lucide-store` icon.
- **No XSS surface**: all user data through JSX text nodes (React auto-escapes); no `dangerouslySetInnerHTML`; fetch URL is fully controlled (no user input).
- **Cancellation guards + cleanup**: `let cancelled = false` pattern + `clearInterval` cleanup on both Marketplace + Portfolio.
- **Tier filter chips only render when there's ≥1 matching vault** (per PR body claim — verified in code).

**One micro-nit (NOT blocking, filed mentally as cleanup):**
- In Marketplace.jsx the `return_24h >= 0` ternary evaluates `null >= 0` as `true` in JS, which would render a `null → '—'` value with the `positive` (green) class instead of color-neutral. Cosmetic only; data is still rendered honestly as `—`.

**Live verification post-merge:** `/marketplace` returns HTTP 200; `/api/vaults/` returns 200 with cold-cache 9.58s (validates the documented N+1 RPC tech debt from #300; subsequent calls warm to <500ms).

#### #312 — Dan-session: Explore page rebuild + STALE root-cause fix (added 2026-05-25 18:30 UTC)

**Another production-grade contrast PR.** 746/188 across 7 files; tests evolved honestly to match the new semantics.

**The honest STALE fix at the root:**
- Pre-#312: `is_stale` was inherited from the on-chain `PriceOracle` slot, regardless of whether the displayed price actually came from the oracle. Result: ~60 of 70 universe symbols showed "STALE" because nobody pushes `setPrice()` for their slots — even though the *displayed* price came from yfinance and was current.
- Post-#312: `is_stale` reflects the **displayed price's** freshness per its actual source. Three branches:
  - Oracle fresh (push within 5 min) → `price_source="oracle"`, `is_stale=False`
  - Yfinance fallback (last bar within 4 days = covers weekends + holidays) → `price_source="yfinance"`, `is_stale=False`
  - No source → `current_price=None`, `price_source="none"`, `is_stale=True`
- New `price_source` field discloses to UI which source produced the displayed price. **Judges can see "Source: yfinance (off-chain fallback)"** in the modal — honest framing, not hidden.

**Test evolution is the model:**
- Old test `test_no_oracle_marks_stale` would have failed against the new (correct) semantics. The PR **renames the test** to `test_no_oracle_falls_back_to_yfinance_not_stale` and flips its assertions to enforce the new contract.
- Test uses `datetime.now(UTC).date()` instead of hardcoded `2026-05-22` so the staleness check (4-day window) doesn't drift over time. **Production-grade test hygiene.**

**Dead-code purge is complete (verified):**
- `grep -nE "Use in Generate|onNavigate" Explore.jsx` returns clean
- `App.jsx` removed the `onNavigate={navigateToPage}` prop from `<Explore />`
- No dangling handler, dead JSX, or unused prop

**AssetModal.jsx (NEW, 388 LOC):**
- Honest empty states everywhere — explicit "No data" copy, never synthesizes a flat line
- Accessibility: `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, Escape-to-close, body-scroll lock
- React safety: `let cancelled = false` cleanup pattern in `useEffect` prevents setState-after-unmount
- 404 handled gracefully (empty series, not error)
- Hand-rolled SVG chart (matches `EfficientFrontier.jsx` idiom, no new chart dep — smaller bundle, fewer security surfaces)
- Per-source labels: "On-chain PriceOracle (Arc)" vs "yfinance (off-chain fallback)" vs "No source available"

**Route signature update:**
- `range: Literal["1D", "1W", "1M", "1Y"] = Query(...)` — FastAPI validates at the boundary
- 404 detail names the range parameter for diagnostics

**Net assessment.** Another model PR contrast against the t2o2 thin-body pattern. Together with #308, these two Dan-session PRs are exactly the production-quality bar Dan named.

#### #311 — t2o2's StockBench real-agent wiring (#301) (added 2026-05-25 18:00 UTC)

**What landed.** 1 file, +87/-21 to `evaluation/stockbench/adapter.py`. Replaces the prior simulated-momentum strategy in `generate_decision` with a weekly call to `PortfolioAgent.propose_portfolio` (every 5 trading days × 3 seeds = ~36 LLM calls total). Caches the agent's output between rebalance days. V_check still applied per decision. Momentum fallback if agent is unavailable.

**Solid.**
- **The prior code was actually fake** — `noise = self.rng.gauss(0, 0.01)` was supposedly "simulating fusion output" but was just random noise. #311 removes the fake and wires the real agent.
- **Scope-down is honestly framed** in the PR body: "Weekly rebalance cadence (every 5 trading days = ~12 decisions per 82-day eval)". This matches pi's relay via Dan: "Wiring the full LLM agent per-tick (246 LLM calls × 3 seeds) would burn ~2 hours of LLM budget and wall clock." The middle-path landed (weekly = 12 × 3 = 36 calls). Defensible.
- The `_REBALANCE_INTERVAL = 5` constant is named — not a magic number buried in the code.

**Suspicious.**
- **PR body is 5 sentences** — better than the worst t2o2 patterns (e.g. #283 was 4 sentences for a migration) but still doesn't surface what's removed (the fake noise) vs what's added.
- **No new tests** in the diff despite a fundamental change to the per-day decision logic. The existing `test_stockbench_adapter.py` covers the old fake-momentum path; whether it still passes against the new real-agent path is the "850 tests pass" claim — but this is exactly the kind of claim that should be live-verified, not test-suite-trusted (per the recurring t2o2 verification-gate pattern).
- **The actual benchmark output is unchanged** — the README's StockBench chart still shows Archimedes at #15/15 with Sortino -0.91. If the real agent now runs in the benchmark, the result should regenerate. Worth running once and updating the chart pre-submission.

**Net assessment.** Net positive: replaces fake with real, honest scope-down. Worth following up post-submission with: (a) a test that the agent path actually fires, (b) a fresh benchmark run with the real agent to update the StockBench chart with the new honest number.

#### #305 — t2o2's Phase 9 fusion mode toggle for #290 (added 2026-05-25 17:30 UTC)

**What landed.** PR #305 — 4 files, +37/-5. Adds mode toggle strip (`🤖 Agent` / `🧪 Fusion (novel)` / `🏗️ Architect`) to `Generate.jsx`; wires `mode` through `GenerateStartRequest` → `_pick_pipeline(mode_override=...)`.

**Solid.**
- Initial concern (PR body claims 3 lines of summary for a major spec ask) was wrong: `FusionResult.jsx` (191 LOC) was already created in earlier commit `5efe7c6` (the bot's prior Phase-9 pass). Generate.jsx already had `fusionJobId`, `fusionResult`, `fusionPollRef` state + polling loop pre-existing. **#305 only had to add the missing user-facing toggle on top of pre-existing infrastructure.** Appropriate scope.
- Live verification: `POST /api/generate/start` with `{"mode":"fusion"}` returns a `job_id` cleanly. Backend respects the mode override.
- No regression on Streaming agent / Architect modes (per code review).

**Suspicious.**
- The `strategic_direction` field from the spec body is not in the form (the unified form is shared across all modes). Minor gap; not a blocker for the demo.
- PR body could have called out that the bulk of fusion UI pre-existed in `5efe7c6` — to be transparent about what's actually new. (Documentation hygiene, not a correctness issue.)

#### Pattern across all three (the real finding)

t2o2-authored PR bodies are **systematically thin**. They tell you what the diff does mechanically (file counts, test pass count) but rarely tell you **what could go wrong in production**, **what to monitor after deploy**, or **what assumptions about the existing state could break**. Dan's PR #282 fix to PR #275's column-drift bug is the canonical example: t2o2 made the change correctly per its own test suite, but no one explicitly thought through "what does this do to a production DB that doesn't have these columns yet?" until Dan ran the live UI manually.

**Recommendation, codified for the next session.** Any t2o2 PR that touches *any* of: `backend/archimedes/models/`, `backend/archimedes/chain/`, `backend/archimedes/api/main.py`, `docker-compose*.yml`, `nginx/`, or `infra/` requires a Dan-or-Chuan explicit pre-merge sign-off, not just CI green. CI green is necessary, not sufficient. Add this to `CLAUDE.md` § "Working with AI agents on this repo" if it isn't already explicit there.

#### What the *non-t2o2* PRs in the same window got right (positive contrast)

- **PR #282 (Dan, schema-drift hotfix).** 18 lines added, 7 removed. **PR body has a Summary, root cause, fix, and test plan with checkboxes.** The root cause section names the specific PR + specific column. This is the documentation pattern t2o2 should be matching.
- **Issue #276 (Maestro-authored, t2o2-assigned) → PR #285 (t2o2 implementation).** Multi-paragraph background section. Names the exact file + line numbers. Specifies anti-goals. References the dead-code-audit-v2 doc as the source of truth. Closed a stub-file gap from this very inventory. **High-quality issue spec → high-quality bot output.**

#### #285 — t2o2's StrategyPublisher implementation: what it got right vs. residual nits

**Right (notable improvement over #283):**
- Used `asyncio.create_task` for fire-and-forget on-chain anchoring. **Handler latency is not impacted** by the anchor call.
- Exception handling at WARNING (not DEBUG). The outer `try/except` properly surfaces anchor failures in production logs. This is the lesson from #283's silent-debug-swallow problem, applied.
- Used the `trace_publisher` pattern from `agent_runner.py` as the precedent — exactly what the spec instructed.
- Added 4 new tests covering edge cases.

**Residual nits (filable as follow-up; not blockers):**
- `metadata_uri=""` is hardcoded. Real value would be e.g. `https://archimedes-arc.app/strategy/{id}` or an IPFS URL. The `StrategyRegistry` contract presumably has a use for this field; passing empty string makes it useless on-chain.
- `paper_hashes = [p.arxiv_id for p in passport.papers if p.arxiv_id]` — variable name says "hashes" but the value is arxiv IDs. The actual hashing happens inside `strategy_publisher.anchor()`. Rename to `paper_ids` for clarity.
- The inner `if passport is None: continue` skip is still at DEBUG level. A submitted-but-unknown strategy ID is a genuine signal that something's off (provider hasn't refreshed, or someone tampered with the request); should be WARNING or INFO.
- `logger = logging.getLogger(__name__)` is declared in the middle of the import block. PEP-8 style nit.

**Net assessment:** Notably higher quality than #283. The contrast suggests **issue-spec quality is the leading indicator of bot output quality.** When the spec was high-effort (#276, Maestro-authored, multi-paragraph, named files + anti-goals + precedents), the bot's output matched. When the spec was loose or implicit (#275/#283, mostly auto-generated from issue body + the bot's own interpretation), the bot's output had hidden production-risky patterns.

---

---

### t2o2-ready issue specs to file overnight

Each spec below is **paste-ready** into `gh issue create --assignee t2o2 --title "..." --body @-`. Each uses the proven template from [`docs/archive/launch-night-operational-runbook.md`](archive/launch-night-operational-runbook.md). Specs marked **[DAN-ONLY]** cannot be t2o2-routed (need a private key, manual judgment, or external account access) and are listed for completeness.

#### SPEC-1 [DAN-ONLY] — Run `verify_arc_e2e.py --execute` and commit the populated evidence

The single highest-leverage submission-day task. Converts "we have the code" into "we have arcscan-link proof." Cannot be t2o2-routed because it requires signing transactions from a wallet with private-key access.

```
TITLE: APIN - Verification - Execute Arc testnet smoke test from a real wallet and capture arcscan evidence

## TLDR
The Phase 5 e2e flow is code-complete (DepositFlow.jsx + verify_arc_e2e.py)
but docs/runbooks/arc-testnet-e2e-evidence.md is the empty template — the
flow has never been signed from a real wallet through to an on-chain trace.
This is THE artifact judges most plausibly inspect to confirm the on-chain
story is real. Goal: one signed execution → populated evidence runbook →
arcscan links verifiable from any browser.

## Prerequisites
1. P0 site-recovery (above) complete: https://archimedes-arc.app/api/health
   returns 200 with status="ok".
2. v_check post-#245 validation gate passes (at least one rebalance trace
   created after 2026-05-25 03:00 UTC).
3. Fresh dev wallet (`cast wallet new`) with NO mainnet funds.
4. DEV_WALLET_PRIVATE_KEY in local .env; the key is never committed.
5. ≥ 20 USDC on Arc testnet from https://faucet.circle.com (20 USDC / 2h refill).
6. RPC URL in local .env (from `arc-canteen login`).

## Steps
```bash
cd backend
python -m archimedes.scripts.verify_arc_e2e --dry-run    # passes prerequisites
python -m archimedes.scripts.verify_arc_e2e --execute    # populates evidence
git diff docs/runbooks/arc-testnet-e2e-evidence.md       # non-empty
git add docs/runbooks/arc-testnet-e2e-evidence.md
git commit -m "[verification] Capture Arc testnet e2e evidence (Issue #175 follow-on)"
git push -u origin dbrowneup/arc-e2e-evidence
gh pr create --title "[verification] Capture Arc testnet e2e evidence"
```

## Acceptance
- [ ] docs/runbooks/arc-testnet-e2e-evidence.md populated with: vault address,
      USDC approve tx, vault.deposit tx, vault.setTargetAllocations tx, agent
      rebalance tx hash, ReasoningTraceRegistry trace anchor tx hash
- [ ] Every tx hash resolves to https://testnet.arcscan.app/tx/<hash> with
      `Success` status
- [ ] /api/traces/<trace_id>/verify returns is_verified=true for the
      anchored rebalance trace
- [ ] PR adds a link from README.md's "Live testnet deploy" section to the
      evidence runbook so judges can find it

## Anti-goals
- DO NOT commit any private key, seed phrase, or wallet credential. The
  evidence file contains only addresses + tx hashes (which are public).
- DO NOT skip any step's evidence capture — partial evidence is worse than no
  evidence because it can mislead about which step succeeded.
- DO NOT modify verify_arc_e2e.py itself in this PR. If a bug surfaces, file
  a separate fix issue and continue manually until it lands.

## Precedent
- Script + runbook pattern: backend/archimedes/scripts/verify_arc_e2e.py +
  docs/runbooks/arc-testnet-e2e.md
- Issue #175 (closed; this is its real-execution follow-on)
```

#### SPEC-2 [t2o2] — Phase 9: Fusion-mode UI on Generate page

The wedge — "novel synthesis from multiple papers, rigor-gated" — is backend-complete (`fusion_evaluator.py` shipped via #128 + #133) but invisible to users. The spec is already drafted at `docs/specs/phase8-9-landing-and-fusion-spec.md` § Phase 9; this is the t2o2 wrapper that fires it.

```
TITLE: APIN - Frontend - Phase 9 fusion-mode UI: third tag on Generate page exposing the fusion engine

## TLDR
The fusion path (path C — strategy_fusion.py + fusion_evaluator.py) is
backend-complete with rigor gate wiring (#128 + #133) but no UI ever invokes
it. Generate.jsx exposes only Streaming agent (path B) and Architect (path
A) modes. Adds a third "Fusion (novel)" tag plus a fusion-specific form +
job tracker + FusionResult panel. Spec lives at
docs/specs/phase8-9-landing-and-fusion-spec.md § Phase 9 — execute that
spec verbatim.

## Maestro pre-execution corrections (audit 2026-05-25)
On origin/main @ c0c2d21:
- Confirmed: backend/archimedes/api/strategies_routes.py exposes
  POST /api/strategies/generate accepting mode=fusion (verified via grep).
- Confirmed: ui/src/components/Generate.jsx currently has two modes only
  (Streaming agent + Architect). No fusion branch.
- Frontend-only work. Backend endpoint already exists; do NOT modify it.

## Scope
Files to ADD:
- ui/src/components/FusionResult.jsx

Files to MODIFY:
- ui/src/components/Generate.jsx (add the third tag + fusion branch +
  polling lifecycle per the spec)

## Acceptance
- [ ] Opening /generate on the live HTTPS URL shows THREE tags in the mode
      strip: Streaming agent · Architect (fast preview) · Fusion (novel)
- [ ] Clicking Fusion (novel) renders a form with: asset classes,
      risk_appetite, strategic_direction, max_papers
- [ ] Clicking "Fuse →" issues POST /api/strategies/generate?mode=fusion
      and receives a job_id (verified via browser DevTools Network tab)
- [ ] Polling GET /api/strategies/generate/{job_id} every 2s renders
      Status: queued → running → done
- [ ] On done with strategy_spec present: FusionResult renders Sharpe + CAGR
      + Max DD + equity-curve sparkline + green rigor pill
- [ ] On done with strategy_spec absent (LLM didn't comply): renders the
      prose fields with "pre-backtest hypothesis" honest framing
- [ ] No regression on Streaming agent + Architect modes (manual click-through)
- [ ] `cd ui && npm run lint` exits 0

## Verify
```bash
cd ui && npm run build && npm run lint
# Open https://archimedes-arc.app/generate in a browser
# Click Fusion (novel); fill form; submit; watch polling
```

## Anti-goals
- DO NOT change backend/archimedes/api/strategies_routes.py or any
  backend/archimedes/services/strategy_fusion*.py file
- DO NOT add a fourth mode or rename the existing two
- DO NOT implement the Deploy-as-vault flow (that's Phase 4 territory;
  render a disabled "Deploy as a vault — coming in Phase 4" button)

## Precedent
- Form + job-poll loop: ui/src/components/GenerationStream.jsx
- Rigor verdict pill: ui/src/components/Strategies.jsx row-expansion's
  Rigor metrics block
- Spec body: docs/specs/phase8-9-landing-and-fusion-spec.md § Phase 9
```

#### SPEC-3 [t2o2] — Restore `/api/*` routing through nginx on live HTTPS

**Only fire AFTER Dan/Chuan have completed Phase 0's manual recovery and the symptom persists.** If the immediate fix from § "P0 recovery procedure" works, this spec is unnecessary.

```
TITLE: APIN - Infra - /api/* routing on live HTTPS hangs; nginx upstream needs verification

## TLDR
Confirmed 2026-05-25 06:13 UTC: TCP connects to ports 80/443 succeed but
HTTP requests to https://archimedes-arc.app/api/health time out after 30s.
nginx is accepting connections but the backend response never returns.
After manual recovery (P0 procedure in the runbook), this PR adds defensive
nginx config + a health-check route that surfaces upstream state.

## Maestro pre-execution corrections (audit 2026-05-25)
On origin/main @ c0c2d21:
- nginx/nginx.conf exists; uses `proxy_pass http://backend:8000` per the
  docker-compose service name
- backend/archimedes/api/agent_routes.py mentions "health/amm" but no
  dedicated health_routes.py exists yet
- /api/health is registered in main.py FastAPI app directly (not in a
  routes file)

## Scope
Files to MODIFY:
- nginx/nginx.conf (add upstream health check + per-location timeouts)

Files to ADD:
- backend/archimedes/api/health_routes.py (a dedicated router consolidating
  /api/health + /api/health/amm + /api/health/agent + similar status
  endpoints with consistent response shape)

## Acceptance
- [ ] curl https://archimedes-arc.app/api/health → 200 with
      {"status":"ok","backend_alive":true, ...} within 2s
- [ ] curl https://archimedes-arc.app/api/health/amm → 200 with per-pool
      liquidity report within 2s (or 503 with explicit "pools not
      initialized" body if AMM is genuinely empty)
- [ ] nginx config has proxy_read_timeout 30s on /api/ locations so a
      hung backend fails fast instead of holding the connection open
- [ ] If backend is unreachable, /api/health returns 503 within 2s rather
      than hanging

## Verify
```bash
# After deploy
time curl -s https://archimedes-arc.app/api/health     # < 2s
time curl -s https://archimedes-arc.app/api/health/amm # < 2s
```

## Anti-goals
- DO NOT change docker-compose.yml (out of scope)
- DO NOT touch other route files (this is a consolidation pass)
- DO NOT remove existing /api/health behavior — only consolidate it

## Precedent
- Existing route pattern: backend/archimedes/api/chat_routes.py
- nginx pattern: nginx/nginx.conf existing /api/ block
```

#### SPEC-4 [t2o2] — Highlight a recent successful rebalance trace on the Reasoning page

After SPEC-1 lands and a real rebalance trace exists, the Reasoning page should default to surfacing it. Today 6,477 historical `skip` traces would crowd it out.

```
TITLE: APIN - Frontend - Reasoning page: default-sort to most-recent + "Latest verified" filter chip

## TLDR
Once SPEC-1 lands a real successful rebalance trace, the Reasoning page
needs to surface it as the demo highlight. Currently the page would render
6,477 historical skip traces (pre-#245) on top of any real rebalance
trace. This PR (a) default-sorts traces by created_at DESC, (b) adds a
"Latest verified" filter chip that filters to decision_type='rebalance'
AND is_verified=true via /api/traces/{id}/verify.

## Maestro pre-execution corrections (audit 2026-05-25)
- Confirmed: ui/src/components/Reasoning.jsx exists and renders traces
- /api/traces/?limit=N is the list endpoint; /api/traces/{id}/verify is
  the verification endpoint (per docs/specs/strategy-passport-spec.md)
- 6,477 skip traces dwarf any real rebalance traces (per
  docs/archive/sunday-night-handoff-2026-05-24.md)

## Scope
Files to MODIFY:
- ui/src/components/Reasoning.jsx — default sort + filter chip

## Acceptance
- [ ] Reasoning page loads with traces sorted most-recent first
- [ ] "Latest verified" filter chip is visible at the top
- [ ] Clicking it filters to traces where decision_type='rebalance' AND
      the verify endpoint returns is_verified=true
- [ ] Empty state copy: "No verified rebalance traces yet — try again after
      the next agent tick" (honest, not silent empty)

## Anti-goals
- DO NOT modify /api/traces endpoint
- DO NOT delete or hide skip traces (they're honest history)

## Precedent
- Filter chip pattern: ui/src/components/Strategies.jsx faceted filters
```

---

#### SPEC-5 [t2o2] — Investigate `/api/vaults/` 8-second hang (post-#283)

Refreshed live-state caught this hang right after PR #283 merged. Vaults are central to the demo (judge clicks Portfolio → expects to see vaults instantly). Hypothesis: `strategy_provider.refresh()` is being called inside the vault-list request handler, and after #283 it now does a synchronous 6-row + paper-refs upsert to the unified table on every call.

```
TITLE: APIN - Backend - /api/vaults/ hangs 8s on live HTTPS; likely strategy_provider.refresh() write-through is on the request path

## TLDR
Live state at 2026-05-25 13:40 UTC: GET https://archimedes-arc.app/api/vaults/
times out at 8 seconds (HTTP 000), while sibling endpoints (/api/strategies,
/api/regime, /api/traces) all return 200 in <500ms. The regression appeared
shortly after PR #283 (T-PE.3 #160 Phase 2 cutover) merged at 13:30 UTC.

## Hypothesis (from critical review of #283)
strategy_provider.refresh() now write-throughs all 6 curated strategies +
their paper_refs FK rows to the unified strategy_passports table on every
reload (force_update=True). If refresh() is being called inside the vault
listing handler (vaults reference strategy IDs), each /api/vaults/ request
now pays the full write-through cost synchronously.

## Maestro pre-execution corrections (audit 2026-05-25 14:00 UTC)
- On origin/main @ 97c9099:
  - backend/archimedes/api/vaults_routes.py exists. Likely callers of
    strategy_provider inside the list handler.
  - strategy_provider.refresh() at services/strategy_provider.py:378 was
    extended by PR #283 commit 8a2c64d to call _sync_to_unified_table.
  - _sync_to_unified_table uses force_update=True per strategy.

## Scope
Files to MODIFY (in priority order, smallest fix first):
- backend/archimedes/api/vaults_routes.py — confirm the vault-list handler
  doesn't trigger strategy_provider.refresh() per request. If it does,
  switch to a cached read or move refresh() to a background task.
- backend/archimedes/services/strategy_provider.py — make _sync_to_unified_table
  conditional on actual file mtime change (skip if no curated file has
  changed since last sync), OR move the sync to a periodic background task
  triggered separately from refresh().

## Acceptance
- [ ] curl -w "%{time_total}" https://archimedes-arc.app/api/vaults/ → < 2s
      consistently (sample 5 times)
- [ ] No regression on /api/strategies/, /api/regime/, /api/traces/ latency
- [ ] Vault list still renders correctly on the live UI

## Verify
```bash
for i in 1 2 3 4 5; do
  time curl -s https://archimedes-arc.app/api/vaults/ > /dev/null
done
```

## Anti-goals
- DO NOT revert PR #283. The unified table cutover should stand; the bug is
  in how the sync hooks into the request path.
- DO NOT change the unified table schema.
- DO NOT silently swallow exceptions further. If the sync fails, log at
  WARNING or ERROR, not DEBUG (this is the second-order fix for the silent-
  drop issue called out in the critical review).
- DO NOT touch /api/vaults/{addr} (the per-vault detail handler) without
  proving it's affected — focus on the list handler.

## Precedent
- Background-task pattern: backend/archimedes/chain/oracle_runner.py
  (separate process loop, not in-request)
- Cached-read pattern: backend/archimedes/services/asset_market_service.py
  (per-symbol yfinance cache)
```

---

### SPEC-1 runbook for Dan to execute (added 2026-05-25 17:00 UTC)

**Security note up front.** Maestro **cannot** accept a private key — not in chat, not in context, not in commits. The script needs the key in a local `.env` only Dan controls. **The wallet MUST be a fresh dev wallet that has never touched mainnet.** Do NOT export a key from a wallet holding real assets.

#### Step 1 — Generate a fresh dev wallet (or use one you already keep clean)

```bash
cd /Users/dbrowne/Desktop/Programming/GitHub/Agora/archimedes
cast wallet new
# Output:
#   Successfully created new keypair.
#   Address:     0xABCD…
#   Private key: 0x1234…
# IMPORTANT: copy the PRIVATE KEY into your local .env in the next step.
# Do NOT paste it into chat, git, or anywhere else.
```

#### Step 2 — Fund the wallet from the Circle faucet

1. Open <https://faucet.circle.com> in your browser
2. Paste the **address** (not the key) from Step 1
3. Select **USDC** + **Arc Testnet**, solve the captcha
4. Claim 20 USDC. Refills every 2 hours if you need more.

#### Step 3 — Set the env (in your local `.env`, which is gitignored)

```
DEV_WALLET_ADDRESS=0xABCD…          # from Step 1
DEV_WALLET_PRIVATE_KEY=0x1234…      # from Step 1 — never commit this
API_BASE=https://archimedes-arc.app
```

Also confirm `RPC` is already set (from `arc-canteen login` per the earlier setup); if missing, source `~/.arc-canteen/env` or paste your `swrm_*` proxy URL.

#### Step 4 — Dry-run (no signing, just prereq verification)

```bash
conda activate archimedes
cd backend
python -m archimedes.scripts.verify_arc_e2e --dry-run
```

Expected output: a ✅ checklist confirming RPC reachable + wallet has gas + USDC balance ≥ 10 + VaultFactory deployed + ReasoningTraceRegistry deployed. If anything is ❌, fix it before --execute.

#### Step 5 — Execute the real run

```bash
python -m archimedes.scripts.verify_arc_e2e --execute
```

This takes 5-10 minutes. The slow step is waiting for the agent's first rebalance tick on the new vault (poll loop up to 5 minutes). The script writes `docs/runbooks/arc-testnet-e2e-evidence.md` automatically.

#### Step 6 — Hand the output to Maestro to commit

When the run completes, paste the script's stdout (and any error) into chat. I'll:
1. Verify the evidence file populated correctly (vault address, deposit tx, setTargetAllocations tx, rebalance tx, trace anchor tx, all with `https://testnet.arcscan.app/tx/...` links)
2. Open a separate branch + PR for the evidence file
3. Add a "Live testnet evidence" link from README → that file
4. Update this audit doc

**Do NOT use the `--wallet <private_key>` CLI flag.** It puts the key in your shell history. The env var is safer.

**If the script fails at any step:** paste the failure output. I'll diagnose + file a precise t2o2 fix-spec rather than re-attempting blind.

---

### Submission-day Dan-task checklist (manual, not t2o2)

A tight Monday checklist. Do in order; the early items unblock the later ones. **Order shifted at the 13:40 UTC refresh** to put `/api/vaults/` first since that's the active demo-blocker.

- [ ] **00. Live-site stability confirmed.** UI loads at `https://archimedes-arc.app/`. Backend `/api/*` mostly responds (200 in <500ms). **`/api/vaults/` hangs 8s — fix via SPEC-5 before any other demo work.** Run § "P0 recovery procedure" only if HTTPS root itself goes back down.
- [ ] **00a. v_check post-#245 validation gate passes.** At least one `decision_type='rebalance'` trace exists with `created_at >= 2026-05-25 03:00 UTC`. Confirms the v_check fix is running in prod.
- [ ] **01. File SPEC-5 (`/api/vaults/` hang) for t2o2** — the cheapest fix is likely Dan-or-Chuan in 10 minutes; if not, t2o2 spec is paste-ready above. Watch for the silent-debug-swallow issue called out in § "Critical review" while you're in `strategy_provider.py`.
- [ ] **02. Run SPEC-1.** Generate fresh dev wallet; fund from faucet; run `verify_arc_e2e --execute`; commit populated evidence runbook. **GATED ON: 00 + 01 passing first** (the script needs a working `/api/vaults/` to verify the deployed vault's state).
- [ ] **03. arc-canteen telemetry backfill.** Log ~10 traction events from the past week (judge / external user conversations). Run `arc-canteen update-traction "<who> <what they thought>"` for each.
- [ ] **04. Demo video v2.** Marten owns; v1 already shipped per launch-plan archive. Coordinate via Discord.
- [ ] **05. ARC-OSS Google Form resubmit.** Final answers from `ARC-OSS-FORM-DRAFT.md` (note: 12 primitives, 806 backend tests, HTTPS URL, v_check evidence runbook link from SPEC-1).
- [ ] **06. Main hackathon submission form.** Same content; same submission.
- [ ] **07. Final repo polish.** Cold-clone read of README → SETUP → ARC.md → ARC-OSS-SHOWCASE in a fresh browser tab; click every external link; verify nothing 404s.
- [ ] **08. Final demo dress rehearsal.** Manually traverse: Generate (Fusion if SPEC-2 lands; Streaming agent otherwise) → Deploy modal → DepositFlow → Portfolio (verify SPEC-5 fix in place) → Reasoning → "Verify on-chain" → arcscan tab. Time it: should fit in the 90s demo slot.

---

### Held / deferred t2o2 specs assigned overnight — watch for blast radius (refreshed)

Five held-UNASSIGNED specs were assigned to `t2o2` overnight (per launch-night operational runbook). **Three have shipped** since the first authoring of this plan; status updated below.

| Issue | Spec | Status as of refresh | Risk | Watch for |
|---|---|---|---|---|
| **#275** (T-PE.3 #160 Phase 1) | Unified `strategy_passports` Postgres table + `passport_loader` + migration | ✅ **MERGED 06:00 UTC** — **schema drift caught by Dan's #282 hotfix** | (Resolved) | See § "Critical review of t2o2 recent work" above for the underlying pattern. |
| **#283** (T-PE.3 #160 Phase 2) | Cutover: write-through unified table on every refresh + generation | ✅ **MERGED 13:30 UTC** — **possibly causing `/api/vaults/` 8s hang in live state** | **MEDIUM** | Inspect whether `refresh()` is called from a request path. Verify silent-debug-swallowed exceptions are not hiding production data drops. See SPEC-5 below. |
| **#164** (T-PE.7) → PR #279 | Portfolio Construction Agent reads regime + applies bull/bear weight schedule | ✅ **MERGED 12:40 UTC** | **MEDIUM** | If next agent tick deploys new weights to an existing vault, judges may see unexpected allocations. Verify by watching the next rebalance trace on `/reasoning`. |
| **#163** (T-PE.6) | Strategy Generation Agent emits BOTH a bull-tilted AND a bear-tilted candidate per Generate call | OPEN, t2o2-assigned, **not started** | **MEDIUM** — 2× LLM cost per Generate; activates `agents/base.py` Protocol. | Watch GLM token spend if it ships. Suggest a feature flag `ARCHIMEDES_BULL_BEAR_GENERATION=true` defaulting to false. |
| **#155** (T3.6) | AWS ALB + CloudFront + ASG: virality-ready backend tier | OPEN, t2o2-assigned, **not started** | **HIGH** — replaces production routing. | If a PR opens with title containing "ALB" or "CloudFront" or "ASG", **flag DO-NOT-MERGE without Dan-or-Chuan eyes**. Comment on the PR before reviewing cold. |
| **#154** (T3.5) | OPTIONAL Bedrock as primary LLM with IAM auth | Still UNASSIGNED | LOW — post-hackathon. Keep held. | n/a |
| **#276** → PR #285 | StrategyPublisher → `POST /api/vaults/metadata` (anchors passports on Arc) | ✅ **MERGED 14:50 UTC** | (Resolved) | **Successfully closed Gap #5 from § "What to brief judges on if they ask what's not built."** `chain/strategy_publisher.py` is now runtime-imported by `vaults_routes.py`. See § "Critical review" — t2o2's implementation here was notably higher quality than #283; the contrast suggests issue-spec quality is the leading indicator of bot output quality. |

**Action:** Dan or Chuan eyes-on for any new t2o2 PR against `models/`, `chain/`, `api/main.py`, `docker-compose*.yml`, `nginx/`, or `infra/`. CI green is necessary, not sufficient. See § "Critical review of t2o2 recent work" for the pattern.

---

### Stay-off branches (other-session work) — refreshed

All branches/PRs merged in this refresh window (no action — track for situational awareness):

- #270 (`dbrowneup/csp-allow-circle-modular-sdk`)
- #263 (`dbrowneup/landing-compression-honesty`)
- #262 (`dbrowneup/onboarding-tour-rework`)
- #273 (`dbrowneup/m4-content-refresh`) — Dan's M.4 doc refresh
- #274 (`dbrowneup/dead-code-audit-v2-wt`) — the file this plan appends to
- #275 (`moonshot/160-unified-passport-store`) — schema-drift caught by #282 hotfix
- #277 (`moonshot/fix-generate-500`) — hotfix to a missing `Response` param for slowapi
- #278 (`dbrowneup/fix-passkey-duplicate-username`) — Safari passkey signin auto-fallback
- #279 (`moonshot/164-regime-portfolio`) — see watch-list
- #280 (`dbrowneup/fix-generate-agentpick-attrs`) — Generate 500 fix (AgentPick missing `.symbol`/`.strategy_id`)
- #281 (`dbrowneup/wallet-circle-polish`) — wallet UX: Circle SDK surface, copy button, USDC balance
- #282 (`dbrowneup/fix-strategy-store-migration`) — **Dan's schema-drift hotfix**
- #283 (`moonshot/160-phase2-cutover`) — see watch-list (and § "Critical review")
- #284 (`moonshot/fix-corpus-ui-rendering`) — Corpus Graph + KG tabs not rendering data
- #285 (`moonshot/276-strategy-publisher-wiring`) — see watch-list (and § "Critical review" for positive-contrast note)
- #286 (`dbrowneup/junk-hunt-bundle-1`) — **Junk-hunt bundle 1**: Corpus Overview categories/years (frontend/backend response-shape fix) + Library efficient-frontier n<3 cap fix. Authored by the parallel Safari MCP session. **Merged 14:00 UTC**. Cleans up a stray F401 that PR #285 left behind in `test_vault_metadata_anchor.py` (positive cross-session signal — the human-driven work is also cleaning up bot-introduced debris).
- #292 (`dbrowneup/wallet-gate-and-catalog-honesty`) — **Junk-hunt bundle 2**: (a) wallet-gate Library/Portfolio/Learnings behind a `<WalletGate>` CTA component so logged-out browsers don't see implied-personalization ("Your AUM $0.00", "Generated (1)" misattributed to a stranger's strategy); (b) Corpus catalog honesty fix — `/api/corpus/overview` now reports the **processed** paper count (~1014, BERTopic-clustered) as `total_papers`, with `metadata_only_papers` available separately for the 10K-row superset. Catalog tab now lists only the processed subset. **Merged 14:36 UTC** by the parallel Safari MCP session.
- #295 (`dbrowneup/fix-deploy-hydrate-timeout`) — **Critical infra fix.** Every deploy since 13:37 UTC reported `cancelled` in GitHub Actions because `hydrate_corpus.py` was iterating 10K papers against a read-only PDF volume and exhausting the SSH job's `command_timeout`. Code WAS landing (containers restart before hydrate), but workflow status was red on every merge. Fix: `-d` flag to background hydrate + pre-flight `_is_writable()` probe to skip read-only volumes. Plus CorpusExplorer + CorpusKG empty-state fixes. **Merged 16:00 UTC.** Restores the deploy-completion signal.
- #296 (`dbrowneup/passport-detail-endpoint`) — **Live judge-blocker fix.** Generated strategies (from fusion/architect) land in `strategy_passports` but `GET /api/strategies/{id}` only knew about `LocalStrategyProvider`. Clicking any generated strategy in `/library` → 404 ("Could not load strategy"). Fix: `_passport_to_strategy_response` helper + fallback in `get_strategy()` to passport store. **Merged 16:03 UTC.**
- #297 (`moonshot/batch-fixes-288-289-291`) — t2o2's batch fix for #288 + #289 + #291. **See § "Critical review of #297 + #300" above for the root-cause-attribution mismatch finding.**
- #300 (`moonshot/fix-vaults-cache`) — t2o2's 30s vault-list cache. Same critical-review section.
- #302 (`dbrowneup/fix-vault-marketplace-fake-data`) — **Junk-hunt bundle 3** by the parallel Safari MCP session. Killed the "Vault T1 / identical name / batch-of-seconds created_at" pattern on /api/vaults/. Real bug (live evidence pre-fix: 6 vaults all named "Vault T1" with stamped-at-request-time timestamps). Quality work: PR body has live evidence + named root cause + tests + honest fallbacks for vaults-deployed-outside-UI. Merged 17:13 UTC. **High quality contrast point** vs the t2o2 pattern of thin PR bodies.
- #304 (`moonshot/298-299-fusion-quality`) — t2o2's fix for fusion strategy quality. **See § "Critical review of #304" above** — #299 (name) shipped fine; #298 (paper_refs) marked closed but acceptance NOT met in production (filed #310).
- #305 (`moonshot/290-fusion-ui`) — t2o2's Phase 9 mode toggle. **See § "Critical review of #305"** — appropriate scope (FusionResult.jsx + polling pre-existed in commit `5efe7c6`).
- #308 (`worktree-agent-abcb017b333339ebf`) — **Dan-session: Fix verify-on-chain 504 with O(N)→O(1).** See § "Critical review of #308" above — **model for production-grade work.** Merged 17:50 UTC.
- #311 (`moonshot/301-stockbench-real-agent`) — t2o2's StockBench wiring. See § "Critical review of #311" — net positive (replaces fake noise with real agent), but no new tests and scope-down should be re-benchmarked.
- #312 (`dbrowneup/explore-rebuild`) — **Dan-session: Explore rebuild + STALE root-cause fix.** See § "Critical review of #312" above — **another model PR.** Merged 18:30 UTC.
- #313 (`dbrowneup/portfolio-marketplace-split`) — **Dan-session: Portfolio + Marketplace surface split.** See § "Critical review of #313" — **third model PR**. Merged 19:00 UTC.

Önder's branches (#235 source-tracker, #239 stockbench-consolidation) — both closed earlier.

**Currently no open PRs except #287 (this audit).** With the recent merges, the active t2o2 queue is now #155 + #163 + #290 + #293 + #303 (newly filed). Per § "Critical review" — review each before merge; CI green is necessary, not sufficient.

**The parallel Safari MCP session** had been live on `dbrowneup/junk-hunt-bundle-1` with WIP edits to `corpus_routes.py` + `portfolio_optimizer.py` + `CorpusGraph.jsx`. With #286 + #292 + #295 + #296 all merged, that session's queued backlog has fully landed.

Önder's branches (#235 source-tracker, #239 stockbench-consolidation) — both closed earlier.

**Active t2o2 queue (refreshed 17:30 UTC after rigorous validation pass):**

| Issue | Status | Validation result |
|---|---|---|
| **#288** vaults 8s hang | ✅ Closed via #297 + #300 | ⚠️ Caveat: #297 misattributed cause; #300 cache hides N+1 root cause (cold-cache 1st call still 8s). Demo-acceptable, not production-grade. Doc'd above. |
| **#289** /api/health + /api/health/amm | ⚠️ **PARTIALLY closed.** /api/health ✅, /api/health/amm ❌ still 404 | **Filed follow-up #309.** |
| **#290** Phase 9 fusion UI mode toggle | ✅ Closed via #305 | Live POST /api/generate/start with mode=fusion returns a job_id. Working. |
| **#291** Reasoning sort + filter chips | ✅ Closed via #297 | Need browser verification (CLI can't render React); but the diff modifies `Reasoning.jsx` correctly. |
| **#298** Empty paper_refs on fusion strategies | ⚠️ **Closed but acceptance NOT met live.** | Live strategies post-#304 deploy still have `source_papers: []`. **Filed follow-up #310.** |
| **#299** Hardcoded "Agent Blend" name | ✅ Closed via #304 | Live names now `"Moderate Blend — {intent}"`. Templated, but derived from brief. Acceptable. |
| **#293** REBEL+SciSpacy KG enrichment | 🟢 IN FLIGHT (pi running locally on MPS, ETA ~15-20 min as of 17:00 UTC) | Will yield real multi-predicate triples; #306 staged to flip "Topic Clusters" → "Knowledge Graph" label once verified |
| **#301** StockBench real agent wiring | 🟢 IN FLIGHT — pi descoped to one-shot agent decision (not per-tick) per advisor guidance | Original ask: 246 LLM calls × 3 seeds = ~2hr LLM budget. New scope: 3 LLM calls total. Honest framing match for baselines. |
| **#303** Bundled arcscan UX wiring | OPEN, t2o2-assigned | No moonshot/* branch yet — bot hasn't started. |
| **#309** /api/health/amm follow-up | NEW, OPEN, t2o2-assigned | Filed 17:30 UTC. |
| **#310** source_papers fallback follow-up | NEW, OPEN, t2o2-assigned | Filed 17:30 UTC. |
| **#163** Always-both bull+bear generation | OPEN | **Pi deferred** — single-candidate path just got fixed (#298/#299/#290), too fragile to risk on submission day. |
| **#155** ALB+CloudFront+ASG | OPEN | High-blast, post-submission. |
| **#154** OPTIONAL Bedrock | UNASSIGNED | Post-hackathon, stays unassigned. |

**The on-chain MVP gap — reframed at 17:00 UTC.** Earlier framing ("we don't have any real on-chain transactions yet") is **no longer accurate**: live `/api/traces/?limit=10` returns 4 real rebalance traces with real `arc_tx_hash` values. The agent IS making real on-chain rebalance transactions. What's still missing:

1. **A user-wallet-driven deposit → rebalance flow demonstrated end-to-end.** SPEC-1 [DAN-ONLY] is still the gate — `verify_arc_e2e.py --execute` must be run from a real wallet to produce committable evidence. **No one has attempted this yet (per Dan, 17:00 UTC). Now is the time.** See § "SPEC-1 runbook (for Dan to execute)" below.
2. **Arcscan-link UX surfacing.** #303 just filed. Three surfaces (Portfolio agent activity feed, Strategy Passport on-chain anchor row, Vault Detail recent-traces) all render the hash as monospace text without making it clickable. Backend already returns the data; pure 3-file frontend wiring. **Highest-leverage demo move that's still in front of us.**
3. **N+1 on-chain reads tech debt** (per § "Critical review of #297 + #300" above). Cache is fine for the demo; replace with multicall or event-driven state sync post-submission.

Per § "Critical review" — review each t2o2 PR before merge. CI green is necessary, not sufficient. The cross-session pattern (Maestro-authored specs in this audit + Safari-MCP-authored #293) continues to be: high-quality issue bodies → higher-quality bot output. **But the #297-attributing-the-wrong-root-cause pattern shows the bot still doesn't verify its own claims against live state.**

**The parallel Safari MCP session** had been live on `dbrowneup/junk-hunt-bundle-1` with WIP edits to `corpus_routes.py` + `portfolio_optimizer.py` + `CorpusGraph.jsx`. With #286 merged, that branch is shipped. (During the refresh authoring at 14:45 UTC I accidentally rebased their branch while my cwd had drifted out of my worktree — recovered cleanly via `git reset` + re-pop of their stash; zero data loss. The worked example reinforces that every `Bash` call must `cd $WORKTREE_PATH &&` explicitly when other agents are operating in the same repo.)

---

### Post-compact resume — three more PRs merged (18:00 UTC, T-6h to deadline)

Resumed after a context compact. Three PRs landed between compact and resume:

#### Critical review of #315 — pi's first model PR (MERGED at 17:48 UTC, `d3bfec3`)

**Verdict: production-grade. Pi met the same bar set by #308/#312/#313.** Dual bull/bear strategy generation. Backend pipeline runs twice per Generate (bull-tilted: momentum/trend/risk-on, bear-tilted: vol-managed/defensive/mean-reversion). Both candidates persisted with `regime_tag`. Frontend renders side-by-side color-bordered cards.

Live validation pass (triggered a real Generate at 17:54 UTC, `job_id=7f7411ca35a040cf`):

- ✅ SSE `pipeline_selected` emits `"regimes": ["bull", "bear"]`
- ✅ `candidates_selected` shows `candidate_count: 2`
- ✅ Bull candidate `f5a80b9497b4f133` → 🟢 prefix, 10 assets: BIL/TSM/NVDA/XOM/GLD/ASELS/USD/TRY/XLE/WHEAT/QQQ (momentum bias visible — NVDA/TSM/QQQ)
- ✅ Bear candidate `1ef3fdf0e3488bf0` → 🔴 prefix, 7 assets: BIL (biggest weight)/ASML/XOM/ASELS/NIKKEI/USD-TRY/WHEAT_FUT (defensive: no NVDA/QQQ, more inflation/currency hedges)
- ✅ Both got `trace_hashed` + `persisted` events with correct regime tags. `done` event carries `all_strategy_ids: {cand_bull: ..., cand_bear: ...}`
- ✅ Frontend diff (`git show d3bfec3 -- ui/`): `GenerationStream.jsx` adds `draftedCandidates` state, renders auto-fit grid of color-bordered cards (positive/negative borders, 🟢 Bull / 🔴 Bear pills, weight previews, per-card "View in Library" buttons). `Generate.jsx` removes auto-navigate-on-done with a clear comment. `RejectedCandidates.jsx` adds regime tag on each candidate.
- ✅ Regime-biased paper retrieval via `_REGIME_BIAS_TERMS` (momentum/trend for bull, vol-managed/defensive for bear) with 2x weight boost
- ✅ 6 new tests (859 total suite, all green per PR body)
- ✅ Cost docs added: `docs/cost-estimates/generate-llm-costs.md` (~$0.15/Generate, 2x previous)

**Small gap (worth filing post-submission, not blocking):** `regime_tag` is in the SQLAlchemy model + `api/schemas.py:216`, but doesn't appear in the JSON returned by `GET /api/strategies/generated` for the newly-persisted bull/bear pair (`regime_tag present: False` from a python `dict.keys()` check). The emoji prefix in `strategy_name` preserves the regime visually, and the SSE-time path renders fine, but a page reload would lose explicit regime metadata. Likely a JSON-serializer dropping null-defaulted fields, or the listing route not selecting the column. **Cosmetic surface gap, not a data integrity issue.**

**Self-correction worth recording:** I initially suspected #315's PR body misrepresented its diff (claimed it modified `GenerationStream.jsx` but my `git log --oneline -- ui/src/components/GenerationStream.jsx` showed no #315 commit). I was wrong — my worktree is on `dbrowneup/docs-final-day` which forked from main BEFORE #315 landed, so `git log <file>` only walks ancestors of my HEAD. **When inspecting another PR's changes from a behind-main feature branch, always use `git show <commit> -- <path>` explicitly, never `git log -- <path>`.** This is the second worktree-discipline lesson today.

#### Critical review of #316 — Reasoning page arcscan upfront (MERGED at 17:56 UTC, `427ec4f`)

**Verdict: production-grade. Clean dan-session work.** Single-file change to `ui/src/components/Reasoning.jsx` (+55/-49). Lifts the arcscan link + block badge out of the `{vResult && ...}` conditional block into a `t.arc_tx_hash`-gated row that renders on first paint. The Verify button now reframes as integrity-confirmation ("re-hash and compare to chain"), with a "Hash verified ✓" state after success. The "Why does this matter?" disclosure is now ungated. Honest fallback `"Not yet anchored on-chain"` when the trace has no `arc_tx_hash`. Pre-existing dep on `vResult` was wrong — the API already returns `arc_tx_hash` + `commit_block_number` in the listing response (verified live: `/api/traces/?limit=1` carries both). No dead code, no fake data, ESLint clean. CI all green. Merged.

#### Critical review of #314 — Explore extended price-history ranges (MERGED at 17:56 UTC, `e8fa782`)

**Verdict: production-grade.** Trivial, +9/-4 across 3 files. Extends `HistoryRange` literal in `explore_schemas.py` to include `5Y/10Y/MAX`; adds the three corresponding entries to `_HISTORY_RANGE_MAP` (5Y→1wk, 10Y→1wk, MAX→1mo — downsampling keeps the chart point-count legible across decades); extends `RANGES` in `AssetModal.jsx`. Honest fallback already in place from the original Explore rebuild (#312) — 404 + empty array → "Historical chart unavailable" UI. `pytest backend/tests/services/test_asset_market_service.py` 12/12, lint+build clean. Real demo win — judges clicking SPY in Explore now see multi-decade context. Merged.

#### Updated post-compact recent merges (since b18b915)

| PR | Title | Quality | Cause |
|---|---|---|---|
| #315 | Dual bull/bear strategy generation (Issue #163) | **Production-grade** — pi's first model PR | Real LLM differentiation, dual SSE events, side-by-side UI cards, 6 new tests |
| #316 | Reasoning arcscan upfront | **Production-grade** — clean dan-session | Right root-cause framing (API already had the data) |
| #314 | Explore 5Y/10Y/MAX ranges | **Production-grade** — clean dan-session | Trivial extension, honest fallbacks already in place |

Audit doc PR #287 rebased onto `e8fa782` cleanly.

#### #310 status — still failing live after #315

The two new bull/bear strategies persisted post-#315 (`f5a80b9497b4f133`, `1ef3fdf0e3488bf0`) both have `source_papers: []`. The #310 follow-up is still pending; pi has not picked it up. Not blocking — the regime story (bull vs bear) is the more visible demo wedge — but the paper-grounding claim depends on this fix landing eventually.

#### Active queue refresh (18:00 UTC)

| Issue | Status |
|---|---|
| **#163** Dual bull/bear generation | ✅ **MERGED via #315.** Production-grade. |
| **#293** REBEL+SciSpacy KG | 🟢 STILL IN FLIGHT — awaiting pi status |
| **#301** StockBench real agent | 🟢 STILL IN FLIGHT |
| **#303** Bundled arcscan UX wiring | OPEN, t2o2-assigned; still no moonshot/* branch |
| **#309** /api/health/amm follow-up | OPEN, t2o2-assigned |
| **#310** source_papers fallback follow-up | OPEN, t2o2-assigned; **still failing live as of 18:00 UTC** |
| **#306** DRAFT KG label restore | Gated on #293 producing artifact |
| **#155** ALB+CloudFront+ASG | Held (post-submission) |
| **#154** OPTIONAL Bedrock | Unassigned (post-hackathon) |

**The on-chain MVP gap — refreshed 18:00 UTC.** Still anchored on SPEC-1 [DAN-ONLY] for the user-wallet-driven evidence run + #303 arcscan-link UX wiring. The rest of the rigor wedge (#293 REBEL) is gravy.

---

### The 18:00–19:00 UTC wave (and the #324 incident)

A dense one-hour window where pi shipped all three of my filed follow-ups + a new spec, dan-session shipped three more UI improvements, and one bot PR briefly broke live rebalancing. Worth recording in detail because it's the most informative slice of the day for the "what to watch when bot work moves this fast" question.

#### #317 (#303 arcscan UX wiring) — MERGED at 18:05 UTC, `2797f1d`

**Verdict: production-grade.** Clean +33/-3 across the exact 3 frontend files my spec listed (`Portfolio.jsx:281`, `StrategyPassport.jsx:269`, `VaultDetail.jsx:332`). Lifts `t.arc_tx_hash` / `s.on_chain_registration_tx` from monospace span/code/div into proper `<a target="_blank" rel="noopener noreferrer">` arcscan links. Portfolio.jsx uses `e.stopPropagation()` so the link doesn't trigger the parent trace-card-click handler. Honest fallback to monospace text when no `arc_tx_hash` exists. Pattern reuses the existing `Reasoning.jsx` implementation cleanly. **One of the highest-demo-leverage wires we filed today — a judge clicking through Portfolio / Strategy passports / Vault Detail now lands on real arcscan tx pages.**

#### #319 (#310 source_papers fallback) — MERGED at 18:05 UTC, `00d63ec` — **LIVE-VERIFIED WORKING**

**Verdict: production-grade fix to the right root cause.** PR body identifies the exact bug: `paper_arxiv_id` is `''` (empty string, falsy in Python) for curated strategies that predate arxiv (Faber 2007, Moreira-Muir 2017, TSMOM 2012 are DOI-anchored, not arxiv-anchored). The previous fallback's `if getattr(s, 'paper_arxiv_id', None):` check was silently skipping all curated strategies. New fix uses `title OR arxiv_id` for the truthy check + populates both fields.

Live verified at 18:11 UTC by triggering a fresh Generate (`job_id=7ae347bf6ab44d8b`): both bull (`acab9d25282e9d10`) and bear (`7f71752aa98dd33a`) candidates persisted with **`source_papers` length 5** including:
- *"Capital Preservation: T-Bill Proxy / USYC Allocation"*
- *"A Quantitative Approach to Tactical Asset Allocation"* (Faber 2007)
- *"The 52-Week High and Momentum Investing"* (Moskowitz / Ooi / Pedersen)
- (two more truncated)

**This is the biggest pitch-credibility unlock today.** The "every strategy is paper-grounded" claim now actually holds end-to-end on a freshly-generated strategy — not just a curated library entry. Closes a gap that had been quietly hollowing out the demo's strongest claim.

#### #318 (#309 /api/health/amm endpoint) — MERGED at 18:05 UTC, `34e8aaa` — **shipped with a runtime bug**

The endpoint was added (no longer 404, so the literal #309 acceptance is met). But live curl returned HTTP 503 with body `{"status":"amm_health_check_failed","reason":"'AsyncContract' object is not callable"}`. The error message is the classic web3.py mistake — calling `loader.amm_router()` (treating a `@property` as a method) instead of `loader.amm_router` (property access). Every other caller in the repo gets it right (`bootstrap_vaults.py`, `agent_routes.py`, `swap_routes.py`, `amm_bootstrap.py`, `config_service.py`); pi's #318 was the only buggy one.

**Filed follow-up #325** with the pinpointed one-character fix + reference precedents. Pi shipped #325 fix at 18:43 UTC (commit `a8484e1`) — live verified at 19:00 UTC: `/api/health/amm` returns HTTP 200 with `pool_count: 5`. Secondary issue: each pool entry still has `"error": "failed to read pool state"` for the per-pool tokenA/B/reserveA/B reads — minor, doesn't block the endpoint contract.

#### #320, #322 — duplicate PRs of #319/#318 — CLOSED

Pi opened multiple branches against the same issues. Closed both with notes pointing at the merged primary PRs.

#### #321 (#307 per-vault scoping, first attempt) — Maestro flagged for rebase, pi reopened as #324

Pi's first per-vault scoping PR (#321) bundled THREE things:
1. The genuinely new `_get_vault_strategy_ids` in `agent_runner.py`
2. A duplicate `/health/amm` implementation conflicting with the just-merged #318
3. A duplicate source_papers fallback fix conflicting with the just-merged #319

Maestro comment on #321 surfaced the overlap + the fact that #321's `/health/amm` actually used the correct `.functions.foo(args).call()` pattern (precedent for the #325 fix). Pi closed #321 and reopened as #324 with the duplicates dropped — but in dropping them also changed the legacy-vault behavior, which led to the next incident.

#### 🚨 #324 (#307 per-vault scoping, rebased) — DEMO-BREAKING REGRESSION, MERGED at 18:18 UTC, hotfixed at 18:33 UTC

The most informative incident of the day for the "what to watch when bot work moves this fast" question.

**What landed (`b53052b`):** the tick loop's no-`VaultMetadata` branch changed from "fallback to global consensus" to:

```python
if vault_strategy_ids is None:
    logger.warning("...skipping rebalance. Deploy via UI to set strategies.")
    continue   # ← THIS LINE silently halted rebalancing on every legacy vault
```

**Why it broke prod:** `curl https://archimedes-arc.app/api/vaults/ | jq '.vaults[].strategy_ids'` returned six empty arrays. All 6 vaults on live were "legacy" (no `VaultMetadata` row). The new `is None → continue` branch silently skipped every one of them every tick.

**Live damage:** total trace count stuck at 35 through two missed tick windows (expected 18:21, 18:26 — both no-ops). The pitch's load-bearing claim "the agent IS making real on-chain rebalances" was untrue for ~17 minutes.

**Maestro race:** I filed an urgent comment on #324 explaining the regression. **Pi merged the PR ~3 minutes before my comment landed.** The merge race is real — for bot-driven PRs at this pace, comment-before-merge requires submitting feedback before the bot's tests finish, not waiting for them.

**Hotfix path (PR #327):**

1. Worktree-isolated edit (`git worktree add /tmp/archimedes-hotfix-324 -b dbrowneup/hotfix-324-legacy-vault-fallback origin/main`) to avoid disturbing my own #287 branch
2. Single-file edit: `continue` → fallback `await self._process_vault(vault_addr, targets, all_signals, regime, tick_id)` for the no-metadata branch, preserving #324's intent for vaults that DO have metadata
3. Three regression tests (`TestPerVaultScopingLegacyFallback`) codifying the contract `_get_vault_strategy_ids → None ≡ "use legacy fallback"`, NOT "skip" — would have caught the original #324 in CI
4. PR opened at 18:25 UTC with full live-evidence body; CI green by 18:33 UTC; squash-merged at 18:33:11 UTC (`0a068ac`); deploy cycled by ~18:36
5. **First post-hotfix trace at `2026-05-25T18:37:19 UTC`** — 4 minutes after merge. 21-minute total gap (18:16:28 → 18:37:19) is the visible damage.

#### #323 (dan-session error banner fix) — MERGED at 18:18 UTC, `cac6c32`

Single-file +9/-3 fix for the same 502 HTML response I hit on my first probe today. The Recent Generations panel was rendering raw nginx 502 HTML as text because `await res.text()` returned the upstream body verbatim. Fix: replace with `Backend returned ${res.status}`, length-cap any error string at 120 chars as defense-in-depth, clear on next successful poll. **Production-grade.**

#### #325 — MERGED at 18:43 UTC, `a8484e1`

Pi's surgical fix for the AsyncContract bug from #318. Confirmed working: `/api/health/amm` returns HTTP 200 with 5 pools listed. Per-pool detail reads still error (`failed to read pool state`) — flagged as secondary follow-up but not blocking.

#### #326 (Generate page consolidation) — MERGED at 18:58 UTC, `8d6b48a`

dan-session UX cleanup: drops the 3-tab Agent/Fusion/Architect mode picker that #290 reintroduced (which defeated the locked auto-router design). Adds a collapsible "How this works · tips · examples" panel with 4 hand-picked example briefs that route cleanly through the auto-router. Anti-goals respected (no backend changes; `mode` still accepted at API level). **Production-grade.**

#### #328 (Portfolio account header) — MERGED at 18:58 UTC, `d835e1c`

dan-session feature add: replaces the 2-tile (Your AUM + Agent) status strip with a 4-tile account header (Wallet USDC | Vault AUM | Unrealized PnL | Agent). PnL math is honest: `pps = totalAssets / totalSupply; userValue = balanceOf × pps; pnl = userValue - shares`. Tooltip spells out the approximation. Honest `—` everywhere uncomputable. Per-vault PnL chip in card with PPS shown in title attribute. Reuses existing 30s poll. **Production-grade.**

#### #329 (`/architecture` informational page) — OPEN, all CI green, one number-reconciliation flag

dan-session +379/-0 across 3 files, adds `/architecture` route between Generate and Library. Pure HTML+CSS infographic (no mermaid, no react-flow, no new deps). 9 sections: PageHeader / HeroStrip / PipelineFlow / AgentCards / MemoryPillar / CorpusPanel / ProtocolsPanel / OnChainPanel / CallToAction. Three agents framing matches the actual system. 6 memory layers match the Xia-aligned framing in `CLAUDE.md`. Four protocols match the Xia 2026 paper (Outcome Embargo, Time-Aware Retrieval, Hierarchy of Truth, Source Tracking).

**One number to reconcile before merge:** the page shows "1,014 papers" in HeroStrip + CorpusPanel + AgentCards descriptions. Live `/api/health` reports `corpus_papers: 9873`. The 1,014 is plausibly the q-fin-only curated baseline (the canonical historical number; what early docs reference); 9,873 is the broader ingested total. **Risk:** a judge clicks `/architecture` (1,014), then hits `/api/health` (9,873), and notices. Recommend a one-character amend before merge, OR a parenthetical "(1,014 curated q-fin; 9,873 ingested total)".

### Recurring t2o2 patterns — updated tally

| Pattern | Instances today | Notes |
|---|---|---|
| Pre-close verification gate skipped (live state not checked) | #297, #298, #318, **#324** | The #324 instance was the most damaging; all four had grep-checkable live evidence the bot didn't run |
| Cache/wrapper hides root-cause bug | #300 cache hides N+1 RPC | Demo-acceptable; post-submission cleanup |
| PR body misattributes root cause | #297 claimed it fixed the 8s latency; actually #300's cache did | Documented above |
| Duplicate PRs for same issue | #320 / #322 / #321 | All closed without merging |
| ORM-without-migration | #275 → #282 hotfix | Earlier in the day |

### Active queue refresh (19:00 UTC)

| Issue / PR | Status |
|---|---|
| **#293** REBEL+SciSpacy KG | 🟢 in flight per pi; status check pending |
| **#301** StockBench real agent | 🟢 in flight per pi |
| **#306** DRAFT KG label restore | Gated on #293 producing artifact (≥5 unique predicates + ≥300 triples) |
| **#329** `/architecture` page | OPEN, dan-session, CI green; **awaiting decision on 1,014 vs 9,873 number** before merge |
| **#287** docs final-day audit | OPEN, this PR; rolling rebase target |
| **AMM pool-detail reads** | Endpoint returns 200 with pool list but per-pool tokenA/B/reserveA/B reads error. Secondary follow-up; not blocking. |
| **SPEC-1 e2e wallet run** | DAN-ONLY, **still not executed**. Highest-value remaining gap. |
| **arc-canteen traction backfill** | 30% rubric weight; likely still zero. |
| **Demo video v2** | Marten's track. |
| **Final demo dress rehearsal** | Not scheduled yet. |

### Production-grade PR scorecard for the day (chronological)

In merge order, all-time-zone tagged:

1. ✅ #308 verify-on-chain O(1) — dan-session — model PR
2. ✅ #311 StockBench real agent wiring — pi — production-grade
3. ✅ #312 Explore rebuild + STALE root-cause fix — dan-session — model PR
4. ✅ #313 Portfolio/Marketplace split — dan-session — model PR
5. ✅ #315 dual bull/bear generation — pi — model PR (pi's first)
6. ✅ #316 Reasoning arcscan upfront — dan-session — clean
7. ✅ #314 Explore extended ranges — dan-session — trivial+correct
8. ✅ #317 #303 arcscan UX wiring — pi — clean
9. ✅ #319 #310 source_papers fix — pi — production-grade root-cause fix (LIVE-VERIFIED 5 papers per strategy)
10. ⚠️ #318 #309 /api/health/amm — pi — shipped with runtime bug; fixed by #325
11. ✅ #323 error banner fix — dan-session — clean
12. 🚨 #324 #307 per-vault scoping — pi — **demo-breaking regression; hotfixed by #327**
13. ✅ #327 legacy-vault fallback hotfix — Maestro — restored rebalancing
14. ✅ #325 AsyncContract one-char fix — pi — surgical
15. ✅ #326 Generate consolidation — dan-session — clean UX restoration
16. ✅ #328 Portfolio account header — dan-session — clean feature add

**Net: 14 PRs merged today. One demo-breaking regression caught + hotfixed within 17 minutes of merge. One frontend PR (#329) still open pending a number reconciliation.**

---

### The 19:00–20:00 UTC wave (the SPEC-1 user-test loop)

Dan started a live SPEC-1 walkthrough as a real user. Real-user discovery surfaced architectural gaps that test suites missed.

#### What Dan caught walking through the live UX

| Bug | Severity | Diagnosis | Disposition |
|---|---|---|---|
| Library shows "only canned/example data" | UX (perceived) | Library DOES default to 'Generated' tab; rigor-failed candidates were polluting the main view | Fixed in #343: split into passing-main + collapsed-rejected |
| "View in Library" button doesn't navigate | P0 — broken | `window.location.hash` bypassed React Router state | Fixed in #343 (and independently in #341, closed as dup) |
| "Considered N candidates" modal semi-transparent | P1 — readability | Backdrop 0.6 + `card` glass-blur stacking | Fixed in #343 |
| 500 on Recent Generations | Transient | `/api/generate/jobs` returns 200 when re-probed; deploy-cycle hiccup | No fix needed |
| "Considered 2" hardcoded | Design intent | Dual-regime emits exactly 2 (bull + bear) — the design from #315 | Working as intended; messaging could be clearer |
| Same-intent generations need differentiator | UX | No timestamp / hash differentiator in Generated list | Deferred — UX polish, not P0 |
| Generate page too dense | UX | Layout decision | Deferred — UX polish |

The really important finding came from Dan trying to **deploy a vault as a user** rather than reading code: he tried `Add new account` in MetaMask but the flow had hiccups. He fell back to an existing wallet. **That fallback exposed the architectural gap Daniel R diagnosed independently** — see below.

#### Daniel R's branch (`dbrowneup/contract-refresh-client-vault-amm-guard`, commit `34edaa2`)

Daniel R opened a 54-file / -3837 LOC / +892 LOC branch with an *exceptional* commit message diagnosing **three real architectural gaps**:

1. **🚨 Vault creator mismatch** — `POST /api/vaults/create` makes the BACKEND the vault's `creator`, not the user. Consequences: `getVaultsByCreator(walletAddr)` returns `[]` for users → Portfolio shows nothing the user actually owns. `vault.setTargetAllocations()` requires creator authority → user cannot modify their own vault. **This is the demo-blocking architectural bug.** Daniel's fix moves vault creation to client-side `walletClient.writeContract(VaultFactory.createVault)` via MetaMask, mirroring how `setTargetAllocations` is already client-signed in `DepositFlow.jsx:175-189`. Adds a follow-up `vault.setAgent(operatorAddr)` so the agent has rebalance authority after user-created vaults.

2. **Stale contract addresses** — Daniel: *"causing silent failures across backend, agent, oracle, and frontend."* Multiple deployment-time addresses in `ui/src/config.js` `NEW_CONTRACTS`, the backend's `chain_client`, and the oracle don't match the latest `contracts/broadcast/<chain-id>/run-latest.json`. Calls against old addresses silently return zero-pool / zero-balance states. Fix: drive frontend addresses from `VITE_ARC_*` build args, backend from `ARC_*` env vars.

3. **AMM liquidity guard** — `MIN_HEALTHY_LIQUIDITY_USDC` was `$1` (effectively no guard). Daniel raises to `$1000` and adds an `_validate_trade_liquidity` preflight in `executor.py` that reads pool reserves and raises `InsufficientLiquidityError` before a doomed swap submits. `agent_runner.py` catches and skips.

**Triage decision:** wholesale-merging Daniel's branch is too risky in 5h (high conflict surface with today's heavy `generation_pipeline.py` + `agent_runner.py` work). **Filed #342** as a top-grade machine spec for t2o2/pi to re-implement the three fixes against current main, with Daniel's branch cited as the implementation precedent and a long anti-goals list (DO NOT touch `_get_vault_strategy_ids` per-#327, DO NOT touch dual-regime per-#315, DO NOT touch trace_publisher, etc.). Spec recommends splitting into 3 sequential PRs (Part 1 contract addresses → Part 3 AMM guard → Part 2 client-side vault create) since Part 2 is highest-risk and benefits from Parts 1+3 landing first.

**Dan's call: all-in.** Per his direction, no hedging; pi has shown the throughput to execute well-scoped specs at scale, the bar today is the user journey actually working end-to-end.

#### Other merges this hour

- **#331** — Generate collapsible: refresh corpus count (1,014 → 9,873) + "Read the full architecture →" link. dan-session, clean. Merged at `789af52`.
- **#339** — explore: route handler must use `HistoryRange` Literal too. Real bug from #314's incomplete fix (`?range=5Y` was failing at route validation before reaching the schema). Trivial +9/-7. Merged at `c8a5dd5`.
- **#340** — cut Topic Clusters tab. **#293 (REBEL+SciSpacy) actually closed at 16:57 UTC** — pi shipped the pipeline — but the API and CorpusKG component disagree on field names (`e.type` / `r.predicate` vs `entity_type` / `relation`), so the tab was showing "N entities, 0 relations" with REBEL data present. Honest path: cut the broken tab rather than expose hollow surface. Follow-up: fix the field-name mismatch + restore. Merged at `f29810d`. **Superseded #306** (closed; restoring the label without fixing the underlying mismatch was the wrong move).
- **#332** — Fix AMM pool state reads: `token0/token1/reserve0/reserve1` (the per-pool-detail-reads-error follow-up I was about to file). Pi shipped autonomously without a Maestro-filed spec. Merged at `28b7867`. **Both surface concerns from the original #318 broken-state are now resolved end-to-end.**
- **`ff1fd11`** — infra: deploy fix (`PUBLIC_DOMAIN` env + accept 301 in homepage check). Direct-to-main from Chuan, no PR. Worth flagging but operationally fine.
- **`e95dffd`** — security hardening !minor (API surface quick-wins). Direct-to-main from Chuan. The `!minor` tag means a semver bump.

#### My open PRs at this checkpoint

- **#343** — Library View-in-nav + rigor section + modal contrast (this PR's batch of Dan's SPEC-1 bugs). Awaiting backend tests. Will merge once CI green.
- **#287** — this audit doc. Rolling rebase target.

#### Closed without merging

- **#306** — restore "Knowledge Graph" label. Superseded by #340.
- **#341** — duplicate of #343's View-in-Library fix from the parallel session.

#### Recurring t2o2 pattern tally (refreshed)

The #342 spec explicitly enumerates the anti-goals because today's wave demonstrated that **pre-close verification gate skips are the dominant t2o2 failure mode** — #297, #298, #318, #324 all closed without checking live state. The hotfix-after-merge cycle (#318→#325, #324→#327) is a known recovery pattern but it costs minutes of broken-live-site that hurts demo credibility. The spec for #342 doubles down on machine-checkable acceptance criteria specifically aimed at live state, not unit tests.

---

### The 20:00–22:00 UTC wave (pi's mega-ship + brand refresh)

Pi shipped at unprecedented velocity in this window — **9 commits, 7 issues closed** (#334 trace identity, #335 honest wallet copy, #336 brief-in-title, #337 LaTeX rendering, #338 UX polish 3/6 items, #345 KG tab rebuild, #346 oracle capability marker), plus my Daniel R-diagnosed three-fix mega-spec **#342 Part 2** (client-side vault create) + **Part 3** (AMM liquidity guard). Pi did NOT ship #342 Part 1 (contract address refresh) — issue closed regardless, partial ship; the existing addresses appear to resolve correctly so Part 1 may not have been load-bearing.

Independent verification of pi's claims (the other Claude session also ran a Safari sweep simultaneously) surfaced 2 genuine partial-ship issues + 2 false alarms:

| Pi claimed | Reality | Disposition |
|---|---|---|
| #334 trace identity fixed | Distinct trace_hashes ✓ but confidence still hardcoded `0.9166` (= 11/12 votes) on every trace | Partial. **Filed #359** for dynamic confidence. |
| #336 passport title from brief | `🟢 Bull Moderate Blend — Trend-following momentum on liquid US equity ETFs` ✓ | Fully working. Other Claude misread the field — they were looking at source-paper anchors, not strategy_name. |
| #342 Parts 1+2+3 shipped | Only Parts 2 + 3 shipped; Part 1 (contract refresh) skipped | Acceptable — the system works without Part 1. Pi closed #342 anyway. |
| #346 oracle as capability marker | 7 of 84 assets now show oracle_address (the synths with deployed oracles); price_source still yfinance because oracle reads still fail upstream | Shipped as scoped. Other Claude's "0/84" was pre-deploy. |
| #338 UX polish (6 items) | 3/6 shipped (Esc-close, filter chips, SSE tail) | Closed earlier with note about deferred items 2/4/5. |
| #345 KG tab rebuilt | 249 entities, 200 relations visible per other Claude | ✓ |
| #344 Strategy passport visual disable | Greyed-out Deploy + PBO=0 honest | ✓ |
| #347 cut Marketplace from sidebar | Live | ✓ |

#### My catch that nobody flagged

**🚨 4 of 5 AMM pools are empty** (`reserve0: 0, reserve1: 0`). Only the first pool is bootstrapped. So when the agent tries to rebalance into 4 of 5 synths, the new AMM guard from #342 Part 3 correctly raises "thin pool — skipped" — visible in live `/api/traces` as "Swap skipped" reasoning. This is a **bootstrap state issue, not a code bug.** **Filed #358** for pi to bootstrap the empty pools with ≥ $1,000 USDC each (the threshold from #342 Part 3). Per Dan's call: do both bootstrap + keep the honest "thin pool skip" framing for any pools that don't get bootstrapped.

### The brand refresh — PR #357 (open as of this audit's last write)

Per Dan's call: drop "Linus for quantitative finance" as the project's user-facing headline. It's opaque outside the software-insider audience and frames the project as a person rather than the product. Also fix the corpus framing: arXiv is a preprint server, not peer-reviewed.

**New external headline:**

> # Agentic trading, grounded in research.
> Plain-English brief in. Autonomous on-chain strategy out — fused from your intent, the quant-finance literature, live market data, and statistical rigor.

The four-pillar framing (user intent + academic research + market data + statistical rigor) carries the accessibility wedge Dan articulated. The new H1 sub-line on Landing.jsx becomes **"Your Intent. Our Rigor."** — a 4-word brand statement that pulls the whole proposition into focus.

**Files touched in PR #357:**
- `README.md` tagline block
- `ui/src/components/Landing.jsx` hero (subtitle + H1 + body)
- `backend/archimedes/main.py` FastAPI description + root JSON tagline
- `ui/src/components/Architecture.jsx` — "peer-reviewed papers" → "academic papers (arXiv preprints across q-fin, ML, math, and agentic AI)"
- `ARC-OSS-SHOWCASE.md` primitive #8 — same correction

**Left alone (internal / historical):** `CLAUDE.md`, `docs/user-stories.md`, `submodules/Linus/` directory.

The "Rigor is the Wedge" section on Landing (with the four selection-bias correction citations: Bailey & López de Prado 2014, Bailey/Borwein/López de Prado/Zhu 2014, walk-forward OOS, look-ahead audit) was left untouched — it's accurate, cite-grade, and the load-bearing differentiator section.

### Branch cleanup pass

Per Dan's authorization, deleted stale remote branches now that the other Claude session is paused:

- `dbrowneup/contract-refresh-client-vault-amm-guard` (Daniel R's, consumed via #342 spec)
- `moonshot/health-amm-309` (pi's old, merged via #318)
- `moonshot/per-vault-strategy-307` (pi's old, merged via #324 then #327)
- `worktree-agent-abcb017b333339ebf` (pi's old worktree branch)

Surviving branches:
- `dbrowneup/docs-final-day` (this PR #287)
- `dbrowneup/brand-refresh-headline` (PR #357, the brand refresh)
- `dbrowneup/architecture-page` (#329, may still be lingering; harmless)
- `dbrowneup/explore-rebuild` / `dbrowneup/hotfix-324-legacy-vault-fallback` / `dbrowneup/portfolio-marketplace-split` (old, all merged — would also auto-delete on next prune)
- `marten` (Marten's, leave alone)
- `main`

### Active t2o2 queue right now

| # | Title | Status |
|---|---|---|
| **#358** | Bootstrap 4 empty AMM pools with starter liquidity | OPEN, **demo polish** |
| **#359** | Dynamic trace confidence: stop hardcoding 0.9166 | OPEN, **honesty polish** |

Two clean, well-scoped specs. Both are < 30 min of pi work each. Both close visible cosmetic gaps a judge would catch.

### What remains for Dan + me (submission window)

1. **SPEC-1 e2e walkthrough** when Dan's back at desktop — now actually viable with #342 Part 2 (client-side vault create) shipped. Vault.creator will equal Dan's wallet for the first time.
2. **Capture SPEC-1 stdout + screenshots → evidence runbook PR** — me
3. **#287 merge** — me, after SPEC-1 evidence committed
4. **#357 merge** — me, ideally before #287 so the new brand is on `main` when judges land
5. **arc-canteen telemetry backfill** — Dan, 10-15 min
6. **ARC-OSS-FORM-DRAFT placeholders + Google Form** — Dan

---

### One-line North Star

**Working flow on live HTTPS: judge → Generate → DepositFlow → real rebalance → on-chain verify, with arcscan tx evidence captured in `docs/runbooks/arc-testnet-e2e-evidence.md`.** That's the entire submission story. Everything in this plan serves it.

---

## Final wave (2026-05-25 23:00–23:50 UTC) — seven PRs merged + AMM Path B exploration

Seven PRs landed in ~50 minutes covering passkey enablement, brand polish, and AMM bootstrap attempt:

| # | Title | Owner | Commit | Status |
|---|---|---|---|---|
| #348 | Inject VITE_CIRCLE_CLIENT_KEY from GitHub Secrets on deploy | pi | `2cb7005` | Closed — verified live: `kH="TEST_CLIENT_KEY:..."` baked into JS bundle, `circlePasskeyEnabled()` returns true |
| #366 | Corpus Catalog: 1 line per entry | dbrowneup | `1bfd4ca` | Closed — merged + deployed |
| #368 (closes #367) | Per-device passkey username — unblock teammates + judges | Önder | `7d979aa` | Closed — verified `archimedes_circle_username` storage key in live bundle |
| #364 | Fix 14 raw-HTML error render sites — shared `apiGet` helper | pi | `c7d185b` | Closed — merged + deployed |
| #362 | AMM bootstrap: auto-calculate from wallet balance | pi | `74381f1` | **Partial — pre-close gate skipped (8th occurrence today)**; see Path B section below |
| #370 | Strategy Passport: brief-specific title (no more 'Capital Preservation' for every generation) | dbrowneup | `b03b8c7` | Closed — merged + deployed |
| #369 | Style spruce-up: Geist font + bigger modals + polished type scale | dbrowneup | `8d72063` | Closed — merged + deployed |

### #348 — passkey enablement (full pattern documented for future hardening)

Pi's choice of GitHub Secrets + deploy.yml injection (rather than the manual EC2 .env edit Maestro originally suggested as Option A) is the durable pattern. The key is now baked into every nginx rebuild automatically. Verified on-chain: `circlePasskeyEnabled()` returns true on live, the Connect Wallet modal will surface the passkey option for all users.

### #367/#368 — passkey username collision unblocks teammates + judges

Önder discovered at ~01:58 TR time that the hardcoded `username = 'archimedes'` locked out everyone but Dan (the original registrant). PR #368 introduced `getOrCreateUsername()` with per-device localStorage namespacing (`archimedes-<8 hex>`). Defensive fallbacks for missing `crypto.randomUUID` and Safari private-mode (no localStorage). One small risk noted in PR comment but not blocking: if Circle's Login API cross-checks username against original registration, Dan's existing session might require a `clearCircleSession()` + re-register — 30-second recovery. Posted as a follow-up consideration; merge proceeded.

### #362 — partial ship pattern repeats (eighth occurrence today)

Pi's `bootstrap_amm_liquidity()` code shipped clean: auto-calculates USDC per pool from wallet balance, lowers `MIN_HEALTHY_LIQUIDITY_USDC` from $1000 → $5 to match testnet reality, idempotent. **But the on-chain outcome was four reverts** at 23:20 UTC:

```
sTSLA: skipped — already has $3.97 reserve
sNVDA: FAILED — ESTIMATION_ERROR: execution reverted
sSPY:  FAILED — ESTIMATION_ERROR: execution reverted
sBTC:  FAILED — ESTIMATION_ERROR: execution reverted
sGOLD: FAILED — ESTIMATION_ERROR: execution reverted
```

Pi declared "✅ Honest approach with available funds" without verifying. Live `/api/health/amm` still shows four of five pools at zero reserves. **Eighth pre-close verification gate skip today** (after #297, #298, #318, #324, #338, #342, #358). Pi acknowledged the pattern in DM and committed to adopting "verify live before closing" going forward.

### Path B exploration — confirmed structurally blocked

Diagnostic probing identified the real constraint via on-chain `balanceOf` + `owner()` calls against each synthetic-token contract:

| Wallet | Role | USDC | Synth-token balances | Mint authority |
|---|---|---|---|---|
| `0xc221dcd6fe7d81ff741f94c08e61f52bea1f9ac9` | Circle operator (in pi's Circle account) | $29.90 | **0 of every synth** | no |
| `0x0546a5a3ddc3c34a2e4f245b384c317551db5b62` | Foundry deployer (Chuan's local keystore) | — | — | **owner() of every synth contract** |

`addLiquidity(USDC, sNVDA, …)` reverts because the operator wallet has nothing to pair USDC with. Pi confirmed `0x0546...` is NOT in their Circle Developer Wallets account — it's Chuan's local Foundry keystore. Dan's separate Circle Console also can't help; any new wallet Dan creates there wouldn't own the synthetic tokens.

**Only two real paths:**
- **A — Ship as-is**: sTSLA pool has $4 reserves; agent's rigor guard ("Swap skipped — thin pool") fires honestly for non-sTSLA allocations. Defensible demo story: rigor IS the product working as designed.
- **B — Chuan-side intervention**: Chuan runs `cast send <synthToken> "mint(address,uint256)" 0xc221... <qty> --private-key $DEPLOYER_KEY` for 4 tokens. ~5 min of Chuan time, zero cost (testnet). After mint, pi's bootstrap script succeeds unchanged.

Dan DM'ing Chuan in parallel; if response in time, Path B is automatic. If not, Path A ships with a README liquidity-floor note. Either way: no further work needed from pi or Maestro on the AMM side.

### Branch state after rebase

`dbrowneup/docs-final-day` (PR #287) rebased onto `origin/main` HEAD `8d72063` — clean, no conflicts. Will be force-pushed with `--force-with-lease` after this append.

### What's left for submission

1. **AMM Path A/B resolution** — Chuan-dependent (DM in flight). README note ships either way.
2. **SPEC-1 e2e walkthrough** — Dan-executes from `docs/runbooks/spec-1-walkthrough.md`, this branch commits the evidence to `docs/runbooks/arc-testnet-e2e-evidence.md`.
3. **#287 merge** — after SPEC-1 evidence committed.
4. **arc-canteen telemetry backfill** — Dan, 10–15 min.
5. **ARC-OSS Google Form** — Dan, final answers from `ARC-OSS-FORM-DRAFT.md`.
6. **Optional — #359 dynamic confidence** — pi's queue, low priority cosmetic.

---

### Recurring pattern this hackathon — pre-close verification gate

Eight issues today (#297, #298, #318, #324, #338, #342, #358, #362) closed before live verification, requiring follow-up work. Most expensive instance: #324 caused a 21-minute rebalance outage on live. Pi has acknowledged the pattern in DM; going-forward expectation: every issue closure includes `curl https://archimedes-arc.app/<endpoint>` output pasted in the closing comment, not just "the deploy ran." The recurring cost is real but each individual diagnosis was fast — root cause is the velocity vs verification trade-off when shipping eight issues in three hours. Worth documenting for future hackathons or any team adopting an agentic-bot workflow.

---

## After-final-wave addendum (2026-05-26 00:00–00:50 UTC)

The "final wave" turned out not to be final. Six more PRs landed in ~50 min covering the third-act polish.

### What shipped on main

| # | Title | Owner | Status |
|---|---|---|---|
| #372 | Honest copy: post-#365 wallet gate + corpus count matches API | dbrowneup | Closed — `9,873` → `1,014` ingested; replaced aspirational corpus breakdown with real top-4 categories |
| #373 | **SPEC-1 evidence: 2 vaults deployed, 8 tx, all confirmed on Arc testnet** | Önder | **The submission load-bearing artifact.** User wallet `0x8b5EEE…` deployed Moreira-Muir + TSMOM vaults; every `vault.creator == user` verified on-chain; 8 receipts replayable on arcscan |
| #287 (this branch) | Final-day audit + this addendum | dbrowneup | Open — rebased onto post-#373 main, force-push pending |

### What's in flight

| # | Title | Owner | State |
|---|---|---|---|
| #374 | Allow vault deploy for failed-rigor strategies | Daniel R | **Open, held** — original premise was "nothing deployable", but #373 just demonstrated 2 strategies are deployable. Erodes the wedge if merged. Kept as break-glass safety net only per Dan's call |
| #375 | Library cleanup + Pending Backtest relabel | other Claude | Open — kills dual-render bug, relabels `status="rejected" + null metrics` as `🟡 Pending Backtest` (honest about "not yet backtested"). No file overlap with Maestro's work |
| #377 (this session) | Faucet menu item + clickable Generate cards + sidebar Home anchor + Learnings roadmap badge | dbrowneup (Maestro) | Open — 4-file polish bundle, branched off `79650f8`, no overlap with #375 |

### The "no strategies pass rigor" diagnosis — UI surfacing, not backend

Dan's report that "not a single strategy passes the rigor gate" was diagnostically incomplete. Live API check on 6 seed strategies:

| Strategy | Status | passes_rigor_gate | DSR p | OOS Sharpe |
|---|---|---|---|---|
| Capital Preservation T-Bill | live | **false** | 0.812 (too cash-like for the test) | 0.431 |
| Faber 2007 SMA | live | false | 0.612 | 0.930 |
| 52-Week High Momentum | candidate | false | 0.609 | 0.910 |
| **Moreira-Muir Vol-Managed** | live | **TRUE ✓** | 0.995 | 0.969 |
| **TSMOM (Moskowitz-Ooi-Pedersen)** | live | **TRUE ✓** | 0.976 | 0.762 |
| Buy-and-Hold Baseline | live | false | — | — |

Two strategies pass. The visibility problem is the Library page rendering both passing strategies under the EfficientFrontier chart as small chips while the Generated tab (default) shows a wall of rejected-status candidates from prior generations. PR #375 fixes the surfacing.

### Önder's #373 — the wedge held empirically

Önder's evidence runbook makes the load-bearing pitch claim concrete:

> *"Of 6 strategies in the library, exactly the 2 that pass DSR ≥ 0.95 + PBO < 0.5 + OOS ratio ≥ 0.5 + look-ahead audit were deployable. The other 4 — including the buy-and-hold baseline — were correctly gate-blocked."*

That sentence is the pitch. The on-chain receipts (8 of them, blocks 44028414–44031374) back it up: judges can replay every step against `testnet.arcscan.app` without trusting any screenshot.

This re-frames #374: Daniel R proposed ungating Deploy because "nothing was deployable." Önder just demonstrated that's not true — the gate works exactly as designed. If #374 ships, the sentence above gets weaker because the gate becomes advisory rather than enforcing. Holding #374 as break-glass only is the right call.

### AMM Path B status — Chuan-blocked, accepted

Pi's parallel `mint authority` check confirmed `0x0546a5a3…db5b62` is Chuan's local Foundry deployer keystore, outside the Circle Developer Wallets account. Dan messaged Chuan directly; no response (likely asleep). Önder's SPEC-1 run succeeded *under* this constraint — the strategies route into SPY/NIKKEI/GOLD/TREASURY/OIL synths which currently have empty pools, so the agent's runtime rigor guard from PR #342 Part 3 will skip those swaps and record honest `"Swap skipped — thin pool"` traces. That outcome is acceptable evidence per the runbook.

### Updated "what's left" — submission window (~2.5h remaining)

1. ✅ **SPEC-1 evidence committed** (Önder, #373)
2. **#375 merge** — pending other Claude's PR landing
3. **#377 merge** — pending CI green
4. **Other Claude's submission polish bundle** — color scheme + text size + Portfolio compaction + Reasoning compaction; in flight as Track 2
5. **#287 force-push + merge** — after this addendum + final state
6. **arc-canteen telemetry backfill** — Dan, ~15 min
7. **ARC-OSS Google Form** — Dan, ~10 min

### Parallel-Claude coordination — what's working

Two hosted Claude sessions running concurrently, both editing this repo. The coordination boundary that's holding:

- **Strict file ownership.** Each session declared its files upfront; no file appears in two PRs.
- **Branch-per-bundle.** Each session ships one PR per logical bundle (other Claude: #375 then submission-polish; Maestro: #377 + this docs commit on #287). No fan-out across branches.
- **PR descriptions list non-overlapping files explicitly** so the other session can verify the boundary.
- **Dan as the human router** between sessions — relays state changes (#373 merging changes the case for #374; the rigor-pass diagnosis changes the priority on #375) so neither session operates on stale context.

Lesson for any future agentic-bot workflow at velocity: a fast human router beats elaborate cross-session protocols, *if* both sessions surface state changes promptly. Cost of the router role on a 5-hour submission window: maybe 15 min of Dan's time over ~12 turns, the cheapest part of the loop.

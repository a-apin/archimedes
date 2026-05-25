# Archimedes Spine+ v2 — Phase Plan

> **Status:** Drafted 2026-05-22; refreshed 2026-05-23 with phase-completion audit.
>
> **As of 2026-05-23:** Phases 0, 1, 2, 3a, 3b, 6, 7 are ✅ LANDED. Phase 3c is 🟡 SKELETON (production KB body deferred). Phases 4 + 5 are still ⏸ pending Marten/Chuan alignment. Phases 8 + 9 are specced separately in [`phase8-9-landing-and-fusion-spec.md`](phase8-9-landing-and-fusion-spec.md) and scheduled for the next implementation block.
>
> **Branch:** `dbrowneup/spine-plus-v2` (PR #135 open), branched off `main` and rebased forward as `main` moves.
>
> **How to read:** each phase has (1) problem statement, (2) scope with concrete
> files, (3) acceptance criteria (machine-checkable where possible), (4)
> anti-goals, (5) dependencies, (6) suggested owner, (7) open questions for
> team weigh-in. The "open questions" sections are the explicit asks for input.

---

## Cross-cutting principles

These apply to every phase. Violating one of these is a review-blocker.

1. **Honesty first.** No mock data, no fake authors, no placeholder copy that
   masquerades as real. If a feature isn't wired, the UI says "pending" or
   isn't surfaced. If an action fails on-chain, the error message says why
   specifically, not "(off-chain only)" as a casual aside.
2. **No new routes go into `api/routes.py`.** It's at ~2315 lines per Marten's
   survey. Every new endpoint lands in a dedicated router file
   (`generate_routes.py`, `explore_routes.py`, etc.) per the pattern already
   set by `chat_routes.py` / `marketplace_routes.py` / `risk_routes.py` /
   `selection_bias_routes.py`. No back-port of existing endpoints — just stop
   making the monolith worse.
3. **Time tracking, not estimating.** Phases log start/end timestamps in this
   doc as they complete. No prospective estimates — we build calibration data
   from observed work.
4. **One PR per phase.** Phase 0 is one specs PR; each subsequent phase is its
   own PR with passing tests + a one-paragraph post-mortem (what was harder
   than expected, what was easier, where the spec needed amendment).
5. **Specs are living.** If reality diverges from a spec during execution,
   update the spec in the same PR. Specs that go stale are worse than no specs.
6. **Lanes are guidance, not gates.** Per [`CLAUDE.md`](../../CLAUDE.md): an
   assigned issue is yours to execute regardless of nominal lane. Flag
   cross-lane review needs in the PR description; don't refuse work.

---

## Decisions captured (locked, do not re-litigate without team alignment)

| Decision | Locked answer |
|---|---|
| Vault semantics | Time-bound execution container (1 strategy + capital + window) |
| Strategy↔Vault cardinality | 1:1 |
| Generate UX | SSE streaming on top of `portfolio_agent.py`; events emitted per iteration |
| Multi-strategy mechanic | Agent runs N candidates internally, surfaces single best, rejects browsable |
| MVP loop | Generate → Library → Deploy as vault → wallet signs deposit + setTargetAllocations → trade on Arc testnet → Portfolio reflects state |
| KB integration scope | Full pipeline (SPECTER2 + HDBSCAN/BERTopic + REBEL/SciSpacy) + scheduled re-runs |
| Phase order after 0+1 | 2 → 3 → 4 → 5 → 6 |

---

# Phase 0 — Architectural Specs

**Status:** ✅ LANDED — `5fc2eb9` (6 specs under `docs/specs/`)
**Branch:** `dbrowneup/spine-plus-v2`
**PR shape:** single docs PR landing 6 new files under `docs/specs/`

## Problem

The current codebase has at least four conceptual ambiguities that cause
every screen to improvise its own version of the truth:

- What is a Vault? (Time-bound vs perpetual; multi-strategy vs single)
- What is the Strategy lifecycle? (Where do generated strategies live before
  deployment? How do they expire?)
- Which of the four portfolio constructors fires when? (Marten's survey gap
  cluster #5 — "load-bearing")
- What does each spine page do that's distinct from the others? (Library vs
  Reasoning currently overlap; Explore is missing)

Phase 0 settles these in writing so Phases 1-6 build on a shared model.

## Scope

Six new spec files. Each is short (300-500 words) and answers the questions
listed below. No code in this phase.

### 0.1 `docs/specs/vault-semantics-spec.md`

**Questions to answer:**
- A Vault is _____ (time-bound execution container holding 1 strategy + capital + window).
- The lifecycle is: created → ___ → ___ → ___ → completed/expired. Name each transition + what triggers it.
- What does "trade window" mean concretely? (Start time, end time, what happens at each boundary.)
- Can a vault be re-used after completion? (Recommended: no; user generates a new strategy and deploys a new vault.)
- What is the on-chain artifact? (The multi-asset NAV `Vault.sol`, deployed Day-10.)
- How does the agent runner interact with a vault during its window? (Default: deploy initial allocation at window-open; rebalance only if the strategy DSL says so; unwind at window-close.)
- What happens if the user doesn't deposit by window-open? (Vault expires un-deployed; recorded in Library as "expired" for post-hoc inspection.)
- Failure modes: window passes mid-trade, oracle goes stale, AMM has no liquidity.

**Acceptance:** every Phase 4 implementation decision can be derived from this doc.

### 0.2 `docs/specs/strategy-lifecycle-spec.md`

**Questions to answer:**
- Lifecycle states (proposed): `Generated → Validated → Deployed → Active → Completed | Expired | Rejected`. Define each.
- Which fields are populated at which transition? (E.g., `rigor_verdict` populates at Validated; `vault_address` at Deployed; `realized_pnl` at Completed.)
- Who triggers each transition? (Generated: agent. Validated: rigor gate. Deployed: user. Active: vault contract event. Completed/Expired: time + agent. Rejected: rigor gate failure.)
- How does this interact with the `StrategyRecord.status` column (currently `candidate | live | retired`)? Either remap or expand the enum.
- What's the "time-bound expiry" semantics? (A generated strategy that isn't deployed within N hours becomes Expired. N = ?)
- Where do expired-un-deployed strategies live in the UI? (Library > Generated tab, filterable, with original reasoning still inspectable.)

**Acceptance:** every Library/Portfolio filter and every state transition in
Phase 2-5 maps cleanly to a state in this doc.

### 0.3 `docs/specs/page-roles-spec.md`

For each spine page, answer: (1) one-sentence purpose, (2) primary API calls,
(3) what it's explicitly NOT for, (4) what links out to what.

Pages to spec:
- `/` Landing — marketing surface, no wallet required
- `/explore` Explore (NEW in v2) — live asset prices + plain-English stats; read-only asset discovery
- `/generate` Generate — streaming strategy generation
- `/library` Library — Generated + Examples tabs; outcome of generation
- `/strategy/:id` Strategy passport (NEW in v2) — full passport with Deploy CTA
- `/corpus` Corpus — paper catalog + overview + graph + KG
- `/portfolio` Portfolio — user's vaults + agent activity + stress scenarios
- `/reasoning` Reasoning — trace browser only (no strategy listing)
- `/learnings` Learnings — realized performance + post-hoc reasoning

**Acceptance:** Library never lists traces; Reasoning never lists strategies;
Explore never appears on Portfolio. No surface overlap remains.

### 0.4 `docs/specs/portfolio-constructor-decision-tree.md`

Per [survey gap cluster #5](../chuan-architecture-survey.md), four
constructors exist with no documented hierarchy:

| File | Lines | Role (proposed) |
|---|---|---|
| `services/portfolio_agent.py` | 850 | LLM-agentic top-level constructor (Day-10) |
| `services/portfolio_constructor.py` | 285 | Orchestrator routing to (a)/(b)/(c) by regime |
| `services/kelly_portfolio.py` | 505 | Kelly sizing + risk parity |
| `services/portfolio_optimizer.py` | ~235 | Pure MVO (Önder's) |

**Questions to answer:**
- Which constructor fires for which entry point? (Generate page → ?, agent runner tick → ?, architect fast preview → ?)
- Are `kelly_portfolio.py` and `portfolio_optimizer.py` alternatives, or composable layers? (Marten guesses: composable — optimizer computes weights, Kelly sizes them.)
- How does `portfolio_agent.py` interact with the deterministic constructors? (Survey hypothesis: replaces them entirely for LLM-agentic path; deterministic chain is the fallback when LLM unavailable.)
- The `_DRIFT_THRESHOLD` differs across files (0.15 vs 0.05). Which is canonical?
- Where does this decision get made in code? (Recommended: a small `pick_constructor()` helper that takes `(mode, llm_available, regime)` and returns the right constructor.)

**Acceptance:** Phase 2 instrumentation knows which constructor's events to
emit. Future bug fixes know which file to edit.

### 0.5 `docs/specs/generation-streaming-spec.md`

The SSE protocol Phase 2 implements. Concretely:

**Endpoint:** `GET /api/generate/stream/{job_id}` (Server-Sent Events)

**Event schema** (each event is `event: <name>\ndata: <json>\n\n`):

```
job_queued          { job_id, brief, ts }
brief_validated     { job_id, asset_classes, risk_appetite, ts }
candidates_selected { job_id, candidate_count, source_arxiv_ids: [...], ts }
agent_iteration     { job_id, iteration_n, max_iterations, ts }
tool_called         { job_id, tool_name, args_summary, ts }
tool_result         { job_id, tool_name, result_summary, ts }
candidate_drafted   { job_id, candidate_id, strategy_name, ts }
candidate_evaluated { job_id, candidate_id, rigor_verdict: { dsr, pbo, oos_sharpe, passes }, ts }
best_selected       { job_id, best_candidate_id, considered_count, ts }
trace_hashed        { job_id, trace_hash, ts }
persisted           { job_id, strategy_id, redirect_url, ts }
done                { job_id, strategy_id, ts }
error               { job_id, message, recoverable: bool, ts }
```

**Questions to answer in the spec:**
- Reconnection semantics: if the client disconnects mid-stream, can it
  reconnect and resume? (Recommended: yes; events are durable in Redis for
  N minutes, client sends `Last-Event-ID` header.)
- Job persistence across page navigation: where do in-flight jobs live so the
  Generate page can re-attach after a back-button? (Recommended: a small
  Redis-backed `jobs:{job_id}` key holding the event log; frontend stores
  `currentJobId` in localStorage and re-subscribes on mount.)
- Backpressure: if the agent emits 100 events/sec, do we batch?
  (Probably not — agent iterations are seconds apart, not milliseconds.)
- Failure mode: agent loop hits `MAX_AGENT_ITERATIONS=12` without producing a
  valid candidate. What does the user see? (`error` event with
  `recoverable: true` and a "regenerate" CTA.)

**Acceptance:** Phase 2 frontend implementation can be built directly from
this doc without re-asking.

### 0.6 `docs/specs/kb-integration-spec.md`

How [`submodules/KnowledgeBase/`](../../submodules/KnowledgeBase/) lands on our
corpus. **No re-implementation** — the spec describes how to invoke the
existing pipeline and where its outputs persist.

**Questions to answer:**
- Which entry-point script in the KB submodule runs the full pipeline?
  (Inspect `submodules/KnowledgeBase/papers_analysis/*.py` and document.)
- Inputs: where does KB read paper text from? (Currently `data/corpus/text/`
  populated by `scripts/hydrate_corpus.py` — that script downloads PDFs +
  extracts text via PyMuPDF.)
- Outputs: SPECTER2 embeddings (N×768 numpy), HDBSCAN cluster_id per paper,
  BERTopic topic_label per paper, REBEL+SciSpacy KG (entities + relations).
  Where does each persist?
  - Embeddings → `archimedes-corpus-artifact` named volume as `embeddings.npy` + `ids.json`
  - Cluster IDs + topic labels → `PaperRecord.cluster_id` + `PaperRecord.topic_label` columns (already exist, currently NULL)
  - KG entities/relations → new tables `kg_entities` + `kg_relations` (alembic-less ALTER TABLE add)
- How is the pipeline triggered?
  - **Operator-triggered for first run:** `python -m archimedes.scripts.run_kb_pipeline`
  - **Scheduled going forward:** new `kb_runner.py` standalone process (mirroring `chain/oracle_runner.py`) — sleeps + polls for "have N new papers landed since last run?", triggers pipeline if so. New docker-compose service.
- Re-run triggers: what counts as "needs re-run"? (Recommended: ≥100 new
  papers OR ≥7 days elapsed since last run, whichever first.)
- What if the KB pipeline takes hours? (Run in a separate container with its
  own resources; doesn't block the API. Status surfaced via
  `kb_runner_state` Redis key.)

**Acceptance:** Phase 3 KB integration work has zero ambiguity about which
scripts to invoke, where outputs go, and how the scheduler runs.

## Phase 0 deliverable summary

- 6 new files under `docs/specs/`, each 300-500 words
- Single PR, "Phase 0: architectural specs for Spine+ v2"
- No code, no tests, no docker changes
- Reviewer focus: do these specs answer enough questions to unblock Phases 1-6?

## Phase 0 suggested owner

**Lead:** Dan (you). You have the conceptual model in your head from this
session.

**Reviewers:** Marten (cross-check against architecture survey), Chuan
(vault-semantics + portfolio-constructor decision tree affect his lane).

## Phase 0 open questions for team

- Strategy expiry TTL: how many hours before a generated-but-undeployed
  strategy goes Expired? (24h? 72h? Live-data-dependent?)
- Does Vault re-use ever make sense? (E.g., re-deploying the same strategy
  into the same vault after window-close.)
- Is the KB pipeline scheduler appropriate as a new docker-compose service,
  or should it be a cron job on the host?

---

# Phase 1 — Junk extermination + UX fixes

**Status:** ✅ LANDED — `f21ac8d` (cherry-pick) + `00d8f09` (follow-up landing
deferred items: Reasoning restructure + Library export move + `?highlight=`
deep-link + Learnings onNavigate + ownership-comment reframe).
**Residual carve-outs (cosmetic, non-blocking):**
- `<span>off-chain only</span>` label at [`Portfolio.jsx:240`](../../ui/src/components/Portfolio.jsx) — Phase 1 acceptance grep (`"(off-chain only)"` with parens) passes, but the label itself still hides the failure reason. Roll into Phase 8 polish.
- MetaMask popup verification still pending on the deploy. Reopen only if the
  popup surfaces multiple chains in practice.

**Dependencies:** None (parallelizable with Phase 0)
**PR shape:** small, focused, no behavior changes beyond mock-data removal + wallet network filter

## Problem

The strip-to-spine commit removed the worst offenders (Marketplace cards,
dev-test Publish form) but the codebase still has at least these
honesty-violation residues, all of which surfaced in user walkthroughs:

1. **PR #37 / PR #38 mock pills** on Reasoning page strategy detail panel
   (hardcoded in `Reasoning.jsx::StrategyDetailView` — not removed in the
   strip)
2. **Off-chain casual asides** ("(off-chain only)") in Portfolio agent
   activity feed when on-chain anchor failed — should surface the *reason*
   for failure, not bury it
3. **MetaMask network filter too broad** — `wallet_requestPermissions` asks
   for 8+ chains; should only request Arc Testnet
4. **Reasoning page still lists strategies** alongside traces — overlap with
   Library; should be traces-only per page-roles-spec
5. **Learnings page is decorative copy** — either populate from real
   Portfolio data or hide from nav until it has signal
6. **Stale ownership comments** in `interfaces/agent.py`, `chain.py`,
   `math.py` reference owners that don't reflect bot-driven authoring
   reality (survey gap #7)
7. **`TODO: Implement with stored price history`** in `api/routes.py:172`
   (survey gap #11) — either implement or close the asset history endpoint
   honestly (404 with explanation)

## Scope (concrete files)

| File | Change |
|---|---|
| `ui/src/components/Reasoning.jsx` | Remove `StrategyDetailView` strategy listing OR move it to `/library`; remove PR #37/#38 RELATED pills |
| `ui/src/components/Portfolio.jsx` | Improve off-chain failure messaging (show actual reason from API) |
| `ui/src/config.js` | Constrain `wallet_addEthereumChain` to Arc only on first connect; don't request broad permissions |
| `ui/src/components/Learnings.jsx` | Either wire to a real `/api/learnings/` endpoint (returning realized PnL per deployed vault) OR hide from `Layout.jsx` nav until Phase 4-5 lands |
| `backend/archimedes/interfaces/*.py` | Update stale docstrings to reflect current authoring reality |
| `backend/archimedes/api/routes.py:172` | Remove the `# TODO` and implement OR return 501 with explicit "not yet implemented" |

## Acceptance criteria

- [ ] `grep -rn "PR #37\|PR #38" ui/` → empty
- [ ] `grep -rn "(off-chain only)" ui/` → empty (replaced with real failure surface)
- [ ] Manual: connecting MetaMask shows only Arc Testnet in the permissions card
- [ ] `grep -nE "Implement with stored price" backend/` → empty
- [ ] `pytest -q` → 265+ passed / 0 failed (no regressions)
- [ ] Build green: `docker compose build nginx backend` succeeds
- [ ] PR description includes a screenshot of MetaMask permissions card showing only Arc

## Anti-goals

- Do NOT refactor `routes.py` or `Strategies.jsx` structurally in this phase.
  Surface-cleanup only.
- Do NOT add new features — Phase 1 is subtractive + small fixes.
- Do NOT change any API contracts. Schema changes belong in their owning phase.

## Suggested owner

**Lead:** anyone with a focused 2-3 hour block. First-thing-post-standup
candidate. Daniel R. is in-character for the frontend mock removal; t2o2 is
fine for the backend cleanup.

## Phase 1 open questions for team

- Should the off-chain-anchor failure surface a "retry" button, or just log
  the reason and move on?
- Learnings page: hide-until-ready (recommended) or stub-with-real-empty-state?

---

# Phase 2 — Streaming Generate on `portfolio_agent.py`

**Status:** ✅ LANDED — `28dd93a` (initial) + `7b1c1e3` (follow-ups: hard cancel, rigor wiring, brief validation)
**Dependencies:** Phase 0.5 (`generation-streaming-spec.md`); Phase 0.4 (`portfolio-constructor-decision-tree.md`)
**PR shape:** larger; includes backend SSE endpoint + frontend stream UI + multi-strategy mechanic

## Problem

Three gaps observed in the strip-to-spine state:

1. **Generate page calls architect synchronously** (`/api/strategies/construct`)
   not fusion (`/api/strategies/generate?mode=fusion`). User sees a 6-strategy
   library output in <1s and rightly thinks "this is suspicious."
2. **No status surface for async generation** — if user navigates away mid-
   generation, the job appears to die.
3. **Single-strategy output** — user pointed out a good system should generate
   multiple candidates internally and surface the best. Aligns with how
   `portfolio_agent.py` already iterates (`MAX_AGENT_ITERATIONS=12`).

## Scope

### Backend

| File | Change |
|---|---|
| `backend/archimedes/api/generate_routes.py` (NEW) | New dedicated router for Generate endpoints. Per cross-cutting principle #2, this does NOT live in `routes.py`. |
| `backend/archimedes/services/portfolio_agent.py` | Instrument iteration loop to emit events (callback hook the SSE endpoint subscribes to). |
| `backend/archimedes/services/generation_pipeline.py` (NEW) | New orchestrator: receives brief, runs `portfolio_agent` N times for N candidates, evaluates each through rigor gate, persists best, emits events at each step. |
| `backend/archimedes/services/job_queue.py` | Extend to support the per-job event log Redis key (`jobs:{job_id}:events`) referenced in the spec. |
| `backend/archimedes/api/generate_schemas.py` (NEW) | Pydantic models for the event payloads. |

### Frontend

| File | Change |
|---|---|
| `ui/src/components/Generate.jsx` | Replace `<StrategyArchitect>` call with new streaming UI. Architect becomes a labeled "fast preview" toggle. |
| `ui/src/components/GenerationStream.jsx` (NEW) | The live event stream renderer. Shows each event as it arrives; renders the agent's intermediate work. |
| `ui/src/components/GenerationStatus.jsx` (NEW) | Compact status table at bottom of Generate page showing in-flight + recently completed jobs. Persists `currentJobId` in localStorage so navigating away + back resumes the stream. |
| `ui/src/components/RejectedCandidates.jsx` (NEW) | Modal/sub-view showing the N-1 rejected candidates with their rigor verdicts. Linked from the "considered N candidates" caption. |

### API contract (informal — `generation-streaming-spec.md` is authoritative)

```
POST /api/generate/start
  body: { brief: { intent, risk_appetite, asset_classes?, capital_usdc, max_papers? } }
  returns 202: { job_id }

GET /api/generate/stream/{job_id}
  Server-Sent Events stream (see spec 0.5 for event types)

GET /api/generate/jobs
  returns { jobs: [{ job_id, state, created_at, ... }] }
  Used by GenerationStatus.jsx to populate the status table.

GET /api/generate/jobs/{job_id}/candidates
  returns { candidates: [...], best_id }
  Used by RejectedCandidates.jsx.
```

## Acceptance criteria

- [ ] Click "Generate" → SSE stream opens within 500ms
- [ ] Each `portfolio_agent` iteration produces a visible event in the UI
- [ ] If user navigates from `/generate` to `/library` and back, the in-flight job is still streaming
- [ ] On completion, "View in Library" CTA links to the persisted strategy
- [ ] When agent produces N candidates, only the best surfaces by default; "considered N candidates" link opens the rejected list with full rigor verdicts visible
- [ ] If `MAX_AGENT_ITERATIONS` hits without a valid candidate: error event with recoverable=true and a regenerate CTA
- [ ] `pytest backend/tests/services/test_generation_pipeline.py` → all green
- [ ] Manual: kill the backend mid-stream → frontend shows "connection lost, retrying"; restart backend → stream resumes from last-known event ID
- [ ] No regression: `/api/strategies/construct` (architect path) still works as the "fast preview" toggle

## Anti-goals

- Do NOT replace `portfolio_agent.py`'s internals — just add a callback hook for events.
- Do NOT use websockets; SSE is sufficient and simpler.
- Do NOT batch events — agent iterations are seconds apart; per-event overhead is negligible.
- Do NOT log strategy text into Redis events (the full text persists in `strategy_store` after `persisted`); events should be summary-shaped.
- Do NOT require LLM credentials for the test suite; mock `portfolio_agent`'s LLM call with a deterministic fixture in tests.

## Suggested owner

**Lead:** Daniel R. (backend FastAPI lane) + t2o2 issue. `portfolio_agent.py`
is `t2o2`-authored; instrumenting it for SSE is a natural extension. The
frontend stream UI is small enough that whichever frontend dev picks it up
can crank it out.

**Reviewers:** Dan (multi-strategy mechanic correctness), Chuan (agent
behavior under iteration limits).

## Phase 2 open questions for team

- N candidates per request: fixed at 3? Configurable per risk profile? Always
  N until rigor gate passes for one?
- Architect "fast preview" toggle copy: how do we describe it honestly?
  Suggestion: "Skip the fusion engine and let the architect pick from the
  curated library — faster but less novel."
- Should the rejected candidates persist long-term (so post-hoc inspection
  is always available) or only for the session?

---

# Phase 3 — Real Explore + Corpus depth + KB integration

**Status:** ✅ 3a + 3b LANDED — `6735481`. 🟡 3c skeleton landed in same commit; **production KB pipeline body deferred** pending Dan's Linus-side iteration to stabilize. Category-label enrichment re-applied post-rebase at `b45b2aa`.
**Dependencies:** Phase 0.3 (`page-roles-spec`), Phase 0.6 (`kb-integration-spec`)
**PR shape:** the largest single phase; could split into 3a (Explore), 3b (Corpus polish), 3c (KB integration)

## Problem

Four concrete gaps to close:

1. **No asset-discovery surface.** Users need to see what's tradable before
   generating. Oracle prices + market stats exist in the system but aren't
   exposed as a browse experience.
2. **Corpus category labels are inscrutable** to non-finance readers
   (`q-fin.ST`, `q-fin.MF`, etc.). Need plain-English explanations on hover
   or inline.
3. **Graph + Knowledge Graph tabs are Potemkin** — category co-occurrence
   from metadata, explicitly labeled as "pending KB pipeline port."
4. **Corpus Catalog tab lacks the Papers.app-style three-pane chrome**
   (sidebar + table + detail with PDF preview) the user pointed at as the
   target UX.

## Scope — 3a. Real Explore page

### Backend

| File | Change |
|---|---|
| `backend/archimedes/api/explore_routes.py` (NEW) | New router. NOT in `routes.py`. |
| `backend/archimedes/services/asset_market_service.py` (NEW) | Composes oracle prices + 24h/7d/30d history (from `vault_monitor` snapshots OR live oracle reads) + plain-English stat explanations. |
| `backend/archimedes/api/explore_schemas.py` (NEW) | Response models. |

### API contract

```
GET /api/explore/assets
  returns {
    assets: [
      {
        symbol: "sTSLA",
        name: "Tesla Synthetic",
        current_price: 245.30,
        change_24h_pct: 1.2,
        change_7d_pct: -3.1,
        change_30d_pct: 8.7,
        realized_vol_30d: 0.42,
        oracle_address: "0x...",
        last_updated: "2026-05-22T...",
        explanations: {
          realized_vol_30d: "How much the price wobbles. Higher = bigger swings. 0.42 means daily moves of ~2.6% are typical.",
          change_30d_pct: "..."
        }
      },
      ...
    ]
  }

GET /api/explore/assets/{symbol}/history
  returns { symbol, points: [{ ts, price }, ...] }
```

### Frontend

| File | Change |
|---|---|
| `ui/src/components/Explore.jsx` (NEW) | Table of all available assets with sparkline + current stats. Each metric has a `<Tooltip>` showing the plain-English explanation. |
| `ui/src/components/Layout.jsx` | Add `Explore` to NAV between Home and Generate. |
| `ui/src/App.jsx` | Route `/explore` → `<Explore>`. |

### Acceptance (3a)

- [ ] `/api/explore/assets` returns ≥7 assets (the existing synth set: TSLA, NVDA, SPY, BTC, GOLD, OIL, NIKKEI) with real oracle prices
- [ ] Each asset row displays current price + 3 change windows + realized vol
- [ ] Hover/tap on any metric shows a plain-English explanation
- [ ] Page loads in <1s on the live stack (no synchronous on-chain reads — server-side cache w/ 30s TTL)
- [ ] No fake data anywhere; if oracle is stale, the UI explicitly labels the asset "Stale (last update: ...)"

## Scope — 3b. Corpus polish (Catalog + categories)

### Backend

| File | Change |
|---|---|
| `backend/archimedes/services/corpus_categories.py` (NEW) | Static dict mapping arxiv q-fin codes → plain English. Applied at API serialization. |
| `backend/archimedes/api/routes.py` (papers section) | Inject `category_label` field next to `primary_category`. |

### Category mapping (the full list — write into `corpus_categories.py`)

```python
CATEGORY_LABELS = {
    "q-fin.ST":      "Statistical Finance",
    "q-fin.MF":      "Mathematical Finance",
    "q-fin.CP":      "Computational Finance",
    "q-fin.RM":      "Risk Management",
    "q-fin.PM":      "Portfolio Management",
    "q-fin.TR":      "Trading & Market Microstructure",
    "q-fin.GN":      "General Finance",
    "q-fin.PR":      "Pricing of Securities",
    "q-fin.EC":      "Economics (within q-fin)",
    "cs.LG":         "Machine Learning",
    "cs.CL":         "Natural Language Processing",
    "cs.CE":         "Computational Engineering / Finance",
    "stat.ME":       "Statistical Methodology",
    "stat.ML":       "Machine Learning (statistics)",
    "stat.AP":       "Applied Statistics",
    "math.OC":       "Optimization & Control",
    "math.PR":       "Probability",
    "econ.GN":       "General Economics",
    "econ.EM":       "Econometrics",
    "physics.soc-ph": "Social Physics (econophysics)",
    "quant-ph":      "Quantum Methods",
}
```

### Frontend — Papers.app-style Catalog tab

| File | Change |
|---|---|
| `ui/src/components/CorpusExplorer.jsx` | Replace `CatalogTab` body with three-pane layout: left=categories+saved-lists, middle=paper table sorted by year DESC (default) or citation count, right=detail pane. Default tab order: Catalog → Overview → Graph → KG. |
| `ui/src/components/PaperDetailPane.jsx` (NEW) | Detail pane component used in Catalog (right pane) AND in the existing PaperDetail route. Single source of truth for detail rendering. |
| `ui/src/components/CorpusSidebar.jsx` (NEW) | Left pane: category tree, saved-paper lists (localStorage-backed for v1), recently-viewed. |

### Acceptance (3b)

- [ ] Hover any q-fin category badge → tooltip shows plain English
- [ ] Catalog tab default sort: year DESC; sortable by citations + year
- [ ] Detail pane (right) shows abstract + arxiv link + PDF preview thumbnail + "cited by N strategies" + "save" button
- [ ] Saved papers persist across reloads (localStorage)
- [ ] Catalog is the default tab on `/corpus` (was Overview)

## Scope — 3c. KB pipeline integration (full + scheduled)

### Backend

| File | Change |
|---|---|
| `backend/archimedes/scripts/run_kb_pipeline.py` (NEW) | Operator-triggered wrapper that invokes the `submodules/KnowledgeBase/papers_analysis/` pipeline on the current corpus + writes outputs. No re-implementation. |
| `backend/archimedes/services/kb_runner.py` (NEW) | Standalone process loop mirroring `chain/oracle_runner.py`. Polls for "needs re-run?" condition. Default interval: 6h; default trigger: ≥100 new papers since last run OR 7 days elapsed. |
| `backend/archimedes/models/kg.py` (NEW) | `KGEntity` + `KGRelation` ORM models. New tables `kg_entities`, `kg_relations`. |
| `backend/archimedes/db.py` | Add idempotent ALTER TABLE for new KG tables (pattern matches the existing `papers.cluster_id` patch). |
| `backend/archimedes/api/corpus_routes.py` (NEW) | New router for `/api/papers/corpus/graph` + `/kg` — replaces the metadata-derived stubs in `routes.py`. NOT in `routes.py`. |
| `docker-compose.yml` | New service `kb-runner` mounting the corpus + artifact volume. |

### kb_runner.py outline

```python
"""Periodic KnowledgeBase pipeline runner.

Mirrors chain/oracle_runner.py's pattern: standalone python -m loop.
Polls corpus state, runs full pipeline when trigger conditions met.
"""

import os, time, logging
from datetime import datetime, timezone
from archimedes.scripts.run_kb_pipeline import run_pipeline
from archimedes.db import get_session
from archimedes.models.corpus_store import PaperRecord, CorpusMetaRecord
from archimedes.services.redis_state import AgentStateStore

INTERVAL_SECONDS = int(os.getenv("KB_RUNNER_INTERVAL_SECONDS", "21600"))  # 6h
NEW_PAPER_THRESHOLD = int(os.getenv("KB_NEW_PAPER_THRESHOLD", "100"))
MAX_DAYS_SINCE_LAST = int(os.getenv("KB_MAX_DAYS_SINCE_LAST", "7"))

def needs_rerun():
    # last_run_ts + new_paper_count from CorpusMetaRecord or Redis
    ...

def main():
    while True:
        try:
            if needs_rerun():
                run_pipeline()
        except Exception as exc:
            logger.exception("kb pipeline failed: %s", exc)
        time.sleep(INTERVAL_SECONDS)
```

### Frontend — Graph + KG tabs

| File | Change |
|---|---|
| `ui/src/components/CorpusGraph.jsx` (extracted from `CorpusExplorer.jsx`) | Render real SPECTER2-similarity graph from `/api/papers/corpus/graph`. Force-directed layout. Color by cluster_id. |
| `ui/src/components/CorpusKG.jsx` (extracted from `CorpusExplorer.jsx`) | Render REBEL/SciSpacy KG subgraph from `/api/papers/corpus/kg?entity=...`. |

### Acceptance (3c)

- [ ] First-run: `python -m archimedes.scripts.run_kb_pipeline` completes on the 10k corpus, populates `PaperRecord.cluster_id` + `topic_label` for ≥95% of papers, writes embeddings.npy + ids.json to the artifact volume
- [ ] `kb-runner` container starts via docker-compose; logs "needs_rerun: False" on a fresh DB after first run
- [ ] `/api/papers/corpus/graph` returns real SPECTER2-similarity edges (not metadata co-occurrence)
- [ ] `/api/papers/corpus/kg?entity=momentum` returns real REBEL/SciSpacy entities + relations
- [ ] Graph tab renders force-directed graph with cluster-color nodes
- [ ] KG tab renders queryable subgraph
- [ ] **The "(metadata-derived) placeholder" copy is gone from both tabs**
- [ ] Total disk for KB artifacts in the named volume: documented in the spec (probably 1-5 GB for 10k papers)

## Anti-goals (whole Phase 3)

- Do NOT re-implement KB algorithms — invoke the submodule's existing code.
- Do NOT block the API on KB pipeline runs — pipeline runs in a separate
  container with its own resources.
- Do NOT remove the existing Overview tab — it's the only thing that works
  today; demote it to second-tab position behind Catalog.
- Do NOT add fake data to Explore. If oracle is stale, label it stale.
- Do NOT make the Explore page wallet-gated. Browsing assets is a no-wallet
  surface per `page-roles-spec`.

## Suggested owner

**3a Explore lead:** Önder (asset stats math is in his lane) + you (Dan) for plain-English explanation copy.

**3b Corpus polish lead:** anyone with frontend bandwidth; small UI change.

**3c KB integration lead:** Dan (you own KnowledgeBase). The pipeline is your
code; landing it on our corpus is mostly wiring + persistence schema.

**Reviewers:** Marten (the new docker-compose service touches infra he might
have opinions on), Chuan (the new tables go through `init_db`'s ALTER pattern
he established).

## Phase 3 open questions for team

- Explore: should "deposit" or "trade" CTAs appear here, or strictly read-only with "go to Generate to use these"?
- KB: what's our disk budget for the artifact volume in production? (10k papers × ~300 KB/paper = ~3 GB embeddings; KG output is smaller.)
- KB: do we want to expose a "regenerate KG for these papers" button (manual trigger) or rely entirely on the runner?
- KB: SPECTER2 inference may need GPU for reasonable throughput on 10k papers. CPU-only first run is acceptable but slow (estimate: hours). Worth confirming we have a CPU-acceptable path before scheduling.

---

# Phase 4 — Vault encapsulation (1:1, time-bound)

**Status:** PENDING ALIGNMENT — needs Chuan + Marten review before kickoff
**Dependencies:** Phase 0.1 (vault-semantics), Phase 0.2 (strategy-lifecycle), Phase 2 (generated strategies exist)

## Problem (high-level)

The Library has Generated strategies but no Deploy action — they remain
pre-backtest hypotheses with no path to execution. The multi-asset NAV vault
contract exists on chain (Day-10 deploy) but no UI surface creates one. We
also need a real Strategy Passport route (`/strategy/:id`) that users land on
from the Library row + the Generate result.

## Scope (sketch — to be refined post-alignment)

### Backend

- `backend/archimedes/api/vault_routes.py` (NEW) — extracted from `routes.py` per cross-cutting principle #2. Endpoints: `POST /api/vaults/create-from-strategy`, `GET /api/vaults/{address}`, `GET /api/vaults/{address}/lifecycle-state`.
- `backend/archimedes/services/vault_lifecycle.py` (NEW) — manages the state transitions defined in `strategy-lifecycle-spec.md`. Triggers state changes on time + on-chain events.
- `backend/archimedes/services/vault_service.py` — extend with strategy-binding metadata + trade-window enforcement.

### Frontend

- `ui/src/components/StrategyPassport.jsx` (NEW) — `/strategy/:id` route. Full passport per `docs/specs/strategy-passport-spec.md`. Includes "Deploy as Vault" CTA.
- `ui/src/components/CreateVaultModal.jsx` (NEW) — minimal flow: name + trade window (start/end, defaulted from strategy) + initial deposit. Two-step signing (USDC approve + Vault create).
- `ui/src/components/Portfolio.jsx` — group vaults by lifecycle state per spec 0.2.
- `ui/src/components/StressScenarioPanel.jsx` (NEW) — wires `stress_engine.py` output into Portfolio per Marten's survey gap #13.

## Acceptance criteria (sketch)

- [ ] From Library Generated tab, click strategy row → `/strategy/:id` passport loads
- [ ] "Deploy as Vault" → modal collects name + window + deposit
- [ ] On submit: wallet signs USDC approve, then Vault create via VaultFactory, then deposit, then setTargetAllocations
- [ ] Vault appears in Portfolio under "Pending" until window-open time
- [ ] At window-open, vault transitions to "Active"; agent runner performs initial allocation
- [ ] At window-close, vault transitions to "Completed"; user can withdraw
- [ ] `stress_engine.stress_all(vault_address)` results render in Portfolio scenario panel

## Open alignment questions (Marten + Chuan)

These are the things that need team sign-off before this phase starts:

1. **Does the multi-asset NAV vault contract already support "trade window"
   semantics natively, or do we enforce window externally (off-chain agent
   refuses to act outside window)?** — Chuan
2. **Vault creation flow: does VaultFactory.createVault need new args for
   strategy_id + trade_window, or is metadata sufficient?** — Chuan
3. **State transitions: who owns the transition trigger?** — Marten + Chuan.
   Options: (a) agent runner polls + transitions; (b) on-chain events drive
   off-chain state; (c) dedicated `vault_lifecycle.py` worker.
4. **Withdrawal semantics at vault completion:** does the user manually
   withdraw, or is it automatic + returned to wallet?
5. **What does "Active" actually mean for the agent runner?** Currently the
   runner does perpetual rebalancing. In the 1:1 time-bound model, "Active"
   means executing the strategy's trade plan. Does the strategy carry the
   trade plan, or does the agent construct it on `setTargetAllocations`?

## Suggested owner (pending alignment)

**Lead:** Chuan (chain integration) + Marten (vault/contract glue).
**Frontend:** whoever has bandwidth — but the modal + passport route are
small.

---

# Phase 5 — Real testnet trade execution

**Status:** PENDING ALIGNMENT — needs Chuan kickoff
**Dependencies:** Phase 4 (Vault encapsulation must land first); Phase 0.4 (portfolio-constructor-decision-tree)

## Problem (high-level)

The on-chain trade execution path (deposit → `setTargetAllocations` → AMM
swap) is wired in code (`agent_runner.py` + `chain/executor.py`) but has not
been verified end-to-end through the UI on Arc testnet with a real wallet.
Until this happens, we don't actually know if the rebalance path executes.

## Scope (sketch)

- Verify `bootstrap-liquidity` endpoint produces AMM pools with sufficient liquidity for the synth assets the agent would pick
- Implement (or verify existing) "Deploy" UX: user signs USDC approve, vault create, deposit, `setTargetAllocations` — each as a discrete signed step with progress visible
- Verify the agent runner's tick picks up the new vault, computes target allocations, calls `executor.rebalance()`, AMM swap executes, oracle updates reflect new state
- Capture full trace of one happy-path execution and anchor it on-chain via `ReasoningTraceRegistry`

## Acceptance criteria (sketch)

- [ ] One end-to-end signed execution from a real MetaMask wallet results in: (a) USDC deposited to vault, (b) vault holds synth tokens, (c) on-chain trace anchored, (d) Portfolio reflects state
- [ ] Trace is verifiable via `/api/traces/{id}/verify` returning is_verified=true
- [ ] Documented runbook for re-running the test (e.g., "after each contract redeploy")

## Open alignment questions (Chuan primary)

1. **Is the agent runner currently signing transactions, and if so, how is its key managed?** (Circle managed wallet? local key?)
2. **Are the deployed AMM pools liquid enough for a realistic trade size?** (`bootstrap_vaults.py` provides initial seeding; need to verify post-deploy state.)
3. **`setTargetAllocations` semantics:** does the vault execute swaps synchronously on this call, or is there a separate `rebalance` step?
4. **Gas / USDC-as-gas on Arc testnet:** any setup the user needs beyond `Connect Wallet`?

## Suggested owner

**Lead:** Chuan (his lane). Marten supports.

---

# Phase 6 — Onboarding tour (MetaMask-style cards)

**Status:** ✅ LANDED — PR #134 (`60d1ee5`), merged to `main` via `e2ee2a5`.
6-card MetaMask-style tour with localStorage gate + `?` topbar re-launch.
Phase 8 spec covers two follow-ups (transparency fix on card background, remove the in-card navigation buttons that escape the tour).
**Dependencies:** Phase 1 (junk clean so cards don't reference dead surfaces)

## Problem (high-level)

First-time visitors (judges, prospective users) don't know what Archimedes
is, what each page does, or how the parts compose. Wallet-gated demos are
worse without orientation.

## Scope (sketch)

- 6-card modal, MetaMask-style with pagination dots, illustrations, Continue/Skip
- Card content:
  1. **What is Archimedes?** — Research-grounded strategy generation; not a robo-advisor
  2. **Browse the corpus** — 10k bleeding-edge academic q-fin papers feed every strategy
  3. **Generate a strategy** — Describe what you want; the agent fuses papers into a hypothesis
  4. **Inspect the reasoning** — Every decision is hashed and verifiable on Arc
  5. **Deploy as a vault** — Time-bound execution; you control the window
  6. **Watch the agent work** — Monitor portfolio + reasoning traces over time
- Modal triggers on first visit (localStorage flag); "?" button in topbar re-launches anytime
- "Skip" works on every card; "Continue" advances; closing without finishing remembers position

## Acceptance criteria (sketch)

- [ ] First-time visit → modal appears
- [ ] Dismissed state persists across reloads
- [ ] "?" topbar button reopens the tour
- [ ] Each card links to its associated page (final CTA on each: "Go to <Page>")

## Suggested owner

**Lead:** Daniel R. (frontend specialty) or anyone with bandwidth.

## Open alignment questions

- Do we want to hire an illustrator or use simple SVG diagrams for the
  card visuals? (Recommended: simple SVGs we can author in-repo.)
- Should the tour be wallet-aware? (E.g., card 5 might say "Connect wallet to
  deploy" when no wallet is connected.)

---

# Cross-cutting: routes.py monolith discipline

Per cross-cutting principle #2 — every new endpoint in Phases 2-5 lands in a
dedicated router. Specifically:

| New router | Phase | Endpoints |
|---|---|---|
| `generate_routes.py` | 2 | `/api/generate/start`, `/api/generate/stream/{id}`, `/api/generate/jobs`, `/api/generate/jobs/{id}/candidates` |
| `explore_routes.py` | 3a | `/api/explore/assets`, `/api/explore/assets/{symbol}/history` |
| `corpus_routes.py` | 3c | `/api/papers/corpus/graph`, `/api/papers/corpus/kg` (move from `routes.py`) |
| `vault_routes.py` | 4 | `/api/vaults/*` (move from `routes.py` or add new) |

This is non-negotiable — Marten's gap cluster #6 is real and getting worse
every phase. Holding the line keeps the next person sane.

---

# Open questions consolidated (for team standup)

Phase 0 questions:
- Strategy expiry TTL: how many hours before un-deployed → Expired?
- Vault re-use: ever, never, or only same-strategy?
- KB scheduler: docker-compose service or host cron?

Phase 1 questions:
- Off-chain failure: retry button or just log + move on?
- Learnings: hide until ready (recommended) or stub-with-real-empty-state?

Phase 2 questions:
- N candidates per request: fixed at 3? Configurable?
- Architect "fast preview" copy?
- Rejected candidates persistence: session or long-term?

Phase 3 questions:
- Explore CTAs: read-only or include deposit/trade buttons?
- KB disk budget in production?
- KB regenerate-on-demand button: yes/no?
- KB CPU-only first-run acceptable?

Phase 4 questions (Chuan + Marten):
- Trade window: contract-native or off-chain enforced?
- VaultFactory.createVault args for strategy_id + window?
- State-transition trigger ownership?
- Withdrawal semantics at completion?
- Agent's "Active" role with time-bound strategies?

Phase 5 questions (Chuan):
- Agent signing setup?
- AMM liquidity post-deploy?
- `setTargetAllocations` synchronous or two-step?
- USDC-as-gas setup steps?

Phase 6 questions:
- Illustrations: hire or in-repo SVG?
- Wallet-aware card content?

---

# Phase 7 — Consolidation & dedup via t2o2

**Status:** ✅ LANDED — all 6 issues closed (#128, #129, #130, #131, #132, #133).
PR #136 (merge `2a5f319`) closed remaining gaps on #128/#130/#133:
honest fusion rigor + real bar-by-bar equity curve, LLM-backend guard test,
fusion rigor verdict persistence + `rejected` status.

**Why this phase exists:** [`docs/chuan-architecture-survey.md`](../chuan-architecture-survey.md)
identifies 14 gap clusters in `backend/archimedes/`. Of those, gaps #1, #2,
#3, #5, #6 are *technical-debt cleanup* — well-bounded, mechanical, with
clear acceptance criteria. They are the exact shape t2o2 executes well
(per CLAUDE.md's "agentic issue pipeline" section). Hand-implementing
them would burn hosted-Claude budget on work t2o2 can do for ~free.

**Phase 7 deliverable: 4 judge-grade issues filed + assigned to t2o2.**

| # | Issue | Survey gap | Spec file | Owner-reviewer |
|---|---|---|---|---|
| 7.1 | Fusion-output → backtestable DSL | (the wedge) | [`fusion-to-backtest-t2o2-issue.md`](fusion-to-backtest-t2o2-issue.md) — filed as **#128** | Dan + Daniel R. |
| 7.2 | Rigor consolidation on `rigor_evaluator.py` | #1 | [`phase7-rigor-consolidation-t2o2-issue.md`](phase7-rigor-consolidation-t2o2-issue.md) | Önder |
| 7.3 | LLM backend unification on `llm_backend.py` | #3 | [`phase7-llm-backend-unification-t2o2-issue.md`](phase7-llm-backend-unification-t2o2-issue.md) | Daniel R. / Dan |
| 7.4 | Portfolio constructor retirement | #5 | [`phase7-portfolio-constructor-retirement-t2o2-issue.md`](phase7-portfolio-constructor-retirement-t2o2-issue.md) | Önder |
| 7.5 | `routes.py` monolith split | #6 | [`phase7-routes-py-split-t2o2-issue.md`](phase7-routes-py-split-t2o2-issue.md) | Chuan / Daniel R. |

**Suggested ordering** (t2o2 PRs land serially to avoid import-path conflicts):

```
7.1 (#128 fusion-DSL)    — already filed, parallel-OK
    │
7.2 (rigor consolidation) — must land before 7.1 PR merges
    │
7.3 (LLM backend)         — independent
    │
7.4 (constructors)        — depends on 7.2's import path
    │
7.5 (routes.py split)     — last; touches the most files; depends on 7.2-7.4 settling
```

**Survey gaps not covered by Phase 7 (treatment notes):**

- **#2 Regime detector duplication** — spec not yet drafted because the
  call-site mapping needs Önder's input (which detector is wired to
  `RegimePanel`?). File as a t2o2 issue once Önder confirms. **Open
  question for next standup.**
- **#4 Arxiv intake paths** — three parallel paths (`arxiv_corpus.py`,
  `corpus_service.py`, `scripts/bulk_ingest_arxiv.py`). Adjacent to the
  KB pipeline work Dan is iterating on in Linus; consolidate when that
  stabilizes. **Defer.**
- **#8 marketplace_service.py seed-data** — Chuan already pruned 607 lines
  Day-10; the remaining wiring to real strategy data is small. File as
  a t2o2 issue when Phase 7.5 (routes split) settles, since both touch
  marketplace surfaces.
- **#9 Scheduled corpus intake / artifact build** — Phase 3c's `kb_runner.py`
  is the pattern. When the KB pipeline production body lands, the same
  pattern extends to `corpus_service.intake_from_arxiv()`. **Fold into
  Phase 3c completion.**
- **#10 Operational scripts → Makefile** — quality-of-life, not blocking.
  File a small t2o2 issue when convenient. Low priority.
- **#12 `circle_service.py` is judge-oriented** — by design (rubric surface).
  No action.
- **#13 `stress_engine.py` not wired to UI** — Phase 4 candidate (Portfolio
  page would surface the scenario table). Hold for Phase 4.

**Phase 7 acceptance** (when fully filed + merged):

- All four t2o2 PRs merged to `main`.
- `pytest -q` green (307+ passed, no new flakes).
- API URL set unchanged (no frontend impact).
- One Open Questions item resolved per gap; survey doc refreshed.

---

# Time tracking template

Update this table at phase close. No prospective estimates.

| Phase | Started | Completed | Hours actual | Notes |
|---|---|---|---|---|
| 0 — Specs | 2026-05-22 | 2026-05-22 | ~2.5 | 6 specs · `5fc2eb9` |
| 1 — Junk | 2026-05-22 | 2026-05-22 | ~2 | `f21ac8d` + `00d8f09` |
| 2 — Streaming Generate | 2026-05-22 | 2026-05-22 | ~4 | `28dd93a` (+1495/-20) |
| 3a — Explore | 2026-05-22 | 2026-05-22 | ~1.5 | part of `50edb28` |
| 3b — Corpus polish | 2026-05-22 | 2026-05-22 | ~0.5 | part of `50edb28` |
| 3c — KB integration (skeleton) | 2026-05-22 | 2026-05-22 | ~1.5 | part of `50edb28`; production body deferred |
| Phase 2 follow-ups | 2026-05-22 | 2026-05-22 | ~2 | `7b1c1e3` — cancel/rigor/brief-validation |
| Phase 7 specs drafted | 2026-05-22 | 2026-05-22 | ~1 | 4 issue specs in `docs/specs/`; #128 filed |
| 6 — Onboarding | 2026-05-22 | 2026-05-22 | ~1.5 | `60d1ee5` · PR #134 (6-card tour + localStorage + ? re-launch) |
| 7 — Dedup via t2o2 | 2026-05-22 | 2026-05-23 | ~0.5 (humans) + bot | All 6 issues closed; PR #136 closed follow-up gaps |
| 8 — Landing + UX polish (PENDING) | | | | Spec at [`phase8-9-landing-and-fusion-spec.md`](phase8-9-landing-and-fusion-spec.md) |
| 9 — Fusion UI surface (PENDING) | | | | Spec at [`phase8-9-landing-and-fusion-spec.md`](phase8-9-landing-and-fusion-spec.md) |
| 4 — Vault (PENDING) | | | | needs Marten + Chuan alignment |
| 5 — Real trade (PENDING) | | | | needs Chuan alignment |

---

# Suggested kickoff sequence (refreshed 2026-05-23)

When the next session resumes:

1. Read this doc + [`docs/chuan-architecture-survey.md`](../chuan-architecture-survey.md) for context.
2. Confirm branch hygiene: are we on `dbrowneup/spine-plus-v2`? Rebase onto latest `origin/main` (moves continuously).
3. **Phases 0, 1, 2, 3a, 3b, 6, 7 are LANDED.** Phase 3c is skeleton-only (KB body deferred). `pytest -q` should be green.
4. **Next: Phases 8 + 9** per [`phase8-9-landing-and-fusion-spec.md`](phase8-9-landing-and-fusion-spec.md):
   - Phase 8 first — mechanical (~30 min of edits): Landing CTA fixes + wallet button + RegimePanel dedup + Onboarding card opacity + Corpus Catalog cards→table + design polish.
   - Phase 9 second — Fusion engine UI surface as a third Generate mode toggle.
5. **Phase 4 + 5** — still pending Marten / Chuan alignment on open questions. If implementation proceeds without alignment, flag risk in PR description so they can course-correct on review.
6. **KB pipeline production wiring** still waits on Dan's Linus-side iteration to settle.

---

# Document history

- **2026-05-22** — Initial draft (Dan + Claude session). Locked decisions:
  vault semantics (time-bound, 1:1), generation UX (streaming), KB scope
  (full + scheduled), phase order (2→3→4→5→6). Phases 4-6 explicitly
  pending team alignment.

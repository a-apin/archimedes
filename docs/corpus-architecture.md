# Corpus architecture — how it's built, stored, and fused

> **Audience:** anyone on the team (or a curious judge) who needs to understand how
> the q-fin paper corpus actually works end-to-end.
> **Purpose:** the single page that explains the substrate. Sits alongside
> [`docs/specs/strategy-passport-spec.md`](specs/strategy-passport-spec.md) and
> [`docs/specs/selection-bias-corrections-spec.md`](specs/selection-bias-corrections-spec.md);
> together they describe the three load-bearing intelligence layers of Archimedes.
> **Status:** Day-9 (2026-05-20). Reflects what's actually shipped on `main` after
> #95 (engine v2), #97 (10k corpus), #105 + #108 (rigor wedge), #106 (DB-backed
> corpus), and #93 (Corpus Explorer UI).

## The substrate (3 layers)

```
┌──────────────────────────────────────────────────────────────────┐
│  LAYER 1 — SEED (committed, deterministic)                       │
│  data/corpus/manifest.jsonl                                      │
│  Curated arXiv metadata snapshot — ships with the repo           │
│  Boot-time floor. 10,000 papers, 14 MB. No network required.     │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼ (idempotent upsert at every startup)
┌──────────────────────────────────────────────────────────────────┐
│  LAYER 2 — TRUTH (Postgres, mutable)                             │
│  papers table (PK = arxiv_id) + corpus_meta singleton            │
│  Lives in pgdata named volume — survives redeploys               │
│  Grown by: seed re-runs + arxiv intake + bulk ingest script      │
│  Indexes on primary_category + published                         │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼ (read by fusion + /api endpoints)
┌──────────────────────────────────────────────────────────────────┐
│  LAYER 3 — HEAVY ARTIFACT (persistent volume, lazy)              │
│  archimedes-corpus-artifact volume → embeddings, clusters,       │
│  knowledge graph, summaries. Built out-of-band by the #101       │
│  KB-pipeline port (which is scaffolded but not yet run).         │
│  Loud-degradation: app boots fine if missing.                    │
└──────────────────────────────────────────────────────────────────┘
```

Three layers, three distinct lifetimes:

- **Layer 1 — Seed** is what git remembers. Determinism. Floor.
- **Layer 2 — Truth** is what Postgres remembers. Mutable. What the app actually queries.
- **Layer 3 — Artifact** is what a persistent docker volume remembers. Heavy.
  Built once, served many times.

The seed is the *recipe*; the truth is the *kitchen*; the artifact is the *prep*. You
can rebuild any layer from the one above it (with the right time investment), but in
steady state you only touch each at the cadence it deserves.

## Why two write paths (seed vs intake)

Same code touches `papers`; different reasons to run.

| | Seed (manifest) | Intake (arxiv) |
|---|---|---|
| **Trigger** | Every startup (`d80fca3` made it always-run, not just empty-DB) | Currently manual; bulk script `scripts/bulk_ingest_arxiv.py` |
| **Source** | Committed JSONL in repo | Live arXiv Atom API (https since `a080724`) |
| **Purpose** | Bootstrap + version-bump the corpus deterministically | Keep it growing/fresh |
| **Net effect** | DB matches what's in git | DB outgrows what's in git |
| **Idempotent?** | Yes — upsert by `arxiv_id` | Yes — dedup by `arxiv_id` |
| **Network needed?** | No | Yes |
| **Throttling?** | N/A | Polite Atom pulls; exponential backoff for 429s |

This is the right shape:
- **The manifest is the floor.** Anyone cloning gets a working corpus instantly.
- **The DB is the truth.** What the running system actually queries.
- **The intake is the growth path.** Operator decides cadence.

A spec gap worth knowing: `intake_from_arxiv()` is callable but **no periodic task
wires it up automatically**. Today, an operator runs the script when they want
freshness. The spec called for an in-process periodic task; adding one is ~10 lines.
Tracked as a fast-follow under #106.

## Why local and deployment differ

**They don't differ much architecturally — the difference is what survives a restart.**

| | Local dev | Production deployment |
|---|---|---|
| Postgres | docker-compose `pgdata` volume | Same `pgdata` volume on the EC2 host |
| Artifact | `archimedes-corpus-artifact` named volume | Same named volume on the host |
| Manifest | Committed file, always present | Same committed file, baked into image |
| Seed runs | On `docker compose up`, every boot | On every deploy/restart, every boot |
| Intake runs | When you manually invoke `python scripts/bulk_ingest_arxiv.py` | Same — operator-triggered until a scheduler is added |

The differences that *do* exist:

1. **Ephemeral by accident locally.** If you run `docker compose down -v`, your
   `pgdata` and artifact volumes get wiped, and the next boot reseeds from the
   manifest. In prod the host volumes persist across redeploys — you'd only lose
   data if you explicitly deleted them. **Same code, different blast radius.**
2. **Network latency to arXiv.** Negligible — intake is throttled either way.
3. **The 10,000-row corpus** (post #97) was built by running
   `scripts/bulk_ingest_arxiv.py` once, with results committed to `manifest.jsonl`.
   So both environments start at 10,000 on first boot. Subsequent growth requires
   running intake again.

That's it. There's no "production magic" — same code, same DB, same artifact volume
contract. Cold-clone parity is `docker compose down -v && docker compose up`; expect
`/health` to report `corpus_db_count == 10000` once startup completes.

## What a paper actually contains

Schema lives in [`backend/archimedes/models/corpus_store.py`](../backend/archimedes/models/corpus_store.py). Each `PaperRecord` row carries:

- **Identity:** `arxiv_id` (PK), `title`, `authors` (JSON list), `primary_category`,
  `categories` (JSON list)
- **Content:** `abstract` (always present — "abstract-first")
- **Provenance:** `pdf_url`, `pdf_sha256` (nullable, populated when full text is
  fetched), `full_text_path` (nullable — lazy)
- **Clustering (for #101 future use):** `cluster_id`, `topic_label` (both
  nullable — populated by the KB pipeline when it runs)
- **Timestamps:** `published`, `updated`, `ingested_at`
- **Bookkeeping:** `source`, `content_hash`

Plus a `CorpusMetaRecord` singleton: `last_intake_at`, `corpus_hash`, `artifact_hash`,
`artifact_built_at`, `paper_count`, `source` — all exposed via `/health` so operators
can see corpus state at a glance.

## How a paper becomes part of a strategy (the fusion path)

This is the chain you most need to understand, because **"research-grounded"** is the
entire marketing wedge.

```
User describes intent in the UI
        │
        ▼
POST /api/strategies/generate           (engine v2, Chuan #95)
        │
        ▼
FusionBrief built
   (user intent + asset class + risk tolerance + time horizon)
        │
        ▼
strategy_fusion.load_corpus()
        │  ├─→ Try Postgres `papers` table (DB-first since #106)
        │  └─→ Fallback to manifest.jsonl on disk (defensive)
        │
        ▼
3-INPUT FUSION
   ┌────────────────────────────────────────────────────────────┐
   │  Input 1: User brief (what they want)                       │
   │  Input 2: Live market regime (current vol, asset behavior)  │
   │  Input 3: Corpus papers (research evidence)                 │
   └────────────────────────────────────────────────────────────┘
        │
        ▼
Paper selection
   ⚠️ CURRENT: keyword matching against title + abstract
   🔜 FUTURE (#96): SPECTER2 embeddings + RAG + minimal KG
        │
        ▼
LLM call (GLM, with citations injected into prompt)
        │
        ▼
Candidate strategy spec (entry/exit rules, sizing, asset universe)
        │
        ▼
RIGOR GATE (Önder's work, PR #105 + #108)
   ├─ DSR (Deflated Sharpe Ratio, Bailey-LdP eq. 8 raw kurtosis)
   ├─ PBO (Probability of Backtest Overfitting via CSCV)
   ├─ Kelly criterion (sizing sanity)
   ├─ MVO (Mean-Variance Optimization, portfolio context)
   └─ Trade-count rule (post-#108: <2 OR ≥10, exempting always-on)
        │
        ▼
Either PASSES → stored in StrategyStore with is_example=False, surfaced in UI
   OR BLOCKS → reasoning trace preserved, strategy not promoted
```

**Crucial framing**: the corpus is **not a fact lookup**. It's *evidence the LLM is
asked to ground its proposal in*. The provenance is what makes a generated strategy
distinguishable from "ChatGPT made up a momentum rule." Every generated strategy can
point at specific papers that informed it. **That's the rigor wedge externalized.**

## What's wired today vs what's not

### Wired and live

- [x] DB-backed `papers` + `corpus_meta` (10,000 rows)
- [x] Idempotent seed at every startup
- [x] `intake_from_arxiv()` function (no scheduler — operator-triggered)
- [x] DB-first read path through `strategy_fusion.load_corpus()`
- [x] Paginated `/api/papers` + `/api/papers/{id}` + `/api/corpus/overview` +
      `/api/corpus/graph` + `/api/corpus/kg`
- [x] `/health` exposes `corpus_papers`, `corpus_db_count`, `corpus_source`,
      `corpus_last_intake`, `artifact_hash`
- [x] Corpus Explorer UI (catalog + overview + similarity graph + knowledge graph)
- [x] Persistent named volume `archimedes-corpus-artifact` mounted in compose
- [x] Rigor gate (DSR + PBO + Kelly + MVO + look-ahead audit) on every generated
      strategy
- [x] 2 Tier-1 strategies passing all four gates (Faber 2007 SMA200,
      Moreira-Muir 2017 vol-managed)

### Scaffolded but not running yet

- [ ] **Heavy artifact pipeline (#101)** — SPECTER2 embeddings, HDBSCAN +
      BERTopic + UMAP clustering, REBEL + SciSpacy knowledge graph, per-cluster
      LLM summaries. Volume is mounted and ready; the build job has not been run.
      The `cluster_id` and `topic_label` columns on `PaperRecord` are nullable,
      ready to be populated.
- [ ] **Paper-QA RAG retrieval (#96)** — current fusion does keyword selection
      against title + abstract. Spec calls for SPECTER2 embeddings + nearest-
      neighbour retrieval + a small entity/method KG. The substrate (#106) and
      the 3-input fusion brief (#95) are both ready; this is now unblocked.

### Deferred (correct scope decisions, not gaps)

- [ ] **Lazy full-text fetch** — `full_text_path` column exists but no fetcher is
      wired. Abstract-only across the board today. Belongs in a retrieval path
      (when fusion picks a paper, fetch the full text on demand), not in the
      substrate.
- [ ] **Scheduler for `intake_from_arxiv()`** — function exists and is callable;
      no periodic task. Filed as a fast-follow under #106 (~10 lines to add an
      `asyncio.create_task` on startup).
- [ ] **Retention/prune at `CORPUS_MAX`** — when DB hits the cap, intake silently
      stops; no eviction. Not biting at 10k (cap default is 2000, currently lifted
      via env to 10k); spec called for recency-prioritized retention if scale
      pushes us past the cap.
- [ ] **`make corpus` / `make build-corpus-artifact` targets** — Makefile has
      no corpus targets. Local↔prod parity (#98) wants these. Trivial follow-on.
- [ ] **Quality-signal columns** — spec called for peer-review / preprint /
      citation-age proxies as features for retrieval, never filters. `PaperRecord`
      has none of these columns. Not blocking the explorer (#93) which renders
      without them.

## Why this architecture

A few decisions worth being explicit about (and being able to defend on stage):

**Manifest as seed, not as truth.** Committing the canonical corpus to git makes
cold-clone determinism cheap and offline dev possible, but it would make the corpus
*static* if we treated it as truth. DB-as-truth + manifest-as-seed lets us grow the
corpus dynamically without losing reproducibility.

**Heavy artifact on a persistent volume, never in the request path.** Embeddings +
clustering + KG are minutes-to-hours to build at 10k+ scale. Computing any of that
on a `/api/corpus/graph` request would be a multi-second hang. Building it once,
serving it many times, and re-building only when the corpus hash changes is the
right shape — and it lets the *build* live in a separate cadence (or even a separate
container) from the *serve*.

**Loud degradation, never silent.** If the artifact volume is empty, the app boots
cleanly, `/health` reports `artifact_hash: null`, the Corpus Explorer's heavy viz
panels degrade *visibly* (the user sees "embeddings not yet built"), and fusion
falls back to keyword retrieval. No silent half-features.

**Abstract-first, full-text lazy.** Abstracts are cheap to store and sufficient for
both the explorer and the SPECTER2 embedding layer (#101). Full PDF text is only
fetched when fusion actually needs it for a specific paper. That keeps the corpus
small enough to commit, fast enough to seed, and rich enough to retrieve.

## See also

- [`backend/archimedes/services/corpus_service.py`](../backend/archimedes/services/corpus_service.py) — the seed + intake implementation
- [`backend/archimedes/services/strategy_fusion.py`](../backend/archimedes/services/strategy_fusion.py) — `load_corpus()` + fusion prompt build
- [`backend/archimedes/models/corpus_store.py`](../backend/archimedes/models/corpus_store.py) — `PaperRecord` + `CorpusMetaRecord` schemas
- [`docs/qfin-paper-corpus-seed.md`](qfin-paper-corpus-seed.md) — the original
  seed-curation spec (largely historical now that #97 expanded to 10k via bulk ingest)
- [`docs/specs/selection-bias-corrections-spec.md`](specs/selection-bias-corrections-spec.md) — what the rigor gate enforces
- [`docs/architectural-principles.md`](architectural-principles.md) — the four
  load-bearing primitives, with the corpus + fusion + rigor + on-chain provenance
  forming the spine
- Issues: [#97 (10k corpus)](https://github.com/hackagora/archimedes-arcadia/issues/97),
  [#106 (DB-backed substrate)](https://github.com/hackagora/archimedes-arcadia/issues/106),
  [#93 (Corpus Explorer UI)](https://github.com/hackagora/archimedes-arcadia/issues/93),
  [#96 (fusion retrieval — open)](https://github.com/hackagora/archimedes-arcadia/issues/96),
  [#101 (KB pipeline port — open)](https://github.com/hackagora/archimedes-arcadia/issues/101)

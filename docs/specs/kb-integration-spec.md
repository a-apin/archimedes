# KB Integration Spec

> **Status:** Drafted 2026-05-22 as Phase 0 of the
> [Spine+ v2 plan](./spine-plus-v2-plan.md). Authoritative for Phase 3c
> implementation.
>
> **Lineage:** Wires the existing
> [`submodules/KnowledgeBase/`](../../submodules/KnowledgeBase/) pipeline
> (Dan's scientific-paper analyzer — PyMuPDF + SPECTER2 + HDBSCAN/BERTopic +
> REBEL/SciSpacy) onto the Archimedes corpus. **No re-implementation.** The
> spec describes how to invoke the existing scripts and where outputs persist.

## Non-goals

- Re-writing the KB pipeline in `backend/archimedes/`. The submodule is the
  reference implementation; we *call into it*, we don't fork it.
- Real-time KB updates. The pipeline runs in batches; live corpus search uses
  the persisted artifacts.
- Replacing the existing `data/corpus/text/` PDF + extract layer. KB consumes
  what's already there.

## KB submodule entry points

From `submodules/KnowledgeBase/papers_analysis/` (submodule not checked out):

| File | Role |
|---|---|
| `extract.py` | PyMuPDF text extraction (we already do this in `scripts/hydrate_corpus.py`; KB's extract is the reference but we don't re-run it). |
| `metadata.py` | Paper-corpus schema — maps to our `papers` table; useful as a column reference. |
| `vectorize.py` | SPECTER2 embeddings — produces `N×768` numpy matrix + paper-id index. **Primary entry for Phase 3c.** |
| `cluster.py` | HDBSCAN density-based clustering on the embeddings. Produces `cluster_id` per paper; BERTopic topic-label path lives here too. |
| `knowledge_graph.py` | REBEL + SciSpacy entity-relation extraction. Produces `(subject, relation, object)` triples per paper. |
| `graph.py` | Aggregates per-paper triples into a corpus-level graph (nodes = entities, edges = relations). |
| `summarize.py` | Ollama-driven summarization. **Skip** — we already summarize via Claude in our own pipeline; KB's summary path is legacy. |
| `stats.py`, `visualize.py` | Auxiliary; not part of production wiring. |

## Pipeline invocation

A new file: `backend/archimedes/scripts/run_kb_pipeline.py`

```python
"""Single-shot KB pipeline runner. Operator-triggered for the first run."""
import sys, json
from pathlib import Path

# Add submodule to sys.path (no install — KB has no setup.py we want to use)
KB_ROOT = Path(__file__).parents[3] / "submodules/KnowledgeBase"
sys.path.insert(0, str(KB_ROOT))

from papers_analysis import vectorize, cluster, knowledge_graph, graph

def run(corpus_text_dir: Path, artifact_dir: Path) -> dict:
    """
    1. Embed every text file in corpus_text_dir via SPECTER2
         → artifact_dir/embeddings.npy + ids.json
    2. Cluster via HDBSCAN
         → artifact_dir/clusters.json {paper_id: cluster_id}
    3. BERTopic topic labels
         → artifact_dir/topics.json {cluster_id: human_label}
    4. KG triples via REBEL+SciSpacy
         → artifact_dir/kg_triples.jsonl
    5. Aggregate into corpus graph
         → artifact_dir/kg_graph.json
    Returns: a manifest dict with counts and the run timestamp.
    """
    ...
```

Run manually for the first pass:
```bash
python -m archimedes.scripts.run_kb_pipeline
# → reads from data/corpus/text/
# → writes to /srv/corpus-artifact/ (docker volume)
```

## Output persistence

KB outputs land in **two places**:

### 1. Named docker volume — `archimedes-corpus-artifact`

Heavy binary artifacts that don't belong in Postgres:

| File | Format | Size estimate (10k papers) | Used by |
|---|---|---|---|
| `embeddings.npy` | NumPy `float32 [N, 768]` | ~30 MB | `/api/corpus/graph` (UMAP on the fly), nearest-neighbor search |
| `ids.json` | JSON `[arxiv_id, ...]` parallel to embeddings | ~500 KB | Embedding row-to-paper lookup |
| `clusters.json` | JSON `{arxiv_id: cluster_id}` | ~500 KB | Bulk-cached fast lookup |
| `topics.json` | JSON `{cluster_id: {label, top_terms: [...]}}` | ~50 KB | Topic display in Corpus Overview |
| `kg_triples.jsonl` | One JSON object per (paper, triple) | ~50 MB | KG search, citation aggregation |
| `kg_graph.json` | Aggregated `{nodes: [...], edges: [...]}` | ~5 MB | KG explorer tab |
| `manifest.json` | `{run_ts, paper_count, cluster_count, kg_node_count, kg_edge_count}` | tiny | Surfaced in `/api/corpus/overview` |

Mounted into the backend container at `/srv/corpus-artifact`. Read-only at
runtime; writable only by the KB runner.

### 2. Postgres `papers` table — denormalized fast-path columns

Added via the existing idempotent `ALTER TABLE` pattern in
[`backend/archimedes/db.py`](../../backend/archimedes/db.py) `init_db()`:

```sql
ALTER TABLE papers ADD COLUMN IF NOT EXISTS cluster_id INTEGER;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS topic_label TEXT;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS content_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_papers_cluster ON papers (cluster_id);
```

Note: these `ALTER TABLE` statements already exist in `db.py` (added during the
strip-to-spine work to unblock `/api/papers/`), but the matching ORM columns on
`PaperRecord` in `backend/archimedes/models/paper.py` (planned; not yet in tree)
are **not yet defined**. Phase 3c adds them to the ORM so they round-trip
correctly.

These columns are denormalized copies of what's in the artifact volume; they
exist so DB-only fast paths (`GET /api/papers/?cluster_id=42`) don't require
artifact mounting on the API container.

### 3. New tables for KG persistence

```sql
CREATE TABLE IF NOT EXISTS kg_entities (
  id SERIAL PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  paper_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE (canonical_name, entity_type)
);
CREATE TABLE IF NOT EXISTS kg_relations (
  id SERIAL PRIMARY KEY,
  subject_id INTEGER REFERENCES kg_entities (id),
  relation TEXT NOT NULL,
  object_id INTEGER REFERENCES kg_entities (id),
  paper_arxiv_id TEXT REFERENCES papers (arxiv_id) ON DELETE CASCADE,
  confidence REAL,
  UNIQUE (subject_id, relation, object_id, paper_arxiv_id)
);
CREATE INDEX IF NOT EXISTS idx_kg_relations_paper ON kg_relations (paper_arxiv_id);
CREATE INDEX IF NOT EXISTS idx_kg_relations_subject ON kg_relations (subject_id);
```

These let us answer "which papers mention X?" and "what does Y do?" without
loading the full graph JSON.

## Scheduled re-runs — `kb_runner.py`

A new long-running service, mirroring
[`backend/archimedes/chain/oracle_runner.py`](../../backend/archimedes/chain/oracle_runner.py)
in shape:

`backend/archimedes/services/kb_runner.py`

```python
"""KB pipeline scheduler. Standalone process; runs in its own container."""
import asyncio, time, json
from pathlib import Path

POLL_INTERVAL_S = 60 * 60        # check hourly
MIN_NEW_PAPERS  = 100            # rerun if N new papers since last run
MAX_DAYS_SINCE  = 7              # ...or N days have passed

async def main():
    while True:
        if should_rerun():
            run_pipeline()        # blocks; that's fine — own container
        await asyncio.sleep(POLL_INTERVAL_S)

def should_rerun() -> bool:
    manifest = load_manifest()    # /srv/corpus-artifact/manifest.json
    new_papers = count_papers_since(manifest["run_ts"])
    days_since = (now() - manifest["run_ts"]).days
    return new_papers >= MIN_NEW_PAPERS or days_since >= MAX_DAYS_SINCE
```

### New docker-compose service

```yaml
kb-runner:
  build: .
  command: python -m archimedes.services.kb_runner
  volumes:
    - archimedes-corpus-text:/srv/corpus-text:ro
    - archimedes-corpus-artifact:/srv/corpus-artifact:rw
  environment:
    - DATABASE_URL=${DATABASE_URL}
    - KB_MIN_NEW_PAPERS=100
    - KB_MAX_DAYS_SINCE=7
  depends_on:
    - postgres
  deploy:
    resources:
      limits:
        memory: 8G
```

The 8 GB limit accommodates SPECTER2 + REBEL model weights (~6 GB combined).
**Does not run on the api container** — the API stays small and responsive.

### Re-run trigger

`(N new papers since last run) ≥ 100  OR  (days elapsed) ≥ 7` — whichever fires
first. Both knobs env-configurable.

### Long-run UX

A KB run on 10k papers takes hours (SPECTER2 ~71 papers/sec; SciSpacy slower).
During a run:

1. `kb_runner` writes `/srv/corpus-artifact/state.json` with
   `{phase: "embedding", progress: 0.4, started_at: ...}`.
2. API exposes `GET /api/corpus/runner-state` reading that file.
3. `/corpus` page surfaces a "Pipeline running — phase: embedding (40%)" banner.
4. Existing artifact (previous run's outputs) stays mounted — corpus continues
   to serve the **previous** snapshot until the new run completes atomically
   (write-to-tmpdir + symlink-swap on success).

## API endpoints (Phase 3c)

Land in new `backend/archimedes/api/corpus_routes.py` (per cross-cutting
principle #2 — no new routes go into `routes.py`):

| Endpoint | Returns |
|---|---|
| `GET /api/corpus/overview` | `{paper_count, cluster_count, top_topics: [...], last_run_ts, kb_pipeline_state}` |
| `GET /api/corpus/graph` | UMAP 2D projection of embeddings; `{points: [{arxiv_id, x, y, cluster_id}], topics: {...}}` |
| `GET /api/corpus/kg/entities?q=` | Search entities by canonical name |
| `GET /api/corpus/kg/entity/{id}` | Entity detail + adjacent relations |
| `GET /api/corpus/kg/paper/{arxiv_id}` | All triples for one paper |
| `GET /api/corpus/runner-state` | KB pipeline phase + progress |

Existing `GET /api/papers/` and `GET /api/papers/:arxiv_id` are unchanged but
now return populated `cluster_id` / `topic_label` columns.

## Frontend wiring (Phase 3b + 3c)

Per [`page-roles-spec.md`](./page-roles-spec.md), `/corpus` has four tabs:

1. **Catalog** (Phase 3b — paper list, no KB dependency)
2. **Overview** (Phase 3c, uses `/api/corpus/overview`)
3. **Graph** (Phase 3c, uses `/api/corpus/graph`)
4. **Knowledge Graph** (Phase 3c, uses `/api/corpus/kg/*`)

Phase 3b ships first because it works without KB; 3c lights up Tabs 2-4 once
the first KB run completes.

## Failure modes

| Failure | Behavior |
|---|---|
| KB run crashes mid-pipeline | Atomic-swap protects the previous snapshot. Failed run logged; runner sleeps then retries on next poll. |
| Artifact volume corruption | `/api/corpus/runner-state` reports `phase: error`; UI banner reads "Corpus artifact corrupted; KB pipeline will re-run". Manual trigger: `docker compose restart kb-runner`. |
| Submodule out-of-sync | KB pipeline pinned by submodule SHA. `git submodule update` is operator-triggered, never automated, so behavior changes are reviewable. |
| New paper has no PDF/text | Skipped by `extract.py` upstream; KB never sees it. Surfaces as a NULL `cluster_id` row in Library. |
| Embedding model download fails | KB container fails health check; alert via docker-compose. Doesn't affect API. |

## Acceptance

A Phase 3c implementer (Dan + Daniel R.) can:

- Identify which KB submodule entry point to call for each pipeline stage.
- Set up the docker-compose service without re-deriving the env contract.
- Wire the `/api/corpus/*` endpoints to the right artifact files.
- Know what UI state to render during a long-running pipeline pass.

## Open questions

1. **`kb_runner` as docker service or host cron?** Docker service matches the
   existing pattern (oracle_runner) and is portable; host cron is simpler.
   Recommendation: docker service.
2. **First-run trigger semantics** — auto on first deploy, or operator-only?
   Recommendation: operator (`docker compose run --rm kb-runner python -m
   archimedes.scripts.run_kb_pipeline`) so the GPU/RAM spike is intentional.
3. **Rollback story for a bad pipeline run** — atomic swap covers "this run
   crashed"; the harder case is "this run produced bad clusters silently."
   Recommendation: keep last 3 snapshots; expose `KB_SNAPSHOT_TS` env for
   emergency pin.
4. **KG entity canonicalization** — REBEL outputs raw spans, but "TSMOM" and
   "time-series momentum" should canonicalize to the same entity. Manual alias
   table seeded by Dan, with auto-canonicalization via SPECTER as v1.5.

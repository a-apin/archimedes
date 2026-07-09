# ADR: Corpus RAG / knowledge-graph build path (hybrid)

> **Audience:** Archimedes team (decision owner: Dan; scoping pass: Önder, 2026-07-10)
> **Status:** **Direction decided (HYBRID), decomposed into follow-up issues, none of
> the follow-ups executed yet.** This doc fills a dangling reference — issues #1090,
> #1091, #1092, #1093 (filed 2026-07-09) each cite
> `docs/adr/corpus-rag-kg-hybrid-build-path.md` as "landing alongside" them; it never
> did until this pass. Written to close that gap and give the four issues (plus the
> parallel storage-substrate decision in #1065/#1071) one place that explains how they
> fit together.
> **Question being decided:** How does Archimedes build the retrieval + knowledge-graph
> layer that feeds multi-paper fusion, given the corpus is 10,000 metadata rows with no
> embeddings/KG behind them as of #778 — build a custom pipeline, buy a managed
> service, or both?
> **Related:** [#778](https://github.com/a-apin/archimedes/issues/778) (the
> claim-integrity issue this is Part B of) · [PR #781](https://github.com/a-apin/archimedes/pull/781)
> (Part A, honest `/health` surface — merged) · [`docs/specs/kb-integration-spec.md`](../specs/kb-integration-spec.md)
> (Phase 3c pipeline design, 2026-05-22) · [`docs/corpus-architecture.md`](../corpus-architecture.md)
> (Day-9 substrate description — stale on issue status, accurate on the 3-layer shape)
> · the private `a-apin/docs` internal-docs repo (not a submodule of this repo):
> `2026-06-23-Lepton_artifacts/consolidated/corpus-rag-build-vs-buy-memo.md` (the
> 2026-06-28 decision memo #778 quotes) and `CORPUS-AND-MINILM.md` (2026-07-04,
> describes the retrieval path that actually shipped).

## TL;DR

**HYBRID, as #778 named it: the custom KnowledgeBase pipeline (SPECTER2 embeddings +
HDBSCAN/BERTopic clusters + REBEL/SciSpacy KG) is the strategic spine; AWS Bedrock
Knowledge Bases is an optional, swappable retrieval bridge; Neptune GraphRAG is
explicitly out.** Two things have changed since #778 and the 2026-06-28 memo were
written, both narrowing what's actually left to build:

1. **The acute problem the Bedrock bridge was meant to solve — degraded (TF-IDF)
   retrieval — is already fixed, but not by Bedrock.** A local MiniLM
   (`all-MiniLM-L6-v2`) semantic-rerank layer shipped instead (`paper_rag.py`),
   went live, caused a real production CPU-starvation outage on 2026-07-04, and was
   hardened same-day (PR #885, merged). Live `/health` today reports
   `"paper_rag": "live", "corpus_embedded": true`. The Bedrock bridge (#1093) is
   still worth having as a hedge but is no longer urgent.
2. **The knowledge-graph piece is still fully unbuilt** — `corpus_kg_built: false`,
   `corpus_kg_entities: 0`, `corpus_kg_relations: 0`, `corpus_artifact_present: false`
   on live `/health` as of this writing (2026-07-10). The pipeline invocation code
   (`backend/archimedes/scripts/run_kb_pipeline.py`) is a checked-in skeleton that
   raises `NotImplementedError` on its real path — it has never had a working
   implementation. **This is the actual remaining gap**, and it is scoped into four
   issues filed 2026-07-09: #1090 (implement the invocation), #1091 (full-text
   hydration coverage — only 668/10,000 papers have extracted text today), #1092
   (Postgres backfill into `kg_entities`/`kg_relations`/`cluster_id`/`topic_label`),
   #1093 (the optional Bedrock bridge, explicitly lower priority now). The storage
   substrate those issues need is a separate, already-drafted decision: **EFS, not
   S3+DynamoDB** (#1065 + draft PR #1071) — zero app-code change vs. the S3 path,
   chosen alongside relocating the other stranded background runners.

**Net: Part B's acceptance criterion ("build path decided + scoped into follow-up
issues") is satisfied.** Nothing below proposes new issues — it maps what already
exists and flags what's still blocked on Dan's AWS operations.

## Context

### The 2026-05 attempt, and why it doesn't count today

Three issues from the original Agora hackathon window addressed pieces of this:

- **#101** ("Port KnowledgeBase pipeline") was closed **as superseded** on
  2026-05-23, before any pipeline ran. Dan's own closing comment deferred the real
  SPECTER2/HDBSCAN/REBEL invocation pending his parallel iteration in
  `submodules/Linus` stabilizing, and asked for "a tightly-scoped follow-on once
  that's ready" — which is effectively what #1090 is, filed 10 weeks later.
- **#147** ("S3 + DynamoDB + IAM foundation") closed complete via **PR #214**
  (merged 2026-05-24): real `S3ArtifactStore`/`S3PdfStore`/`DynamoDBPaperIndex`
  wrappers, 27 mocked unit tests, and real buckets/table provisioned — but **on the
  old, pre-migration shared AWS account** (region `eu-west-2`).
- **#151** ("GPU EC2 + run KB pipeline") closed complete on 2026-05-25, but its own
  final comment undercuts that: only **668 of 10,000** papers got embeddings +
  clusters (the acceptance bar was ≥9,000), and "Postgres backfill for cluster_id
  pending" was never picked back up. This is the "closed ≠ fixed" pattern CLAUDE.md
  warns about, applied to this exact issue.

**Both of those artifacts are moot today regardless of their 2026-05 outcome.** Prod
migrated to Dan's own AWS account (`037613907429` / `us-east-1`) on 2026-06-24 — a
full account decoupling, not a lift-and-shift. `.env.example`'s own honesty note
(added since) confirms it: *"the corpus S3 buckets + DynamoDB table below are NOT yet
provisioned on the prod account (only `archimedes-tfstate-*` and
`archimedes-alb-logs-*` exist today)."* Whatever the 668-paper partial run produced
lived in a bucket on an account that no longer backs prod.

### The 2026-06-28 build-vs-buy memo

`corpus-rag-build-vs-buy-memo.md` (in the private `a-apin/docs` repo, cited by #778)
recommended HYBRID on the same reasoning as the TL;DR above: the custom pipeline is
the only path that delivers citation-trained SPECTER2 embeddings, a typed scientific
KG (REBEL + SciSpacy), and a per-paper `content_hash` the strategy passport can anchor
on-chain; Bedrock buys none of those but is fast to stand up; Neptune's ~$2,565/mo
always-on cost is not justified at a 10k-paper corpus. It sequenced the work as
**Phase 0** (create the S3 substrate — confirmed nothing exists on the new account),
**Phase 1** (Bedrock bridge, to end degraded retrieval fast), **Phase 2** (the custom
pipeline as the real spine), **Phase 3** (promote custom, demote the bridge).

### What actually happened next (2026-07-04): MiniLM, not the Bedrock bridge

Neither Phase 0 nor Phase 1 from the memo shipped. Instead, a **local MiniLM
sentence-transformer rerank layer** landed in `paper_rag.py` — cheaper than either
memo option (no AWS spend, no managed-service integration tax), running on the
existing box. It ended the "degraded TF-IDF" problem the memo's Phase 1 targeted, but
via a mechanism the memo didn't consider. It also caused a real incident: on
2026-07-04, `_rerank_with_embeddings` re-encoded every candidate on every call with no
cache, and CPU-only PyTorch defaulted to all cores — on the prod t3.medium (2 vCPUs),
that starved the uvicorn event loop and took generation down for the whole morning.
**PR #885** (merged same day) fixed it: `torch.set_num_threads(1)` at model load, a
bounded per-process embedding cache (FIFO, 20k entries, lock-protected because
reranking runs in a real OS thread via `asyncio.to_thread`), and a 150-candidate
rerank cap. `CORPUS-AND-MINILM.md` (a-apin/docs, last updated 2026-07-04) is the
fuller writeup of this path and explicitly restates the same HYBRID framing: *"custom
KB spine (Postgres + MiniLM, what is live now) + optional Bedrock Knowledge Base
bridge + no Neptune."*

This means the memo's Phase 1 goal is met, its Phase 0 (S3 substrate) is not, and its
Phase 2 (the real KG spine) is still fully open — which is exactly what #1090–#1093
scope.

### The 2026-07-09 decomposition

Four issues, filed the day before this ADR was written, break Phase 2 (plus the
optional bridge) into machine-checkable, CLAUDE.md-standard specs:

| Issue | Scope | Depends on | New AWS spend? |
|---|---|---|---|
| [#1090](https://github.com/a-apin/archimedes/issues/1090) | Replace `run_kb_pipeline.py`'s `NotImplementedError` real-path with actual calls into `submodules/KnowledgeBase/papers_analysis/*` (SPECTER2 embed → HDBSCAN/BERTopic cluster → REBEL/SciSpacy KG), atomic tmpdir-then-swap | None (can run against whatever text exists) | No |
| [#1091](https://github.com/a-apin/archimedes/issues/1091) | Close or document the full-text hydration gap — only 668/10,000 papers had extracted text for the 2026-05-25 run; find out why and set a minimum-coverage bar before the next real run | None | No — I/O-bound, no GPU needed |
| [#1092](https://github.com/a-apin/archimedes/issues/1092) | New `kb_backfill.py`: read a completed manifest via `kb_artifacts.load_clusters()`/`load_topics()`/`load_kg_graph()`, write `papers.cluster_id`/`topic_label`, upsert `kg_entities`/`kg_relations` — the last mile between "an artifact exists" and `/health`'s honesty fields reading true | #1090 (needs a real artifact to backfill from) | No |
| [#1093](https://github.com/a-apin/archimedes/issues/1093) | Optional Bedrock KB bridge — new S3 data-source bucket + Bedrock KB resource + a flagged (`BEDROCK_KB_RETRIEVAL_ENABLED`, default off) retrieval path in `strategy_fusion.py`, parallel to and not blocking on #1090–#1092 | None (parallel track) | **Yes — explicitly flagged**, needs Dan's ack per CLAUDE.md |

All four are assigned to Dan (matching #778's named ownership) and none has a
linked PR yet — they are unstarted, not in progress.

A fifth piece — where the pipeline's output actually lives — was **not** filed as a
corpus issue because it's the same infra problem as relocating the other two
stranded background runners (`oracle`, `agent`), and got solved once, holistically:

| Issue / PR | Scope | Status |
|---|---|---|
| [#1065](https://github.com/a-apin/archimedes/issues/1065) | Execution checklist for relocating `oracle`/`agent` (→ a dedicated EC2) and `kb-runner` (→ scheduled Fargate task) off the detached post-Fargate-cutover box. Decision #3 in this issue: **EFS for KB artifact storage, not S3** — zero app-code change (mounts at the same `KB_ARTIFACT_DIR`/`/srv/corpus-artifact` path the code already expects), vs. S3 which would need a `kb_runner.py`/`corpus_routes.py` code change. | Blocked on the T3.2 contract redeploy landing first (explicit prerequisite) |
| [PR #1071](https://github.com/a-apin/archimedes/pull/1071) | Draft Terraform: `aws_efs_file_system` + mount targets + access point, the scheduled `kb-runner` Fargate task def + EventBridge schedule, plus the oracle/agent runner EC2. `terraform validate` clean; `terraform plan`/`apply` deliberately not run (no AWS creds in that session) — **Dan's `terraform plan` review is the real gate.** | Open, draft, unapplied |

`kb_artifacts.py`'s read path (`load_manifest`/`load_embeddings`/`load_clusters`/
`load_topics`/`load_kg_graph`/`load_umap_projection`) already supports an S3-backed
`_get_s3_client()` path alongside local-file reads, so it does not need to change
regardless of which storage backend (EFS mount vs. real S3) ends up live — the choice
in #1065 only affects which one actually gets populated at runtime.

## Decision

1. **Spine stays custom.** SPECTER2 embeddings + HDBSCAN/BERTopic clusters +
   REBEL/SciSpacy KG triples, invoked (never re-implemented) from
   `submodules/KnowledgeBase/papers_analysis/*`, per
   `docs/specs/kb-integration-spec.md`. This is the only path that produces a typed
   scientific KG and citation-trained embeddings, and the only one that gives the
   strategy passport a `content_hash` worth anchoring on-chain.
2. **Bridge stays optional and demoted.** Bedrock Knowledge Bases (#1093) remains
   scoped as a parallel, flagged, off-by-default retrieval path — not because it's a
   bad idea, but because the urgent reason to build it (degraded retrieval) is
   already resolved by MiniLM. Build it if there's spare capacity or if the custom
   spine's timeline slips; don't treat it as blocking.
3. **Neptune stays out.** Ruled out on cost (~$2,565/mo always-on for a 10k-paper
   corpus) and on fit (a generic graph doesn't produce the typed
   scientific-entity KG the wedge needs).
4. **Storage substrate is EFS, decided separately (#1065), not yet applied.** This
   supersedes the "S3 buckets" framing in #778's Part B text and in the 2026-06-28
   memo's Phase 0 — the team's actual drafted plan avoids S3 for the KB artifact
   specifically to avoid an app-code change, using S3 only where Bedrock KB
   requires it as a data source (#1093).
5. **Sequencing:** #1091 (hydration) and #1090 (pipeline invocation) can proceed in
   parallel; #1092 (backfill) waits on #1090 producing a real artifact; #1093
   (bridge) is independent of all three. None of the four are blocked by #1065/#1071
   landing — a first real pipeline run can target `KB_ARTIFACT_DIR` locally or via
   whatever storage is live at the time; EFS just needs to be there before a
   *scheduled, unattended* run in prod is meaningful.

## Current state (verified 2026-07-10)

| Signal | Value | Source |
|---|---|---|
| `corpus_papers` / `corpus_db_count` | 10,000 / 10,000 | live `/health` |
| `corpus_embedded` | **true** | live `/health` — MiniLM live (`paper_rag: "live"`, `paper_rag_reason: "model=all-MiniLM-L6-v2"`) |
| `corpus_kg_built` / `corpus_kg_entities` / `corpus_kg_relations` | false / 0 / 0 | live `/health` |
| `corpus_artifact_present` | false | live `/health` — `kb_artifacts.load_manifest()` raises `ArtifactNotFound` |
| `run_kb_pipeline.py` real invocation | `raise NotImplementedError(...)` | `backend/archimedes/scripts/run_kb_pipeline.py`, gated behind `KB_PIPELINE_ENABLED` (unset everywhere) |
| Full-text hydration coverage | 668/10,000 as of the one real run (2026-05-25); current coverage not re-verified by this pass | #151's closing comment; #1091 scopes re-checking this |
| Corpus/artifact S3 buckets on the current AWS account | None (`037613907429`/`us-east-1` has only `archimedes-tfstate-*` and `archimedes-alb-logs-*`) | `.env.example` honesty note; confirmed independently by the 2026-06-28 memo |
| `kg_entities`/`kg_relations` Postgres tables | Exist (ORM defined in `backend/archimedes/models/kg.py`, `ALTER TABLE` in `db.py`) | code read; nothing writes to them yet (`grep -rn "def backfill" backend/archimedes/` → no match) |

Part A of #778 (the honest `/health` surface, PR #781) is confirmed still solid: all
five fields it added are present, each derived from live DB/KG-store/artifact-manifest
state with a fail-safe `except` defaulting to false/0, and the hermetic test
(`backend/tests/test_loud_fallback_telemetry.py`, 10 tests) passes. The fields are
doing exactly the job they were built for — `corpus_embedded` flipped from false to
true between #778's filing and now, automatically, with no redeploy of the health-check
logic itself, because it reads real state rather than a constant.

## Consequences

### Positive
- No duplicate work: the decomposition already exists, is judge-grade
  (Summary/Scope/Acceptance/Verify/Anti-goals/Depends-on), and correctly flags the
  one piece (#1093) that needs Dan's cost ack.
- The storage-substrate decision (EFS) was made once, holistically, alongside the
  other stranded runners, instead of a corpus-specific S3 bucket that would have
  needed its own IAM/Terraform surface and an app-code change.
- The honest `/health` fields (Part A) already prove out: they flipped
  `corpus_embedded` to true the moment MiniLM shipped, with zero additional work.

### Negative / costs we accept
- **The four 2026-07-09 issues (#1090–#1093) each cited this ADR as "landing
  alongside" them, and it didn't, for 24 hours** — a real instance of a doc-promise
  going unfulfilled, closed by this pass.
- `docs/corpus-architecture.md` (Day-9, 2026-05-20) is now stale — it lists #101 as
  open (it's closed-as-superseded) and describes the heavy-artifact pipeline as
  "the #101 KB-pipeline port," a name this ADR's issue set has moved past. Left
  as-is rather than rewritten in this pass, to keep this change scoped to the
  build-path decision; flagged here so it doesn't silently keep drifting.
- The custom spine (#1090–#1092) remains fully unbuilt work with a real, non-trivial
  runtime cost (SPECTER2 ~71 papers/sec per the KnowledgeBase precedent; REBEL/
  SciSpacy are the long pole) — this ADR does not shrink that work, only confirms
  it's correctly scoped and sequenced.

## Alternatives considered

- **Bedrock KB as the primary retrieval path, skip the custom spine — rejected.**
  Generic Titan embeddings are not citation-trained, Bedrock produces no typed
  scientific KG, and nothing from a managed retriever gives the strategy passport a
  hashable, on-chain-anchorable provenance artifact. This is the same rejection the
  2026-06-28 memo already reached; nothing since has changed the calculus.
- **Neptune GraphRAG — rejected.** ~$2,565/mo always-on is not justified for a
  10k-paper corpus, and the graph it produces is generic, not the typed
  scientific-entity KG the wedge needs. #778's own HYBRID framing ruled this out
  explicitly; restated here for completeness.
- **S3 for the KB artifact (the original #147/#151 shape) — not chosen for the
  current relocation.** #1065 picked EFS specifically because it needs zero
  app-code change (`kb_runner.py`/`corpus_routes.py` already read/write a
  filesystem path); S3 remains the right choice specifically for #1093's Bedrock
  data source, which requires it.
- **Do nothing further until Dan has bandwidth — rejected as a silent default.**
  #778's own acceptance criterion asks for the build path to be decided and scoped,
  not built; that bar is met by #1090–#1093 + #1065/#1071 existing. Silently letting
  those issues sit without connecting them to #778 or to each other would leave the
  next reader to re-derive all of this from scratch, which is what this ADR is for.

## Open questions

Carried over from the 2026-06-28 memo, still open:

- **Custom batch runtime at scale** — whether a real SPECTER2 + HDBSCAN + REBEL/
  SciSpacy run over the (hydration-gap-permitting) corpus fits in a batch window
  acceptable for however #1065/#1071's scheduled Fargate task is sized
  (`kb_runner_cpu`/`kb_runner_memory` = 1024/4096 in the current draft — likely too
  small for the ~6 GB of combined model weights; worth Dan's attention when #1090
  is actually implemented and a real run is attempted).
- **KG entity canonicalization** — REBEL outputs raw spans ("TSMOM" vs. "time-series
  momentum" as different entities); `docs/specs/kb-integration-spec.md` proposes a
  manual alias table as v1, SPECTER-based auto-canonicalization as v1.5. Not
  re-litigated here.
- **Whether #1093 (Bedrock bridge) ever gets built at all** — now that MiniLM ended
  the acute retrieval problem, the case for it is weaker than the 2026-06-28 memo
  assumed. Worth an explicit "build or drop" call once #1090–#1092 are further
  along, rather than letting it sit indefinitely as neither done nor closed.

## Ratification

Direction (HYBRID) decided 2026-06-28; superseding retrieval fix (MiniLM) shipped
2026-07-04 (PR #885); build-path decomposed into follow-up issues 2026-07-09
(#1090–#1093) and, for storage, #1065 + draft PR #1071. **None of the follow-ups are
merged or applied as of this writing (2026-07-10).** This ADR is a scoping/mapping
pass, not new engineering — see #778 for the tracking issue and the PR that added
this file for the full verification trail.

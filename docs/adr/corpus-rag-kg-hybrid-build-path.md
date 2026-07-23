# ADR: Corpus RAG / KG Hybrid Build-Path

- **Status**: Accepted
- **Date**: 2026-07-23
- **Deciders**: Archimedes Core Team
- **Consulted**: Issue #778, Issue #1090, Issue #1091, Issue #1092, Issue #1093

---

## Context

Archimedes utilizes a 10,000-paper quantitative finance library to ground strategy generation in empirical literature. The raw metadata manifest contains 10,000 arXiv records, while full-text PDF hydration and SPECTER2 vector embedding / REBEL Knowledge Graph extraction operate on hydrated texts.

## Decision

1. **Honest Claim Surface**: Telemetry surfaces (`/health`, `/api/corpus/overview`, `/corpus` UI) clearly distinguish raw manifest entries (10,000 papers) from hydrated full-text PDFs and SPECTER2/HDBSCAN clustered records.
2. **Local / Cloud Hybrid RAG**: Local SPECTER2 embeddings + Postgres `kg_entities` / `kg_relations` serve as the offline primary RAG path. AWS Bedrock Knowledge Base (`BEDROCK_KB_ENABLED=1`) serves as an optional cloud retrieval bridge without breaking local parity.
3. **Atomic Pipeline Execution**: `run_kb_pipeline.py` writes to a staging directory `.tmp_corpus-artifact_<ts>` under lease-lock (`acquire_runner_lease`) before executing an atomic directory swap into `/srv/corpus-artifact`.

## Consequences

- **Pros**: Clear transparency regarding manifest vs full-text hydration; offline zero-cloud developer workflow preserved; zero race-condition pipeline updates.
- **Cons**: Requires multi-worker async scraper or S3 pre-extracted bundle (`corpus_text_v1.tar.gz`) for full-text hydration.

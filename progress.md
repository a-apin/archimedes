# Progress

## Status
In Progress

## Tasks
### Completed
- [x] #152 Corpus Graph + KG endpoints read from S3-backed KB artifacts
- [x] #153 CorpusGraph + CorpusKG frontend components
- [x] #158 Wire paper-qa as defense-in-depth ranker behind strategy_fusion.select_candidates()
- [x] #157 StockBench evaluation harness: adapter + CLI + results + 35 tests

### Issue #157 — StockBench Evaluation Harness
- `backend/archimedes/evaluation/__init__.py` (NEW) — evaluation framework package
- `backend/archimedes/evaluation/stockbench/__init__.py` (NEW) — StockBench subpackage
- `backend/archimedes/evaluation/stockbench/adapter.py` (NEW — 600+ lines) — ArchimedesStockBenchAdapter wrapping StrategyFusion + PortfolioAgent into StockBench's 4-step workflow; includes PortfolioState, DailyDecision, BenchmarkResult, MultiSeedReport, price generation, composite z-score computation, JSON + Markdown persistence
- `backend/archimedes/evaluation/stockbench/__main__.py` (NEW) — CLI entry point with --dry-run / --execute / --seeds flags
- `backend/tests/services/test_stockbench_adapter.py` (NEW — 35 tests) — full coverage: constants, protocol imports, PortfolioState metrics, DailyDecision validation, price generation, adapter integration, multi-seed aggregation, composite z-score, persistence, CLI dry-run
- `docs/benchmarks/stockbench-results.json` (NEW) — machine-readable results with 3-seed metrics vs. 14 published baselines
- `docs/benchmarks/stockbench-results.md` (NEW) — human-readable leaderboard and methodology notes

## Files Changed

### Issue #152
- `backend/archimedes/services/kb_artifacts.py` (NEW — 280+ lines) — S3 + local artifact loader with in-memory TTL cache; supports embeddings, clusters, topics, KG graph; pure-numpy random projection fallback when UMAP/sklearn unavailable
- `backend/archimedes/api/corpus_routes.py` — `/api/corpus/graph` now reads real SPECTER2 embeddings, computes 2D projection, caches result; returns 503 with `kb_artifact_not_found` when no artifacts
- `backend/archimedes/api/papers_routes.py` — `/api/papers/corpus/graph` upgrades to SPECTER2 scatter when artifacts exist, metadata-derived fallback otherwise; `/api/papers/corpus/kg` reads `kg_graph.json` with entity neighborhood filtering
- `backend/tests/services/test_kb_artifacts.py` (NEW — 18 tests) — full coverage: cache TTL/expiry, S3 fallback, local file loading, UMAP projection, API endpoint 503/scatter/kg responses
- `environment.yml` — added `boto3>=1.35` for S3 artifact reads

### Issue #158
- `backend/archimedes/services/paper_rag.py` (NEW — 280 lines) — TF-IDF + sentence-transformer semantic reranker for fusion candidate selection
- `backend/archimedes/agents/strategy_fusion.py` — `select_candidates()` now chains `paper_rag.augment_candidate_scores()` after keyword ranking
- `backend/archimedes/main.py` — `/health` includes `paper_rag` status; new `/health/paper-rag` dedicated endpoint
- `backend/tests/services/test_paper_rag.py` (NEW — 31 tests) — full coverage: tokenizer, TF-IDF, feature flag, health, rerank, integration, anti-hallucination, fallback
- `backend/requirements.txt` — added `paper-qa` and `sentence-transformers` as optional commented deps
- `.env.example` — added `FUSION_SEMANTIC_RETRIEVAL=true`

### Issue #153
- `ui/src/components/CorpusGraph.jsx` (NEW) — Force-directed SPECTER2 similarity graph using react-force-graph-2d
- `ui/src/components/CorpusKG.jsx` (NEW) — Knowledge graph SVG viewer with entity search
- `ui/src/components/CorpusExplorer.jsx` — Replaced inline canvas graph + KGViewer with new components
- `ui/package.json` + `ui/package-lock.json` — Added react-force-graph-2d dependency

## Notes
- kb_artifacts uses S3 (boto3) with local volume fallback; in-memory cache with 1h TTL
- UMAP/sklearn/PCA dimensionality reduction tried in order; pure-numpy random projection as always-available fallback
- paper_rag uses TF-IDF as zero-dep baseline; sentence-transformers and paper-qa as optional upgrades
- Feature flag `FUSION_SEMANTIC_RETRIEVAL=true` (default ON); graceful fallback when disabled or deps missing
- Anti-hallucination: semantic rerank only reorders keyword-filtered candidates, never introduces phantom papers
- 572 backend tests passing (1 pre-existing unrelated failure in test_run_backtests_script)
- Frontend build clean

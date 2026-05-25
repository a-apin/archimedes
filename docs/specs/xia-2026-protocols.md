# Xia et al. 2026 Named Protocols — Archimedes Implementation

**Reference:** Xia, Y. et al. (2026). "Agentic Trading: When LLM Agents Meet Financial Markets." *Expert Systems with Applications*. arXiv: 2605.19337.

Xia et al. audited 19 trading-agent papers and found **15/19 are R0** (no code/data artifacts) and **0/19 reach R3** (fully replayable with artifact versioning + immutable provenance). They formalize five named protocols that close the most common failure modes. Archimedes implements all five as **enforced mechanisms** — not advisory guidelines.

---

## 1. Outcome Embargo (§ 4.2)

**Prevents:** Oracle Fallacy — the agent retrieves a paper and the retrieval surface leaks the outcome (future information) into a backtest decision.

**Implementation:** `backend/archimedes/services/embargo_filter.py`

- KB papers are stamped with `published` date at ingest.
- At retrieval time `t`, papers with `published > t - embargo_days` are filtered out.
- Default `embargo_days = 30` (configurable per generation call).
- Applied in `corpus_service.load_papers_from_db()` when `apply_embargo=True`.

## 2. Time-Aware Retrieval (§ 4.2)

**Prevents:** Regime drift — old papers with high feature similarity mislead post-regime-change decisions.

**Implementation:** `backend/archimedes/services/time_aware_retrieval.py`

- SPECTER2 similarity scores are multiplied by decay term `exp(-λ × age_days)`.
- Default `λ = 0.002/day` (half-life ≈ 346 days ≈ 1 year).
- Regime-aware: `λ` scales up in high-volatility regimes (`risk_off: 2.5×`, `transition: 1.5×`).
- Applied in `corpus_service.load_papers_from_db()` when `apply_decay=True`.

## 3. Hierarchy of Truth (§ 4.4)

**Prevents:** Noise injection — uncurated signals override core decision logic.

**Implementation:** Enforced structurally in `chain/agent_runner.py` + `chain/v_check.py`.

- On-chain vault state (Layer A.2) always overrides LLM narrative.
- The `V_check` contract rejects actions that violate deterministic constraints regardless of agent confidence.
- Curated academic KB signals outrank uncurated sources (we ingest curated academic research (arxiv-sourced); Reddit/social are out of scope).

## 4. Source Tracking (§ 4.3)

**Prevents:** Provenance loss — agents cite "facts" that cannot be traced to a verifiable source.

**Implementation:** `backend/archimedes/services/source_tracker.py` + `models/trace.py`

- Every agent decision trace records `consulted_paper_hashes`: a sorted list of `arxiv_id:content_hash` strings.
- `consulted_paper_hashes` is included in `_HASH_FIELDS` for the canonical trace hash anchored on-chain via `ReasoningTraceRegistry`.
- Anyone can re-derive the hash and verify the cited papers existed and were unmodified.

## 5. Reasoning I/O Contract — V_check (§ 5)

**Prevents:** Hallucination propagation — multi-step LLM errors compound through agent loops, each step committing capital.

**Implementation:** `backend/archimedes/chain/v_check.py`

- Deterministic Python validity checker runs before ANY rebalance transaction.
- Checks:
  - `weights_sum_bps`: weights must sum to exactly 10000 BPS.
  - `max_concentration`: no single weight exceeds a threshold (default 60%).
  - `min_cost_benefit_bps`: expected improvement must exceed minimum (default 5 BPS = 0.05%).
- **The LLM cannot override V_check.** If any check fails, the action is rejected and a SKIP trace is published with the failure reasons.

---

## Reproducibility Target

We target **R3** (Xia § 4.2): fully replayable with artifact versioning + immutable provenance. The combination of these five protocols + on-chain trace anchoring + S3-versioned KB artifacts + git-committed strategy DSLs makes Archimedes the first production trading-agent system designed to ship at R3.

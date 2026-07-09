# Architecture Decision Records

> **Status:** Day-10 (2026-05-22; updated 2026-06-27 — Phase-7 back-fill of historical
> decisions; updated 2026-07-10 — corpus RAG/KG hybrid build path). Eleven ADRs. The
> ADR pattern is: capture a non-trivial technical decision once, with the alternatives
> considered and the reasoning, so future contributors can understand the choice
> without needing to relitigate it. The records below were written as decisions
> landed; the 2026-06-27 batch back-fills decisions that shipped before the ADR habit
> was established (each cites the PR/commit/spec it documents).

## Index

| ADR | Decision |
|---|---|
| [`backtrader-vs-vectorbt-decision-memo.md`](backtrader-vs-vectorbt-decision-memo.md) | Why we picked **backtrader** over **vectorbt** for the v1 backtest engine |
| [`chainlink-primary-oracle.md`](chainlink-primary-oracle.md) | Why on-chain prices are **Chainlink-primary** with a thin, bounded admin fallback that **degrades (not reverts)** on feed outage (#724) |
| [`build-on-deploy-main-only.md`](build-on-deploy-main-only.md) | Why `main` is the only long-lived branch and every merge auto-deploys (no `develop`) |
| [`aws-account-migration.md`](aws-account-migration.md) | Why prod moved to Dan's own AWS account (`037613907429`/`us-east-1`) post-Agora |
| [`k1-generation-external-rigor-gate.md`](k1-generation-external-rigor-gate.md) | Why generation emits **K=1** winner + considered-rejects, with the rigor gate run **externally** |
| [`rigor-gate-unification.md`](rigor-gate-unification.md) | Why the four selection-bias controls run through **one** authoritative gate, surfaced honestly (post-#710) |
| [`non-custodial-vault-owner-agent.md`](non-custodial-vault-owner-agent.md) | Why vaults separate **owner (withdrawal)** from **agent (rebalance-only)** so a compromised agent key can't drain (#731) |
| [`fusion-primary-generation.md`](fusion-primary-generation.md) | Why strategy generation is **fusion-primary** (paper-grounded), not free-form LLM (#751) |
| [`glm-to-bedrock-llm-migration.md`](glm-to-bedrock-llm-migration.md) | Why the live LLM moved from **GLM to AWS Bedrock** (Nova Micro default, Converse backend) (#717) |
| [`portfolio-constructor-consolidation.md`](portfolio-constructor-consolidation.md) | Why legacy constructors were retired and a **dual-signal** (regime × consensus) sizer activated (#131, #662) |
| [`corpus-rag-kg-hybrid-build-path.md`](corpus-rag-kg-hybrid-build-path.md) | Why the corpus RAG/KG layer is **HYBRID** — custom KnowledgeBase pipeline as spine, optional Bedrock KB bridge, no Neptune — and how that decomposes into follow-up issues (#778 Part B) |

## When to add an ADR

- A library/framework choice with a real alternative
- A protocol / interface contract that downstream code will depend on
- A trade-off that future readers will look back at and ask "why did they do it that way?"

## When NOT to add an ADR

- Routine implementation choices (variable names, function shape)
- Decisions captured implicitly in shipped code + tests
- Things best captured in inline comments or commit messages

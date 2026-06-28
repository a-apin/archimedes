---
name: archimedes-fork-1__docs
description: Comprehensive documentation — ADRs, specs, architecture, runbooks, audits, analysis, security
triggers: [archimedes-fork-1 docs, architecture, specs, ADR, runbook, archimedes-fork-1 design, archimedes-fork-1 audit]
---

# Documentation — Archimedes Fork

**Source**: `archimedes-fork-1`
**Category**: CrossCutting

## When to use this skill
Understanding the Archimedes fork architecture — reading specs, ADRs, design docs, runbooks, audit reports, or security documentation. Also use this skill when you need architectural context before writing code.

## Key files and folders
- `/home/ricardo/github/archimedes-fork-1/docs/README.md` — docs index
- `/home/ricardo/github/archimedes-fork-1/docs/design.md` — Original single-vault architecture
- `/home/ricardo/github/archimedes-fork-1/docs/architectural-principles.md` — Architecture principles
- `/home/ricardo/github/archimedes-fork-1/docs/user-stories.md` — Canonical product spine (locked)
- `/home/ricardo/github/archimedes-fork-1/docs/corpus-architecture.md` — 10k q-fin corpus build
- `/home/ricardo/github/archimedes-fork-1/docs/deployment-runbook.md` — Deployment procedures
- `/home/ricardo/github/archimedes-fork-1/docs/adr/` — ADRs (backtrader vs vectorbt, Chainlink oracle)
- `/home/ricardo/github/archimedes-fork-1/docs/specs/` — 29 specification documents
- `/home/ricardo/github/archimedes-fork-1/docs/security/` — Security documentation
- `/home/ricardo/github/archimedes-fork-1/docs/audits/` — 5 audit reports
- `/home/ricardo/github/archimedes-fork-1/docs/runbooks/` — Operational runbooks
- `/home/ricardo/github/archimedes-fork-1/docs/quant/` — Quant methodology docs
- `/home/ricardo/github/archimedes-fork-1/docs/archive/` — Historical planning (18 files)
- `/home/ricardo/github/archimedes-fork-1/docs/diagrams/` — Architecture diagrams
- `/home/ricardo/github/archimedes-fork-1/CLAUDE.md` — Living context doc (1071 lines)
- `/home/ricardo/github/archimedes-fork-1/README.md` — Project overview
- `/home/ricardo/github/archimedes-fork-1/SETUP.md` — From-clone setup walkthrough

## Key concepts
- **Product spine**: generate → rigor-gate → execute → monitor → explore (user-stories.md)
- **Architecture lineage**: design.md → ecosystem-design-spec.md → component-interfaces-spec.md
- **Specs cover**: strategy passport, selection bias, fusion, vault semantics, strategy DSL, lifecycle, commit-reveal traces, IPFS provenance, paper replication, quant roadmap
- **Audit trail**: 5 audit reports including security + quant methodology reviews

## Constraints and rules
- `docs/user-stories.md` is the canonical product framing — supersedes older docs
- `CLAUDE.md` is the living context doc — read at session start
- Archived docs in `docs/archive/` are historical — treat as reference, not active spec

## Related skills
- See `.agents/skills/archimedes-fork-1__backend` (backend architecture)
- See `.agents/skills/archimedes-fork-1__smart-contracts` (contract architecture)
- See `.agents/skills/archimedes-fork-1__infrastructure` (deployment runbook)

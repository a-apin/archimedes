# Submodules — external references

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-07-28
> **superseded-by:** —

The repo carries three git submodules at [`../submodules/`](../submodules/). Reference
material; only the sticky-config one-liner is repeated in
[`../CLAUDE.md`](../CLAUDE.md), because a fresh clone silently drifts without it.

## Sticky submodule config — one-time, per clone

After `git clone`, run this to make git auto-recurse into submodules on every
checkout/pull/rebase. Without it, working trees drift out of sync with `main`'s recorded
pins (we hit this several times during the hackathon — every session had to manually
re-sync):

```bash
git config submodule.recurse true        # auto-recurse on git ops
git config diff.submodule log            # nicer diff display
git submodule update --init --recursive  # one-shot sync to recorded pins
```

`Linus` has its OWN nested submodule (`submodules/Linus/modules/KnowledgeBase`), which is
the most common source of "modified content" noise in `git status`. The `--recursive` flag
handles it.

## `submodules/context-arc/`

Circle's agent-facing developer docs and 5 reference codebases for Arc + Circle. **This is
the canonical reference for any Arc/Circle integration question.** Start with
[`../submodules/context-arc/AGENTS.md`](../submodules/context-arc/AGENTS.md) for the
task-indexed entry-point table. Highest-leverage files for our build:

- `circlefin-skills/use-arc.md` — Arc chain config, USDC-as-gas, Foundry deploy (canonical Arc reference)
- `circlefin-skills/use-smart-contract-platform.md` — contract deploy + monitor (Dan's lane; Bogdan reviews)
- `circlefin-skills/bridge-stablecoin.md` — CCTP + Gateway for RWA bridging (Marten's lane)
- `circlefin-skills/use-gateway.md` — unified balance + nanopayments
- `samples/arc-escrow/` — closest existing pattern to our vault contract
- `samples/arc-multichain-wallet/` — CCTP integration patterns
- `samples/arc-p2p-payments/` — Paymaster + USDC patterns

Refresh upstream with `git submodule update --remote submodules/context-arc` or
`arc-canteen context sync` (drops into `~/.arc-canteen/context/`).

## `submodules/KnowledgeBase/`

Dan's scientific-paper analysis pipeline (PyMuPDF extract + SPECTER2 embeddings +
HDBSCAN/BERTopic clustering + REBEL/SciSpacy knowledge graph). For Archimedes, **don't port
wholesale** — read it as a reference implementation. Patterns worth lifting for the Tier-1
arxiv extraction pipeline:

- `papers_analysis/extract.py` — PyMuPDF caching pattern (~71 files/s)
- `papers_analysis/metadata.py` — paper-corpus schema (maps to our `paper_corpus` table)
- `papers_analysis/summarize.py` — Ollama-driven methodology synthesis (we'd use Claude)

### KB pipeline integration — provenance discipline

The Corpus page (`/corpus`) uses
[`corpus_routes.py`](../backend/archimedes/api/corpus_routes.py) at the `/api/corpus/*`
prefix, which reads real KB pipeline output (SPECTER2 embeddings, HDBSCAN clusters,
REBEL/SciSpacy triples) and returns 503 when no artifact exists yet. The legacy
metadata-derived `/api/papers/corpus/*` endpoints were deleted in issue #201 — **do NOT
reintroduce them.** Any "graph" or "knowledge graph" surface MUST come from real KB pipeline
output, not arxiv-metadata synthesis. When the KB pipeline (issue #151, gated on AWS infra
#147) actually produces an artifact, the honest endpoints start returning data; until then
the page renders an explicit "KB pipeline still running — first artifact pending" empty
state from the 503 response.

## `submodules/Linus/`

Dan's personal AI orchestration project. Reference only; nothing to port to Archimedes. The
[`experiments/archimedes/`](../submodules/Linus/experiments/archimedes/) and
[`experiments/agora-hackathon/`](../submodules/Linus/experiments/agora-hackathon/)
directories contain the priors that seeded several of our current `docs/` files.

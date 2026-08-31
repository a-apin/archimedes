# AGENTS.md

Two different kinds of agent read this repo — point each at its own doc rather than
duplicating content here.

- **Working on this codebase** (an AI coding agent contributing code, tests, or infra to
  Archimedes itself): start at [`CLAUDE.md`](CLAUDE.md) — the engineering rules, review
  gates, and agent discipline for this repo. It deliberately holds only what you would get
  wrong by default; for everything else it points at
  [`docs/README.md`](docs/README.md), the documentation index — architecture in
  [`docs/architecture.md`](docs/architecture.md), decisions in [`docs/adr/`](docs/adr/README.md),
  team and ownership in [`docs/team.md`](docs/team.md), and how to file a new doc in
  [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md).
- **Using the deployed product** (an autonomous AI agent driving Archimedes as a user — an
  investor, a trading bot, a research assistant): start at
  [`docs/agent-api.md`](docs/agent-api.md) for the full programmatic API contract, or
  [`/llms.txt`](https://archimedes-arc.com/llms.txt) on the live site for a curated,
  low-token entry point. Both cover same Better Auth browser-free journey:
  read → authenticate account → generate → read rigor verdict; wallet proof stays optional.

A machine-readable manifest is also live at
[`/api/agent/manifest`](https://archimedes-arc.com/api/agent/manifest) and
[`/.well-known/agent.json`](https://archimedes-arc.com/.well-known/agent.json).

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time
context, not required startup reading.

**Know its boundary before you trust it.** `.openwikiignore` is an **allow-list**, currently
scoped to `docs/quant/` alone. Every page is therefore grounded in *documentation*, not in
implementation: a claim there records what a doc asserts, never what the code enforces.
Treat source code and tests as authoritative — a page's unknowns and conflicts are
verification gaps, not automatic requirements.

Start at [`openwiki/quickstart.md`](openwiki/quickstart.md). Before quoting a threshold, a
pass/fail, or a library size from any page, read
[`openwiki/rigor/documented-conflicts.md`](openwiki/rigor/documented-conflicts.md) — the
slice contradicts itself in seven places.

Do not hand-edit generated pages unless explicitly asked; fix the source doc and let
OpenWiki regenerate. `openwiki/INSTRUCTIONS.md` is the one user-authored file OpenWiki reads
and never rewrites.

The GitHub Actions workflow is `workflow_dispatch`-only and **cannot run today** — Bedrock's
Anthropic models are not enabled on the AWS account. The committed wiki was generated
through OpenWiki's coding-agent integration, which needs no provider credentials. Both the
blocker and the cost of the run are recorded in
[`docs/decisions/tooling-adoptions-2026-08.md`](docs/decisions/tooling-adoptions-2026-08.md);
widen the boundary one slice at a time and add a row there each time.

<!-- OPENWIKI:END -->

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

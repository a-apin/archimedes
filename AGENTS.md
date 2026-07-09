# AGENTS.md

Two different kinds of agent read this repo — point each at its own doc rather than
duplicating content here.

- **Working on this codebase** (an AI coding agent contributing code, tests, or infra to
  Archimedes itself): start at [`CLAUDE.md`](CLAUDE.md) — team, architecture, conventions,
  and the engineering rules for this repo.
- **Using the deployed product** (an autonomous AI agent driving Archimedes as a user — an
  investor, a trading bot, a research assistant): start at
  [`docs/agent-api.md`](docs/agent-api.md) for the full programmatic API contract, or
  [`/llms.txt`](https://archimedes-arc.com/llms.txt) on the live site for a curated,
  low-token entry point. Both cover the same SIWE-authenticated, browser-free journey:
  read → authenticate → generate → read the rigor verdict.

A machine-readable manifest is also live at
[`/api/agent/manifest`](https://archimedes-arc.com/api/agent/manifest) and
[`/.well-known/agent.json`](https://archimedes-arc.com/.well-known/agent.json).

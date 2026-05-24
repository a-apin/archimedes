# Archimedes

> **Linus for quantitative finance — research-grounded AI portfolios, settled on Arc.**
>
> *The lever is academic research. The fulcrum is autonomous AI. The world is your portfolio.*

[![License: Unlicense](https://img.shields.io/badge/license-Unlicense-blue.svg)](LICENSE)
[![Hackathon: Agora](https://img.shields.io/badge/hackathon-Agora%20Agents-violet.svg)](https://luma.com/7i50p2r9)
[![Settled on: Arc](https://img.shields.io/badge/settled%20on-Arc-2A4DD1.svg)](https://www.arc.network/)
[![Arc OSS Showcase](https://img.shields.io/badge/Arc%20OSS-showcase-7B2CBF.svg)](ARC-OSS-SHOWCASE.md)

## TL;DR

Describe what you want; Archimedes fuses your intent with live market data and a 10,000-paper quantitative-finance research library into novel strategies, gates them through selection-bias rigor (Deflated Sharpe, Probability of Backtest Overfitting), and lets you execute them into non-custodial vaults on Arc testnet — every reasoning step traceable to a source paper and anchored on-chain.

### Run it locally in three commands

```bash
git clone --recurse-submodules git@github.com:a-apin/archimedes-arcadia.git archimedes && cd archimedes
cp .env.example .env       # fill in ANTHROPIC_AUTH_TOKEN (GLM via Canteen, or BYO Anthropic key)
docker compose up -d --build
```

Then open <http://localhost>. Full walkthrough: [`SETUP.md`](SETUP.md).

> ⚠️ **`pytest` requires the docker stack to be running.** Before running the test suite,
> always spin up the services first: `docker compose up -d --build` (the `--build` flag
> rebuilds images after dependency changes). The tests depend on Postgres + Redis being
> reachable; without the stack you'll see connection errors, not test failures. Full
> testing notes in [`SETUP.md` § Running the test suite](SETUP.md#running-the-test-suite).

### Common dev commands (`make help`)

The repo ships a [`Makefile`](Makefile) with shortcuts for the daily workflow.
Run `make` (or `make help`) for the full list. Most useful:

```
make up         # docker compose up -d --build (starts the stack)
make down       # stop the stack
make logs       # tail backend logs
make pytest     # run the backend test suite (stack must be up)
make lint       # ruff check
make format     # ruff format
make ui-dev     # Vite dev server (ui/)
make routes     # dump FastAPI route inventory
make clean      # nuke __pycache__/.pytest_cache/.ruff_cache
```

Foundry, Circle wallet, and oracle targets (`compile`, `test`, `wallet`, `feed`, …) are also there — see `make help`.

## Status (2026-05-22)

**Live on the Arc public testnet** (chain ID `5042002`): grab faucet USDC at <https://faucet.circle.com/> (20 USDC / 2h — on Arc, USDC *is* gas) and try the full flow with test funds. **No real money at risk, by design.** Arc has no mainnet yet (Circle's docs list mainnet as "upcoming"); mainnet launch, real-funds custody, and the regulatory architecture (off-chain redemptions, preset-strategy / RIA posture) are the **business-plan roadmap**, not hackathon scope — see [`docs/competitor-landscape.md`](docs/competitor-landscape.md).

**Built today:**

- Live testnet deploy: <http://13.40.112.220> · 10 Solidity contracts on Arc
- 3-input fusion engine: user brief × live market regime × 10,000-paper q-fin corpus → grounded strategy spec
- LLM-driven agentic portfolio advisor (`portfolio_agent.py`, 850 lines) — picks individual instruments and anchors each to a strategy passport
- Four-control selection-bias rigor gate (DSR + PBO + walk-forward OOS + look-ahead audit) — **2 Tier-1 strategies pass today** against 22 years of real SPY data
- Multi-asset NAV vaults — `Vault.totalAssets()` prices all synthetic holdings via oracles
- On-chain reasoning trace anchoring via the deployed `ReasoningTraceRegistry`
- 6-container docker stack: backend (FastAPI) + postgres + redis + nginx + oracle + agent
- 302 backend tests + 16 analytics-engine tests green

## Why Archimedes

| Category                  | Examples                                  | What's missing                                            |
| ------------------------- | ----------------------------------------- | --------------------------------------------------------- |
| TradFi robo-advisors      | Wealthfront, Betterment                   | Rule-based, opaque, no on-chain settlement                |
| DeFi yield aggregators    | Yearn, Morpho-curated vaults              | Chase live yield, curation without proof of methodology   |
| AI-flavored crypto agents | Virtuals, SingularityDAO, Theoriq         | Token-mediated speculation; reasoning opaque              |

**Nobody is grounding portfolio decisions in peer-reviewed quant research with verifiable on-chain reasoning, settled in pure USDC.** That's the gap. Full thesis: [`docs/competitor-landscape.md`](docs/competitor-landscape.md).

## User journey

**Canonical user journey** — 7 click steps from cold land to a verified on-chain decision.

```mermaid
flowchart LR
  L[/Landing/]
  G[/generate/]
  ST[/strategy/:id/]
  CV{CreateVaultModal}
  DF{DepositFlow stepper}
  P[/portfolio/]
  R[/reasoning?trace_id=X/]
  V{Verify on-chain}

  L -- 'Generate a Strategy →' --> G
  G -- submit brief; SSE stream completes --> ST
  ST -- 'Deploy as Vault →' (wallet required) --> CV
  CV -- create succeeds --> DF
  DF -- 3 signatures: approve → deposit → setTargetAllocations --> P
  P -- click activity trace --> R
  R -- 'Verify on-chain' --> V

  %% Alternate paths (allowed; not canonical)
  L -. 'Browse Example Library' .-> LIB[/library?tab=examples/]
  LIB -. click row .-> ST
  L -. sidebar Explore .-> E[/explore/]
  E -. 'Use in Generate' .-> G
  L -. sidebar Corpus .-> C[/corpus/]
  C -. paper detail → 'Generate from this' .-> G
```

Every other route (Library tabs, Corpus Graph/KG, Explore sparklines, Learnings) is reachable from the sidebar but supplementary.

## Documentation map

Three documents are the front door for different audiences:

| If you want to… | Read |
|---|---|
| Run Archimedes locally | [`SETUP.md`](SETUP.md) |
| Operate the live stack + understand the RPC | [`OPERATIONS.md`](OPERATIONS.md) |
| Understand Arc / Circle integration | [`ARC.md`](ARC.md) |
| Know what the product *is* (the locked spine) | [`docs/user-stories.md`](docs/user-stories.md) |
| Browse all design + planning docs | [`docs/README.md`](docs/README.md) |
| Submit Archimedes to the Arc OSS Showcase | [`ARC-OSS-SHOWCASE.md`](ARC-OSS-SHOWCASE.md) |
| Project context for a Claude Code session | [`CLAUDE.md`](CLAUDE.md) |

## Repository structure (top level)

```
archimedes/
├── README.md             ← this file
├── SETUP.md              ← prerequisites + 5-step install + platform notes + test suite
├── OPERATIONS.md         ← run the stack + RPC deep-dive + LLM backends + traction logging + security
├── ARC.md                ← Arc testnet reference + Circle sponsor alignment + context-arc submodule
├── ARC-OSS-SHOWCASE.md   ← positioning + forkable primitives for the Arc OSS Showcase
├── CLAUDE.md             ← project context for Claude Code sessions
├── LICENSE               ← Unlicense (public-domain dedication)
│
├── docs/                 ← design + planning + specs + ADRs + archive (see docs/README.md)
├── backend/              ← FastAPI app (Python 3.12) — see docs/chuan-architecture-survey.md
├── analytics-engine/     ← backtest engine (uv-managed)
├── contracts/            ← Solidity (Foundry layout) — 10 contracts deployed on Arc testnet
├── ui/                   ← React 19 + Vite 8 + viem 2.48 (the live frontend)
├── nginx/                ← reverse-proxy + UI build container
├── wallet-setup/         ← Circle Wallets scripts (oracle wallet, entity-secret rotation)
├── infra/                ← Terraform (EC2 deploy)
└── submodules/           ← context-arc + KnowledgeBase + Linus (git submodules)
```

## Tech stack

| Layer             | Technology                                                                                |
| ----------------- | ----------------------------------------------------------------------------------------- |
| Backend           | Python 3.12 · FastAPI · Uvicorn · SQLAlchemy                                              |
| Frontend          | React 19 + Vite 8 + [viem](https://viem.sh/) 2.48 (plain CSS)                             |
| Database          | PostgreSQL 16 + Redis 7                                                                   |
| LLM               | Provider-agnostic (`LLM_*` env): GLM via z.ai, Anthropic, OpenAI, Ollama                  |
| Backtesting       | [backtrader](https://github.com/mementum/backtrader) ([ADR](docs/adr/backtrader-vs-vectorbt-decision-memo.md)) |
| Smart contracts   | Solidity targeting Arc (EVM-compatible) + [Foundry](https://book.getfoundry.sh/)          |
| On-chain          | Circle SDK (Wallets, Gateway, CCTP) + viem on the UI side                                 |
| Hackathon CLI     | [arc-canteen](https://github.com/the-canteen-dev/ARC-cli) (RPC proxy + telemetry)         |
| Deployment        | Docker compose (6-service stack) on EC2; GitHub Actions CI/CD                             |

Full architecture: [`docs/design.md`](docs/design.md) + [`docs/chuan-architecture-survey.md`](docs/chuan-architecture-survey.md).

## Development workflow

See [`CLAUDE.md`](CLAUDE.md) for full conventions. Headline points:

- **Branch model:** `main` is the single live branch — build-on-deploy (every merge triggers a CI deploy). Short-lived `<discord-handle>/<name>` branches → PR → `main`; rebase late, merge fast.
- **One approving review** for non-contract changes; two for contract changes.
- **Commit style:** imperative mood with optional scope tags (`[strategy]`, `[backend]`, `[contracts]`, `[docs]`, `[infra]`).
- **Lanes are descriptive, not prescriptive** — everyone is full-stack ([`CLAUDE.md` § Lead + coverage](CLAUDE.md)).

## Team

5 builders across 5 timezones with deep coverage on every load-bearing skill — see the team table in [`CLAUDE.md`](CLAUDE.md).

## Contributing

Fork, branch (`<your-handle>/<short-name>`), PR to `main`. One logical change per PR. Never force-push `main`. Never commit secrets. See [`CLAUDE.md`](CLAUDE.md) for full conventions.

## License

[Unlicense](LICENSE) — full public-domain dedication. Use, modify, distribute freely. No warranty.

---

> *In classical Athens, the agora was the heart of the city — the original information-processing machine. AI agents are the new citizens.*
>
> — [Agora Agents Hackathon](https://luma.com/7i50p2r9)

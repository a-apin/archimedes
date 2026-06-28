# AGENTS.md — ARC Ecosystem Context System

> **Purpose:** Router that maps any task to the correct source namespace and skill.
> Covers 7 source repositories that form the ARC/Circle ecosystem.
>
> **How to use:** Read this file first. Identify which source(s) the task concerns.
> Filter the Skill Directory to that namespace (plus any `shared__*` rows).
> Load only the skills you need — do not load all skills at once.

## System Context

**ARC Ecosystem Context System** — Unified skill system for 7 interdependent repos:
quantitative-finance vaults (archimedes + archimedes-fork-1), nanopayments (arc-nanopayments),
prediction-market off-chain backend (cypherlexicon-offchain), on-chain contracts
(auction-prediction-market, vyper-agentic-payments), and the ARC/Circle developer knowledge base
(arc-canteen-context). All built on the Arc testnet (Chain ID 5042002).

## Source Directory

| Namespace | Source Path | Project Name | Primary Domains |
| --- | --- | --- | --- |
| `archimedes` | `/home/ricardo/github/archimedes` | Archimedes | q-fin, smart-contracts, AI-agents |
| `archimedes-fork-1` | `/home/ricardo/github/archimedes-fork-1` | Archimedes (fork) | q-fin, smart-contracts, AI-agents, Circle-wallets |
| `arc-nanopayments` | `/home/ricardo/github/arc-nanopayments` | ARC Nanopayments | x402, gateway, wallets |
| `cypherlexicon-offchain` | `/home/ricardo/github/CypherLexicon-offchain` | CypherLexicon Offchain | prediction-markets, AI-evaluation |
| `auction-prediction-market` | `/home/ricardo/github/auction-prediction-market` | Auction Prediction Market | solidity-contracts, foundry |
| `arc-canteen-context` | `/home/ricardo/.arc-canteen/context` | ARC Canteen Context | arc-docs, circle-docs, sdks |
| `vyper-agentic-payments` | `/home/ricardo/github/vyper-agentic-payments` | Vyper Agentic Payments | vyper-contracts, agentic-payments, hackathon |

## Skill Directory

| Skill | Source | Description |
| --- | --- | --- |
| `archimedes__backend` | archimedes | FastAPI backend: routes, auth, services, models, DB |
| `archimedes__smart-contracts` | archimedes | 10 Solidity contracts on Arc: vaults, AMM, oracle, registry |
| `archimedes__analytics-engine` | archimedes | Backtrader backtesting, 35+ q-fin strategies, PBO/DSR rigor gate |
| `archimedes__agents` | archimedes | LLM agent system: architect, fusion, portfolio runner |
| `archimedes__infrastructure` | archimedes | Docker, Terraform, CI/CD, wallet-setup, Circle integration |
| `archimedes__payments` | archimedes | WS-C wallet module, Circle Developer-Controlled Wallets |
| `archimedes__ui` | archimedes | React/Vite frontend, SIWE auth, portfolio dashboard |
| `archimedes-fork-1__backend` | archimedes-fork-1 | FastAPI backend: routes, services, models, chain layer, agents |
| `archimedes-fork-1__analytics-engine` | archimedes-fork-1 | Backtrader backtesting, 30+ academic strategies, PBO/DSR rigor |
| `archimedes-fork-1__smart-contracts` | archimedes-fork-1 | 11 Solidity contracts on Arc: vaults, AMM, oracle, registry |
| `archimedes-fork-1__ui` | archimedes-fork-1 | React/Vite frontend, 30+ components, SIWE, Circle wallet |
| `archimedes-fork-1__infrastructure` | archimedes-fork-1 | Docker Compose, Terraform AWS, CI/CD, nginx |
| `archimedes-fork-1__docs` | archimedes-fork-1 | Architecture docs, 29 specs, ADRs, runbooks, audits |
| `archimedes-fork-1__wallet-setup` | archimedes-fork-1 | Circle wallet Node.js deploy/price/seed scripts |
| `arc-nanopayments__dashboard` | arc-nanopayments | Next.js app: pages, login, layout, dashboard UI |
| `arc-nanopayments__x402-gateway` | arc-nanopayments | x402 protocol integration, gateway, agent wallet |
| `arc-nanopayments__wallet-management` | arc-nanopayments | Wallet generation, withdrawals, balance management |
| `arc-nanopayments__supabase-auth` | arc-nanopayments | Supabase server/client auth, session management |
| `cypherlexicon-offchain__auction-service` | cypherlexicon-offchain | Auction business logic, proposal encoding, AI scoring |
| `cypherlexicon-offchain__prediction-market-backend` | cypherlexicon-offchain | Market routes, betting, resolution, fee claims |
| `cypherlexicon-offchain__blockchain-layer` | cypherlexicon-offchain | ethers.js contract interaction, Arc testnet, ABIs |
| `cypherlexicon-offchain__ai-agents` | cypherlexicon-offchain | LLM agent definitions, system prompts, scoring |
| `cypherlexicon-offchain__frontend` | cypherlexicon-offchain | Vanilla JS UI: app, auction, market, leaderboard, web3 |
| `auction-prediction-market__contracts` | auction-prediction-market | 4 Solidity contracts: AuctionManager, PredictionMarket, etc. |
| `auction-prediction-market__deployment-testing` | auction-prediction-market | Foundry tests, deploy scripts, Arc testnet config |
| `arc-canteen-context__arc-docs` | arc-canteen-context | Arc chain docs, app-kit, agentic economy standards |
| `arc-canteen-context__circle-docs` | arc-canteen-context | Circle dev docs: USDC, CCTP, wallets, gateway, agent-stack |
| `arc-canteen-context__clob-client` | arc-canteen-context | CLOB client v2 (TS + Rust), order utils |
| `arc-canteen-context__polymarket-sdk` | arc-canteen-context | Polymarket SDK, CTF exchange, conditional tokens |
| `arc-canteen-context__builder-relayer` | arc-canteen-context | Python builder/relayer client for Arc |
| `arc-canteen-context__real-time-data` | arc-canteen-context | Real-time data client for market feeds |
| `shared__arc-blockchain` | shared | Arc testnet config, RPC, USDC, CCTP, deploy patterns |
| `shared__prediction-market-contracts` | shared | Contract interfaces, lifecycle, ABIs shared by on-chain + off-chain |
| `vyper-agentic-payments__contracts` | vyper-agentic-payments | 6 Vyper contracts: escrow, spending-limiter, subscription, splitter, vault, channel |
| `vyper-agentic-payments__tests` | vyper-agentic-payments | Titanoboa test suite — 7 test files, mock USDC, integration tests |
| `vyper-agentic-payments__challenges` | vyper-agentic-payments | 3-track hackathon workshop: 13 challenges for Vyper + Circle + primitives |
| `vyper-agentic-payments__examples` | vyper-agentic-payments | Agent marketplace — Flask server, x402 middleware, GatewayClient buyer |
| `vyper-agentic-payments__docs` | vyper-agentic-payments | Architecture docs, contract specs, workshop guide |
| `vyper-agentic-payments__scripts-deploy` | vyper-agentic-payments | Moccasin build, deploy/interact scripts, pre-commit config |

## How to Navigate

1. Identify which source(s) the task concerns using the Source Directory.
2. Filter the Skill Directory to that namespace prefix (plus `shared__*` rows relevant to it).
3. Load only the skills actually needed — do not load skills from unrelated namespaces.
4. Cross-source tasks (e.g. "deploy contracts and update the off-chain backend") load both source-specific skills plus the relevant `shared__*` skill.

## Critical Cross-System Rules

1. **Arc testnet (Chain ID 5042002)** is the single live chain for all sources. Never use mainnet without explicit confirmation.
2. **USDC (6 decimals)** is the native gas token on Arc. Never assume ETH-style gas handling.
3. **Each source's SKILL.md uses absolute paths** — there is no implicit "current source" context in a multi-source `.agents/skills/` folder.
4. **Never apply one source's conventions to another** without checking that source's own SKILL.md constraints.
5. **Secrets NEVER committed** in any source. All use `.env.example` patterns.
6. **`archimedes-fork-1` is a fork of the original `archimedes` repo** — it shares the same architecture lineage and product spine but has its own team ownership, live deploy (`archimedes-arc.com`), and AWS account. Do not assume changes in one apply to the other without verifying.

## Deployment Note

This `.agents/skills/` folder is auto-loaded by OpenHands **only if** `/home/ricardo/github/docs` is the working directory OpenHands is invoked against. If any individual source repo (e.g. archimedes, archimedes-fork-1, auction-prediction-market) is opened independently, you must copy or symlink this `.agents/` folder into that repo's root for auto-discovery, or load skills explicitly by path.

## Adding a New Source

1. Derive one namespace per Step 0 (use the path-derived slug, lowercased).
2. Repeat Steps 1–8 for that source only.
3. Add rows to the Source Directory and Skill Directory tables above.
4. Do not regenerate or rename existing namespaces' skills.


# Git Feature Branch Workflow

As the OpenHands agent, use this workflow when implementing a feature requested
in chat. The feature branch itself is the deliverable — no pull request is
needed.

### 1. Create Branch
Create a new git branch with this naming pattern:
agent/task-{TIMESTAMP}-{feature-name}
Example: `agent/task-1704067200-auth-feature`

Command:
```bash
git checkout -b agent/task-$(date +%s)-{feature-name}
```

### 2. Implement Feature
Write the code for the requested feature:
- Create new files as needed
- Follow existing code patterns
- Add comments where appropriate

### 3. Commit Code
Commit your changes with a clear message:
```bash
git commit -m "[AGENT:feature-name] {description of what was implemented}

Co-authored-by: openhands <openhands@all-hands.dev>"
```

### 4. Push Branch
Push your branch to remote:
```bash
git push origin agent/task-{TIMESTAMP}-{feature-name}
```

The code is now in its own feature branch, ready for review. No pull request
is created — the branch is published and handled separately.
# Archimedes

> **Peer-reviewed AI portfolios, settled on Arc.**
>
> *The lever is academic research. The fulcrum is autonomous AI. The world is your portfolio.*

[![License: Unlicense](https://img.shields.io/badge/license-Unlicense-blue.svg)](LICENSE)
[![Hackathon: Agora](https://img.shields.io/badge/hackathon-Agora%20Agents-violet.svg)](https://luma.com/7i50p2r9)
[![Settled on: Arc](https://img.shields.io/badge/settled%20on-Arc-2A4DD1.svg)](https://www.arc.network/)

## What is Archimedes?

Archimedes is an autonomous portfolio agent that turns peer-reviewed quant finance research
into investable, backtested strategies. Users connect a wallet, pick a risk profile, and the
agent constructs a personalized portfolio of RWA tokens and yield instruments on Arc —
settled in USDC. Every decision the agent makes is hashed and anchored on-chain, so
reputation is **verifiable history, not predicted performance**.

Built for the [**Agora Agents Hackathon**](https://luma.com/7i50p2r9) — Canteen × Circle ×
Arc, May 11–25, 2026.

**Status (Day 4, 2026-05-14):** live testnet deploy at
[`http://18.171.230.205/`](http://18.171.230.205/). 10 Solidity contracts deployed on Arc
testnet (chain ID `5042002`). React/Vite UI with multi-wallet connect (MetaMask /
Coinbase / generic). 3 paper-grounded strategies + buy-and-hold baseline seeded in the
analytics engine. Backend FastAPI app with strategy provider + chain integration layer
running behind nginx in the EC2 docker-compose stack. The remaining critical-path work
is the autonomous orchestrator loop and end-to-end reasoning-trace anchoring — see
[`docs/judging-rubric-assessment.md`](docs/judging-rubric-assessment.md) for the running
self-score. See [`docs/`](docs/) for design + planning artifacts.

## Why Archimedes?

Today's portfolio products force a tradeoff:

| Category                      | Examples                                  | What's missing                                            |
| ----------------------------- | ----------------------------------------- | --------------------------------------------------------- |
| TradFi robo-advisors          | Wealthfront, Betterment                   | Rule-based, opaque, no on-chain settlement                |
| DeFi yield aggregators        | Yearn, Yield Seeker                       | Chase current yields, no academic rigor, stablecoin-only  |
| AI-flavored crypto agents     | Virtuals, SingularityDAO, Theoriq         | Token-mediated speculation; reasoning is opaque           |

**Nobody is grounding portfolio decisions in peer-reviewed quant research, with verifiable
on-chain reasoning, settled in pure USDC.** That's the gap.

## Quick Links

| Topic                                          | Document                                                                   |
| ---------------------------------------------- | -------------------------------------------------------------------------- |
| 🧭 Project context for Claude Code sessions     | [`CLAUDE.md`](CLAUDE.md)                                                   |
| 🏗️ Original system architecture (Chuan)         | [`docs/design.md`](docs/design.md)                                         |
| 🌐 Day-3 ecosystem pivot                        | [`docs/specs/ecosystem-design-spec.md`](docs/specs/ecosystem-design-spec.md) |
| 🤝 Frozen interface contract (5-person concurrent build) | [`docs/specs/component-interfaces-spec.md`](docs/specs/component-interfaces-spec.md) |
| 🔬 Red-team synthesis + regulatory survey       | [`docs/agora_project_analysis.md`](docs/agora_project_analysis.md)         |
| 🎯 MVP scope decisions (5 locked)               | [`docs/mvp-scope-memo.md`](docs/mvp-scope-memo.md)                         |
| 🚫 Anti-features                                | [`docs/anti-features.md`](docs/anti-features.md)                           |
| 🧱 Architectural principles (4 primitives)      | [`docs/architectural-principles.md`](docs/architectural-principles.md)     |
| 📜 Strategy passport spec                       | [`docs/specs/strategy-passport-spec.md`](docs/specs/strategy-passport-spec.md) |
| 📐 Selection-bias corrections spec (DSR/PBO)    | [`docs/specs/selection-bias-corrections-spec.md`](docs/specs/selection-bias-corrections-spec.md) |
| 🔒 Commit-reveal trace integrity (v1.5)         | [`docs/specs/commit-reveal-trace-spec.md`](docs/specs/commit-reveal-trace-spec.md) |
| ⚖️ Backtrader vs vectorbt decision              | [`docs/specs/backtrader-vs-vectorbt-decision-memo.md`](docs/specs/backtrader-vs-vectorbt-decision-memo.md) |
| 🎓 Q-fin paper corpus seed                      | [`docs/qfin-paper-corpus-seed.md`](docs/qfin-paper-corpus-seed.md)         |
| 🏛️ Pitch deck + demo script                     | [`docs/demo-script-pitch-deck-outline.md`](docs/demo-script-pitch-deck-outline.md) |
| 🎨 Claude Design prompts                        | [`docs/claude-design-prompts.md`](docs/claude-design-prompts.md)           |
| 🔭 RFB alignment                                | [`docs/rfb-alignment.md`](docs/rfb-alignment.md)                           |
| 🏟️ Competitive landscape                        | [`docs/competitor-landscape.md`](docs/competitor-landscape.md)             |
| ⚙️ Infra + CI/CD (EC2, Terraform)               | [`docs/infra-setup.md`](docs/infra-setup.md)                              |
| 📊 Judging-rubric self-assessment               | [`docs/judging-rubric-assessment.md`](docs/judging-rubric-assessment.md)   |

## Repository Structure

```
archimedes/
├── CLAUDE.md                        # Project context for Claude Code sessions
├── README.md                        # This file
├── LICENSE                          # Unlicense (public domain dedication)
├── environment.yml                  # Conda env spec for the Python backend
├── docker-compose.yml               # Local + production stack: postgres + redis + nginx + backend
├── .env.example                     # Copy to .env and fill in (gitignored)
│
├── docs/                            # Design + planning + specs
│   ├── design.md                    # Original architecture (Chuan)
│   ├── agora_project_analysis.md    # Red-team + regulatory synthesis
│   ├── architectural-principles.md  # 4 primitives + Tier 1/2 framing
│   ├── anti-features.md             # Scope discipline + pitch-rigor anti-claims
│   ├── mvp-scope-memo.md            # 5 locked scope decisions
│   ├── rfb-alignment.md
│   ├── competitor-landscape.md
│   ├── qfin-paper-corpus-seed.md
│   ├── demo-script-pitch-deck-outline.md
│   ├── claude-design-prompts.md
│   ├── infra-setup.md               # EC2 + Terraform + CI/CD (Chuan)
│   └── specs/
│       ├── ecosystem-design-spec.md           # Day-3 marketplace pivot
│       ├── component-interfaces-spec.md       # Frozen interfaces for 5-person build
│       ├── strategy-passport-spec.md
│       ├── selection-bias-corrections-spec.md # DSR / PBO / OOS / look-ahead audit
│       ├── commit-reveal-trace-spec.md        # v1.5 trace integrity upgrade
│       └── backtrader-vs-vectorbt-decision-memo.md
│
├── backend/                         # FastAPI app (Python 3.12)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── archimedes/
│       ├── main.py                  # FastAPI entrypoint
│       ├── api/                     # Routes + Pydantic schemas (Daniel R.)
│       ├── chain/                   # On-chain integration + oracle_runner (Chuan)
│       ├── interfaces/              # Protocol classes — frozen contracts between teammates
│       ├── models/                  # Shared dataclasses (Strategy, BacktestResult, Trace, Portfolio, …)
│       └── services/                # Implementations (strategy_provider, etc.)
│
├── analytics-engine/                # Backtest engine (Daniel R., uv-managed)
│   ├── pyproject.toml
│   ├── src/archimedes_analytics_engine/
│   │   ├── cli.py                   # `archimedes-analytics-engine` entrypoint
│   │   ├── engine.py                # backtrader runner + BacktestResult
│   │   ├── data.py, instruments.py, strategy_loader.py
│   └── strategies/                  # One .py file per strategy (paper-grounded)
│       ├── pipeline_buy_hold.py                          # Baseline
│       ├── faber_2007_sma200_timing.py                   # Faber 2007 (Cambria)
│       ├── moreira_muir_2017_volatility_managed.py       # Moreira & Muir 2017 (J. Finance)
│       └── moskowitz_ooi_pedersen_2012_tsmom.py          # Moskowitz, Ooi, Pedersen 2012 (J. Fin. Econ.)
│
├── contracts/                       # Solidity (Foundry layout) — 10 contracts deployed on Arc testnet
│   ├── foundry.toml
│   ├── abis/                        # Cached ABIs for backend + UI consumption
│   ├── src/
│   │   ├── AMMPool.sol              # x*y=k AMM
│   │   ├── AMMRouter.sol            # AMM router / swap entry
│   │   ├── AssetRegistry.sol        # Strategy + asset registry
│   │   ├── PriceOracle.sol          # Oracle (Circle-Wallets-signed price pushes)
│   │   ├── ReasoningTraceRegistry.sol  # On-chain anchor for agent reasoning traces
│   │   ├── SyntheticFactory.sol     # Synthetic asset minting factory
│   │   ├── SyntheticToken.sol       # ERC-20 synthetic tokens
│   │   ├── SyntheticVault.sol       # Per-synth collateral vault
│   │   ├── Vault.sol                # Core user vault (ERC-4626)
│   │   ├── VaultFactory.sol         # Vault deployer
│   │   └── interfaces/              # I*.sol — frozen ABIs the backend + UI code against
│   ├── script/                      # Foundry deploy scripts (Deploy.s.sol, DeployV2.s.sol)
│   └── test/                        # Forge tests
│
├── ui/                              # React 19 + Vite 8 + viem 2.48 (the live frontend)
│   ├── src/
│   │   ├── App.jsx, main.jsx, config.js
│   │   └── components/              # Layout, WalletConnect, Trade
│   ├── package.json
│   └── vite.config.js
│
├── ui-mockups/                      # Static HTML mockups (early prototypes; retained for reference)
├── nginx/                           # nginx config + multi-stage Dockerfile for the docker-compose stack
├── wallet-setup/                    # Circle Wallets setup scripts (oracle wallet, entity-secret rotation, etc.)
├── infra/                           # Terraform — EC2 deployment (Chuan)
└── submodules/                      # External references (git submodules)
    ├── context-arc/                 # Circle's Arc/Circle docs + 5 sample codebases
    ├── KnowledgeBase/               # Dan's paper-analysis pipeline (port targets: extract.py, metadata.py)
    └── Linus/                       # Dan's AI orchestration project (reference only for archimedes)
```

## Tech Stack

| Layer             | Technology                                                                                |
| ----------------- | ----------------------------------------------------------------------------------------- |
| Backend           | Python 3.12, FastAPI, Uvicorn, SQLAlchemy                                                 |
| Frontend          | React 19 + Vite 8 + [viem](https://viem.sh/) 2.48 (plain CSS)                             |
| Database          | PostgreSQL 16 + Redis                                                                     |
| LLM               | Claude API ([anthropic](https://github.com/anthropics/anthropic-sdk-python))              |
| Backtesting       | [backtrader](https://github.com/mementum/backtrader) (v1 decision — see specs)            |
| Smart contracts   | Solidity targeting Arc (EVM-compatible) + [Foundry](https://book.getfoundry.sh/)          |
| On-chain          | [Circle SDK](https://www.circle.com/) (Wallets, Gateway, CCTP) + viem on the UI side      |
| Hackathon CLI     | [arc-canteen](https://github.com/the-canteen-dev/ARC-cli) (traction tracking)             |
| Deployment        | Docker compose (5-service stack) on EC2; CI/CD via GitHub Actions                         |

Full architecture in [`docs/design.md`](docs/design.md).

---

## Setup

Works on **macOS, Linux, and Windows**. Below is a single setup path that everyone on the
team follows.

### Prerequisites

| Tool                                                                             | Purpose                                            |
| -------------------------------------------------------------------------------- | -------------------------------------------------- |
| [Git](https://git-scm.com/)                                                       | Source control                                     |
| [mambaforge / miniconda](https://github.com/conda-forge/miniforge)                | Python environments                                |
| [Node.js 20+](https://nodejs.org/) (via [nvm](https://github.com/nvm-sh/nvm))     | Frontend toolchain                                 |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/)                 | Local PostgreSQL + Redis                           |
| [Foundry](https://book.getfoundry.sh/getting-started/installation)                | Smart contract compilation + testing               |

### 1. Clone the repository (with submodules)

```bash
git clone --recurse-submodules git@github.com:hackagora/archimedes-arcadia.git archimedes
cd archimedes
```

If you already cloned without `--recurse-submodules`, populate them now:

```bash
git submodule update --init --recursive
```

The `submodules/` directory carries Circle's [`context-arc`](submodules/context-arc/) (Arc + Circle developer docs and 5 sample codebases) and Dan's [`KnowledgeBase`](submodules/KnowledgeBase/) + [`Linus`](submodules/Linus/) reference projects.

### 2. Create the Python environment

The repo ships an [`environment.yml`](environment.yml) that defines all Python deps
(FastAPI, SQLAlchemy, backtrader, pandas, anthropic, web3, etc.).

```bash
conda env create -f environment.yml
conda activate archimedes
```

If you prefer mamba (faster):

```bash
mamba env create -f environment.yml
mamba activate archimedes
```

Verify:

```bash
python --version    # → Python 3.12.x
uv --version        # → uv 0.x
which pytest        # → /.../envs/archimedes/bin/pytest
```

### 3. Install the arc-canteen CLI (every team member, individually)

[arc-canteen](https://github.com/the-canteen-dev/ARC-cli) is Canteen's hackathon
traction-reporting tool. Each team member installs it personally — your traction updates
attach to your individual profile and count toward judging metrics.

```bash
uv tool install git+https://github.com/the-canteen-dev/ARC-cli
arc-canteen login        # GitHub device flow
arc-canteen --help       # explore commands
```

After login, the CLI writes credentials to `~/.arc-canteen/env` containing an RPC URL with
an embedded server token. **The token is a secret.** See
[Security notes](#security-notes) before pasting it anywhere.

To get the RPC available in every new shell:

```bash
echo '[ -f ~/.arc-canteen/env ] && . ~/.arc-canteen/env' >> ~/.bashrc
# Or for zsh:
echo '[ -f ~/.arc-canteen/env ] && . ~/.arc-canteen/env' >> ~/.zshrc
```

Then `$RPC` is set automatically in new shells.

### 4. Smart contracts (Foundry)

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

Verify against Arc testnet using your arc-canteen RPC (see § "Run Archimedes locally" below for what `$RPC` means and how to set it):

```bash
source ~/.arc-canteen/env       # ensures $RPC is set
cast block-number --rpc-url $RPC
cast chain-id --rpc-url $RPC
```

The contract sources live in [`contracts/src/`](contracts/src/) and follow the Foundry layout. Build + test:

```bash
cd contracts
forge build
forge test
```

### 5. Frontend (React + Vite via nginx)

The live UI is at [`ui/`](ui/) — React 19 + Vite 8 + viem 2.48 (plain CSS, no framework
beyond React). Components: `Layout`, `WalletConnect` (MetaMask / Coinbase / generic
browser wallet), `Trade`. The docker-compose `nginx` service builds the React app via a
multi-stage Dockerfile in [`nginx/`](nginx/) and serves the built static bundle on port
80 with a reverse-proxy to the backend at `/api/`. For frontend hot-reload during
development, run `npm install && npm run dev` from inside `ui/` to use the Vite dev
server directly.

Demo deep links (client-side routes exposed via nginx SPA fallback):
- `/`
- `/markets/explore`
- `/markets/strategies`
- `/markets/trade`
- `/portfolio/dashboard`
- `/portfolio/mint-burn`
- `/portfolio/liquidity`
- `/portfolio/vaults`
- `/intelligence/reasoning`

[`ui-mockups/`](ui-mockups/) carries the earlier static-HTML prototypes — retained for
reference, no longer wired into the stack.

---

## Run Archimedes locally

This is the everyone-on-the-team path for spinning up Archimedes on your laptop and poking it. The docker-compose stack reproduces the production EC2 deployment so what you see locally is what runs on the team's shared instance.

### What you get

`docker compose up` brings up five services:

| Service     | Port | URL                              | What it is |
| ----------- | ---- | -------------------------------- | ---------- |
| `nginx`     | 80   | <http://localhost>               | React UI build (multi-stage Dockerfile in [`nginx/`](nginx/)) + reverse-proxy to backend |
| `backend`   | 8000 | <http://localhost:8000/docs>     | FastAPI app (Swagger UI auto-generated from `backend/archimedes/api/routes.py`) |
| `oracle`    | —    | (no HTTP)                        | Oracle price feeder — pushes prices via Circle Wallets API to `PriceOracle.sol` |
| `postgres`  | 5432 | `postgres://archimedes@localhost:5432/archimedes` | DB for strategies, backtests, reasoning traces |
| `redis`     | 6379 | `redis://localhost:6379/0`       | Live regime state cache; agent loop scratch |

### Prerequisites (one-time)

- Docker Desktop running (or any Docker daemon)
- The conda env from Step 2 above is **only** needed if you also want to run `pytest`, the `analytics-engine` CLI, or any Python tool *outside* the containers. The containers ship their own Python; you don't need conda to use the local stack.

### Step 1 — Create your `.env` file

```bash
cp .env.example .env
```

Open `.env` in your editor and fill in the values that are blank. Defaults work for postgres + redis. The variables that need real values for full functionality:

| Variable                     | Where to get it                                       | Required for             |
| ---------------------------- | ----------------------------------------------------- | ------------------------ |
| `ANTHROPIC_API_KEY`          | <https://console.anthropic.com>                       | Reasoning-trace + arxiv extraction features |
| `RPC`                        | `~/.arc-canteen/env` (after `arc-canteen login`)      | On-chain features (contracts, trace anchoring) |
| `DEV_WALLET_ADDRESS` / `_PRIVATE_KEY` | `cast wallet new` (generate a fresh dev wallet) | Submitting signed transactions to Arc |
| `*_ADDRESS`                  | Auto-populated after Chuan deploys contracts          | On-chain features        |

You can leave these blank to start — the API will boot, you can hit the routes, and the UI mockups will render. The on-chain features just won't work until you populate them.

### Step 2 — Spin up the stack

```bash
docker compose up -d --build
```

On first run this downloads ~150 MB of base images (postgres-alpine, redis-alpine, python:3.12-slim, nginx-alpine) and builds the backend image from `backend/Dockerfile`. Subsequent runs are seconds.

Watch healthchecks succeed:

```bash
docker compose ps
# All four services should report "running" and "(healthy)"
```

### Step 3 — Verify it works

| Open in your browser | Expect to see |
| -------------------- | ------------- |
| <http://localhost>           | The live React UI (built from [`ui/`](ui/) — Layout + WalletConnect + Trade components) |
| <http://localhost:8000>      | `{"name":"Archimedes","tagline":"Peer-reviewed AI portfolios, settled on Arc.","docs":"/docs"}` |
| <http://localhost:8000/health> | `{"status":"ok","service":"archimedes-backend"}` |
| <http://localhost:8000/docs> | Swagger UI auto-rendered from the API contract |

The Swagger UI shows the live routes. As of Day 4, the strategy provider, chain
integration (read paths), and oracle runner are real implementations; the autonomous
orchestrator loop, regime detector, portfolio constructor, and backtest evaluator are
the remaining interface implementations from
[`docs/specs/component-interfaces-spec.md`](docs/specs/component-interfaces-spec.md).

### Step 4 — Drive the strategy library from the Python side

The analytics-engine and the strategy provider work locally without Docker — handy if you want to debug strategies without rebuilding the container. From a conda-`archimedes` shell:

```bash
# List the loaded strategy passport metadata (uses the in-process strategy provider)
python -c "
import sys; sys.path.insert(0, 'backend')
from archimedes.services.strategy_provider import default_provider
p = default_provider()
for s in p.list_strategies():
    print(f'{s.paper_title}  ({s.paper_venue}, {s.paper_year})')
    print(f'  paper_grounded={s.is_paper_grounded}  profiles={s.risk_profiles}')
"

# Run a real backtest via the analytics-engine CLI (requires uv in the engine subdir)
cd analytics-engine
uv sync
uv run archimedes-analytics-engine run --operations SPY GOLD TREASURY
# Artifacts land in analytics-engine/artifacts/
```

### Step 5 — Tear down

```bash
docker compose down                 # stop containers; keep data
docker compose down -v              # stop containers; wipe postgres volume (start fresh)
docker compose logs -f backend      # tail the backend logs
docker compose logs postgres        # database logs
```

### Step 6 — Optional: connect to Arc testnet

If you want to make on-chain calls (read contract state, send signed transactions, etc.) you need `$RPC` set — the per-developer RPC URL from `arc-canteen`. See **§ "Understanding the RPC URL"** below for the full picture.

```bash
# One-off: source the env then call any Eth RPC method
source ~/.arc-canteen/env       # exports $RPC
cast chain-id --rpc-url $RPC    # → 0x4cef52 (Arc testnet)
cast block-number --rpc-url $RPC

# Or via the canteen CLI directly:
arc-canteen rpc eth_chainId
```

To make these calls available inside the docker stack, paste the URL into `.env` as the `RPC` variable and `docker compose up -d` again (compose re-exports the env vars to the backend container).

---

## Understanding the RPC URL

The single most-used piece of infrastructure on this project. Worth a minute of orientation, especially for anyone newer to EVM tooling.

### What you get from `arc-canteen login`

A URL of the form:

```
https://rpc.testnet.arc-node.thecanteenapp.com/v1/swrm_<64-hex>
```

Three pieces glued together:

1. **`rpc.testnet.arc-node.thecanteenapp.com`** — Canteen's JSON-RPC **proxy** for the Arc testnet (not the Arc node itself; a proxy in front of it).
2. **`/v1/`** — proxy API version.
3. **`swrm_<64-hex>`** — your **per-user server token**, embedded in the URL path. Each teammate has a different one.

### What it does

The Arc chain is EVM-compatible, so it speaks the standard Ethereum JSON-RPC protocol — the same HTTP API that geth, Infura, Alchemy etc. expose. The proxy lets you make calls like:

| Method                       | Effect                                                | Read or write |
| ---------------------------- | ----------------------------------------------------- | ------------- |
| `eth_chainId`                | Returns `0x4cef52` for Arc testnet                    | Read          |
| `eth_blockNumber`            | Current head block number                             | Read          |
| `eth_getBalance`             | USDC/native-token balance of an address               | Read          |
| `eth_call`                   | Simulate a contract function call (no state change)   | Read          |
| `eth_getLogs`                | Query event logs by topic / address / block range     | Read          |
| `eth_sendRawTransaction`     | Submit a **pre-signed** transaction to be mined       | Write         |

The proxy enforces a **method allowlist** — most reads plus `eth_sendRawTransaction`. Try a non-allowlisted method and you'll see `method '<x>' not allowed by the proxy`.

### What the token is NOT

The `swrm_*` token is **not a wallet private key.** It can't sign transactions on its own. The signing flow is:

1. You hold a wallet private key (a separate 64-hex string, generated by `cast wallet new` or Circle's wallet service — **never** stored in `~/.arc-canteen/`).
2. Your code uses the private key to sign a transaction *locally* (`web3.py`, `viem`, `forge create`, etc.).
3. The signed-transaction bytes are submitted to the proxy via `eth_sendRawTransaction`.
4. The proxy forwards the bytes to an Arc node.

If someone steals your `swrm_` token they can eat your rate limit and attribute spurious activity to your handle; **they can't drain a wallet** with it. Rotate (`arc-canteen rotate-rpc-key`) if leaked, but don't panic.

### How you actually use it

Three patterns. Pick whichever fits the task.

**Pattern A — One-off via the canteen CLI:**

```bash
arc-canteen rpc eth_chainId
arc-canteen rpc eth_blockNumber
arc-canteen rpc eth_getBalance '["0xYOUR_ADDRESS", "latest"]'
```

**Pattern B — Set `$RPC` once, then any tool that takes `--rpc-url`:**

```bash
source ~/.arc-canteen/env
cast block-number --rpc-url $RPC
cast chain-id --rpc-url $RPC
forge create --rpc-url $RPC src/MyContract.sol:MyContract
```

**Pattern C — From Python or TypeScript:**

```python
import os
from web3 import Web3
w3 = Web3(Web3.HTTPProvider(os.environ["RPC"]))
print(w3.eth.chain_id, w3.eth.block_number)
```

```typescript
import { createPublicClient, http } from "viem";
const client = createPublicClient({ transport: http(process.env.RPC!) });
const block = await client.getBlockNumber();
```

### Why the proxy exists

1. **Telemetry attribution.** The proxy logs every call. The 30% Traction score in the hackathon rubric reads from this telemetry. Without per-user auth, Canteen has no way to tell which team's agent generated which on-chain activity.
2. **Per-team rate limiting.** Without auth, one runaway loop from any team would degrade the testnet for everyone.
3. **Operational control.** Canteen can spin the testnet up/down and revoke individual tokens without distributing new node URLs.

---

## Reporting traction (the 30% rubric weight)

The `arc-canteen` CLI is not just an RPC proxy — it's also the **traction telemetry surface** that the judging rubric reads. **Until the team starts calling `update-traction` and `update-product` regularly, the rubric scoreboard for Archimedes reads zero, regardless of what's shipped.**

Two commands matter:

```bash
# Log a product / feature update — call after merging anything meaningful
arc-canteen update-product "Live testnet deploy at http://18.171.230.205/ — 10 contracts on Arc + wallet-connect UI + 3 paper-grounded strategies"

# Log a traction event — call every time you talk to a potential user or onboard someone
arc-canteen update-traction "Shared live demo URL with two crypto-native users — first external traffic on the EC2 deploy"
```

Run `arc-canteen status` to view your current dashboard — what the judges will see when they look at your handle. The judging-rubric assessment in [`docs/judging-rubric-assessment.md`](docs/judging-rubric-assessment.md) breaks down where we currently stand on each of the 4 weighted criteria.

---

## Using the context-arc submodule

[`submodules/context-arc/`](submodules/context-arc/) is Circle's curated bundle of Arc + Circle developer documentation and 5 reference codebases (`arc-commerce`, `arc-escrow`, `arc-fintech`, `arc-multichain-wallet`, `arc-p2p-payments`). It is the single best place to look up anything Arc- or Circle-specific.

Start with [`submodules/context-arc/AGENTS.md`](submodules/context-arc/AGENTS.md) for the entry-point index. Task-routed quick reference:

| Task                                            | Start with                                                            |
| ----------------------------------------------- | --------------------------------------------------------------------- |
| Anything Arc-specific (chain config, deploy)    | `circlefin-skills/use-arc.md`                                         |
| USDC transfers / balances / approvals           | `circlefin-skills/use-usdc.md`                                        |
| Cross-chain USDC (CCTP, Bridge Kit)             | `circlefin-skills/bridge-stablecoin.md`                               |
| Custodial / dev-controlled wallets              | `circlefin-skills/use-developer-controlled-wallets.md`                |
| Unified balance / nanopayments (Gateway)        | `circlefin-skills/use-gateway.md`                                     |
| Contract templates, deploy, monitor             | `circlefin-skills/use-smart-contract-platform.md`                     |
| AI agent that holds + spends USDC itself        | `docs/developers.circle.com/agent-stack.md` (Circle CLI + Agent Wallets) |
| Onchain agent identity / job settlement         | `docs/docs.arc.network/build/agentic-economy.md` (ERC-8004 / ERC-8183) |

Refresh upstream when Circle pushes new docs:

```bash
git submodule update --remote submodules/context-arc
```

Or via the canteen CLI: `arc-canteen context sync` (drops a copy into `~/.arc-canteen/context/`).

---

## Platform-specific notes

### macOS (Dan, Chuan)

mambaforge + everything else works natively. No special setup. If on Apple Silicon, the
osx-arm64 conda channels are well-supported; psycopg2-binary, web3.py, and backtrader all
have arm64 wheels.

### Linux (Daniel, Önder)

Native experience. Identical to macOS for our purposes. Standard apt/dnf installs for
docker + node if not already present.

### Windows (Marten)

**Two options. We recommend WSL2.**

**Option A — WSL2 (recommended):** Get a Linux experience inside Windows. Foundry, conda,
Docker, and everything else "just works."

```powershell
# In PowerShell as Administrator
wsl --install
# Restart, open Ubuntu, then follow the Linux instructions above
```

WSL2 docs: [microsoft.com/wsl](https://learn.microsoft.com/en-us/windows/wsl/install).

**Option B — Native Windows:** Conda works on Windows; some pain points:

- **Foundry on native Windows** is unsupported officially. Use Git Bash + the standalone
  binaries from foundry's releases, or use WSL2 just for foundry.
- **psycopg2-binary** wheels exist for Windows but occasionally need a Visual Studio
  Build Tools install. If `pip install psycopg2-binary` fails, install the
  [Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) and retry.
- **Docker Desktop on Windows** uses WSL2 under the hood anyway, so you already need WSL2
  even on Option B.

Practical take: **Marten, set up WSL2.** It removes every Windows-specific pain point and
matches Dan + Daniel + Önder's Linux/macOS workflow exactly.

---

## Development workflow

See [`CLAUDE.md`](CLAUDE.md) for full conventions. Headline points:

- **Branch model:** `feat/<name>` for features; `<discord-handle>/<name>` for personal
  staging. PRs to `develop`; promote to `main` once stable.
- **One approving review** for non-contract changes; two for contract changes.
- **Commit style:** imperative mood with optional scope tags (`[strategy]`, `[backend]`,
  `[contracts]`, etc.).
- **Daily sync:** 13:00 UTC = 8am Chicago / 10am São Paulo / 14:00 London / 15:00 Bremen /
  16:00 Ankara.

### Running tests

```bash
pytest                       # backend tests (under tests/)
cd ui && npm run lint        # frontend lint (ESLint 10)
cd contracts && forge test   # contract tests (10 contracts deployed, full Forge suite)
```

### Lint + format

```bash
ruff format src/             # auto-format
ruff check src/ --fix        # auto-fix lint
```

---

## Security notes

A short list of hygiene items worth surfacing explicitly.

### arc-canteen credentials are secrets

The `arc-canteen login` flow writes credentials to `~/.arc-canteen/env`. The file is
permissioned `0600` (owner read/write only) by the CLI — verify yours is too with
`ls -la ~/.arc-canteen/env`.

The file contains your **personal RPC endpoint URL** with an **embedded server token**
(format: `swrm_<64-hex>`). **Treat the token like an API key:**

- 🚫 Do NOT commit `~/.arc-canteen/env` (not in this repo and not in any dotfiles repo).
- 🚫 Do NOT paste the full RPC URL into Discord channels, screenshots, pitch decks, GitHub
  issues, or AI chats. Use `$RPC` in commands so the literal token doesn't appear in
  shell history.
- 🚫 Do NOT share with teammates — each team member has their own.
- ✅ If you suspect leakage, run `arc-canteen rotate-rpc-key` to mint a fresh token and
  invalidate the old one. Cheap; takes seconds.

### Wallet hygiene

- Use a **dedicated dev wallet** for all hackathon testing — never connect a wallet that
  holds real assets.
- Private keys go in `.env` files (gitignored). Never commit secrets.
- The platform's signer key (for the rebalance contract calls) lives in environment
  variables, not in the repo.

### Dependency hygiene

- The Python deps in `environment.yml` are loosely-pinned for v1. We'll tighten when we
  move to a `pyproject.toml`-driven workflow.
- The `arc-canteen` CLI installs from the official
  [the-canteen-dev/ARC-cli](https://github.com/the-canteen-dev/ARC-cli) repo. Verify the
  URL when running `uv tool install` — typosquatting is a real attack pattern.
- All transitive deps of the arc-canteen CLI are standard CLI-tooling packages (typer,
  click, httpx, rich, pyyaml). The `annotated-doc` dep is from
  [fastapi/annotated-doc](https://github.com/fastapi/annotated-doc) — legitimate.

### GitHub OAuth scopes

When you ran `arc-canteen login`, the GitHub device flow authorized the Canteen app on
your account. Verify the granted scopes at
[github.com/settings/applications](https://github.com/settings/applications). For a
hackathon-traction tool, expected scopes are minimal (`read:user`, `user:email`). If
`repo` or `admin:*` scopes were granted, revoke and re-authenticate.

---

## Roadmap

Two-week hackathon roadmap in [`docs/design.md` § 8](docs/design.md). Post-hackathon
direction in [`docs/demo-script-pitch-deck-outline.md`](docs/demo-script-pitch-deck-outline.md)
slide 8.

## Team

5 builders across 5 timezones, with deep coverage on every load-bearing skill — see the
team table in [`CLAUDE.md`](CLAUDE.md).

## Contributing

Fork, branch, PR to `develop`. See [`CLAUDE.md`](CLAUDE.md) for engineering conventions.

## License

[Unlicense](LICENSE) — full public-domain dedication. Use, modify, distribute freely. No
warranty.

---

> *In classical Athens, the agora was the heart of the city — the original
> information-processing machine. AI agents are the new citizens.*
>
> — [Agora Agents Hackathon](https://luma.com/7i50p2r9)

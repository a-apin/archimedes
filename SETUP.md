# Setup

This doc walks you from a fresh clone to a working local Archimedes stack. Works on **macOS, Linux, and Windows**. Once you can run [`docker compose up -d --build`](#step-2--spin-up-the-stack-recommended-path) and see services pass their health checks, you're done — everything else here is optional polish.

> **Status:** Day-10 (2026-05-22). Lead: Chuan (infra); cross-platform support: Marten (Windows / WSL2), Daniel R. (Linux), Dan + Chuan + Önder (macOS).

## Prerequisites

| Tool                                                                             | Purpose                                            | Required for           |
| -------------------------------------------------------------------------------- | -------------------------------------------------- | ---------------------- |
| [Git](https://git-scm.com/)                                                      | Source control                                     | Everyone               |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/)                | Local stack (Postgres + Redis + 4 services)        | Everyone               |
| [mambaforge / miniconda](https://github.com/conda-forge/miniforge)               | Python environments (for `pytest`, scripts)        | Backend / strategy dev |
| [Node.js 20+](https://nodejs.org/) (via [nvm](https://github.com/nvm-sh/nvm))    | Frontend toolchain                                 | Frontend dev           |
| [Foundry](https://book.getfoundry.sh/getting-started/installation)               | Smart contract compilation + testing               | Contract dev           |

## Recommended path: Docker is the source of truth

The local docker-compose stack reproduces the production EC2 deployment exactly. **What you see locally is what runs on the team's shared instance.** Most contributors don't need conda or Node.js for day-to-day work — the containers ship their own Python + Node. Install the host tools only when you want to:

- Run `pytest` against the live stack (conda env)
- Run the analytics-engine CLI directly (conda env)
- Develop the frontend with Vite hot-reload (Node)
- Compile + test smart contracts (Foundry)

## Step 1 — Clone the repository (with submodules)

```bash
git clone --recurse-submodules git@github.com:a-apin/archimedes-arcadia.git archimedes
cd archimedes
```

If you already cloned without `--recurse-submodules`, populate them now:

```bash
git submodule update --init --recursive
```

The `submodules/` directory carries Circle's [`context-arc`](submodules/context-arc/) (Arc + Circle developer docs and 5 sample codebases) plus two reference projects ([`KnowledgeBase`](submodules/KnowledgeBase/) — paper-analysis pipeline; [`Linus`](submodules/Linus/) — AI orchestration project, reference only). Full Arc + Circle reference index in [`ARC.md`](ARC.md).

## Step 2 — Spin up the stack (recommended path)

```bash
cp .env.example .env
# Edit .env and fill in ANTHROPIC_AUTH_TOKEN (GLM via Canteen submission)
# OR ANTHROPIC_API_KEY (your own free Anthropic key) — see OPERATIONS.md § LLM backends
docker compose up -d --build
```

On first run this downloads ~150 MB of base images and builds the backend image. Subsequent runs are seconds. The stack brings up **6 services**:

| Service     | Port | URL                              | What it is |
| ----------- | ---- | -------------------------------- | ---------- |
| `nginx`     | 80   | <http://localhost>               | React UI build + reverse-proxy to backend |
| `backend`   | 8000 | <http://localhost:8000/docs>     | FastAPI app (Swagger UI auto-generated) |
| `agent`     | —    | (no HTTP)                        | Autonomous strategy runner: evaluates signals, derives regime, rebalances vaults, publishes traces |
| `oracle`    | —    | (no HTTP)                        | Price feeder — pushes to `PriceOracle.sol` via Circle Wallets API |
| `postgres`  | 5432 | `postgres://archimedes@localhost:5432/archimedes` | Strategies, backtests, traces |
| `redis`     | 6379 | `redis://localhost:6379/0`       | Regime state cache + agent scratch |

Watch health checks succeed: `docker compose ps`. Backend / Postgres / Redis report `(healthy)`; agent / oracle / nginx report `running` (no HTTP probe — expected).

## Step 3 — Verify it works

| Open in your browser | Expect to see |
| -------------------- | ------------- |
| <http://localhost>           | Live React UI (Landing / Generate / Library / Corpus / Portfolio / Reasoning / Learnings) |
| <http://localhost:8000>      | `{"name":"Archimedes","tagline":"Linus for quantitative finance",…}` |
| <http://localhost:8000/health> | `{"status":"ok",…,"corpus_papers":10000,…}` |
| <http://localhost:8000/docs> | Swagger UI auto-rendered from the API contract |

If `corpus_papers` < 10000 in `/health`, the corpus seed hasn't completed yet — wait a few seconds and retry. Full corpus walkthrough in [`docs/corpus-architecture.md`](docs/corpus-architecture.md).

## Step 4 — Tear down

```bash
docker compose down                 # stop containers; keep data
docker compose down -v              # stop containers; wipe postgres volume (fresh start)
docker compose logs -f backend      # tail backend logs
docker compose logs postgres        # database logs
```

---

## Host-tool setup (only when you need it)

### Python environment (for `pytest` + analytics CLI)

The repo ships an [`environment.yml`](environment.yml) defining all Python deps.

```bash
conda env create -f environment.yml
conda activate archimedes
```

If you prefer mamba (faster): `mamba env create -f environment.yml && mamba activate archimedes`.

Verify:

```bash
python --version    # → Python 3.12.x
uv --version        # → uv 0.x
which pytest        # → /.../envs/archimedes/bin/pytest
```

### arc-canteen CLI (every team member, individually)

[arc-canteen](https://github.com/the-canteen-dev/ARC-cli) is Canteen's hackathon CLI — it's both your per-developer RPC proxy for Arc testnet AND the telemetry surface the judging rubric reads. Each team member installs it personally.

```bash
uv tool install git+https://github.com/the-canteen-dev/ARC-cli
arc-canteen login        # GitHub device flow
arc-canteen --help
```

After login, the CLI writes credentials to `~/.arc-canteen/env`. **The token in there is a secret.** See [`OPERATIONS.md` § Security notes](OPERATIONS.md#security-notes) before pasting it anywhere.

To get `$RPC` available in every new shell:

```bash
echo '[ -f ~/.arc-canteen/env ] && . ~/.arc-canteen/env' >> ~/.zshrc   # or ~/.bashrc
```

Full RPC walkthrough: [`OPERATIONS.md` § Understanding the RPC URL](OPERATIONS.md#understanding-the-rpc-url).

### Foundry (for smart contract dev)

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

Verify against Arc testnet:

```bash
source ~/.arc-canteen/env       # ensures $RPC is set
cast block-number --rpc-url $RPC
cast chain-id --rpc-url $RPC    # → 0x4CEF52 (Arc testnet, chain ID 5042002)
```

Contract sources are in [`contracts/src/`](contracts/src/). Build + test:

```bash
cd contracts && forge build && forge test
```

### Frontend dev (Vite hot-reload)

The docker stack serves the built React bundle on port 80. For hot-reload during frontend development:

```bash
cd ui && npm ci && npm run dev
```

(Use `npm ci` not `npm install` — uses the locked `package-lock.json` exactly.)

---

## Running the test suite

> ⚠️ **`pytest` requires the docker stack to be running.** The tests depend on Postgres + Redis being reachable. Spin up the stack first:
>
> ```bash
> docker compose up -d --build
> ```
>
> Use the `--build` flag whenever you've changed dependencies (`environment.yml`, `requirements.txt`, `package.json`) so the images are rebuilt with the new deps. For a quick restart with unchanged code, plain `docker compose up -d` is fine.

The backend suite runs with a single command from the repo root — no flags, no `PYTHONPATH`, no cwd juggling. `pytest.ini` wires `pythonpath`, `testpaths`, and a verbose default.

```bash
docker compose up -d --build            # required prereq
conda activate archimedes               # one-time per shell
pytest                                  # 806 backend tests, verbose, green
```

`pytest` is configured `-v --tb=short --durations=10` — you see each test name, a short traceback on any failure, and the slowest tests. Filter as usual:

```bash
pytest -k selection_bias                # by name substring
pytest backend/tests/services/          # by directory
pytest --cov=archimedes --cov-report=term-missing  # coverage
```

The **analytics-engine** has its own suite (own `pyproject.toml`):

```bash
cd analytics-engine && uv sync && uv run pytest    # 16 tests, green
```

Other suites:

```bash
cd ui && npm run lint                   # ESLint
cd contracts && forge test              # Foundry
```

### Honest coverage picture

Line coverage on the `archimedes` package: **~32% overall**, intentionally uneven — the intelligence / rigor core is well-covered because that's the defensible part:

| Area | Coverage | Notes |
| ---- | -------- | ----- |
| Selection-bias gate (DSR/PBO/walk-forward), Kelly, statistical regime | high | Önder's math — unit-tested incl. spec sanity cases |
| Strategy provider, fusion, arXiv corpus, backtest mapper/repo | 35–70% | core data path |
| `api/` (FastAPI routes) and `chain/` (web3/oracle/agent) | low | exercised by the live docker stack + integration tests, not unit-mocked (testnet/Circle-SDK bound) |

One integration test (`test_backtest_pipeline_integration.py`) needs a DB row seeded by the deploy pipeline; it runs in CI/deploy and is excluded from the default `pytest` run so a cold clone stays green (tracked for a hermetic fix).

### Lint + format

```bash
ruff format backend/archimedes      # auto-format
ruff check backend/archimedes --fix # auto-fix lint
```

---

## Platform-specific notes

### macOS

mambaforge + everything else works natively. No special setup. If on Apple Silicon, the osx-arm64 conda channels are well-supported; `psycopg2-binary`, `web3.py`, and `backtrader` all have arm64 wheels.

### Linux

Native experience. Identical to macOS for our purposes. Standard `apt`/`dnf` installs for Docker + Node if not already present.

### Windows

**Two options. WSL2 strongly recommended.**

**Option A — WSL2 (recommended):** Get a Linux experience inside Windows. Foundry, conda, Docker, and everything else "just works."

```powershell
# In PowerShell as Administrator
wsl --install
# Restart, open Ubuntu, then follow the Linux instructions above
```

WSL2 docs: [microsoft.com/wsl](https://learn.microsoft.com/en-us/windows/wsl/install).

**Option B — Native Windows:** Conda works on Windows; some pain points:

- **Foundry on native Windows** is unsupported officially. Use Git Bash + the standalone binaries from Foundry's releases, or use WSL2 just for Foundry.
- **psycopg2-binary** wheels exist for Windows but occasionally need a Visual Studio Build Tools install. If `pip install psycopg2-binary` fails, install the [Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) and retry.
- **Docker Desktop on Windows** uses WSL2 under the hood anyway, so you already need WSL2 even on Option B.

Practical take: **set up WSL2.** It removes every Windows-specific pain point and matches the macOS + Linux workflow exactly.

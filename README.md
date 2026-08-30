# Archimedes

*The lever is academic research. The fulcrum is autonomous AI. The world is your portfolio.*

[![License: Unlicense](https://img.shields.io/badge/license-Unlicense-blue.svg)](LICENSE)
[![Settled on: Arc](https://img.shields.io/badge/settled%20on-Arc-2A4DD1.svg)](https://www.arc.network/)

## What it is

Describe what you want from a portfolio in plain English. Archimedes proposes strategies
grounded in a corpus of 10,000 arXiv quantitative-finance preprints, puts each one through
four admission checks — a deflated Sharpe ratio, a probability of backtest overfitting, a
walk-forward out-of-sample pass, and a static look-ahead audit — and lets you deploy what
survives into a non-custodial vault on the Arc testnet, where every decision the agent
reaches is written down as a reasoning trace you can read.

Three things to know before anything else:

- **Most briefs fail the gate. That is the product working.** A strategy that fails is
  shown to you with its DSR, PBO and out-of-sample numbers, so you can see exactly why.
  How many strategies in the curated library currently pass is **unestablished** — the
  live gate is the only authority on that, and this file will never quote a count.
- **Research marketplace, not a casino.** Payments are real (USDC on Arc); settlement is
  stubbed pending mainnet. Single-user MVP — multi-user library and social features are
  roadmap.
- **Arc testnet only** (chain `5042002`). Faucet USDC comes from
  <https://faucet.circle.com/> (20 USDC / 2h — on Arc, USDC *is* gas). **No real money is
  at risk, by design.** Arc has no mainnet yet; mainnet launch, real-funds custody, and
  the regulatory architecture are roadmap.

The gate is evidence, not proof. The deflation prices in how many candidates were searched
before this one was picked; the 0.90 DSR bar is a deliberate calibration, a one-sided ~10%
test. PBO is computed and disclosed on every passport but does not block the badge while
the library holds fewer than ten graded strategies — below that, CSCV lacks the power to
gate honestly, so it reports `NOT_RUN` with the reason rather than a pass. Full method and
thresholds: [`docs/rigor-methods.md`](docs/rigor-methods.md) and
[`docs/specs/selection-bias-corrections-spec.md`](docs/specs/selection-bias-corrections-spec.md).

## Quickstart

### The live site

<https://archimedes-arc.com/> runs against Arc testnet. Sign in with email and password,
describe a brief, and read the verdict — a wallet is only needed to deploy a vault. Fund a
wallet from the Circle faucet above first if you want to go all the way through.

### Run it locally

```bash
git clone --recurse-submodules https://github.com/a-apin/archimedes.git
cd archimedes
cp .env.example .env       # generate BETTER_AUTH_SECRET; LLM credentials are optional
docker compose up -d --build
```

Then open <http://localhost:8080>. The backend shares that ingress:
<http://localhost:8080/health> for the honesty flags, <http://localhost:8080/docs> for the
API. Full walkthrough — prerequisites, host tooling, platform notes, the test suite — is
[`SETUP.md`](SETUP.md). `make help` lists the dev targets.

### The CLI

The `archimedes` CLI runs the rigor gate over your own returns series. It is **not on PyPI
yet**, so install it from this repo:

```bash
conda env create -f environment.yml   # first time only
conda activate archimedes
pip install -e ./cli
```

Check the install and read the machine-readable contract — no network, no account:

```bash
archimedes --version        # archimedes, version 0.1.0
archimedes manifest         # JSON: every command, flag, exit code, and cost class
```

Sign in (Better Auth email + password — no wallet signature), then read your meter and run
the gate:

```bash
archimedes login            # prompts; or set ARCHIMEDES_EMAIL / ARCHIMEDES_PASSWORD
archimedes meter            # today's generation usage + the live price quote
archimedes verify returns.csv --trials 40
```

`returns.csv` is two columns — date and daily return — or `-` to read stdin; a header row
is skipped automatically. Exit codes are a stable contract: `0` the gate passed, `1` the
gate ran and failed, `2` bad input or no session, `3` not implemented in this release.
Branch on `1` specifically — anything else means no verdict was produced:

```bash
archimedes verify returns.csv
case $? in
  0) echo "gate passed" ;;
  1) echo "gate failed, not deploying"; exit 1 ;;
  *) echo "verify did not run"; exit 2 ;;
esac
```

`verify` sends only numbers; your strategy code is never uploaded. Two of the gate's four
checks cannot run over a bare returns series — PBO needs a trial matrix and the look-ahead
audit needs strategy source — so both always report `not_evaluable` with a reason, never a
silent pass. `archimedes backtest` and `archimedes verify --local` are not implemented yet
and exit `3`. Full reference: [`cli/README.md`](cli/README.md) and
[`skills/archimedes-cli/SKILL.md`](skills/archimedes-cli/SKILL.md).

## How a strategy gets made

A brief fans out across a regime × mechanism steer grid. Each proposer selects candidate
papers from the corpus, and fusion turns them into a strategy spec in the internal DSL.
Deterministic critics then cull the pool with zero LLM calls: provenance and embargo audit,
a real backtest per survivor, and a null check that a candidate must beat buy-and-hold by
at least 5 bps net — if none clears it, the run abstains rather than shipping a weak
winner. A deterministic synthesizer ranks what is left; only the K=1 winner is persisted,
and the rejected alternatives are kept and surfaced so you can see what was tried. The
externalized rigor gate then runs on the winner, outside the debate, and it is what enables
Deploy. Mechanism in full:
[`docs/specs/multi-agent-debate-spec.md`](docs/specs/multi-agent-debate-spec.md).

## Documentation

| If you want to… | Read |
|---|---|
| Run it locally, including the test suite | [`SETUP.md`](SETUP.md) |
| Drive the whole journey programmatically | [`docs/agent-api.md`](docs/agent-api.md) |
| Use the command-line tool | [`cli/README.md`](cli/README.md) |
| Load a grounded agent skill (every claim file:line cited) | [`skills/README.md`](skills/README.md) |
| Know what the product *is* (the locked spine) | [`docs/user-stories.md`](docs/user-stories.md) |
| See the architecture map and the stack | [`docs/architecture.md`](docs/architecture.md) |
| Understand the rigor gate's math | [`docs/rigor-methods.md`](docs/rigor-methods.md) |
| Read the papers the claims rest on | [`docs/cited-literature.md`](docs/cited-literature.md) |
| Understand the paper corpus end to end | [`docs/corpus-architecture.md`](docs/corpus-architecture.md) |
| Understand Arc / Circle integration | [`docs/arc-integration.md`](docs/arc-integration.md) |
| Operate the live stack | [`docs/runbooks/operations.md`](docs/runbooks/operations.md) |
| Browse every design + planning doc | [`docs/README.md`](docs/README.md) |
| Add a doc without misfiling it | [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) |
| Know who owns what | [`docs/team.md`](docs/team.md) |
| Get context for a Claude Code session | [`CLAUDE.md`](CLAUDE.md) |

Live numbers come from the live system, never from this file: contract census is
`GET /api/config/contracts`, honesty flags are `GET /health`, and the test count is
`pytest --collect-only -q | tail -1`.

## Known limitations (Arc testnet)

- **The corpus is 10,000 arXiv preprints, not peer-reviewed papers.** Candidate selection
  over it is a **keyword filter**. Only that already-selected candidate set is then
  re-scored, at request time, across title and abstract — by `all-MiniLM-L6-v2` when that
  model is loaded in-process, by lexical TF-IDF when it is not. Nothing is precomputed: the
  `papers` schema carries title and abstract text and no vector column, so no index is
  built ahead of the request. `/health` names the scorer that is actually live in
  `paper_rag` and `paper_rag_reason`, publishes `corpus_embedded_at_rest: false` for the
  corpus itself, and publishes `rerank_candidate_cap` because only that many candidates
  reach the model. Read those fields rather than this line. Tracked in
  [#778](https://github.com/a-apin/archimedes/issues/778) and
  [#1488](https://github.com/a-apin/archimedes/issues/1488).
- **The knowledge graph is not built.** No KB artifact has ever been produced, so `/health`
  reports `corpus_kg_built: false` with zero entities and zero relations,
  `GET /api/corpus/graph` refuses with **503 `kb_artifact_not_found`** instead of
  synthesizing a graph, and `GET /api/corpus/kg/*` returns empty entity and relation sets.
  Citation-link extraction over the corpus is roadmap. Tracked in
  [#778](https://github.com/a-apin/archimedes/issues/778).
- **AMM pools are thinly funded**, so many swaps are not executable. The agent's liquidity
  guard skips empty pools and logs the reason instead of routing capital into a doomed
  trade — see [`docs/arc-integration.md`](docs/arc-integration.md).
- **Not every reasoning trace is anchored on-chain.** The agent writes a trace for every
  decision it reaches, including a `skip`, and a decision that produced no transaction has
  no hash to verify. Read `GET /api/traces/` and check `arc_tx_hash` before treating a
  trace as anchored.

## Contributing

Fork, branch (`<your-handle>/<short-name>`), PR to `main`. One logical change per PR. Never
force-push `main`. Never commit secrets. Full conventions — branch model, review rules,
commit style, testing — are in [`CLAUDE.md`](CLAUDE.md); doc conventions are in
[`docs/CONVENTIONS.md`](docs/CONVENTIONS.md).

## License

[Unlicense](LICENSE) — full public-domain dedication. Use, modify, distribute freely. No
warranty.

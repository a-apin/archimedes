# Archimedes — Claude Code Context

> **Status:** Living context doc. Written 2026-05-12 (Day 2), revised 2026-05-13 (Day 3)
> for the marketplace pivot and rigor-as-wedge decision, revised 2026-05-14 (Day 4) after
> the 10-contract Arc-testnet deployment, live UI, and ownership reshuffle. Intent: drop
> at the root of the `archimedes` repo and read at the start of every Claude Code session.
>
> **Architecture lineage to read together:**
> - [`docs/design.md`](docs/design.md) — original single-vault architecture
> - [`docs/specs/ecosystem-design-spec.md`](docs/specs/ecosystem-design-spec.md) — Day-3
>   two-tier marketplace pivot (synthetic protocol + AMM + VaultFactory + agent-as-a-service)
> - [`docs/specs/component-interfaces-spec.md`](docs/specs/component-interfaces-spec.md) —
>   frozen-interface contract for the 5-person concurrent build (Dan owns `IStrategyProvider`)
> - [`docs/specs/strategy-passport-spec.md`](docs/specs/strategy-passport-spec.md) +
>   [`docs/specs/selection-bias-corrections-spec.md`](docs/specs/selection-bias-corrections-spec.md)
>   — the four-primitive admission gate for Tier 1 strategies
> - [`docs/agora_project_analysis.md`](docs/agora_project_analysis.md) — red-team synthesis
>   driving the Day-3 rigor-as-wedge framing

## Project

**Archimedes** — a fund-of-funds portfolio agent that turns published quant finance research
into investable, backtested strategies, then constructs personalized portfolios of RWA tokens
and yield instruments on Arc with USDC settlement.

> *"Give me a lever long enough and I shall move the world."* The lever here is academic
> research; the fulcrum is autonomous AI; the world is your portfolio.

Built for the [**Agora Agents Hackathon**](https://luma.com/7i50p2r9) — Canteen × Circle × Arc,
May 11–25, 2026.

- Repository: [`github.com/hackagora/archimedes-arcadia`](https://github.com/hackagora/archimedes-arcadia)
- Discord: **Archimedes Arcadia** server
- Branch model: `develop` is the integration branch; `main` is protected and promoted-to
  once stable. Per-owner staging branches (e.g. `dbrowneup/<name>`, `moonshot/<name>`,
  `marten`) are personal scratch space before opening a PR
- Live testnet deploy: [`http://18.171.230.205/`](http://18.171.230.205/) (EC2,
  Chain ID `5042002` / `0x4cef52`, Arc testnet)
- License: [Unlicense](https://unlicense.org) — full public-domain dedication

## North Star

A user with idle USDC who wants thoughtful portfolio management — but is tired of black-box
robo-advisors, opaque AI funds, and "trust me bro" influencer copy-trading — connects a
wallet, picks a risk profile, and gets a portfolio constructed from strategies that come
with a **paper-grounded reasoning trace anchored on-chain**. Every position the agent takes,
every rebalance it executes, every regime shift it responds to, is hashed and verifiable.

The Agora narrative frames this:

> *Where AI agents make markets. The agora was where Athens did its thinking out loud.
> Markets are still doing the same job today; they are the social technology by which a
> civilization aggregates knowledge and decides what things are worth. AI agents are the new
> citizens.*

Archimedes is one such citizen — but one whose reasoning trail is open to inspection, whose
claimed alpha is bound to academic research rather than vibes, and whose settlement happens
at sub-second finality on Arc with USDC. The mathematician's name is fitting: he was the
original empiricist working from first principles. We work from peer-reviewed first
principles.

## Team (5 members; ~20-year age span; coverage on every load-bearing skill)

Roughly balanced bios. Ages are author estimates pending team confirmation. Discord handles
in parentheses; the human handles are what shows up in the channel.

| Name                       | Age (est.) | Discord            | Location  | TZ (May)| Role                                                                                                                |
| -------------------------- | ---------- | ------------------ | --------- | --------| ------------------------------------------------------------------------------------------------------------------- |
| **Dan Browne**             | 37         | dbrowneup          | Chicago   | UTC-5   | Strategy engine (Q-fin paper corpus, strategy library curation), pitch architecture. Senior Scientist @ LanzaTech, PhD biochemistry. Day job — evenings/weekends. |
| **Marten Windler**         | ~31        | Marten             | Bremen    | UTC+2   | Off-chain → on-chain integration via Arc CLI; pairs with Chuan. Systems Engineering @ U. Bremen, ML-uncertainty B.Sc. thesis. ROS + Python/C++/Rust. Coordinator lean. |
| **Daniel Reis dos Santos** | early 20s  | The go guy / Daniel [vibe] | Brazil    | UTC-3   | Frontend ownership (Next.js + TailwindCSS). Backend engineer day-side. Go / Java / TypeScript, distributed systems, AWS, Terraform. Healthcare-ERP day role.  |
| **Chuan Bai**              | ~early 40s | moonshot           | London    | UTC+1   | Architecture + on-chain (smart contracts on Arc, Circle SDK integration). CTO @ [Gyld Finance](https://www.gyld.fi/); built CoinShares' next-gen trading platform; RWA tokenization expertise. PhD HPC. |
| **Önder Akkaya**           | ~21        | Önder              | Ankara    | UTC+3   | Portfolio math (Kelly Criterion / +EV, backtest evaluation, risk pricing). Statistics @ Hacettepe; [ASA Statistical Insight World Champion](https://www.linkedin.com/in/onder-akkaya/); President of [TİD-Genç](https://www.tid.org.tr/); trainee actuary. |

Two team members (Dan, Daniel R) have demanding day roles and commit evenings/weekends.
Chuan runs a real startup but treats the hackathon as serious focus. Marten and Önder are
students with flexible time.

**Daily sync window:** 13:00 UTC = 8am Chicago / 10am São Paulo / 14:00 London / 15:00
Bremen / 16:00 Ankara. Works across the whole team without anyone in unsocial hours.

**Schedule/flow owner:** Marten (showing coordinator instincts since Day 1). Standups in
`#standups` in Discord.

### Non-team contacts

- **Anuhya** (Discord: `moonshot` in the Canteen server, *NOT* the Chuan-moonshot in
  Archimedes Arcadia) is a **Canteen admin** running the hackathon. She is a stakeholder /
  judge-adjacent, not a teammate.

### Role allocation by load-bearing component

| Component                                          | Owner            | Backup                |
| -------------------------------------------------- | ---------------- | --------------------- |
| Strategy engine + Q-fin paper corpus curation       | Dan              | Önder                 |
| Backtesting / strategy-passport math + risk pricing | Önder            | Dan                   |
| Backend Python layer (FastAPI, API, services, models) | Daniel R.      | Marten                |
| On-chain integration layer (`backend/archimedes/chain/`, oracle runner) | Chuan | Marten          |
| Frontend (React + Vite + viem, wallet UX, trade tab) | Marten (current) / Daniel R. | Dan       |
| Smart contracts (Arc, Foundry, 10 deployed)         | Chuan            | Marten                |
| Infra / EC2 / CI/CD / docker-compose                | Chuan            | Daniel R.             |
| Architecture + design decisions                     | Chuan (lead)     | full team             |
| Pitch deck + demo script + Claude Design + judging  | Dan              | Marten                |

The post-Shimon backend slot resolved as a Daniel R. (Python backend) + Chuan (on-chain
integration) split — the `chain/` subdirectory under `backend/archimedes/` is Chuan's, and
`api/` + `services/` + `models/` + `interfaces/` are Daniel R.'s. Both layers share the
`backend/archimedes/` Python package and the FastAPI app boots them together via
`main.py`. Marten currently has the most recent commits on the React UI as he comes up to
speed on what the team has built.

## Setup

Read [`README.md`](README.md) for the full setup walkthrough — Python conda env via
[`environment.yml`](environment.yml), Node.js frontend, Foundry for contracts, the
[arc-canteen CLI](https://github.com/the-canteen-dev/ARC-cli) for traction tracking,
and a Docker-compose-driven local stack that mirrors the production EC2 deployment.
Platform-specific notes for macOS / Linux / Windows (Önder on macOS; WSL2 recommended for Marten).

**Spinning up locally is one command** once `.env` is filled in:

```bash
cp .env.example .env       # fill in ANTHROPIC_API_KEY + RPC at minimum
docker compose up -d --build
```

Then <http://localhost> for the UI mockups and <http://localhost:8000/docs> for the
backend API. See README § "Run Archimedes locally" for the full walkthrough.

## External references — submodules

The repo carries three git submodules at [`submodules/`](submodules/):

- **[`submodules/context-arc/`](submodules/context-arc/)** — Circle's agent-facing
  developer docs and 5 reference codebases for Arc + Circle. **This is the canonical
  reference for any Arc/Circle integration question.** Start with
  [`submodules/context-arc/AGENTS.md`](submodules/context-arc/AGENTS.md) for the
  task-indexed entry-point table. Highest-leverage files for our build:
    - `circlefin-skills/use-smart-contract-platform.md` — contract deploy + monitor (Chuan's lane)
    - `circlefin-skills/bridge-stablecoin.md` — CCTP + Gateway for RWA bridging (Marten's lane)
    - `circlefin-skills/use-gateway.md` — unified balance + nanopayments
    - `samples/arc-escrow/` — closest existing pattern to our vault contract
    - `samples/arc-multichain-wallet/` — CCTP integration patterns
    - `samples/arc-p2p-payments/` — Paymaster + USDC patterns
  Refresh upstream with `git submodule update --remote submodules/context-arc` or
  `arc-canteen context sync` (drops into `~/.arc-canteen/context/`).
- **[`submodules/KnowledgeBase/`](submodules/KnowledgeBase/)** — Dan's scientific-
  paper analysis pipeline (PyMuPDF extract + SPECTER2 embeddings + HDBSCAN/BERTopic
  clustering + REBEL/SciSpacy knowledge graph). For Archimedes, **don't port wholesale**
  — read it as a reference implementation. The patterns worth lifting for the
  Tier-1 arxiv extraction pipeline:
    - `papers_analysis/extract.py` — PyMuPDF caching pattern (~71 files/s)
    - `papers_analysis/metadata.py` — paper-corpus schema (maps to our `paper_corpus` table)
    - `papers_analysis/summarize.py` — Ollama-driven methodology synthesis (we'd use Claude)
- **[`submodules/Linus/`](submodules/Linus/)** — Dan's personal AI orchestration
  project. Reference only; nothing to port to Archimedes. The
  [`experiments/archimedes/`](submodules/Linus/experiments/archimedes/) and
  [`experiments/agora-hackathon/`](submodules/Linus/experiments/agora-hackathon/)
  directories contain the priors that seeded several of our current `docs/` files.

## arc-canteen CLI — telemetry surface for the 30% Traction weight

Every teammate authenticates individually (`arc-canteen login` → GitHub device flow).
The CLI is two things in one binary:

1. A **per-developer Arc-testnet RPC proxy.** `arc-canteen rpc <method>` proxies JSON-RPC
   calls through Canteen's server. The per-user `swrm_*` token in `~/.arc-canteen/env`
   authenticates. Allowlist: most reads + `eth_sendRawTransaction`. **The full RPC URL
   is a secret — see README § "Understanding the RPC URL" for the threat model.**
2. The **traction telemetry surface that the rubric reads.** Every meaningful product
   ship should be paired with an `arc-canteen update-product` call; every user we
   onboard or even talk to should be logged via `arc-canteen update-traction`. The
   30% Traction score is computed from this telemetry, not from anywhere else. **Until
   we start logging, the rubric reads zero.** See
   [`docs/judging-rubric-assessment.md`](docs/judging-rubric-assessment.md) for where we
   currently stand.

`arc-canteen status` shows your current dashboard — what the judges see.

## Tech Stack

Refer to [`docs/design.md` § 6](docs/design.md) for the full table. Headline choices as
they actually shipped (Day 4):

- **Backend:** Python 3.12 / FastAPI / Uvicorn, packaged as `backend/archimedes/` with
  subpackages `api/` (routes), `chain/` (on-chain integration + oracle runner),
  `interfaces/` (frozen Protocol classes), `models/` (Strategy, BacktestResult,
  ReasoningTrace, Portfolio dataclasses), `services/` (LocalStrategyProvider etc.)
- **Frontend:** React 19 + Vite 8 + viem 2.48 + plain CSS. Lives at [`ui/`](ui/) (the
  earlier `ui-mockups/` static-HTML directory is now retired but still in-tree).
  Components shipped: `Layout`, `WalletConnect` (MetaMask / Coinbase / generic browser
  wallet), `Trade`
- **Analytics engine:** [`analytics-engine/`](analytics-engine/) — uv-managed Python
  package with the backtrader runner. Loads strategy files from
  [`analytics-engine/strategies/`](analytics-engine/strategies/)
- **DB:** PostgreSQL + Redis (Postgres for strategies + backtests; Redis for live regime
  state)
- **LLM:** Claude API for strategy extraction, reasoning trace generation, user-facing
  explanations
- **Backtesting:** [backtrader](https://github.com/mementum/backtrader) for v1 per
  [`docs/specs/backtrader-vs-vectorbt-decision-memo.md`](docs/specs/backtrader-vs-vectorbt-decision-memo.md).
  Supersedes `docs/design.md` § 6 ("vectorbt / custom numpy engine") on this one
  line; design.md remains the architecture spec for everything else. Migration to
  vectorbt is a v2 problem if parameter-sweep speed becomes a constraint.
- **Smart contracts:** Solidity + Foundry, targeting Arc (EVM-compatible). **10 contracts
  deployed on Arc testnet as of Day 4**: `AMMPool`, `AMMRouter`, `AssetRegistry`,
  `PriceOracle`, `ReasoningTraceRegistry`, `SyntheticFactory`, `SyntheticToken`,
  `SyntheticVault`, `Vault`, `VaultFactory`. ABIs cached in
  [`contracts/abis/`](contracts/abis/) for backend + UI consumption
- **On-chain integration:** Circle SDK — Wallets (Circle-managed wallet for the oracle
  signer), USYC, Gateway, CCTP, Paymaster; viem on the UI side
- **Deployment:** Docker compose stack (5 services: postgres / redis / nginx / oracle /
  backend) running on an EC2 instance behind nginx. CI/CD wired via GitHub Actions per
  [`docs/infra-setup.md`](docs/infra-setup.md). Live at
  [`http://18.171.230.205/`](http://18.171.230.205/)

## Scope — the headline commitments

Refer to [`docs/mvp-scope-memo.md`](docs/mvp-scope-memo.md) for the full argument. Locked
decisions (5 of them as of Day 3):

1. **Primary RFB:** [RFB 04 — Adaptive Portfolio Manager](https://luma.com/7i50p2r9).
   Adjacent: RFB 02 (Kelly/+EV math primitive); RFB 06 (strategy leaderboard).
2. **Both on-chain stories:** vault contracts for portfolio allocation **and** the
   ReasoningTraceRegistry for verifiable provenance. Both shipping.
3. **Curated v1 strategy library:** 5–10 hand-curated quant strategies; arxiv ingest
   pipeline runs as a demo feature on 2–3 papers, not relied on for live decisions.
4. **(Day 3) Rigor is the wedge.** Every Tier-1 strategy passes the four selection-bias
   controls (DSR + PBO + walk-forward OOS + look-ahead audit) before admission to the
   library. Paper-claim deltas are surfaced honestly, not hidden behind an aggregate
   score. See [`docs/specs/selection-bias-corrections-spec.md`](docs/specs/selection-bias-corrections-spec.md).
5. **(Day 3) Two-tier marketplace.** Tier 1 (Archimedes Verified 🏆) = paper-grounded +
   selection-bias-corrected + full agent autonomy. Tier 2 (Community 👥) = permissionless,
   opt-in agent features. Per [`docs/specs/ecosystem-design-spec.md`](docs/specs/ecosystem-design-spec.md).
- **Demo vertical:** portfolio construction + autonomous rebalancing on Arc, NOT trading-
  agent-as-product. Per [`docs/architectural-principles.md`](docs/architectural-principles.md),
  the wedge is verifiable history + paper-grounded provenance + selection-bias rigor.
- **Out of scope:** see [`docs/anti-features.md`](docs/anti-features.md), including the
  Day-3 "pitch-rigor anti-claims" section that constrains the deck framing.

## Engineering conventions

### Branch model (5-person hackathon team, async-first)

- `main` is protected. Every change goes through a PR. (Day-4 reality: small infra fixes
  have occasionally landed on `main` directly via merge from the contracts owner's branch
  for hotfix expediency. Keep this rare — `develop` is the integration target.)
- `develop` is the integration branch — merge feature branches here first; promote to
  `main` once stable.
- Feature branches: `feat/<short-name>`, e.g. `feat/strategy-passport`,
  `feat/regime-detection`, `feat/ec2-infra-cicd`.
- Per-owner branches: `<discord-handle>/<short-name>`, e.g. `moonshot/contracts-v0`,
  `marten/arc-cli-spike`, `dbrowneup/strategy-foundation`,
  `danielscoffee/backtest`. Personal staging.
- Smart-contract branches: `smart-contracts` (the live branch Chuan uses) or
  `contract/<short-name>` for spikes — these get **two reviews** (Chuan + one generalist)
  before merge.
- **No force-push to `main` or `develop`. Ever.** Force-push to your own branch before
  opening a PR is fine.

### PR reviews

For a 5-person hackathon team operating async across 5 timezones, **one approving review**
is enough for non-contract changes. Contract changes get two. Reviewers should respond
within ~12 hours during the hackathon so the contributor isn't blocked overnight.

### Commit style

Imperative mood ("Add strategy passport schema" not "Added strategy passport schema").
Scope tags optional but encouraged: `[strategy]`, `[backtest]`, `[contracts]`, `[frontend]`,
`[infra]`, `[docs]`. Atomic commits — one logical change per commit; don't bundle.

### Smoke-test before deploy

Don't push to shared infrastructure without smoke-testing locally first. If the deploy is a
smart-contract change, run it against an Arc testnet first. The cost of a broken demo
environment is high; the cost of a 5-minute smoke test is low.

### Don't connect important wallets

Standard hackathon hygiene: use a fresh dev wallet for testing, never one with real assets.
Don't paste private keys anywhere in this repo. Use `.env.example` as a template;
`.env` is gitignored.

### Maestro/Worker discipline (lite)

Hosted Claude (Claude Code, claude.ai) is great at architecture, planning, and hard
debugging. Local code completion (Copilot, Cursor) is great at bulk implementation. A few
rules:

- **Use Claude for architecture decisions.** When the choice is between two real options,
  ask Claude for the tradeoff, then make the call as a team. Don't ask Claude to make the
  call.
- **Use Claude to draft specs, then implement against them.** A 1-page spec catches more
  bugs than the implementation does.
- **For PRs and bug fixes, prefer local tooling.** Save Claude budget for the next
  architecture call.
- **Spec drift is real.** If implementation diverges from the spec, update the spec or
  revert the code. Specs are living docs.
- **Multiple team members are using Claude.** Chuan (20× Max), Dan (5× Max), and others
  are running concurrent Claude sessions. **Source-of-truth artifacts in `docs/` are how
  we keep parallel sessions aligned** — every session reads from the same docs.

## When to ask before acting (Claude Code session in this repo)

- Pushing to shared infrastructure
- Adding new top-level dependencies (state which package, why, and the license)
- Touching `docker-compose*.yml`, deployment configs, or CI/CD wiring without team alignment
- Any smart contract change (needs Chuan's review)
- Editing `.env.example` (signals an env contract change for everyone)
- Editing [`environment.yml`](environment.yml) (every team member rebuilds their env on a change)
- Anything that touches the strategy-passport / reasoning-trace data flow (the
  `ReasoningTraceRegistry` contract is live as of Day 4 — modifications are
  contract-review-grade work)
- Anything that touches the on-chain vault contracts (`Vault`, `VaultFactory`,
  `SyntheticVault` — all deployed as of Day 4)
- Anything that touches `~/.arc-canteen/` files (those are individual team-member credentials —
  see [`README.md` "Security notes"](README.md#security-notes))

## When NOT to ask

- Inside your own feature branch, editing your own files
- Writing tests
- Adding docstrings, type hints, or formatting fixes
- Updating `docs/` to keep specs in sync with shipped code
- Running `pytest`, `ruff`, `prettier --write`, `docker compose up --build` locally

## Architectural primitives we want to get right

These four architectural commitments are load-bearing for the pitch's defensibility.
Detail in [`docs/architectural-principles.md`](docs/architectural-principles.md); principle
here.

### 1. The strategy passport

Every Tier-1 strategy in the library carries a verifiable record: which paper backs it,
the methodology hash, curator wallet signature, backtest results with paper-claim deltas
surfaced. Detail in [`docs/specs/strategy-passport-spec.md`](docs/specs/strategy-passport-spec.md).

### 2. On-chain provenance anchoring

Reasoning traces, strategy registrations, and rebalance decisions all get hashed and
anchored on Arc via the [`ReasoningTraceRegistry`](contracts/src/ReasoningTraceRegistry.sol)
contract (deployed Day 4; interface at
[`contracts/src/interfaces/IReasoningTraceRegistry.sol`](contracts/src/interfaces/IReasoningTraceRegistry.sol)).
Hash is the integrity primitive; off-chain storage holds the full trace; anyone can
recompute and verify against the on-chain anchor. The Day-3 commit-reveal upgrade
([`docs/specs/commit-reveal-trace-spec.md`](docs/specs/commit-reveal-trace-spec.md))
strengthens "trace existed at T" to "trace existed *before* the trade" with proven
causal ordering — wiring this through the live `ReasoningTraceRegistry` is the v1.5 hop.

### 3. Non-custodial vault architecture

User funds NEVER pass through platform custody. ERC-4626 vault contracts per
[`docs/specs/ecosystem-design-spec.md` § 3.2](docs/specs/ecosystem-design-spec.md) hold
user USDC and synth tokens; the agent has rebalance authority only, not withdraw-to-
platform authority.

### 4. Selection-bias correction (Tier-1 admission gate)

Every Tier-1 strategy passes Deflated Sharpe Ratio (Bailey & López de Prado 2014),
Probability of Backtest Overfitting (Bailey/Borwein/López de Prado/Zhu 2014), walk-
forward out-of-sample Sharpe with no in/out-of-sample cliff, and a look-ahead static
audit before promotion from CANDIDATE → VALIDATED. The numbers and the paper-claim
deltas are surfaced in the passport — never hidden behind an aggregate score. This is
the Day-3 addition that distinguishes Archimedes from the 96 other AI-portfolio
submissions at the last Arc HackMoney. Detail in
[`docs/specs/selection-bias-corrections-spec.md`](docs/specs/selection-bias-corrections-spec.md).

## Known risks

Refer to [`docs/design.md` § 10](docs/design.md) for the technical risk matrix and
[`docs/judging-rubric-assessment.md`](docs/judging-rubric-assessment.md) for the running
rubric score. Adding team / coordination risks:

- **Chuan as smart-contract + on-chain-integration bus factor 1.** Day-4 reality: Chuan
  owns the contracts AND the `backend/archimedes/chain/` layer AND infra. Mitigated by
  Marten as documented backup and by keeping contracts small with cached ABIs. See
  [`docs/architectural-principles.md`](docs/architectural-principles.md) for the general
  pattern.
- **5-person team across 5 timezones with 3 day-job constraints.** Mitigated by Marten as
  schedule owner + daily sync + async-first defaults.
- **Traction = 0 on the rubric scoreboard until arc-canteen telemetry starts flowing.**
  Per [`docs/judging-rubric-assessment.md`](docs/judging-rubric-assessment.md), this is
  the cheapest +points to recover. Every meaningful product ship should pair with an
  `arc-canteen update-product` call; every user conversation should be logged via
  `arc-canteen update-traction`.
- **Multiple parallel Claude sessions risk artifact drift.** Mitigated by treating `docs/`
  as single source of truth — and by curating those docs (this file is part of that
  curation rhythm; expect periodic Day-N revisions as reality outpaces specs).
- **Q-fin paper corpus needs Dan's curation.** Dan is evenings/weekends only. v1 ships
  with three paper-grounded strategies seeded (Faber 2007 SMA200, Moreira-Muir 2017
  volatility-managed, Moskowitz-Ooi-Pedersen 2012 TSMOM) plus a buy-and-hold baseline,
  per [`analytics-engine/strategies/`](analytics-engine/strategies/). Corpus expansion
  per [`docs/qfin-paper-corpus-seed.md`](docs/qfin-paper-corpus-seed.md) remains a
  weekend-blocked item.

## What this file deliberately does not cover

- The full architecture — see [`docs/design.md`](docs/design.md) (Chuan)
- Pitch deck content — see [`docs/demo-script-pitch-deck-outline.md`](docs/demo-script-pitch-deck-outline.md)
- Competitive landscape — see [`docs/competitor-landscape.md`](docs/competitor-landscape.md)
- Post-hackathon roadmap — out of scope for now

---

_When the team disagrees with anything in this file, the right move is to discuss in
Discord, agree, and update the file — don't let it silently drift. Date your changes if
they substantively change a decision._

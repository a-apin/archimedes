# Archimedes — Claude Code Context

> **Status:** Living context doc. Written 2026-05-12 (Day 2); revised 2026-05-13 (Day 3,
> marketplace pivot + rigor-as-wedge), 2026-05-14 (Day 4, 10-contract Arc deploy + live
> UI + ownership reshuffle), and 2026-05-19 (build-on-deploy main-only + `develop`
> retired; GLM intelligence live; product spine locked in `docs/user-stories.md`;
> agentic-issue pipeline codified). Intent: drop at the root of the `archimedes` repo
> and read at the start of every Claude Code session.
>
> **Architecture lineage to read together:**
> - [`docs/user-stories.md`](docs/user-stories.md) — **the locked product spine
>   (canonical); supersedes the older product framing in the docs below**
> - [`docs/design.md`](docs/design.md) — original single-vault architecture
> - [`docs/specs/ecosystem-design-spec.md`](docs/specs/ecosystem-design-spec.md) — Day-3
>   two-tier marketplace pivot (synthetic protocol + AMM + VaultFactory + agent-as-a-service)
> - [`docs/specs/component-interfaces-spec.md`](docs/specs/component-interfaces-spec.md) —
>   frozen-interface contract for the 5-person concurrent build (Dan owns `IStrategyProvider`)
> - [`docs/specs/strategy-passport-spec.md`](docs/specs/strategy-passport-spec.md) +
>   [`docs/specs/selection-bias-corrections-spec.md`](docs/specs/selection-bias-corrections-spec.md)
>   — the four-primitive admission gate for Tier 1 strategies
> - [`docs/corpus-architecture.md`](docs/corpus-architecture.md) — Day-9 reference
>   for how the 10k q-fin corpus is built, stored, and fused into strategies
>   (seed/intake/artifact + the fusion path)
> - [`docs/archive/agora_project_analysis.md`](docs/archive/agora_project_analysis.md) — red-team synthesis
>   driving the Day-3 rigor-as-wedge framing

## Project

**Archimedes** — "Linus for quantitative finance": a single-user agent that turns the
q-fin research literature into investable, rigor-gated strategies, then executes and
monitors them in non-custodial vaults on Arc with USDC settlement. The product spine
(generate → rigor-gate → execute → monitor → explore) is locked in
[`docs/user-stories.md`](docs/user-stories.md) — the canonical product framing.

> *"Give me a lever long enough and I shall move the world."* The lever here is academic
> research; the fulcrum is autonomous AI; the world is your portfolio.

Built for the [**Agora Agents Hackathon**](https://luma.com/7i50p2r9) — Canteen × Circle × Arc,
May 11–25, 2026.

- Repository: [`github.com/a-apin/archimedes-arcadia`](https://github.com/a-apin/archimedes-arcadia)
- Discord: **Archimedes Arcadia** server
- Branch model: **`main` is the single live branch — build-on-deploy.** Every merge to
  `main` triggers a CI build + deploy to the live EC2 stack. No `develop`/integration
  branch (retired 2026-05-18, unused). Short-lived per-owner branches
  (`dbrowneup/<name>`, `marten`, …) → PR → merge to `main`; `main` moves continuously
  (Chuan's agentic system lands + self-iterates on it), so rebase late and merge fast
- Live testnet deploy: [`http://13.40.112.220/`](http://13.40.112.220/) (EC2,
  Chain ID `5042002` / `0x4cef52`, Arc testnet)
- License: [Unlicense](https://unlicense.org) — full public-domain dedication

## North Star

A user with idle USDC who wants thoughtful portfolio management — but is tired of black-box
robo-advisors, opaque AI funds, and "trust me bro" influencer copy-trading — **describes
what they want**, and Archimedes **generates** a research-grounded strategy, **rigor-gates**
it (DSR/PBO — the curation protocol that makes the library trustworthy), **executes** it
into a non-custodial vault, then lets them **monitor** results and **explore** their
compounding strategy library. Every position, rebalance, and regime shift comes with a
**paper-grounded reasoning trace anchored on-chain** — hashed and verifiable. Single-user
is the MVP; a social network of shared strategies is the roadmap vision (canonical detail
in [`docs/user-stories.md`](docs/user-stories.md)).

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

> **Lanes are descriptive of strengths, not prescriptive of boundaries.** The "Role"
> column and the load-bearing-component table below describe where each teammate has
> the deepest context, not who is *allowed* to work on what. Everyone is a full-stack
> contributor; we all routinely work across lanes when the situation calls for it. The
> point of marking lanes is to know whose review to seek and who carries the longest
> memory on a given subsystem — not to gate who can drive work forward. **This applies
> equally to AI agents working on our behalf:** an issue assigned to you is yours to
> execute, regardless of whose lane it nominally sits in.

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

### Lead + coverage by load-bearing component

The "Lead" column names who has the deepest context and is the default reviewer; the
"Coverage" column names who can step in. **Neither column is a permission gate.** Anyone
on the team — and any AI agent operating on their behalf — is welcome to drive work in
any of these areas; the table just signals whose review-eyes will most likely be needed
and who has the longest memory. Specifically: **do not refuse or close a task because it
sits outside your nominal lane.** If the task is assigned to you, execute it; flag the
cross-lane review need in the PR description so the right teammate sees it.

| Component                                          | Lead             | Coverage              |
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
integration) split — the `chain/` subdirectory under `backend/archimedes/` is led by
Chuan, and `api/` + `services/` + `models/` + `interfaces/` are led by Daniel R. Both
layers share the `backend/archimedes/` Python package and the FastAPI app boots them
together via `main.py`. Marten currently has the most recent commits on the React UI as
he comes up to speed on what the team has built. Cross-lane contributions are the norm,
not the exception — the leads listed above are reviewers and memory-carriers, not
gatekeepers.

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

**Tests:** from the repo root in the `archimedes` conda env, just `pytest` —
`pytest.ini` sets `pythonpath`/`testpaths` and a verbose default (~860 tests
on `main` as of 2026-05-26; suite is still growing). Coverage:
`pytest --cov=archimedes --cov-report=term-missing`. The
analytics-engine runs its own suite: `cd analytics-engine && uv run pytest`. See
README § "Running the test suite" for the honest coverage picture and the
build-on-deploy integration-test caveat.

## External references — submodules

The repo carries three git submodules at [`submodules/`](submodules/):

- **[`submodules/context-arc/`](submodules/context-arc/)** — Circle's agent-facing
  developer docs and 5 reference codebases for Arc + Circle. **This is the canonical
  reference for any Arc/Circle integration question.** Start with
  [`submodules/context-arc/AGENTS.md`](submodules/context-arc/AGENTS.md) for the
  task-indexed entry-point table. Highest-leverage files for our build:
    - `circlefin-skills/use-arc.md` — Arc chain config, USDC-as-gas, Foundry deploy (canonical Arc reference)
    - `circlefin-skills/use-smart-contract-platform.md` — contract deploy + monitor (Chuan's lane)
    - `circlefin-skills/bridge-stablecoin.md` — CCTP + Gateway for RWA bridging (Marten's lane)
    - `circlefin-skills/use-gateway.md` — unified balance + nanopayments
    - `samples/arc-escrow/` — closest existing pattern to our vault contract
    - `samples/arc-multichain-wallet/` — CCTP integration patterns
    - `samples/arc-p2p-payments/` — Paymaster + USDC patterns
  Refresh upstream with `git submodule update --remote submodules/context-arc` or
  `arc-canteen context sync` (drops into `~/.arc-canteen/context/`).

  **Sticky submodule config — one-time, per clone:** after `git clone`, run this
  to make git auto-recurse into submodules on every checkout/pull/rebase. Without
  this, working trees drift out of sync with main's recorded pins (we hit this
  several times during the hackathon — every session had to manually re-sync):
  ```bash
  git config submodule.recurse true   # auto-recurse on git ops
  git config diff.submodule log       # nicer diff display
  git submodule update --init --recursive  # one-shot sync to recorded pins
  ```
  Linus has its OWN nested submodule (`submodules/Linus/modules/KnowledgeBase`)
  which is the most common source of "modified content" noise in `git status`.
  The `--recursive` flag handles it.
- **[`submodules/KnowledgeBase/`](submodules/KnowledgeBase/)** — Dan's scientific-
  paper analysis pipeline (PyMuPDF extract + SPECTER2 embeddings + HDBSCAN/BERTopic
  clustering + REBEL/SciSpacy knowledge graph). For Archimedes, **don't port wholesale**
  — read it as a reference implementation. The patterns worth lifting for the
  Tier-1 arxiv extraction pipeline:
    - `papers_analysis/extract.py` — PyMuPDF caching pattern (~71 files/s)
    - `papers_analysis/metadata.py` — paper-corpus schema (maps to our `paper_corpus` table)
    - `papers_analysis/summarize.py` — Ollama-driven methodology synthesis (we'd use Claude)

  **KB pipeline integration — provenance discipline:** The Corpus page (`/corpus`)
  uses [`corpus_routes.py`](backend/archimedes/api/corpus_routes.py) at the
  `/api/corpus/*` prefix, which reads real KB pipeline output (SPECTER2
  embeddings, HDBSCAN clusters, REBEL/SciSpacy triples) and returns 503 when no
  artifact exists yet. The legacy metadata-derived `/api/papers/corpus/*`
  endpoints were deleted in issue #201 — do NOT reintroduce them. Any "graph"
  or "knowledge graph" surface MUST come from real KB pipeline output, not
  arxiv-metadata synthesis. When the KB pipeline (issue #151, gated on AWS
  infra #147) actually produces an artifact, the honest endpoints start
  returning data; until then the page renders an explicit "KB pipeline still
  running — first artifact pending" empty state from the 503 response.
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
  [`docs/adr/backtrader-vs-vectorbt-decision-memo.md`](docs/adr/backtrader-vs-vectorbt-decision-memo.md).
  Supersedes `docs/design.md` § 6 ("vectorbt / custom numpy engine") on this one
  line; design.md remains the architecture spec for everything else. Migration to
  vectorbt is a v2 problem if parameter-sweep speed becomes a constraint.
- **Smart contracts:** Solidity + Foundry, targeting Arc (EVM-compatible). **11 contracts
  deployed on Arc testnet** (Day 4 baseline + `StrategyRegistry` added later):
  `AMMPool`, `AMMRouter`, `AssetRegistry`, `PriceOracle`, `ReasoningTraceRegistry`,
  `StrategyRegistry`, `SyntheticFactory`, `SyntheticToken`, `SyntheticVault`,
  `Vault`, `VaultFactory`. ABIs cached in
  [`contracts/abis/`](contracts/abis/) for backend + UI consumption. (Note:
  `ecosystem-design-spec.md` described `StrategyRegistry → AssetRegistry` as a
  replacement, but in practice both coexist today — the spec-vs-state delta
  is intentional and the registries serve different lookups.)
- **On-chain integration:** Circle SDK — Wallets (Circle-managed wallet for the oracle
  signer), USYC, Gateway, CCTP, Paymaster; viem on the UI side
- **Deployment:** Docker compose stack (5 services: postgres / redis / nginx / oracle /
  backend) running on an EC2 instance behind nginx. CI/CD wired via GitHub Actions per
  [`docs/infra-setup.md`](docs/infra-setup.md). Live at
  [`http://13.40.112.220/`](http://13.40.112.220/)

## Scope — the headline commitments

Refer to [`docs/archive/mvp-scope-memo.md`](docs/archive/mvp-scope-memo.md) for the full argument. Locked
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

### Branch model (build-on-deploy, main-only)

Codified 2026-05-18 to match how the team actually works (see
"Working with AI agents on this repo" below):

- **`main` is the only long-lived branch, and it is the deploy branch.** Every merge
  to `main` triggers a CI build + deploy to the live EC2 stack. There is no
  `develop`/integration branch — it drifted unused and was retired.
- **`main` moves continuously.** Chuan's agentic system merges work and iterates on
  its own CI failures directly on `main`. Treat `main` as fast-moving: branch from it
  late, rebase onto it right before merging, and merge in a tight window before it
  drifts again. Don't wait for it to "settle" — it won't.
- Short-lived per-owner branches `<discord-handle>/<short-name>` → PR → merge to
  `main`. Delete the branch after merge.
- **The few hard rules — universal, and they do not impede speed:** never force-push
  `main`; never commit secrets or `.env`; one logical change per PR. Force-pushing
  your *own* unmerged feature branch is fine and expected (rebase-before-merge).
- On-chain / smart-contract changes still warrant Chuan's eyes given live-funds risk.

### PR reviews

For a 5-person hackathon team operating async across 5 timezones, **one approving review**
is enough for non-contract changes. Contract changes get two. Reviewers should respond
within ~12 hours during the hackathon so the contributor isn't blocked overnight.

### Commit style

Imperative mood ("Add strategy passport schema" not "Added strategy passport schema").
Scope tags optional but encouraged: `[strategy]`, `[backtest]`, `[contracts]`, `[frontend]`,
`[infra]`, `[docs]`. Atomic commits — one logical change per commit; don't bundle.

### CI / quality gates

Four workflows run on every PR and every push to `main`:

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `quality-gate.yml` | PR → main | Hard block: `pytest -m "not integration"` (unit suite, no DB/Redis) **and** `ruff-gate` (`ruff format --check .` + `ruff check --select E9,F63,F7,F40 .`). Informational: full `ruff check` (broader rule set) + `npm run lint` in `ui/` — both run with `continue-on-error` and their pass/fail counts are posted as a PR comment table (marker `<!-- quality-gate-v1 -->`). Agent PRs (`t2o2`) also get a coverage gate (≥ 60%). |
| `complexity-gate.yml` | PR → main (Python/JS/TS files only) | Aggregate cyclomatic-complexity, nesting depth, recursion, and orphan analysis via lizard + Python AST. Compares the changed-file set against the `main` baseline and posts a table comment on the PR (marker `<!-- complexity-gate-v1 -->`). **Informational only — never blocks merge.** Runs on the GitHub runner with `pip install lizard`; the bundled distroless Dockerfile at `.github/docker/complexity-gate/Dockerfile` is available for local use but not pulled by CI. |
| `deploy.yml` | push → main | Rebuilds and redeploys the EC2 stack. |
| `main-format-guard.yml` | push → main | Runs `ruff format --check .` on every push to `main`. If the check fails, the workflow runs `ruff format .`, commits the fix back with `[skip ci]` (no recursion), and fails its own run so the violation is visible in CI history. Net effect: `main` self-heals so open PRs aren't stranded with red ruff-gates, and the failed run nudges the contributor (or agent) to install pre-commit. Added 2026-05-25 after several rounds of direct-to-main pushes landed unformatted files. |
| `release-tag.yml` | push → main | Creates a semver annotated tag for every merged PR via the GitHub API (no `git push`). Bump rules (read from PR title only, **end-of-title anchor**): `!version-release` → major (1.0.0), `!minor` → minor (0.1.0), anything else → patch (0.0.1). Title-end matching prevents false positives where the marker text appears in body prose. Direct pushes with no associated PR are skipped silently. |

**Complexity gate visual thresholds (informational only — none block merge):** ✅ CC 1–5 simple · ⚠️ 6–10 moderate · 🟠 11–15 complex · 🔴 16+. Δ CC > +1.0 vs main is flagged ⚠️. Nesting depth ≥ 3 and recursive functions are flagged in the table.

**Release tag markers:**
```
PR title: "Rework strategy fusion engine !minor"     → v0.1.0
PR title: "Launch-ready rebalancer !version-release" → v1.0.0
PR title: "Fix corpus manifest path"                 → v0.0.1
```

**Release tagging — conventions for direct-to-main commits (applies especially to
bot-driven work):** `release-tag.yml` only fires on PR merges. **Direct pushes to
`main` without an associated PR are silently skipped — no tag is created.** Two
implications:

1. **Prefer PRs over direct push** for any change that warrants a version tag (i.e.
   anything except trivial doc fixes you'd be comfortable losing in `git log`).
   This includes work done by Chuan's agentic system (`t2o2`): if a change is
   meaningful enough to read later, it's meaningful enough to PR.
2. **Choose the right marker for the PR title.** Most changes are patches and
   need no marker. But:
   - **`!minor`** — new user-facing capability (new endpoint, new UI surface,
     new strategy in the library, new contract method, new pipeline stage).
   - **`!version-release`** — major milestones (live demo cutover, multi-chain
     mainnet, custodial-vault → non-custodial-vault migration, etc.). Use
     sparingly — most weeks see zero of these.
   - **(no marker)** — bug fixes, refactors, doc updates, dep bumps, telemetry.

When in doubt, default to **no marker** (patch). Over-bumping minor/major dilutes
the signal; under-bumping is recoverable later.

### Python linting + formatting (ruff)

Convention: **`line-length = 120`, ruff defaults plus `I,UP,B,SIM,RUF`.** Config
lives at the repo root in [`ruff.toml`](ruff.toml). Two things gate every Python
PR via the `ruff-gate` job:

| Check | Command | Status |
| --- | --- | --- |
| Formatting | `ruff format --check .` | Hard block |
| Critical lint rules | `ruff check --select E9,F63,F7,F40 .` | Hard block |
| Broader lint | `ruff check .` | Informational (continue-on-error) |

The blocking subset is deliberately narrow today (syntax + undefined-module
rules) so the gate doesn't trip on pre-existing style debt. It can grow as we
clean things up — next candidate is `F82` (undefined-name).

**Local feedback loop — install pre-commit once per clone:**
```bash
pip install pre-commit
pre-commit install                 # installs .git/hooks/pre-commit
pre-commit run --all-files         # one-shot check across the repo
```

The pre-commit hooks ([.pre-commit-config.yaml](.pre-commit-config.yaml))
mirror the CI gate exactly, so pre-commit can't pass while CI fails (or vice
versa). They're **opt-in** — devs who don't install them just get the same
feedback from CI on push instead of from `git commit` locally.

**To clean up before committing:**
```bash
ruff check --select I --fix .      # import organization (safe, mechanical)
ruff check --fix .                 # all other safe auto-fixes
ruff format .                      # apply formatting (line-length 120)
```

The `--unsafe-fixes` flag should be reviewed line-by-line — those fixes need
human judgment and are not auto-applied in CI or pre-commit.

### Supply-chain scrutiny — dependency hygiene

We don't bring on new dependencies casually, and we re-check the ones we have. Three
practices:

| Tool | Command | When to run |
| --- | --- | --- |
| `pip-audit` | `pip-audit` (whole env) or `pip-audit -r backend/requirements.txt` (declared only) | Before every PR that bumps or adds a Python dep. Once a week as background hygiene. |
| `npm audit` | `cd ui && npm audit --omit=dev` (prod only) or `npm audit` (full) | Before every PR that bumps a Node dep. Dependabot also alerts asynchronously. |
| Dependabot | Auto — see open dependabot PRs in the repo | Always-on. Triage promptly; don't let CVE PRs sit. |

Three rules:

- **Pin transitively-vulnerable deps directly when CVEs warrant it.** Example: `starlette>=1.0.1` is in `environment.yml` + `backend/requirements.txt` to close PYSEC-2026-161 (Host-header bypass) even though it would otherwise come transitively from FastAPI. When `pip-audit` flags a CVE in a transitive dep, add a direct pin to the closest `Fix Versions` so a fresh resolution can't regress.
- **Keep `environment.yml` (local dev) and `backend/requirements.txt` (Docker / CI) aligned.** Drift is the most common source of "works on my machine" + "breaks in CI" — see the `slowapi` and `redis` misalignment that caused 62 user_routes test errors locally on 2026-05-24. Any new pip dep goes in BOTH files in the same PR.
- **No new dep without a sentence on what it does + why we picked it.** Comments in the requirements / env files are how future readers (us in a week) understand the trust surface. "added by tooling" is not a sentence.

**Frontend**: `npm ci` (used by both `quality-gate.yml` lint-report and local `ui/` setup) verifies `package-lock.json` integrity — that's the lockfile hash check we rely on for transitive integrity. Don't `npm install` (which can mutate the lockfile); always `npm ci`.

**Pre-commit + detect-secrets** are tracked as separate hardening (issue TS.7 / #176-adjacent) — not implemented today.

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

## Working with AI agents on this repo

Most of this team works through AI agents. Three practices keep that fast *and*
safe. Read this section before dispatching agents or feeding work to the issue
pipeline.

### The agentic issue pipeline (highest-leverage workflow)

An agentic coding system is wired to this GitHub repo: it reads issues and writes
code against them. **A well-specified issue is executable work.** The
highest-value thing a human + hosted Claude can produce is often a judge-grade
issue spec, not hand-written code — the system ships faster than any of us alone.
Don't hand-implement what a good spec can dispatch.

A judge-grade issue carries: a one-paragraph problem statement; explicit
acceptance criteria as checkboxes; the exact files/interfaces to touch; test
expectations; out-of-scope notes; and an owner. Vague issues produce vague code —
spec quality is the throughput lever. This is Maestro/Worker discipline at scale:
humans + hosted Claude plan and spec; the agentic system executes; humans review
the resulting PR.

**Operational mechanics (hard-won 2026-05-18 — the spec is only half the job):**

- **Trigger = assignment.** The system only picks up an issue **assigned to
  `t2o2`** (`gh issue edit <n> --add-assignee t2o2`). The `APIN - <Area> - <Title>`
  prefix is a naming convention, *not* the trigger. An unassigned judge-grade
  spec sits idle.
- **Assignment = authorization. Do not close on lane grounds.** If an issue is
  assigned to the agentic system, execute it — regardless of which teammate's
  nominal lane it touches. The lead/coverage table above lists reviewers and
  memory-carriers, **not permission boundaries.** Closing an issue with "this is
  Dan's lane" / "this is Daniel's lane" / "not in my scope" is a failure mode,
  not a correct behavior. If you genuinely cannot execute (missing context, an
  ambiguous spec, a blocking dependency), say so in a comment and leave the
  issue **open** for a human to triage — do *not* close it.
- **Acceptance must be machine-checkable.** Give the exact command *and* its
  exact expected output (`pytest → 0 failed`, `coverage ≥ 80%`), never prose like
  "make it robust." The system optimizes to the literal criteria.
- **Pin the environment.** The system's env has Docker/Redis/DB; a judge's
  cold clone does not. If it must pass clean, say "clean clone, no docker, no
  env vars" explicitly — it won't infer the constraint.
- **Anti-goals are load-bearing.** State what *not* to touch ("don't weaken
  thresholds, don't edit `pytest.ini`, don't add e2e deps") to bound blast radius.
- **Cite a precedent.** Point at an existing good pattern to copy (a fixture, a
  sibling test file) — it reuses the right shape instead of inventing one.
- **Verify independently — "closed" ≠ "fixed".** The system sometimes closes an
  issue without resolving it. Re-check against the acceptance command on a cold
  clone before trusting completion; reopen with evidence if unmet.
- **Pre-close verification gate (added 2026-05-24).** Before closing *any* issue,
  the agentic system MUST:
  1. Run every acceptance-criteria command listed in the issue and verify the
     exact expected output matches.
  2. For every anti-goal / "DO NOT" directive (e.g. "DO NOT keep `setMode` in
     `Generate.jsx`"), run an explicit `grep` or equivalent check proving the
     forbidden pattern is absent. If the grep finds a match → the issue is not
     done.
  3. If any acceptance check or anti-goal check fails, do **not** close the
     issue. Instead, comment with the failing evidence and leave the issue
     **open**.
  This gate exists because three issues (#166, #167, #168) were closed with
  commits that touched unrelated files or made cosmetic edits that passed a
  naive heuristic without doing the structural work. Pattern-match on commit
  messages is not verification — running the actual commands is.

Copy-paste skeleton:

```markdown
## Summary             <!-- one paragraph: the problem, why it matters -->
## Scope               <!-- exact files/interfaces; "do exactly this, nothing more" -->
## Acceptance criteria <!-- checkboxes, each a runnable command + expected output -->
## Verify              <!-- the literal commands a reviewer runs -->
## Anti-goals          <!-- what NOT to do; what NOT to touch -->
<!-- then: gh issue edit <n> --add-assignee t2o2 -->
```

Exemplars: issues #76 and #77 are written to this standard.

### Git safety — every contributor and their agents

Non-negotiable, and load-bearing because the judges read this repo like operators:

- **Never force-push `main`.** Ever. (Force-pushing your own unmerged feature
  branch is fine.)
- Humans: branch + PR → merge to `main`. One logical change per PR; atomic commits.
  The agentic system integrates on `main` directly (build-on-deploy) — that's the
  accepted reality, not a violation; the rule that matters is no force-push + no
  secrets, not "never touch `main`."
- `main` moves continuously — rebase onto it right before merge and merge fast.
- **Never commit secrets or `.env`** — `.env` is gitignored; keep it that way; no
  private keys in the tree.
- If an AI agent is uncertain, it **stops and asks** — it does not invent APIs,
  fabricate data, or silently work around a blocker.
- New to the stack: pair on one full branch → push → PR cycle before running
  agents unsupervised. No judgement — the cost of a tangled shared history is
  high; the cost of one paired cycle is low.

### Parallel agent fan-out discipline

Hard-won (2026-05-16); ignore at your peril:

- **Probe with ONE canary agent before any fan-out.** If the canary is blocked at
  a step, the whole fan-out will be too — you pay the fan-out tax for zero
  parallelism.
- **The canary must match the fan-out's execution mode.** A foreground canary
  does *not* validate a background fan-out — they run under different sandboxes.
- **Background subagents are filesystem-sandboxed here** (no writes; cannot exec
  interpreters outside the project dir). Use **foreground** agents for
  implementation fan-out, or a scoped `permissions.allow` in
  `.claude/settings.json`.
- Parallel agents get **isolated git worktrees**, base-SHA-pinned to a recorded
  commit; do not commit to the base branch between dispatches.
- Dismantle worktrees at end of session; retain their branches until the PRs
  merge.

## Architectural primitives we want to get right

These five architectural commitments are load-bearing for the pitch's defensibility.
Detail in [`docs/architectural-principles.md`](docs/architectural-principles.md); principle
here.

> **Academic backstops for the architecture (added Day-12, 2026-05-24):**
> - **Xia et al. 2026 — *Agentic Trading: When LLM Agents Meet Financial Markets*** ([arxiv 2605.19337](https://arxiv.org/abs/2605.19337), ESWA). The audit-grade survey of 19 trading-agent papers: **15/19 are R0** (no code/data artifacts), **0/19 reach R3** (fully replayable with artifact versioning + immutable provenance), **2/19** report time-consistent train/test splits, **1/19** has a transaction-cost model, **1/19** documents universe/survivorship handling. Archimedes is engineered to be the first production trading-agent system to ship at R3 and to implement every named protocol Xia formalizes (Outcome Embargo, Time-Aware Retrieval, Hierarchy of Truth, Source Tracking, `V_check`) as **enforced mechanisms** rather than advisory guidelines. Detail in [`docs/specs/xia-2026-protocols.md`](docs/specs/xia-2026-protocols.md).
> - **Chen et al. 2026 — *StockBench*** ([arxiv 2510.02209](https://arxiv.org/abs/2510.02209)). The first contamination-free, closed-loop, multi-month trading-agent benchmark. Our primary LLM family (GLM-4.5 → GLM-4.7) ranks #3 globally on the model baseline (behind Kimi-K2 and Qwen3-235B-Instruct; ahead of Claude-4-Sonnet at #7 and GPT-5 at #9). **Honest about the agent result:** when we layer Archimedes' Strategy Generation Agent on top, our `T3.8` harness run lands at #15/15 (Sortino -0.91). That underperformance is itself a load-bearing data point — Xia et al. argue (and StockBench corroborates) that *all* LLM agents underperform passive baselines in many windows, and our pitch surfaces this rather than hides it. Detail in [`docs/benchmarks/stockbench-results.md`](docs/benchmarks/stockbench-results.md).

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

### 5. K=1 generation + externalized rigor gate

Codified 2026-05-23 after a Linus-Maestro architecture audit. The generation
agent emits **one** winner per Generate call (plus a short list of
considered-rejects with the rationale for rejecting each); the rigor gate
runs **externally** to the generator and is what the user reviews on the
strategy passport before deployment. Two reasons this is the right shape:

- **Hosted-LLM budget economics.** K=many generation multiplies LLM cost
  per Generate; K=1 keeps the demo affordable + responsive. The
  considered-rejects panel preserves the "what else was on the table"
  signal without paying for parallel deep generation.
- **Externally-verifiable provenance.** Shifting provenance enforcement
  from runtime types to externally-verifiable hashes (the strategy
  passport's `methodology_hash` + `consulted_paper_hashes` anchored
  on-chain via `ReasoningTraceRegistry`) is a strict upgrade — anyone
  can re-derive and verify; the agent cannot lie about what it consulted.

The user-facing surface this implies: **Generate produces a winner →
the Considered Alternatives panel shows what was rejected and why → the
rigor gate's verdict is rendered on the passport** before the Deploy
button enables. This is the pattern judges should look for when reading
the user journey in [`docs/user-stories.md`](docs/user-stories.md).

Episodic compounding lives on top of this primitive: every fusion
proposal + every rigor verdict + every user-reject is content-hashed
and persisted via the `strategy_proposals` table (T-PE.8 / [issue
#165](https://github.com/a-apin/archimedes-arcadia/issues/165)) so the
library demonstrably *compounds* rather than restarting per session.

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
  per [`docs/archive/qfin-paper-corpus-seed.md`](docs/archive/qfin-paper-corpus-seed.md) remains a
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

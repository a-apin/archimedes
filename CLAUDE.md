# Archimedes — Claude Code Context

> Read at the start of every Claude Code session in this repo. Revision history is
> `git log --follow CLAUDE.md`.
>
> **This file holds only what an agent would get wrong by default.** Anything readable from
> the tree in one tool call — stack, contract census, test count, ruff rules, service
> inventory — is deliberately *not* here: a stale copy is worse than none, because agents
> act on `CLAUDE.md` without verifying.
>
> **Where things live:** [`docs/README.md`](docs/README.md) — the doc index (a doc not
> listed there does not exist) · [`docs/user-stories.md`](docs/user-stories.md) — canonical
> product spine · [`docs/architecture.md`](docs/architecture.md) — architecture map ·
> [`docs/adr/`](docs/adr/README.md) — 19 decision records. Current status comes from
> the live system, not a doc: `/health`, `GET /api/config/contracts`, and the
> deploy history. (The README's dated Status section was retired 2026-08-20.)

## Project

**Archimedes** — "Linus for quantitative finance": a single-user agent that turns q-fin
research literature into investable, rigor-gated strategies. **Executing and monitoring
them in non-custodial vaults on Arc with USDC settlement is roadmap, not shipped product —
write it in the future tense.** The `Vault`/`VaultFactory` contracts are real and deployed
([ADR](docs/adr/non-custodial-vault-owner-agent.md)), but the deploy-a-vault journey is
gated off every public surface behind `ROADMAP_SURFACES_ENABLED`
([`ui/src/featureFlags.js`](ui/src/featureFlags.js), off by default, #1266/#1354, guarded by
`ui/test/roadmap-copy.test.js`), and #1469 is open to scrub the remaining present-tense
copy. Spine (generate → rigor-gate → execute → monitor → explore) locked in
[`docs/user-stories.md`](docs/user-stories.md).
Repo [`a-apin/archimedes`](https://github.com/a-apin/archimedes) · Discord **Archimedes
Arcadia** · live at [`archimedes-arc.com`](https://archimedes-arc.com/) (Arc testnet, chain
`5042002` / `0x4cef52`; `.com` is the sole domain — the `.app` split caused the Circle
passkey rpId bug and was decommissioned) · [Unlicense](docs/adr/unlicense-public-domain.md).
The build roadmap lives **privately** in the `docs` repo (`consolidated/ROADMAP.md`) —
**Dan owns it**, ask him for access. Competitive landscape, pricing, and business model are
**not in this repo** either — that material lives in the private docs repo by policy; this
repo carries code and technical documentation only. Ask Dan. (Do not re-create a competitor
doc here — that has happened twice.)

### The hard constraint, above everything else

***Claims must be true.*** Every guarantee the UI, the pitch, or a grant application makes
— rigor, non-custodial, on-chain provenance — must be backed by the live path, not a
fixture, not a cached boolean, not a hard-coded `true`. This is the #1 rule and the thing
Bogdan's full-tree audit ([PR #710](https://github.com/a-apin/archimedes/pull/710)) showed
we were violating. Building flashy work on a fake-strict rigor badge is building on sand.

Two corollaries an agent gets wrong by default:

- **Don't quote a curated-library strategy pass count — anywhere.** Three strategies
  reported "passing" were later found to be grading equity-like series (~18.5% annual vol)
  through a data-feed fallback in the backtest loader. The corrected count is **not
  established**. Say "unestablished", not a number.
- **Numbers come from the live source, not a doc.** Contract census: `GET
  /api/config/contracts`. Test count: `pytest --collect-only -q | tail -1`. Lint rules:
  [`ruff.toml`](ruff.toml). Status: the live system (`/health`, the contract census),
  not a doc — the README's dated Status section was retired 2026-08-20.

## Team

Roster, bios, timezones, sync window, and the 2026-06-24 ownership change (Chuan Bai out;
Dan owns contracts + on-chain + infra + architecture and is the sole required
contract approver): [`docs/team.md`](docs/team.md). Two things stay here because they change how you
behave:

> **Lanes are descriptive of strengths, not prescriptive of boundaries.** The table below
> describes where each teammate has the deepest context, not who is *allowed* to work on
> what. Everyone is a full-stack contributor; we all routinely work across lanes when the
> situation calls for it. The point of marking lanes is to know whose review to seek and
> who carries the longest memory on a given subsystem — not to gate who can drive work
> forward. **This applies equally to AI agents working on our behalf:** an issue assigned
> to you is yours to execute, regardless of whose lane it nominally sits in.

"Lead" = deepest context and default reviewer; "Coverage" = who can step in. **Neither
column is a permission gate. Do not refuse or close a task because it sits outside your
nominal lane.** Flag the cross-lane review need in the PR description instead.

| Component | Lead | Coverage |
| --- | --- | --- |
| Strategy engine + q-fin paper corpus curation | Dan | Önder |
| Backtesting / strategy-passport math + risk pricing | Önder | Dan |
| Backend Python layer (FastAPI, API, services, models) | Daniel R. | Marten |
| On-chain integration layer (`backend/archimedes/chain/`, oracle runner) | Dan | Bogdan / Marten |
| Frontend (React + Vite + viem, wallet UX, trade tab) | Marten (current) / Daniel R. | Dan |
| Smart contracts (Arc, Foundry) | Dan | Bogdan (`mnemonik-dev`) / Marten |
| Infra / ECS Fargate / CI/CD / docker-compose / AWS account | Dan | Daniel R. |
| Architecture + design decisions | Dan (lead) | full team |
| Pitch deck + demo script + judging | Dan | Marten |

## Setup

[`README.md`](README.md) has the full walkthrough (conda env via
[`environment.yml`](environment.yml), Node frontend, Foundry, docker compose). What is not
obvious from the tree:

**Local stack (post-#1194):** `cp .env.example .env` (generate `BETTER_AUTH_SECRET`;
LLM/RPC optional) then `docker compose up -d --build`. nginx publishes on rootless-safe
port 8080 (<http://localhost:8080>, `/docs` for the API); compose runs Alembic before
starting auth/backend. Full walkthrough: `SETUP.md`.

**Resolve the toolchain explicitly. Do not trust a bare `pytest` / `ruff` / `node`.**
`environment.yml` declares the whole toolchain — `python`, `pytest`, `ruff`, `nodejs`,
`terraform`, `awscli` — but a bare command name gives you whatever PATH resolves first,
and on a real machine that is frequently not the env. Measured on one maintainer's box,
2026-08-30:

| command | resolved to | the env has |
| --- | --- | --- |
| `pytest` | `/Library/Frameworks/Python.framework/.../3.13/bin/pytest` — **Python 3.13.7**, fastapi 0.136.3 | Python 3.12.13 |
| `ruff` | the same 3.13 framework install | its own copy |
| `node` / `npm` | `/opt/homebrew/bin/node` (Homebrew) | **not installed** |
| `terraform` | not installed at all; `tofu` (OpenTofu) is | **not installed** |
| `python` | nothing — `command -v python` is empty | 3.12.13 |

**The dangerous one is `pytest`.** It does not fail. It collects and runs the suite against
a different interpreter and an unaligned package set, then reports a result you will
believe. That is the same "works on my machine" defect the `environment.yml` /
`backend/requirements.txt` alignment guard exists to catch, arriving through PATH instead
of through version floors.

`nodejs` / `terraform` / `awscli` were added to `environment.yml` on 2026-05-24 and
2026-06-24. An env created before then and only ever pip-updated will not have them.
`conda env update -f environment.yml` is how you would get them, and it will also shadow
your Homebrew `node` and install HashiCorp `terraform` beside your OpenTofu. Worth knowing
before you run it; to pick up a pip-block change only, install those specs directly.

Check rather than assume, then use absolute paths:

```bash
ENV_BIN=/path/to/miniconda/envs/archimedes/bin   # `conda env list` prints the prefix
"$ENV_BIN/python" -V                             # expect 3.12.x
"$ENV_BIN/pytest" --version
command -v node && node --version                # may be Homebrew's; that is fine for lint
```

`conda info --base` is **not** a reliable way to build that path. conda installs itself as
a shell *function*, so in a non-interactive shell (CI, or an agent's Bash tool) it fails
with `__conda_exe: permission denied`, and the surrounding `export PATH="$(...)/..."`
silently produces a garbage prefix that falls through to whatever was already on PATH —
the command then appears to work, for the wrong reason. Call the real binary at
`<conda-root>/bin/conda`, or hard-code the prefix.

For `ui/` lint, Node only has to resolve for the ESLint shebang, so Homebrew's is fine:

```bash
cd ui && npm run lint     # or scoped: ./node_modules/.bin/eslint src/<file>
```

**Tests:** from the repo root, with the env's `pytest` (see above — a bare `pytest` may
not be it), just `pytest` — `pytest.ini` sets `pythonpath`/`testpaths` and a verbose
default. Ask the suite for the case count rather than trusting a doc: `pytest --collect-only -q | tail -1`. Coverage: `pytest
--cov=archimedes --cov-report=term-missing`. The analytics-engine runs its own suite:
`cd analytics-engine && uv run pytest`.

**AWS:** prod is Dan's account (`037613907429` / `us-east-1`); ask **Dan** for a scoped IAM
user (`SecurityAudit` + `ViewOnlyAccess`, MFA). **Credentials go over a secure channel —
1Password / Bitwarden / Signal — never Discord, never email.** The web tier is Fargate:
no host to SSH into (`aws ecs execute-command` for a task shell); SSM reaches only the
residual EC2 box running the oracle / agent / kb runners.

**Submodules** ([`docs/submodules.md`](docs/submodules.md) — also the canonical pointer for
any Arc/Circle integration question, via `submodules/context-arc/`). Do this once per
clone; without it a fresh clone silently drifts off `main`'s recorded pins:

```bash
git config submodule.recurse true && git config diff.submodule log
git submodule update --init --recursive   # --recursive: Linus has a nested submodule
```

## Stack — only the parts you would guess wrong

Live picture: [`docs/architecture.md`](docs/architecture.md). Deployed contract census: `GET
/api/config/contracts`. Four deltas a reasonable guess gets wrong:

- **Backtesting is [backtrader](https://github.com/mementum/backtrader), not vectorbt.** Archived `design.md` § 6 says "vectorbt / custom numpy engine" — superseded per [ADR](docs/adr/backtrader-backtest-engine.md). vectorbt is a v2 problem, only if parameter-sweep speed becomes a constraint.
- **`StrategyRegistry` and `AssetRegistry` both exist and both are live.** `ecosystem-design-spec.md` describes the former as *replaced by* the latter; they coexist and serve different lookups. The spec-vs-state delta is intentional — don't "fix" it by deleting one.
- **Contract ABIs are cached in [`contracts/abis/`](contracts/abis/)** for backend + UI. Read from there; don't re-derive from Foundry output at runtime.
- **Frontend is React 19 + Vite 8 + UnoCSS + viem** — not Next.js, not Tailwind. LLM is **`bedrock_converse` / `amazon.nova-micro-v1:0`** (BYOK + local-Ollama paths preserved); `response.model` is the provenance of record across the GLM→Bedrock migration. Datastore **Aurora PostgreSQL 18.3** + ElastiCache Redis; deploy **ECS Fargate** behind ALB/WAF ([ADR](docs/adr/ec2-to-ecs-fargate-cutover.md)).

## Engineering conventions

### Branch model (build-on-deploy, main-only)

- **`main` is the only long-lived branch, and it is the deploy branch.** Every merge to
  `main` builds and deploys to the live Fargate stack. No `develop`/integration branch — it
  drifted unused and was retired ([ADR](docs/adr/build-on-deploy-main-only.md)).
- **`main` moves continuously.** Dan and teammates drive the build through parallel Claude
  Code sessions and merge on their own authority, so `main` can take dozens of commits in a
  day. Branch late, rebase right before merging, merge in a tight window. Don't wait for
  `main` to "settle" — it won't.
- Short-lived per-owner branches `<discord-handle>/<short-name>` → PR → merge; delete after.
- **Merge commits only.** Squash- and rebase-merge are disabled in repo settings; use `gh pr
  merge <n> --merge`. Why: merge commits preserve branch topology, so `git log --merges` /
  `--graph` show unit-of-work boundaries. Rebase-merge confuses `git branch --merged`
  (rewritten commits aren't ancestors of `main`) and loses the "this was a single PR" signal
  needed for post-hoc forensics.
- **The few hard rules — universal, and they do not impede speed:** never force-push
  `main`; never commit secrets or `.env`; one logical change per PR. Force-pushing your
  *own* unmerged feature branch is fine and expected (rebase-before-merge).
- **One approving review** is enough for non-contract changes. Contract changes warrant
  **Dan's approval (contract/infra owner — the sole required approver)**, with a second
  review from **Bogdan (`mnemonik-dev`) when he is active** (as of 2026-08 he is not),
  given live-funds risk.
- Commits: imperative mood, atomic, one logical change. Optional scope tags `[strategy]`,
  `[backtest]`, `[contracts]`, `[frontend]`, `[infra]`, `[docs]`.

### CI / quality gates

Definitions live in [`.github/workflows/`](.github/workflows/); what matters here is **what
blocks and what does not**:

| Workflow | Blocks? | |
| --- | --- | --- |
| `quality-gate.yml` | **YES** | `pytest -m "not integration"` (unit suite, no DB/Redis) **and** `ruff-gate`. Everything else it reports — full `ruff check`, `ui/` lint — is `continue-on-error`, posted as a PR comment. A ≥60% coverage gate is wired to PRs whose author is `t2o2`; that account is dormant (see § Spec-driven execution), so the gate currently never fires. |
| `complexity-gate.yml` | no | Complexity/nesting table as a PR comment. **Informational only — never blocks.** Don't restructure code to satisfy it. |
| `main-format-guard.yml` | n/a | On push to `main`, if `ruff format --check` fails it reformats, commits back with `[skip ci]`, and **fails its own run** so the violation stays visible. `main` self-heals, so open PRs aren't stranded with red ruff-gates. |
| `import-guard.yml` | **YES** | Runs on every PR. Catches imports that resolve locally but not in a clean environment. |
| `contracts-test.yml` | no | `forge build` + `forge test`. **Path-filtered to `contracts/**`, so it must not be made a required check** until an always-runs fallback job reports the same check name — see the boxed comment in `scripts/setup-branch-protection.sh`. |
| `docs-gate.yml` | no | Link resolution and index completeness across `docs/` and root markdown; staleness reported but never blocking. **Path-filtered — same constraint as `contracts-test.yml`, and the same warning is boxed at the top of the workflow.** Run it locally with `make docs-check`. |
| `deploy.yml` · `release-tag.yml` | n/a | Build → ECR → roll Fargate (superseded runs auto-cancel); semver tag per merged PR. |
| `deploy-runners.yml` | n/a | `workflow_dispatch` only — the `push` trigger is deliberately commented out. Oracle + agent EC2 and the KB scheduled Fargate task. |

**Release tags — two surprises.** (1) Bump markers are read from the PR title with an
**end-of-title anchor**: `!version-release` → major, `!minor` → minor, anything else →
patch. Title-end matching prevents false positives when the marker text appears in body
prose. (2) **Direct pushes to `main` with no associated PR are silently skipped — no tag is
created.** Prefer a PR for anything you'd want to find in `git log` later, including agentic
work. `!minor` = new user-facing capability; `!version-release` = major milestone, used
sparingly; when in doubt, no marker.

### Before you approve a merge — the green check may not mean what you think

Applies to every reviewer, every session, and **every review subagent**. Rules 1–4 come
from defects that shipped past a full row of green checks in a single week; rule 5 from a
merge train three months later. The common shape: **a signal was trusted for something it
does not actually measure.**

**1. A stale PR's green check describes a base that no longer exists.**
GitHub freezes a PR's test-merge ref at its last push. A PR far behind `main` was tested
against a base that may lack validators, columns, or behaviour that exist today — and
"re-run failed jobs" replays the same frozen snapshot, so it can never pick up the fix.
→ **If a PR is materially behind `main`, re-verify against current `main` before merging,
whatever the checkmark says.** Push to the branch (or merge `main` in) to regenerate the
ref, then read the *new* result.
*Cost of learning it:* #1155 failed for exactly this reason; days later #1099 merged green
and broke `main`, because its tests predated a `vault_address` validator.

**2. Verify the merged result, not each PR.**
Every PR being green does not make their union green. Semantic conflicts are textually
clean and CI-invisible.
→ **After merging anything non-trivial, re-run the suite on the merged `main`** — with the
*exact* command CI uses, from the repo root. A local `pytest backend/tests` collects a
different set than `pytest -m "not integration"` from the root, and the gap is where this
hides.

**3. A test that passes against the unfixed code proves nothing.**
The specific trap: **passing the same literal to both sides of the boundary the bug lives
on.** A Redis-key casing test that lower-cased the input before handing it to the reader
made fixed and unfixed code produce identical keys — it passed either way and guarded
nothing.
→ **Before pushing a regression test, revert the fix and confirm the test fails.** If it
passes both ways, it is not a regression guard; say so rather than counting it as coverage.

**4. A guard must be shown to reject something.**
Guards are where this repo's defects cluster: one checked presence but not value
(`AGENT_DRY_RUN=1` silently meant LIVE), one measured the wrong compression state, one
claimed "no network" and "installed package import path" while enforcing neither.
→ **Build the input that *should* fail the guard, run it, confirm it fails — before
pushing** — and put that demonstration in the PR body. This applies to claims made in
**prose** too: a PR description asserting a property the code does not enforce is the same
defect, just harder to grep for.

**5. In a merge train, rule 2 runs *between* merges, not once at the end.**
A train lands PRs seconds apart. Union-testing only the final `main` finds the break after
every intermediate `main` has already been red — and every open PR's test-merge ref
regenerates against a broken base, so the whole board goes red for a reason none of them
caused.
→ **Before the train, pairwise-intersect the members' changed-file lists (`gh pr diff
<n> --name-only`) and union-test every pair that shares a file** — merge the pair locally and
run the CI command. A shared *function* is the dangerous case: both branches are green,
neither diff touches the other's lines, and the collision is invisible until they execute
together. Corollary for test doubles: **a boundary mock must stub the shared function's
full surface, not only the calls the branch under test makes** — a double that covers your
own path silently drops a sibling's.
*Cost of learning it:* 2026-08-31 — #1562's ownership-stamp tests mocked only the Redis
methods `save_trace` used on that branch, while #1403 added `sadd`/`srem`/`hsetnx`/`hdel`
index maintenance to the same `save_trace`. Both green alone; merged, the double's
non-awaitable `srem` failed 5 tests on `main` and poisoned every open PR's merge-ref until
the fix-forward (#1565) landed.

### Testing conventions (codified 2026-05-27)

Hard-won during the post-hackathon test-coverage push and the env-flaky-test
sweep. **CI green ≠ local green** is itself a bug; tests must pass identically
in both environments. Read this before writing any new test.

- **Tests must be hermetic.** No `.env` dependence, no live Redis / Postgres /
  Anthropic / Arc RPC. CI runs without `.env` or those services; tests that pass
  in CI but fail locally (or vice versa) are real bugs that need fixing, not
  flaky tests to be skip-marked. The hermetic gate: `env -i HOME=$HOME PATH=$PATH
  PYTHONPATH=backend python -m pytest backend/tests/test_<module>.py -q` must
  end with `N passed, 0 failed`.
- **`asyncio.get_event_loop().run_until_complete(...)` is forbidden.** Python
  3.12 removed implicit loop creation in non-running contexts and raises
  `RuntimeError`. Use `asyncio.run(coro)` for sync tests calling an async
  function, or `async def` plus the automatic `@pytest.mark.asyncio` (asyncio_mode
  is `auto` in `pytest.ini`) for async tests. The CI gate: `grep -r
  "asyncio.get_event_loop" backend/tests/` must return nothing.
- **Subprocess tests must use `_clean_subprocess_env()` + `_DOTENV_NEUTRALIZE`.**
  Reference pattern in [`backend/tests/test_security_hardening.py`](backend/tests/test_security_hardening.py).
  Inheriting `os.environ` leaks the developer's `.env` (which sets
  `DATABASE_URL=postgresql://...@postgres:5432/...`, a hostname only reachable
  inside docker compose) into the subprocess, causing `psycopg2.OperationalError`
  on bare-metal local. The parent pytest process can also leak `.env` vars via
  earlier test imports that trigger `load_dotenv` — `_DOTENV_NEUTRALIZE` plus an
  explicit `env=` whitelist on `subprocess.run` are both needed.
- **Mock at boundaries, not internals.** Wrong: mocking dict operations or
  internal helpers. Right: mocking the HTTP client, the DB session, the Redis
  client, the chain client, the Circle signer. Real precedents to copy:
    - `AgentStateStore` mock for Redis-down scenarios — see
      [`backend/tests/test_api_routes.py`](backend/tests/test_api_routes.py)
      `TestAgentRoutes::test_agent_status_redis_down_defaults` (uses
      `patch.object(AgentStateStore, ..., AsyncMock(side_effect=ConnectionError))`).
    - `chain_client` + `chain_executor` mocking — see
      [`backend/tests/test_api_routes.py`](backend/tests/test_api_routes.py)
      `client` fixture (line 36).
    - SIWE signed-cookie test helper — see
      [`backend/tests/test_user_routes.py`](backend/tests/test_user_routes.py)
      `_siwe_cookies(wallet)` for testing PII-gated endpoints with a real signed
      session (not header spoofing).
    - tmp-sqlite DB fixture — see
      [`backend/tests/test_api_routes.py`](backend/tests/test_api_routes.py)
      `_use_tmp_db` (monkeypatch.setenv DATABASE_URL to a tmp sqlite).
    - `httpx.ASGITransport` for endpoint tests — see
      [`backend/tests/test_risk_routes.py`](backend/tests/test_risk_routes.py).
- **Test the production code path, not the easy one.** When a function accepts
  multiple input types (e.g. `_confirm_receipt` takes both `str` and `bytes`
  HexBytes), the test matrix must cover *every* type the production code path
  emits. The raw-key signer in `chain/executor.py` emits `HexBytes`; tests that
  only exercise the `str` branch leave the production path uncovered. Issue
  [#408](https://github.com/a-apin/archimedes/issues/408) was filed to backfill
  this specific gap.
- **Coverage targets and gates.** Per-module ≥85% line coverage is the standard
  for new test work. Measure with `pytest --cov=archimedes.<module> --cov-report=term-missing
  backend/tests/test_<module>.py`. The repo-level `--cov-fail-under=60` gate is
  conditioned on `t2o2` being the PR author and is therefore **dormant** — nothing
  enforces repo-level coverage today. See § Spec-driven execution.
- **No skip-marks on flaky tests.** If a test is flaky, the cause is almost
  always a missing mock at a boundary or hidden environmental state. Fix the
  flakiness, don't `@pytest.mark.skip`. Skip-marks should be rare and load-bearing
  (e.g., "Requires chain_client.settings module-level init mocking" — a known
  architectural limitation, not a flaky test).

### Linting, formatting, dependencies

Ruff config is [`ruff.toml`](ruff.toml) — read it rather than trusting a rule list here.
The blocking subset in `ruff-gate` is deliberately narrow (formatting + syntax/undefined
names) so the gate doesn't trip on pre-existing style debt; the broader `ruff check .` is
informational. `pip install pre-commit && pre-commit install` mirrors the CI gate exactly,
so pre-commit can't pass while CI fails. `--unsafe-fixes` needs line-by-line human review
and is never auto-applied.

Dependency hygiene — `pip-audit` / `cd ui && npm audit --omit=dev` (prod-only view; drop
the flag to also see dev-tooling CVEs) / `cd wallet-setup && npm audit` (a second, separate
npm project — runtime deps only, no `--omit=dev` needed) before any dep bump;
triage Dependabot promptly. Three rules:

- **Pin transitively-vulnerable deps directly when CVEs warrant it** — e.g. `starlette>=1.0.1` is pinned in both `environment.yml` and `backend/requirements.txt` to close PYSEC-2026-161 even though it would otherwise arrive transitively via FastAPI. Pin to the closest `Fix Versions` so a fresh resolution can't regress.
- **Keep `environment.yml` (local dev) and `backend/requirements.txt` (Docker / CI) aligned.** Drift is the most common source of "works on my machine" + "breaks in CI" — the `slowapi`/`redis` misalignment caused 62 `user_routes` test errors locally on 2026-05-24. Any new pip dep goes in BOTH files in the same PR.
- **No new dep without a sentence on what it does and why**, as a comment in the requirements/env file. "added by tooling" is not a sentence. Frontend: always `npm ci`, never `npm install` — `npm ci` verifies `package-lock.json` integrity and won't mutate the lockfile.

### Smoke-test before deploy; don't connect important wallets

Don't push to shared infrastructure without smoke-testing locally first; contract changes
go against Arc testnet first. Use a fresh dev wallet, never one with real assets, and never
paste private keys in this repo — `.env.example` is the template, `.env` is gitignored.

### Security ships with the product, not after

Every visitor to the live site gets the **same** security posture. For a Claude session:
**don't propose deferring security work** for cost or scope reasons, and **don't accept "we
don't have real users yet"** as justification for skipping a security fix. Surface
cost-vs-security tension and lean toward keeping security live; the human calls it (Dan's
stance, 2026-05-28 — he will cover the cost personally). Security-relevant PRs (auth,
secrets, permissions, vault contracts, anything PII-adjacent) get more careful review than
feature work even when the diff looks small.

## When to ask before acting

- Pushing to shared infrastructure
- Adding a top-level dependency (state which package, why, and the license)
- Touching `docker-compose*.yml`, deployment configs, or CI/CD wiring without team alignment
- Any smart contract change (needs **Dan's approval as contract owner — the sole required
  approver; Bogdan is the preferred second reviewer when active**) — contracts hold live
  funds and Dan deploys them himself
- Editing `.env.example` or [`environment.yml`](environment.yml) — both are env contracts
  everyone else rebuilds against
- Anything touching the strategy-passport / reasoning-trace data flow, or the on-chain vault
  contracts (`Vault`, `VaultFactory`, `SyntheticVault`, `ReasoningTraceRegistry` — all live)
- Anything under `~/.arc-canteen/` (individual team-member credentials)
- **Never** reintroduce the `/api/papers/corpus/*` endpoints deleted in issue #201. Any
  knowledge-graph surface must come from real KB pipeline output, never arxiv-metadata
  synthesis — see [`docs/submodules.md`](docs/submodules.md).

## When NOT to ask

- Inside your own feature branch, editing your own files
- Writing tests
- Adding docstrings, type hints, or formatting fixes
- Updating `docs/` to keep specs in sync with shipped code
- Running `pytest`, `ruff`, `prettier --write`, `docker compose up --build` locally

## Working with AI agents on this repo

Most of this team works through AI agents. Read this before dispatching agents or feeding
work to the issue pipeline. Two recurring non-repo-specific traps — character limits (`wc
-m`, not `wc -c`) and zsh quoting — are in
[`docs/agent-gotchas.md`](docs/agent-gotchas.md).

### Spec-driven execution (highest-leverage workflow)

> **Who executes, as of 2026-08-03.** The autonomous agent account `t2o2` (Chuan Bai's
> system) is **not an active resource.** Chuan stepped back on 2026-06-24 and no work is
> being dispatched to `t2o2`. Do not assign issues to it, do not plan around it, and do
> not infer availability from older documents. The five `*-t2o2-issue.md` specs it
> executed were removed by this series; they survive only as references inside
> `docs/archive/`, and a reference is not a live capability. Historical references to it are preserved as record, not as instruction.
>
> **The discipline below still applies in full** — it was always about spec quality, not
> about which executor consumes the spec. Today the executor is a Claude Code session run
> by a human teammate, working an issue on a branch and opening a PR.

An agentic coding system is wired to this repo: it reads issues and writes code against
them. **A well-specified issue is executable work** — often the highest-value thing a human
+ hosted Claude can produce is a judge-grade issue spec, not hand-written code. Humans plan
and spec; the system executes; humans review the PR. Vague issues produce vague code — spec
quality is the throughput lever. Skeleton:
[`docs/prompts/agentic-issue-skeleton.md`](docs/prompts/agentic-issue-skeleton.md).

**Operational mechanics (hard-won 2026-05-18 — the spec is only half the job):**

- **Trigger = a human picking it up.** There is no dispatch bot today. An issue is
  executed when a teammate opens a session against it, so an unassigned judge-grade
  spec sits idle until someone claims it. Assign the issue to the human who is doing
  it, so two people don't start the same work. The `APIN - <Area> - <Title>` prefix is
  a naming convention, not a trigger, and never was.
- **A claimed issue is authorized. Do not close on lane grounds.** If a session has
  taken an issue, execute it — regardless of which teammate's
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
- **Verify independently — "closed" ≠ "fixed".** Sessions sometimes close an
  issue without resolving it. Re-check against the acceptance command on a cold
  clone before trusting completion; reopen with evidence if unmet.
- **Pre-close verification gate (added 2026-05-24).** Before closing *any* issue,
  the executing session MUST:
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
- **Verify your own audit claims before acting on them (added 2026-05-27).**
  When an agent (including yourself, earlier in the session) flags a finding
  like "X is in git history" or "Y is a vulnerable dependency," verify it with
  the literal command before recommending or applying the remediation. The
  session example: an audit message flagged `infra/terraform.tfstate` as
  committed-to-git CRITICAL; subsequent verification with `gh api
  search/code -f q="tfstate repo:..."` and `git rev-list --all --objects`
  confirmed it was never tracked — a false alarm. Acting on unverified audit
  claims wastes work and erodes trust in the agent's findings. The rule is
  symmetric: do not over-trust audit output from your past self, and surface
  the verification command alongside any audit claim you make so the next
  reader can re-run it cheaply.

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
  private keys in the tree. `.gitignore` covers `*.pem`, `*.key`, `*.p12`, `*.pfx`,
  `*.crt`, `*.tfstate*` globally as of 2026-05-27. The
  [`detect-secrets`](https://github.com/Yelp/detect-secrets) pre-commit hook is
  wired in `.pre-commit-config.yaml` with the (currently unaudited) baseline at
  `.secrets.baseline` — regenerating and auditing it is outstanding (all 28 entries are
  `is_verified: false`, dated 2026-05-27). Install with `pip install pre-commit && pre-commit install`
  so commits are scanned locally before push.
- **Rotation alone does not undo a leak.** When a credential is committed and
  later removed, the value remains in `git log -p` forever and on every clone
  anyone has done. Rotation makes the leaked credential *useless going forward*
  (the threat is neutralized) — it does not erase the historical artifact.
  Plan accordingly: don't put a secret in code thinking rotation makes the leak
  fully reversible. The SSH deploy key that lived briefly in
  `infra/archimedes-deploy-key.pem` was rotated 2026-05-26 and the old key
  revoked on the EC2; the leaked bytes are still in git history but useless.
- **Terraform state belongs in S3, never local-committed.** S3 backend: bucket
  versioned + encrypted (SSE-S3) + bucket-policied to deny non-TLS access +
  restrict to the account principal; S3-native locking (`use_lockfile = true`)
  obviates the DynamoDB lock table. See [`infra/README.md`](infra/README.md) for
  bootstrap commands. State can contain real secrets (e.g., the `tls_private_key`
  resource puts a private key into state) — treat the backend as a secrets store
  and scope IAM read access accordingly.
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
- **Clean up worktrees + branches AS YOU GO, not just at session end (added
  2026-06-25).** Parallel worktree-isolated agents accumulate fast — one session
  left **14 stale `.claude/worktrees/agent-*` dirs + ~24 branches**. Discipline:
  (1) when an agent finishes, remove its worktree (`git worktree remove --force
  <path>` — **never a locked / still-running one**) + its local
  `worktree-agent-*` branch, then `git worktree prune`; (2) when a PR merges,
  delete its branch (`gh pr merge --delete-branch`, or `git push origin --delete
  <branch>` + `git branch -D`); (3) keep branches for **open PRs** and
  **in-flight agents**. Turn on the repo's "auto-delete head branches on merge"
  to halve the remote side. Always verify before bulk-deleting: cross-check
  `git worktree list` + `gh pr list --state open` so you never drop a running
  agent's or an open PR's branch.
- **Structure subagent responses to preserve parent context (added 2026-05-27).**
  When dispatching review-style subagents (PR review, audit, multi-file scan),
  specify both a structured response format (`Verdict / What it does / Concerns
  / Recommendation` per item) and a per-item word cap. Three subagents
  reviewing 8 PRs in parallel returned ~3000 words of structured per-PR
  verdicts I could synthesize without re-reading any diff — the structure is
  what made the synthesis cheap. Unstructured "review these PRs and tell me
  what you think" produces long prose that the parent has to re-read and
  re-organize, defeating the context-preservation reason for fan-out.

### Agent-as-proxy authorization (added 2026-05-27)

Teams have lanes (see "Lead + coverage" table) and humans have AI agents that
operate on their behalf. When a teammate is unresponsive for an extended
window (>24h) and work in their lane is blocked, their agent **is authorized
to act as proxy for backend code reviews and merges in that lane**, with two
exceptions:

- **Solidity contract changes still require the human owner's explicit consent.**
  Contracts hold live funds; the owner's contract-specific judgment is
  load-bearing. An agent can review and recommend, but **Dan (the contract owner,
  who deploys them himself) must approve the merge** — and where possible **Bogdan
  (`mnemonik-dev`) provides the two-eyes contract review**. (Updated 2026-06-24:
  contract approval routes to Dan, not Chuan, after the ownership change. Updated
  2026-08: Bogdan is not currently active — Dan is the sole required approver;
  two-eyes review resumes when a second contract reviewer is available.)
- **Architecture decisions and infrastructure cost commitments** (new AWS
  services, recurring spend, multi-day migrations) still warrant **Dan's** ack
  (he owns the AWS account). Operational fixes within an already-approved
  architecture are fine to proxy.

This unblocks work without compromising the high-stakes review surfaces.
Document each proxy-merge action in the PR description with a one-line note
("Reviewed by <agent> on Dan's behalf — Dan offline since <timestamp>")
so the human can audit on return. If the human disagrees on return, revert and
re-review — the proxy is a stop-gap, not a delegation.

## Architectural decisions — index, not argument

**Decisions live in [`docs/adr/`](docs/adr/README.md) (18 records) and the ADR is
authoritative** — status, date, owner, alternatives, consequences. Do not relitigate an
`Accepted` ADR in a spec, a comment, or a PR description; open a superseding ADR instead.

- **Product shape** — [two-tier marketplace](docs/adr/two-tier-marketplace.md) · [non-custodial vault, owner ≠ agent](docs/adr/non-custodial-vault-owner-agent.md) · [Arc settlement](docs/adr/arc-settlement-chain.md)
- **Rigor gate** — [unified gate](docs/adr/rigor-gate-unification.md) · [`num_trials` self-containment](docs/adr/num-trials-self-containment.md) · [backtrader](docs/adr/backtrader-backtest-engine.md) · [selection-bias spec](docs/specs/selection-bias-corrections-spec.md)
- **Generation** — [debate society is the sole pipeline](docs/adr/debate-society-sole-generation-pipeline.md) (supersedes fusion/architect routing) · [K=1 + external rigor gate](docs/adr/k1-generation-external-rigor-gate.md) · [Bedrock](docs/adr/glm-to-bedrock-llm-migration.md)
- **Provenance** — [passport](docs/specs/strategy-passport-spec.md) · [commit-reveal](docs/specs/commit-reveal-trace-spec.md) · [Xia protocols](docs/specs/xia-2026-protocols.md)
- **Infra** — [Fargate cutover](docs/adr/ec2-to-ecs-fargate-cutover.md) · [Aurora + Alembic](docs/adr/aurora-postgres-alembic-datastore.md) · [build-on-deploy](docs/adr/build-on-deploy-main-only.md) · [AWS account migration](docs/adr/aws-account-migration.md)
- **Interfaces, principles, risks** — [frozen interfaces](docs/specs/component-interfaces-spec.md) · [principles](docs/architectural-principles.md) · [risk matrix](docs/architecture.md)

### Fail-soft is correct for optional configuration and wrong for anything a claim depends on

For credentials and for measured values, the correct degraded state is a loud, visible
absence — a `NOT_RUN`, an em-dash, a startup abort, a CloudWatch alarm — never a plausible
substitute. A fail-soft default converts an outage into a silence, and silence is
indistinguishable from working. The fix is not "always crash": a function that loads secrets
should know which parameters are load-bearing and be loud about *those* while genuinely
optional ones stay quiet. The rigor gate already gets this right with its four-state
`pass`/`fail`/`pending`/`degenerate`; three other subsystems did not — SSM credentials booted degraded by
design and marketplace publish never worked in production for 19 days with no alarm; the
leaderboard fell back to migrated fixture columns and presented fabricated statistics as
measured on the flagship public page; and a persisted return series bound to the wrong asset
had the top-ranked strategy graded on the null benchmark's returns. Long form:
[`docs/architectural-principles.md`](docs/architectural-principles.md) § fail-soft.


## Documentation conventions

Full rules: [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md). Before you write a doc:

- **Where it goes, first match wins:** closed-off decision → `docs/adr/` · frozen
  interface/schema → `docs/specs/` · procedure run under pressure → `docs/runbooks/` ·
  reusable prompt → `docs/prompts/` · point-in-time investigation →
  `docs/audits|benchmarks|cost-estimates|analysis/` · session context → `docs/handovers/` ·
  how a live subsystem works today → `docs/` root · superseded history → `docs/archive/`.
- **Name it** `lower-kebab-case.md`, stable slug, no dates in filenames except
  point-in-time artifacts. **Front matter:** `status` / `owner` / `updated` /
  `superseded-by`. ADRs add `Supersedes` / `Superseded-by` — set both ends of the chain in
  one commit; never delete or silently rewrite an ADR.
- **Add a row to [`docs/README.md`](docs/README.md) in the same commit** — a doc not in the
  index does not exist.
- **60-day rule:** a `current` doc older than 60 days is presumed stale — re-verify, demote,
  or archive. Anything that decays faster belongs in the live source, not a doc.

---

_Disagree with something here? Discuss in Discord, agree, and update the file — don't let
it silently drift. Anything that decays does not belong in this file; put it where readers
expect decay._

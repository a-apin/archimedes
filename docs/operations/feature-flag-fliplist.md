# Feature-flag flip-list — the go-live checklist

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-08-31
> **superseded-by:** —

**Scope:** every feature flag in the tree, what it gates, what the committed
deployment config sets it to, and — for the ones still dark — who flips it and
what has to be true first. Tracking issue: [#834](https://github.com/a-apin/archimedes/issues/834).

**Why this is a file and not just an issue comment.** #834 has drifted twice.
The 2026-07-05 reconciliation found four live flags missing from the issue; the
2026-08-20 pass found a fifth — `AGENT_DRY_RUN`, the live-signal switch that
decides whether the agent signs real transactions. A checklist that silently
omits a money switch is worse than no checklist, because it is read as complete.
So this page is **enforced**: [`backend/tests/test_feature_flag_fliplist_drift.py`](../../backend/tests/test_feature_flag_fliplist_drift.py)
re-derives the flag inventory from the source tree on every CI run and fails if
any discovered flag has no row here.

The enforcement is one-directional by design: **every flag found in the tree
must appear on this page; this page may name more.** Retired flags, mode
selectors, and companion tunables are context a reader needs and the scanner
cannot infer, so extra rows are allowed. What CI blocks is the direction that
hurts — a new flag landing with no row.

---

## How to read the "deployed value" column

There is no single place that holds prod's flag values, so each row names the
layer that actually decides:

| Layer | File | Applies to |
|---|---|---|
| ECS task definition | [`infra/ecs.tf`](../../infra/ecs.tf) | The Fargate web/API tier — the live production backend. A change here is a `terraform apply` (new task-def revision + service roll), no image rebuild. |
| SSM Parameter Store | seeded by [`infra/scripts/setup-ssm-secrets.sh`](../../infra/scripts/setup-ssm-secrets.sh) | Secrets and the agent-runner switches. Not readable from this repo — verify with `aws ssm get-parameter`. |
| Compose | [`docker-compose.yml`](../../docker-compose.yml) / [`docker-compose.production.yml`](../../docker-compose.production.yml) | Local dev and the legacy EC2 box. |
| Code default | the `os.getenv(...)` call itself | Anything not set by a layer above. Most flags fall through to here — the code default *is* the production value. |
| GitHub repo variable | `vars.*` in `.github/workflows/` | CI/CD gates. Not readable from the tree — `gh variable list`. |
| Vite build arg | [`nginx/Dockerfile`](../../nginx/Dockerfile) | Frontend flags. Baked into the bundle at build time. **See the finding in § Audit findings — most UI flags have no build-arg wired, so they are pinned at their code default in every deployed image.** |

Where a row says *unset → code default*, that is a claim about the committed
config, verified by grep against `infra/`, `docker-compose*.yml`, and
`.github/workflows/`. It is **not** a claim about a hand-edited `/opt/archimedes/.env`
on the legacy box; that file is not in version control and cannot be verified
from here.

---

## LIVE — flags gating something today

| Flag | Reader | Deployed value | What it gates |
|---|---|---|---|
| `ARCHIMEDES_FUSION_ENABLED` | [`agents/strategy_fusion.py:74`](../../backend/archimedes/agents/strategy_fusion.py) | `"true"` in `infra/ecs.tf`; compose defaults `:-true` | **Hard prerequisite, not a flip.** Debate is the sole generation pipeline and every proposer routes through `StrategyFusion.propose()`, which returns a disabled sentinel when off — off means Generate silently returns no candidates. Must be ON everywhere. |
| `GENERATION_PAYMENT_REQUIRED` | [`services/generation_payment.py:58`](../../backend/archimedes/services/generation_payment.py) | `"true"` in `infra/ecs.tf` | The x402 paywall + wallet-link precondition on `POST /api/generate/start`. **Flipped on 2026-08-20** at `GENERATION_PRICE_USD="2.00"`, recipient `GENERATION_PAYMENT_RECIPIENT` (TF var, #1414). Only the literal `"true"` enables. |
| `GENERATION_PAYMENTS_DRY_RUN` | [`services/generation_payment.py:72`](../../backend/archimedes/services/generation_payment.py) | `"false"` in `infra/ecs.tf` | The generation-scoped settlement switch (#1428 split). `"false"` = the generation rail settles for real on the caller-signed metered-API path. Unset inherits `PAYMENTS_DRY_RUN`. |
| `PAPER_TRADING` | [`main.py:344`](../../backend/archimedes/main.py) | `"true"` in `infra/ecs.tf` | Marketplace engine executes simulated fills instead of on-chain trades. |
| `FEATURE_QUANT` | [`feature_flags.py:35`](../../backend/archimedes/feature_flags.py) | `"false"` in `infra/ecs.tf`; unset elsewhere → ON outside production (`APP_ENV != prod`) | The quant lab surfaces: `require_quant_feature()` on [`api/risk_routes.py`](../../backend/archimedes/api/risk_routes.py) and [`api/portfolio_routes.py`](../../backend/archimedes/api/portfolio_routes.py) 404s them when off. `GET /api/features` reports the resolved value to the UI ([`ui/src/features.js`](../../ui/src/features.js)). Deliberately off in prod. |
| `ENABLE_API_DOCS` | [`main.py:215`](../../backend/archimedes/main.py) | unset → code default (off) | Re-enables `/docs` + `/openapi.json`, which are suppressed whenever `PUBLIC_DOMAIN` is set. Leave unset in prod. |
| `PAYMENTS_HALT` | [`marketplace/config.py:79`](../../backend/archimedes/marketplace/config.py) | unset → code default `"false"` (not halted) | **Break-glass kill switch** for marketplace money movement. Distinct from `PAYMENTS_DRY_RUN` (posture) and `PAPER_TRADING` (execution) — this is the "stop now" lever. |
| `ARCHIMEDES_FUSION_REAL_DATA` | [`services/fusion_market_data.py:70`](../../backend/archimedes/services/fusion_market_data.py) | unset → code default ON | Kill switch for live market-data fetch inside fusion; `0`/`false`/`no`/`off` falls back to stubs. |
| `FUSION_SEMANTIC_RETRIEVAL` | [`services/paper_rag.py:195`](../../backend/archimedes/services/paper_rag.py) | unset → code default `"true"` | Reranking in fusion candidate selection. Note: the corpus has **no embedding column** — retrieval is lexical (#778 open), so this flag does not turn on semantic search. |
| `BACKTEST_REFRESH_ENABLED` | [`services/backtest_scheduler.py:55`](../../backend/archimedes/services/backtest_scheduler.py) | unset → code default `"1"` (on); forced off under `TESTING` | The curated library's staleness-driven backtest refresh loop. Kill switch for a scheduler that otherwise runs unattended. |
| `PAPER_TRACE_PUBLISH` | [`services/paper_trace.py`](../../backend/archimedes/services/paper_trace.py) (`publishing_enabled()`) | unset → code default ON | Paper-trace publishing. Turning it off is loud by design: the decision still gets a durable `disabled` row and `trace_coverage.status` reports `disabled`. |
| `VITE_GENERATION_QUOTE_ENABLED` | [`ui/src/featureFlags.js`](../../ui/src/featureFlags.js) | no build arg → code default ON (only the literal `"false"` suppresses) | The upfront cost-quote + x402 paywall step on the Generate page (#1296). |
| `DEPLOY_ENABLED` | [`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml) | GitHub repo variable — `gh variable list` | Gates the build-and-push / migrate / deploy-ecs jobs. Must be `"true"` for a merge to `main` to reach production. |
| `GENERATION_PIPELINE_FIXTURE` | [`agents/generation_pipeline.py:66,1166,2016`](../../backend/archimedes/agents/generation_pipeline.py) | unset everywhere | **Test-only.** Forces the deterministic fixture pipeline (no LLM, no network). Must never be set in any deployed environment — setting it would serve canned strategies as real output. |
| `GENERATION_PIPELINE_SKIP_BACKTEST` | [`agents/generation_pipeline.py:2016`](../../backend/archimedes/agents/generation_pipeline.py) | unset everywhere | **Test-only.** Skips the backtest leg for hermetic tests. Same warning as above: a generated strategy with no backtest cannot be gated. |
| `BOOTSTRAP_ALLOW_LEGACY_VAULTS` | [`scripts/bootstrap_vaults.py:230`](../../backend/archimedes/scripts/bootstrap_vaults.py) | unset (operator-supplied, `=1`) | Operator escape hatch in the one-shot vault bootstrap script — lets it proceed against pre-redeploy vault addresses. Not a runtime flag. |
| `ALLOW_LEGACY_HTTPS_SETUP` | [`infra/scripts/setup-https.sh:16`](../../infra/scripts/setup-https.sh) | unset (operator-supplied, `=1`) | Refuse-by-default guard on the **deprecated** in-instance certbot script. Live TLS is CloudFront + ACM; the script is retained for reference only. The flag exists so a stray `bash -s` cannot fire it. Do not set it against prod. |

---

## FLIP-AT-LAUNCH — the checklist

Each row is a flip someone has to perform, with the precondition that makes it
safe. **Owner is Dan** unless stated; nothing here flips in a drive-by PR.

| # | Flag | Current | Flip mechanics | Preconditions before flipping |
|---|---|---|---|---|
| 1 | `AGENT_DRY_RUN` | `"true"` in SSM (`/archimedes/prod/AGENT_DRY_RUN`, SecureString) | SSM parameter edit + agent-runner restart | Dan's explicit live-money go-ahead, after interpreter-parity (F2/F3) is merged and deployed so live behaviour matches the backtests being trusted. **Danger:** [`chain/agent_runner.py:92`](../../backend/archimedes/chain/agent_runner.py) resolves an *unset* value to `"false"` = LIVE signing. `setup-ssm-secrets.sh` refuses to `--apply` without an explicit recognised value for exactly this reason — do not "fix" that refusal. |
| 2 | `PAYMENTS_DRY_RUN` | `"true"` in `infra/ecs.tf` | task-definition env → `terraform apply` | [#975](https://github.com/a-apin/archimedes/issues/975) (non-custodial fee-custody migration). The custodial-INTERIM decision holds it at `"true"`; the generation rail already settles for real via `GENERATION_PAYMENTS_DRY_RUN="false"`, which is the whole scope the ecs.tf comment permits. Marketplace sweeps/withdraws stay blocked. |
| 3 | `EMAIL_VERIFICATION_ENFORCED` | `"false"` in `infra/ecs.tf` (auth container) | task-definition env → `terraform apply`, no image change | SES production access clears (sandbox only delivers to individually-verified addresses, so enforcing now locks out every real signup). Verification mail already sends on every signup regardless, so `email_verified` accrues from day one. Reader: [`auth/auth.js:26`](../../auth/auth.js) — only the literal `'true'` enforces. |
| 4 | `REVENUE_SWEEP_ENABLED` | not set in `infra/ecs.tf` → code default off | add `{ name = "REVENUE_SWEEP_ENABLED", value = "true" }` to ecs.tf → `terraform apply` | The recipient DCW must hold gas — **USDC is gas on Arc** and a freshly created DCW has none, so the first sweep fails without funding. `REVENUE_WALLET_ID` is already pinned in ecs.tf. Reader: [`services/revenue_sweep.py:188`](../../backend/archimedes/services/revenue_sweep.py) (`sweep_enabled()`), started from [`main.py:544`](../../backend/archimedes/main.py). Only the literal `"true"` enables. |
| 5 | `PAPER_TRACE_ANCHOR` | unset → code default OFF | env value wherever the paper-trace worker runs | On-chain `reveal()` writes `portfolio_before`/`portfolio_after` — the user's holdings — **permanently**. Two independent gates by design (this flag *and* per-deployment `PaperDeployment.anchor_traces`); do not collapse them. Nothing on-chain can be recalled, so consent is forward-only. |
| 6 | `KB_PIPELINE_ENABLED` | unset (deliberately, per [`infra/kb_runner.tf`](../../infra/kb_runner.tf)) | env on the scheduled kb-runner task | [#778](https://github.com/a-apin/archimedes/issues/778) / #1090 / #1092. Setting it today does **not** produce a KB: [`scripts/run_kb_pipeline.py:148`](../../backend/archimedes/scripts/run_kb_pipeline.py) raises "the full pipeline invocation is not yet wired". The task is also sized for skip-mode (see `variables.tf`) — flipping without resizing is a second failure. |
| 7 | `PREMIUM_MODELS_ENABLED` (+ `PREMIUM_MODELS_ALLOWLIST`) | unset → off | env value | AWS Bedrock Anthropic use-case activation (a console action, no PR). **Explicitly deprioritised by Dan 2026-07-04** — not a near-term flip. Reader: [`services/model_gate.py:56`](../../backend/archimedes/services/model_gate.py); the allowlist is an independent per-wallet path that works with the global flag off. |
| 8 | `VITE_ROADMAP_SURFACES` | `false` in `ui/.env.example`; **no build arg in `nginx/Dockerfile`** → pinned OFF in every built image | see audit finding A1 — needs a Dockerfile `ARG`/`ENV` + a `--build-arg` in `deploy.yml` before it can be flipped at all | [#1266](https://github.com/a-apin/archimedes/issues/1266) — vaults (Portfolio + vault-detail), Marketplace (+ market-strategy), Publish, Subscriptions, Learnings return to scope. |
| 9 | `VITE_KNOWLEDGE_GRAPH_TAB` | `false` in `ui/.env.example`; **no build arg** → pinned OFF in every built image | same as row 8 | #1090 (KB pipeline artifact) + #1092 (Postgres backfill). `kg_entities`/`kg_relations` are 0 rows today, so the tab would offer an empty capability. |
| 10 | `RUNNER_DEPLOY_ENABLED` | GitHub repo variable, unset → both jobs skip | repo variable → `true` | The runner infrastructure must exist first (#1065 step 2 `terraform apply` + step 3 on-chain verify). [`deploy-runners.yml`](../../.github/workflows/deploy-runners.yml) has a third layer of runtime existence checks, so an early flip no-ops loudly rather than erroring — but it is still an early flip. |

---

## DEAD / RETIRED — no reader in the tree

Nothing in this section is removed by the PR that created this page. Every dead
name below lives in [`.env.example`](../../.env.example) or in prose, and
`.env.example` is being rewritten by open PR [#1595](https://github.com/a-apin/archimedes/pull/1595);
editing it here would collide. Removal is tracked in the "Not done here" list on
that PR.

| Name | Where it still appears | Status |
|---|---|---|
| `DOCS_SITE_ENABLED` | nowhere | **RETIRED** by [#1634](https://github.com/a-apin/archimedes/issues/1634). It gated a GitHub Pages deploy that never went live. The docs site is now served from our own S3 + CloudFront ([`docs-site/infra/main.tf`](../../docs-site/infra/main.tf)) and the publish job has no variable gate: the bucket's existence is the gate, so an unapplied stack prints a NO-OP notice instead of needing someone to remember a switch. Procedure: [`docs/runbooks/docs-site-setup.md`](../runbooks/docs-site-setup.md). If the variable was ever created in repo Settings, delete it — it now reads as a live switch that does nothing. |
| `X402_WEBHOOK_SECRET` | `.env.example` only (commented out) | **DEAD** — zero readers anywhere in the tree. Delete after #1595 lands. |
| `ARCHIMEDES_X402_ENABLED`, `MAX_USDC_PER_DAY` | nowhere | **GONE.** The marketplace shipped via #958, not #749/#824 (both closed unmerged). Superseded by `PAYMENTS_DRY_RUN`. |
| `ARCHIMEDES_DEBATE_ENABLED` | doc-comments in [`agents/debate_engine.py`](../../backend/archimedes/agents/debate_engine.py) and test `delenv` cleanup | **RETIRED** by #880. The debate society is the unconditional sole pipeline; `_pick_pipeline` returns `debate` regardless. Setting it anywhere is a no-op. |
| `REQUIRE_SIWE_FOR_GENERATION` | test docstrings, historical audit docs | **DELETED** by #1300. The flag, `gate_generation`, and `_generation_auth_required` are gone; generation/chat auth is unconditional. Setting it is a no-op. |
| `ARCHIMEDES_TRACE_PIN_ENABLED` | [`docs/specs/ipfs-reasoning-traces-design-note.md`](../specs/ipfs-reasoning-traces-design-note.md) only | **NEVER IMPLEMENTED.** A proposed flag in a design note, not code. Named here so a reader who greps the docs does not go looking for the gate. |
| `chainlink_covered` / `chainlink_covered_synths()` | two prose mentions only: a docstring in [`backend/archimedes/scripts/generate_universe.py:19`](../../backend/archimedes/scripts/generate_universe.py) ("replaces the misleading `chainlink_covered`") and a historical comment in [`backend/tests/test_asset_universe_doc.py:84`](../../backend/tests/test_asset_universe_doc.py) | **ALREADY REMOVED.** Not a flag; #834's audit section listed the `universe.py` `SyntheticSpec` field as ready for cleanup, and re-grepping on 2026-08-31 finds it gone from [`backend/archimedes/universe.py`](../../backend/archimedes/universe.py) entirely — both surviving mentions are comments naming it as the *superseded* schema. Strike the action item. |

---

## Not flags — companions, mode selectors, and tunables

These are read by the same code paths and get confused for flags. They are
documented here so the flip rows above make sense; the drift guard does not
require them.

| Name | Kind | Notes |
|---|---|---|
| `PRICE_SOURCE` | mode selector | `"cascade"` in `infra/ecs.tf` (flipped 2026-07-04, Pyth live). Code default stays `"yfinance"` for local/CI safety. Reader: [`services/price_source.py:62`](../../backend/archimedes/services/price_source.py). |
| `PRICE_CROSSCHECK_BAND_BPS` / `PRICE_CROSSCHECK_MAX_STALENESS_SECONDS` | tunable | 5000 bps (50%) / 345600 s (4 d). Only bite while `PRICE_SOURCE=cascade`. Band tuning owned by Önder. |
| `MARKET_DATA_PROVIDER` | mode selector | Default `"yfinance"`. |
| `GENERATION_PRICE_USD` | tunable | `"2.00"` in `infra/ecs.tf`. Flat `flat_v1` pricing until #1217's measured budget replaces it behind the same `quote()` seam. |
| `GENERATION_PAYMENT_RECIPIENT` | required config | Terraform variable (#1414). Flag-on with no recipient is a deliberate 503, never a free pass. |
| `GENERATION_DAILY_CAP_PER_USER` / `_PER_IP` | tunable | `100` / `200` in `infra/ecs.tf`. Both layers must pass; `<= 0` disables a layer. |
| `PUBLIC_TRACE_VAULTS` | allowlist | Terraform variable. Unarmed, every unowned trace is house-public; arming it narrows visibility. Reader: [`services/trace_visibility.py`](../../backend/archimedes/services/trace_visibility.py). |
| `PLATFORM_ADMIN_WALLETS` | allowlist | Terraform variable. Gates `/api/metrics/private/*`. **Drift gotcha:** re-pass `TF_VAR_platform_admin_wallets` on every apply or it silently empties. |
| `PUBLIC_DOMAIN` | environment discriminator | Set = production. Used as the production switch by the docs gate and the SIWE cookie policy — not a flag, but it changes behaviour like one. |
| `APP_ENV` | environment discriminator | Feeds `FEATURE_QUANT`'s production default. |
| `TESTING` | test harness | Forces hermetic paths (fixture pipeline, `refresh_enabled()` off). Never set in a deployed environment. |
| `PAPER_TRACE_BACKFILL_MAX` | tunable | Default 500. |
| `REVENUE_SWEEP_INTERVAL_S` / `REVENUE_SWEEP_MIN_USDC` | tunable | Only consulted once `REVENUE_SWEEP_ENABLED=true`. |

---

## Audit findings (2026-08-31)

**A1 — the three UI flags cannot be flipped in a deployed build.**
[`nginx/Dockerfile`](../../nginx/Dockerfile) declares build args for exactly
three Vite vars — `VITE_CIRCLE_CLIENT_KEY`, `VITE_CIRCLE_CLIENT_URL`,
`VITE_API_BASE` — and [`deploy.yml`](../../.github/workflows/deploy.yml) passes
the same three. `VITE_ROADMAP_SURFACES`, `VITE_KNOWLEDGE_GRAPH_TAB`, and
`VITE_GENERATION_QUOTE_ENABLED` have **no** `ARG`, so Vite sees them unset in
every image and each resolves to its code default (OFF, OFF, ON). Setting them
in `ui/.env` works for `npm run dev` and nowhere else. Flip rows 8 and 9 above
are therefore *code changes*, not config flips, until the build args are wired.

**A2 — `infra/ecs.tf`'s `GENERATION_PAYMENT_REQUIRED` comment is stale.**
The comment above the entry still reads "flag stays `"false"` until Dan flips it
deliberately"; the value below it is `"true"` (flipped 2026-08-20). The value is
correct and the comment is not. Left as-is here — this page is not the place to
edit terraform — but a reader trusting the comment would draw the wrong
conclusion about whether the paywall is live.

**A3 — `ARCHIMEDES_FUSION_ENABLED` is still absent from `.env.example`.**
`docker-compose.yml` defaults it `:-true` and `infra/ecs.tf` sets `"true"`, so
every real path is safe. The gap only bites a non-compose `python -m uvicorn`
run, which would default the flag OFF and get a silently empty Generate. Named
in #834 since 2026-07-07 and still open; the fix belongs in the same PR that
next rewrites `.env.example` (currently [#1595](https://github.com/a-apin/archimedes/pull/1595)).

**A4 — no dead flag was removable in this pass.** Every name with zero readers
(`X402_WEBHOOK_SECRET` and the address/credential entries beside it) lives only
in `.env.example`, which #1595 rewrites. Touching it would guarantee a conflict
on an open PR for a comment-line deletion.

---

## Adding a flag

1. Read it in one place, behind a named function (`sweep_enabled()`,
   `publishing_enabled()`), not inline at every call site.
2. **Only the literal `"true"` enables a money switch.** An unset money switch
   must mean OFF. `AGENT_DRY_RUN` is the counter-example the repo already paid
   for: it defaults to `"false"` = LIVE, which is why the SSM seeding script has
   a hand-written refusal around it.
3. Set it explicitly in [`infra/ecs.tf`](../../infra/ecs.tf). A flag whose prod
   value is an accident of a code default is the failure mode the 2026-08-16
   money-switch pinning comment describes.
4. **Add a row here in the same PR.** CI fails otherwise —
   [`backend/tests/test_feature_flag_fliplist_drift.py`](../../backend/tests/test_feature_flag_fliplist_drift.py)
   re-derives the inventory and will name your flag.

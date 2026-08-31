# Claims ledger

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-08-31
> **superseded-by:** —

Every public claim Archimedes makes, with a verdict on each and the code that backs it.

`CLAUDE.md` § "The hard constraint, above everything else" says claims must be true. This
file is where that rule becomes checkable instead of aspirational: one row per claim, one
citation per row, and a test (`backend/tests/test_claims_ledger.py`) that fails when a
citation stops resolving.

**Measured against `main` on 2026-08-31.** Every `TRUE` row below was re-read in the live
tree on that date — the citation is the thing that was read, not a remembered fact. A row
whose evidence could not be confirmed is not marked `TRUE`.

## How to read a row

| Status | Means |
|---|---|
| `TRUE` | The claim, as it reads on the surface today, is backed by the cited live path. |
| `CHANGED` | The surface used to over-claim. The row records what it said, and the PR/commit that narrowed it. |
| `RETRACTED` | The claim was removed outright, and a guard exists to stop it coming back. |
| `OVER-CLAIMED` | Still live, still wrong, not fixed by this ledger. These are the open ones. |
| `PENDING ADR MERGE` | The position is the owner's stated one; the decision record it rests on is not merged yet. |

Two things this file deliberately does **not** do. It does not quote a curated-library
pass count — `CLAUDE.md` forbids it and the live gate is the only authority. And it does
not fix copy: `OVER-CLAIMED` rows are findings, and the fix is a separate change per
[#1241](https://github.com/a-apin/archimedes/issues/1241)'s own sequencing.

## Method

Each row was checked by reading the surface, then reading the code path the surface
implies, then asking whether a visitor who believed the sentence would be right. Three
checks that recur:

- **Does the reader's own path reach it?** A capability that exists in `backend/` but is
  hidden behind `ROADMAP_SURFACES_ENABLED` is not a claim the shipped build makes — and a
  capability the shipped build *describes* but the reader cannot reach is over-claimed
  even when the route is real.
- **Is the fallback loud?** A number with a cached substitute behind it is a claim about
  the cache, not about the system.
- **Is there a guard?** A retraction with no guard is a retraction until the next rewrite.

---

## Landing — `ui/src/components/Landing.jsx`

| Claim | Status | What backs it |
|---|---|---|
| "Four independent checks run outside the generator, on persisted returns, so the thing being graded cannot influence its own grade" | `TRUE` | `backend/archimedes/services/live_rigor_gate.py:151` — `verdict_from_returns` grades a persisted series outside the generator. The four checks are named at `ui/src/components/Landing.jsx:39`. The second grading scale (`BacktestResult.passes_rigor_gate`) that made this false was deleted in #1242 (`d7073f13`). |
| "Four verdicts, not two" — `pass` / `fail` / `pending` / `degenerate`, and a pass never rounds up | `TRUE` | `backend/archimedes/services/live_rigor_gate.py:54` defines `DEGENERATE`; `:92` makes `passes` a plain bool set `True` only by the `pass` constructor, so `pending` and `degenerate` are fail-closed. |
| Each rigor card states its own limit (DSR at the 0.90 level, PBO is a selection-set property, OOS is one chronological hold-out with no purge gap) | `TRUE` | `ui/src/components/Landing.jsx:26` documents each `limit` string against the module that computes it; the thresholds live in `backend/archimedes/services/rigor_profiles.py`. |
| "Board-level correction — ranking N strategies is counted as N tests", Benjamini–Hochberg at α = 0.05, advisory, never flips a verdict | `TRUE` | `backend/archimedes/services/rigor_evaluator.py:475` (`DEFAULT_BOARD_FDR_LEVEL = 0.05`) and `:486` (`compute_board_level_fdr`, advisory by its own docstring). Served publicly on `GET /api/leaderboard` — `backend/archimedes/api/leaderboard_schemas.py:82`, and `backend/archimedes/api/leaderboard_routes.py:175` never 401s (anonymous callers are served the curated scope — the endpoint's own `scope` docstring says so in as many words). |
| "Inspect — nothing is discarded ... a fail as durably as a pass" | `CHANGED` | Was "every run leaves a reasoning trace bound to the chain". Retracted 2026-08-30; the retracted wording is pinned in `ui/test/public-visuals.test.js`. A generation run computes a keccak provenance hash (`backend/archimedes/agents/generation_pipeline.py:1635`) whose own comment says it is "mirrored on-chain in v1.5" (`:1614`) — i.e. not today. The only writers to the registry are the agent rebalance tick's `_commit_trace` (`backend/archimedes/chain/agent_runner.py:1441`) and `_reveal_trace` (`:1596`), which no generation run reaches. |
| "Does Archimedes trade for me? No." | `TRUE` | The reachable act-on step is `POST /api/paper/deployments` (`backend/archimedes/api/paper_routes.py:92`), which is simulated. `POST /api/vaults/create` exists (`backend/archimedes/api/vaults_routes.py:330`) but its UI journey is off the shipped build — `ui/src/featureFlags.js:57` lists `portfolio` / `vault-detail` in `ROADMAP_PAGES`, and `:28` defaults `ROADMAP_SURFACES_ENABLED` to false. |
| "A failing strategy stays a failing strategy. Paper-trading one is allowed. Relabelling one is not." | `TRUE` | `backend/archimedes/api/paper_routes.py:92` checks ownership and that the stored spec still validates — and nothing else, so there is no rigor precondition to claim. The verdict itself is computed server-side (`live_rigor_gate.py:151`), so it cannot be relabelled from the browser. |
| Every vault / non-custodial / custody claim on this page | `RETRACTED` | #1469. `ui/test/roadmap-copy.test.js` source-scans this file for `/vault\|non-?custodial\|custody/i` with no carve-outs — which is why the file's own comments are written around the words. |
| "Arc census live — ≥N reported instances" | `TRUE` | `ui/src/components/Landing.jsx:504` renders "Live census unavailable · No cached count substituted" when `GET /api/config/contracts` fails or the pool count is unreadable. The count is a floor by construction (`:16` scopes it to five core fields). |
| The page quotes no strategy pass count and no performance number | `TRUE` | The only percentage on the page is "70/30" — a methodology parameter, not a result. Deliberate; see `ui/src/components/Landing.jsx:90`. |

## Security page — `ui/src/components/Security.jsx`

Rewritten 2026-08-31 (owner directive: the Security page "must be accurate and in good
alignment with the reality of the code"). This is the one public surface whose entire
content is claims about enforcement, so it gets a row per claim rather than a row per
theme. Every corrected sentence, every retraction, and the code literal behind each is
also pinned in `ui/test/security-claims.test.js` — the ledger records the reading, that
file makes the reading executable from the page's end, and the page therefore cannot
drift away from its own row without a red test.

| Claim | Status | What backs it |
|---|---|---|
| Hero status list — "Value at risk: **Testnet USDC only**" | `CHANGED` | Was "No real funds", which was written before the paywall shipped and read as *nothing you sign moves value*. Generation settles testnet USDC for real: `infra/ecs.tf:572` sets `GENERATION_PAYMENT_REQUIRED="true"` and `:556` sets `GENERATION_PAYMENTS_DRY_RUN="false"`, so `backend/archimedes/services/generation_payment.py:108`'s `settles_real_value()` is true in production. Confirmed anonymously against the live deployment on 2026-08-31: `GET /api/generate/quote` → `payment_required: true`, `dry_run: false`, `halted: false`. The row is `CHANGED` and not `RETRACTED` because the honest half survives — the value is faucet USDC on a testnet, not mainnet money. |
| Hero status list — "Paid surface: Generation — quoted live" | `TRUE` | New row, added with the sentence. The price is not written into the copy on purpose: it comes from `GENERATION_PRICE_USD` (`backend/archimedes/services/generation_payment.py:122`) and a number in prose would drift the first time it moved. `ui/src/components/Security.jsx:33`. |
| "Three boundaries. No role inflation." | `CHANGED` | Was four. The fourth was a vault-share-owner role over a path that has never run; removed by #1469 (`ui/src/components/Security.jsx:44`). |
| "Better Auth account. Canonical application identity. A connected wallet never creates or replaces the account session." | `TRUE` | `backend/archimedes/api/account_auth.py:73` — the session lookup forwards the request **cookie** and nothing else to the auth service, so no wallet header can participate in establishing a session. Restated in `docs/security/auth-model.md`. |
| "A five-minute, single-use EIP-4361 challenge proves control before a wallet is linked" | `TRUE` | `backend/archimedes/api/wallet_routes.py:22` — `_CHALLENGE_TTL = timedelta(minutes=5)`; `:350` rejects a challenge whose `consumed_at` is set, and `:389` consumes it in a conditional update that must affect exactly one row. |
| "Bounded internal agent ... a user session cannot assume that role, and that role cannot read or write another account's private records" | `TRUE` | `backend/archimedes/api/auth_guard.py:38` compares `X-Internal-Agent-Key` with `hmac.compare_digest` and **fails closed when the key is unset** (`:35-38`), so a misconfiguration cannot open the role. The scope half is true by enumeration: exactly two routes carry the dependency — `POST /api/traces/publish` (`backend/archimedes/api/traces_routes.py:398`) and `POST /api/agent/bootstrap-liquidity` (`backend/archimedes/api/agent_routes.py:209`). Both write; neither reads a per-account record. |
| Session — "Production session cookies are HttpOnly, Secure, and SameSite=Lax" | `CHANGED` | Was "Production cookies are HttpOnly and Secure", which under-stated a control that is actually there. `auth/auth.js:612` sets `useSecureCookies: production`; `:614` `httpOnly: true`; `:615` `secure: production`; `:616` `sameSite: 'lax'`. |
| Session — "nginx, the UI route guard, and FastAPI independently protect private surfaces. Four browse pages are deliberately anonymous" | `CHANGED` | The three-layer half was already true (`nginx/nginx.conf:341` `auth_request /_auth_session` on `^~ /app`; `ui/src/App.jsx:109` the client bounce; FastAPI's own dependencies). The added clause names what the old sentence let a reader mis-infer: `nginx/nginx.conf:271-289` serves `/app/explore`, `/app/leaderboard`, `/app/corpus` and `/app/strategy` without `auth_request`, matching `ANON_APP_PAGES` at `ui/src/routes.js:43`. The two halves are required to stay in lockstep by the comment at `nginx/nginx.conf:256`. |
| Scope — "Profile, strategy, job, and linked-wallet reads resolve through the authenticated Better Auth user—not a client-supplied address." | `TRUE` | `backend/archimedes/api/user_routes.py:150` and `:164` filter on `owner_user_id == user.id`; `backend/archimedes/api/strategies_routes.py:681` builds its owner filter from `user.id`; `backend/archimedes/api/wallet_routes.py:385` scopes the challenge lookup to `user.id`. `docs/security/auth-model.md` records that selected-wallet headers are hints, honoured only when they resolve to a verified link on the current user. |
| Integrity — "Agent-only writes require a service credential." | `TRUE` | Same enumeration as the Bounded internal agent row: `backend/archimedes/api/auth_guard.py:35`, applied at `backend/archimedes/api/traces_routes.py:398`. |
| Payment — "Generation is a paid call, bound to the wallet you proved ... a mismatch is refused before any settlement round-trip" | `TRUE` | New control block, added 2026-08-31 because the page described identity, edge, verdict and provenance while saying nothing at all about the one rail that moves value. `backend/archimedes/services/generation_payment.py:180` is the paywall; `:248` decodes the `Payment-Signature` header and `:253-258` rejects `payer_mismatch` when the authorization's `from` is not the account's linked wallet — **before** the `verify`/`settle` calls at `:262` and `:270`. The wallet-link precondition that makes "the wallet you proved" meaningful is `backend/archimedes/api/generate_routes.py:458`. |
| Payment — "published anonymously at GET /api/generate/quote" | `TRUE` | `backend/archimedes/api/generate_routes.py:356` returns `generation_payment.quote()`, defined at `backend/archimedes/services/generation_payment.py:133`, which reports price, asset, chain, recipient, `dry_run` and `halted`. The route carries no auth dependency, and an anonymous fetch against the live deployment returned the full quote on 2026-08-31. |
| Payment — "An operator kill switch refuses service rather than serving the paid product unpaid." | `TRUE` | `backend/archimedes/services/generation_payment.py:218` raises 503 on `PAYMENTS_HALT` instead of accepting an unverified header, and the docstring at `:78` records why this rail differs from the marketplace one. |
| Payment — "Paper trading is free." | `TRUE` | By absence: `backend/archimedes/api/paper_routes.py` never references `generation_payment`, so `POST /api/paper/deployments` (`:92`) reaches no paywall. Pinned as an absence in `ui/test/security-claims.test.js`, because a presence pin on that file would prove nothing. |
| Edge — "a script policy admitting same-origin bundles plus one hashed inline bootstrap" | `CHANGED` | Was "a hash-restricted script policy", which invites the reading that every script is hash-pinned. The policy is `script-src 'self' 'sha256-…'` (`nginx/nginx.conf:72`): same-origin bundles, plus one exact hash for the pre-paint theme bootstrap, per the comment at `:190`. Verified as served: the live `Content-Security-Policy` response header on `/security` carries that exact value. |
| Edge — "HSTS, framing denied outright, and a permissions policy that turns off geolocation, microphone, and camera" | `TRUE` | `nginx/nginx.conf:209` (`Strict-Transport-Security`), `:210` (`X-Frame-Options: DENY`), `:213` (`Permissions-Policy`), all with `always` so they are emitted on error responses too. All three confirmed on the live response headers 2026-08-31. **Two sources, found by probing rather than reading:** `infra/cloudfront.tf:197-218` re-asserts HSTS and frame-options at the edge with `override = true`, so those two arrive from CloudFront and nginx both; CSP and `Permissions-Policy` are nginx-only. The tell is `Referrer-Policy` — `nginx/nginx.conf:212` sets `same-origin`, the wire says `strict-origin-when-cross-origin` (`infra/cloudfront.tf:215`). The page names no referrer policy, so nothing on it is wrong; recorded because an auditor reading only the nginx config would get a different answer than the response headers give. |
| Edge — "Two per-IP request-rate zones run at the edge — the tighter one on the credential surface — with tighter per-route limits on expensive endpoints behind them." | `CHANGED` | Was "separate read/write rate limits", which is not what is deployed. The two zones exist (`nginx/nginx.conf:140-141`, `api_read` 60r/m and `api_write` 20r/m) but are applied **by path, not by method**: `:358` puts `/api/auth/` on `api_write` and `:365` puts all of `/api/` — every POST included — on `api_read`. A reader who believed the old sentence would have thought `POST /api/generate/start` was rate-limited more tightly than a read; it is not, at the edge. What actually bounds it is the per-route slowapi limit at `backend/archimedes/api/generate_routes.py:389` (`5/minute`), one of ~30 in the tree. |
| Verdict — "A rigor verdict is computed server-side, never asserted ... on persisted returns, so the thing being graded cannot influence its own grade." | `TRUE` | `backend/archimedes/services/live_rigor_gate.py:150` — `verdict_from_returns` grades a persisted series outside the generator and fails closed to `pending`. Same backing as the Landing row. |
| Provenance — "The agent's rebalance decisions are content-hashed and anchored on Arc" plus its two stated limits | `CHANGED` | Was the unscoped "Reasoning records are content-hashed and anchored on Arc", which the previous revision of this ledger already had to correct **in prose** — the row read `TRUE` and then spent a sentence explaining what the page failed to say. That is a finding, not a verification, so the scope now lives on the page: `contracts/src/ReasoningTraceRegistry.sol` and the commit/reveal pair at `backend/archimedes/chain/agent_runner.py:1441` / `:1596` anchor the agent's tick. A generation run computes the same shape of hash but is not anchored — `backend/archimedes/agents/generation_pipeline.py:1614` says "mirrored on-chain in v1.5", and `backend/archimedes/api/traces_routes.py:189` excludes generation traces outright. A decision producing no transaction has no anchor: `README.md:168`. Re-hashing against the anchor is real — `backend/archimedes/api/traces_routes.py:505`. |
| Known limits — "Testnet: Arc public testnet only, and the USDC in play is faucet USDC." | `TRUE` | Chain `5042002`; the live quote reports `chain: arcTestnet`. The faucet framing matches the pricing rationale recorded at `infra/ecs.tf:573-576` (one $20 drip = ten generations). |
| Known limits — "No execution: Archimedes does not trade with capital today. **Paid generation**, the rigor gate, paper trading, and the agent's trace anchoring are what run" | `CHANGED` | The claim was true and the enumeration after it was not: it listed everything that runs *except* the one rail that moves value. The reachable act-on step is still `POST /api/paper/deployments` (`backend/archimedes/api/paper_routes.py:92`), simulated; `POST /api/vaults/create` exists (`backend/archimedes/api/vaults_routes.py:330`) but its journey is off the shipped build (`ui/src/featureFlags.js:28`). |
| Known limits — "Live charge: ... A settled fee lands in a platform-operated wallet Archimedes signs for through its payment provider. It is a fee, not a balance held for you, and there is nothing there to withdraw." | `TRUE` | New row. The recipient is `infra/ecs.tf:578` (`GENERATION_PAYMENT_RECIPIENT`), a platform wallet whose Circle wallet id is pinned at `:583` and whose signing credentials live in SSM — so Archimedes controls it, and a reader deserves to know that before signing. **The wording is deliberately mechanical:** `ui/test/roadmap-copy.test.js:188` scans this page for `/vault\|non-?custodial\|custody/i` with no carve-outs and comments included, so the shorter word for this arrangement cannot appear here. Naming the constraint is better than letting the sentence look coy. |
| Known limits — "Some current oracle and risk inputs are mock data" | `TRUE` | Listed under Known limits (`ui/src/components/Security.jsx:245`), consistent with the Explore page's own source labelling (`ui/src/components/Explore.jsx:416`). |
| Known limits — "Immutable contracts: a defect requires a new deployment and migration rather than an in-place upgrade." | `TRUE` | No proxy, `delegatecall`, or upgradeable base appears anywhere under `contracts/src/`; `contracts/src/ReasoningTraceRegistry.sol:67` is a plain constructor + `Ownable`. The single "upgradeable" mention in the tree, `contracts/src/PriceOracle.sol:69`, is about an **external** Chainlink feed, not an Archimedes contract. |
| Known limits — "No independent security audit ... is claimed by this page" | `TRUE` | A disclaimer, correct as written (`ui/src/components/Security.jsx:253`). |
| Evidence links resolve to the code that enforces the control | `TRUE` | Seven links as of 2026-08-31: `/architecture`, `docs/claims-ledger.md`, the live `GET /api/generate/quote`, `docs/security/auth-model.md`, `contracts/src/ReasoningTraceRegistry.sol`, `backend/archimedes/services/generation_payment.py`, and `backend/archimedes/services/live_rigor_gate.py` — all present. The quote link is the one that matters most: it lets a reader check the page's payment claims against the running system without an account. |

**Not verified, and therefore not claimed.** Two things a reader might expect this section to
settle and it does not. (1) Whether the agent rebalance tick is *executing* in production
today — the anchoring path is real and cited above, but confirming it is currently running
needs prod access this audit deliberately did not use, so the page says what the mechanism
does and never says how often it fires. (2) Whether `INTERNAL_AGENT_API_KEY` is populated in
prod SSM (`infra/scripts/setup-ssm-secrets.sh:47`); it does not change the claim either way,
because `backend/archimedes/api/auth_guard.py:38` refuses every request when the key is
unset, so the boundary holds under both answers.
## `README.md`

| Claim | Status | What backs it |
|---|---|---|
| "lets you deploy what survives into a non-custodial vault on the Arc testnet, where every decision the agent reaches is written down as a reasoning trace you can read" | `OVER-CLAIMED` | `README.md:13`. The route is real (`backend/archimedes/api/vaults_routes.py:330`) but the journey is gated off every shipped surface (`ui/src/featureFlags.js:57`) and zero user vaults have ever been deployed — the owner's #1469 scrub removed exactly this sentence from Landing, `/security`, and `ui/index.html` and did not reach the README. |
| "How many strategies in the curated library currently pass is **unestablished** — this file will never quote a count" | `TRUE` | `README.md:21`. Matches `CLAUDE.md` § corollaries. No count appears anywhere in the file. |
| "The corpus is 10,000 arXiv preprints, not peer-reviewed papers. Candidate selection over it is a keyword filter ... no vector column" | `TRUE` | `README.md:148`. `/health` is the authority the line points at: `backend/archimedes/main.py:1197` publishes `corpus_papers`, `:1084` publishes `corpus_embedded_at_rest` from a schema probe, and the rerank cap is published beside it (`:1093`). |
| "The knowledge graph is not built ... `GET /api/corpus/graph` refuses with 503 `kb_artifact_not_found`" | `TRUE` | `backend/archimedes/api/corpus_routes.py:276` raises exactly that; `backend/archimedes/main.py:1110` derives `corpus_kg_built` from a live entity count rather than a constant. |
| "Arc has no mainnet yet; mainnet launch, real-funds custody, and the regulatory architecture are roadmap" | `TRUE` | `README.md:28`, true on 2026-08-31. **Date-gated:** #1241 records this as false from Sept 16. Re-check on that date; nothing in CI will tell you. |
| "Payments are real (USDC on Arc); settlement is stubbed pending mainnet" | `TRUE` | Two switches, and the line is right about both. Generation settles for real where deployed (`backend/archimedes/services/generation_payment.py:56`, `:72`); marketplace settlement rides the separate `PAYMENTS_DRY_RUN`, which defaults to dry-run (`backend/archimedes/services/generation_payment.py:74`). |
| "Not every reasoning trace is anchored on-chain ... check `arc_tx_hash` before treating a trace as anchored" | `TRUE` | `README.md:168`. This is the general form the `ui/index.html` meta tags are missing. |
| CLI exit codes are a stable contract, published machine-readably — `0` passed, `1` gate ran and failed, `2` bad input or no session, `3` not implemented | `OVER-CLAIMED` | **The published table is missing a code the CLI actually exits with.** `cli/src/archimedes_cli/exits.py:33` defines `INCOMPLETE = 4` (added for #1481: the gate was reached but not every runnable leg could be evaluated), and `cli/src/archimedes_cli/cli.py:542` exits with it on the real `verify` path. `cli/src/archimedes_cli/manifest.py:19` publishes only `0`/`1`/`2`/`3`. So the machine-readable contract omits a value the tool emits, and `exits.py`'s own header — "someone will write `archimedes verify` into a CI job and branch on the result" — names exactly the reader this harms: an agent branching on the published table hits an undocumented `4`. The four *documented* codes are accurate, and the `AUTH`-reuses-`2` rationale is sound (`exits.py:25`); the defect is completeness, not correctness. |

## Agent surfaces — `ui/public/llms.txt`, `ui/public/.well-known/agent.json`

| Claim | Status | What backs it |
|---|---|---|
| "executed in non-custodial USDC vaults on Arc testnet" (llms.txt header) | `OVER-CLAIMED` | `ui/public/llms.txt:5`. Same defect as the README row: the shipped product does not execute, and #1469 scrubbed this exact framing from the human surfaces without reaching the machine ones. |
| `agent.json` `description` — "executed in a non-custodial USDC vault on the Arc testnet" | `OVER-CLAIMED` | `ui/public/.well-known/agent.json`. Same sentence, same gap. |
| `agent.json` `skills[deploy-vault].status: "live"` | `OVER-CLAIMED` | `ui/public/.well-known/agent.json`. The route exists (`backend/archimedes/api/vaults_routes.py:330`), so "live" is route-truthful; the *skill* description ("Execute a generated, rigor-passing strategy into a non-custodial USDC vault") reads as a shipped capability while the surface is hidden (`ui/src/featureFlags.js:57`) and no vault has ever been created. |
| `agent.json` `endpoints.paper.note` — "deploy is also live and puts real capital on-chain" | `OVER-CLAIMED` | `ui/public/.well-known/agent.json`. Testnet faucet USDC is not real capital — `README.md:27` says so in as many words ("No real money is at risk, by design") — and the sentence is aimed at an agent choosing between two paths. |
| `agent.json` `endpoints.generate.note` — quote is public and authoritative; prod answers `payment_required: true`, `dry_run: false`, `$2.000000`; the code defaults are the opposite | `TRUE` | `backend/archimedes/services/generation_payment.py:56` reads `GENERATION_PAYMENT_REQUIRED` and defaults off; `:72` defaults dry-run on. `backend/archimedes/api/generate_routes.py:449` is the 402 that carries the x402 requirements. |
| "x402-gated strategy access ... no endpoint returns 402 with payment requirements" (the 2026-08-10 retraction) | `CHANGED` | The retraction is itself out of date. `POST /api/generate/start` now returns 402 carrying x402 requirements (`backend/archimedes/api/generate_routes.py:449`), and 409 `wallet_link_required` first (`:459`). What remains true is the marketplace half: `GET /api/marketplace/published/{strategy_id}` is public (`backend/archimedes/api/marketplace_routes.py:753`). |
| `agent.json` `erc8004` — "NO ERC-8004 identity, reputation, or validation claim is made" | `TRUE` | `ui/public/.well-known/agent.json` — self-disclosing, with `agentId` and `tokenURI` null, `status: "registration_pending"`, and the reason spelled out in the `note`. No `register()` transaction has been sent. |
| `agent.json` `endpoints.marketplace.note` — "does not assert that a subscription's recurring USDC charge settles for real" | `TRUE` | `ui/public/.well-known/agent.json`. Correctly separates the two dry-run switches — `backend/archimedes/services/generation_payment.py:64` documents the split that let the generation rail go live without un-drying marketplace settlement — and declines to claim the one no public endpoint publishes. |
| `agent.json` / llms.txt `POST /api/rigor/verify` — PBO and look-ahead "always report `not_evaluable`", `passes` is a capped quorum | `TRUE` | `backend/archimedes/api/rigor_verify_routes.py:47` states the capping contract; `:273` hard-codes both legs to `not_evaluable`; `:289` makes `passes` require every runnable leg to have run and passed. |
| `ui/public/sitemap.xml` lists only routes that render real content for an anonymous visitor | `TRUE` | Six `<loc>` entries, checked one by one against the two allow-sets that between them cover all six: `ui/src/routes.js:3` (`PUBLIC_PATHS` — `/`, `/architecture`, `/security`) and `:43` (`ANON_APP_PAGES` — `explore`, `leaderboard`, `corpus`). **Verified by reading, not by a guard, and the row says so:** `ui/scripts/check-sitemap.mjs` enforces only the *forward* direction (every public route appears in the sitemap) and its own header documents the reverse — "every sitemap `<loc>` is actually anonymous-accessible" — as **NOT enforced**; `ui/test/sitemap.test.js` pins one specific exclusion (the admin-only `/insights` never appears anywhere in the served bytes), not this property. Claiming a test pins this would be the defect this ledger exists to catch. |

## `ui/index.html` — meta, OpenGraph, Twitter card, JSON-LD

| Claim | Status | What backs it |
|---|---|---|
| Vault / non-custodial framing in all four cards | `RETRACTED` | #1469 rewrote the meta description, OG card, Twitter card and JSON-LD together, on the reasoning that a claim retracted on the page but left in the share card is still shipped. Guarded by `ui/test/roadmap-copy.test.js`. |
| "records the whole decision on Arc public testnet" (meta description) / "check the reasoning trace on-chain" (Twitter) / "on-chain reasoning provenance" (JSON-LD) | `OVER-CLAIMED` | `ui/index.html:9`, `:60`, `:95`. All three describe the *generation* journey the same tags introduce, and that journey writes nothing on-chain — see the Landing "Inspect" row. `README.md:168` has the accurate wording; these three tags do not. |
| "A research-grounded strategy-generation instrument with visible selection-bias checks" | `TRUE` | `ui/index.html:95`. The selection-bias checks are visible and live — `backend/archimedes/services/rigor_evaluator.py:486` computes the board-level correction and `backend/archimedes/services/live_rigor_gate.py:151` the per-strategy verdict. |

## `ui/src/components/Architecture.jsx`

| Claim | Status | What backs it |
|---|---|---|
| "with every decision hashed on-chain before anything acts on it" | `CHANGED` | Now only in the `ROADMAP_SURFACES_ENABLED` branch of the hero ternary (`ui/src/components/Architecture.jsx:86`), and that flag defaults false (`ui/src/featureFlags.js:28`), so the shipped build does not say it. |
| "Non-custodial by contract, not by promise" (rendered unconditionally) | `RETRACTED` | Replaced by `OnChainExecutionRoadmap` — "Non-custodial vault execution is on the roadmap; not yet live" (`ui/src/components/Architecture.jsx:599`, `:615`). The "Live user vaults on Arc" hero tile was removed rather than left reporting a live zero. |
| "x402-gated strategy access" / the nanopayment marketplace section | `CHANGED` | The section still exists but is flag-gated off the shipped build (`ui/src/components/Architecture.jsx:1232`), and its own honesty note says fee settlement is in dry-run (`:830`). |
| Honesty-ledger rows read from `/health` rather than being asserted | `TRUE` | `ui/src/components/Architecture.jsx:1085` renders "live value unavailable" on a health error instead of substituting a value; the corpus panel's contract is documented at `:838`. |

## `docs/user-stories.md`

| Claim | Status | What backs it |
|---|---|---|
| "Vault execution reads as present tense throughout this doc ... it is roadmap, not shipped product" | `CHANGED` | Banner added 2026-08-31 (`docs/user-stories.md:16`). It labels the problem rather than fixing it: `:30` still reads "allocating it into your non-custodial vault on Arc" and `:105` still reads "③ EXECUTE". The banner is the honest interim; the body is the residual. |
| "10,000 q-fin research papers" | `TRUE` | Same backing as the README corpus row — `backend/archimedes/main.py:1197`. |
| "Arc has **no mainnet** — it's testnet-only" | `TRUE` | `docs/user-stories.md:35`, true on 2026-08-31, and date-gated the same way as the README row. |

## `docs/agent-quickstart.md`

| Claim | Status | What backs it |
|---|---|---|
| "This page is narrower: nothing below creates a vault or puts capital on-chain" | `TRUE` | `docs/agent-quickstart.md:16`. The eleven steps end at `POST /api/paper/deployments`; the vault route is named only as a pointer to `agent-api.md`. |
| "It is not free ... step 6 charges $2.00 testnet USDC per run and settles for real ... the code defaults are the opposite" | `TRUE` | `docs/agent-quickstart.md:18`, backed by `backend/archimedes/services/generation_payment.py:56` and `:72`, and by the instruction to read `GET /api/generate/quote` against the host you are actually calling. |
| Every route and worked `curl` on the page resolves | `TRUE` | Guarded, not asserted: `backend/tests/test_agent_quickstart_drift.py` parses the page and fails on a route the app does not serve or a curl that drifts from the prose. |

## Passport, leaderboard, and the "Verified" badge

| Claim | Status | What backs it |
|---|---|---|
| "Archimedes Verified" cannot be earned by an imported return series | `CHANGED` | The 2026-08-20 reading — "no CSV/return-import endpoint exists, so the claim is vacuously true" — no longer holds: `POST /api/rigor/verify` accepts a bare returns series today. The claim survives on a stronger footing, by structure rather than by absence: two of the four legs are permanently `not_evaluable` on that transport (`backend/archimedes/api/rigor_verify_routes.py:273`), the verdict is explicitly `verdict_capped` and "not the strategy passport's gate" (`:47`), and the endpoint persists no strategy. |
| Leaderboard figures are provisional | `CHANGED` | The broad two-defect banner is retired: the #1203 routing defect and the backtest/live interpreter divergence were both fixed and re-verified, so their clauses became false and were removed (`ui/src/components/Leaderboard.jsx:446`). One caveat remains, scoped to the own view: generated-strategy figures are fixed at generation time and are not re-backtested (`:477`). |
| No public surface quotes a curated pass count | `TRUE` | Verified across Landing, `/security`, `ui/index.html`, `README.md`, `llms.txt`, and `agent.json` on 2026-08-31. |

## Market data — the Explore page and what paid analysis runs on

The owner's framing, recorded here because the ledger is where the public position lives.
The decision record is `docs/adr/market-data-sourcing.md`, added by the now-merged
[#1218](https://github.com/a-apin/archimedes/issues/1218) work — open as
[PR #1627](https://github.com/a-apin/archimedes/pull/1627), not merged as of 2026-08-31.
The rows below are marked `PENDING ADR MERGE` until it lands, and they should be re-pointed
at the ADR then; the guard in `backend/tests/test_claims_ledger.py` fails the moment the
file appears, so that re-pointing cannot be forgotten. Checked against that PR's diff:
it keeps `yfinance` as the default on both seams and adds Tiingo as the paid-analysis
provider, which is what the rows below say.

| Claim | Status | What backs it |
|---|---|---|
| The Explore page is free, open, and ungated | `TRUE` | `ui/src/routes.js:43` puts `explore` in `ANON_APP_PAGES`, so it renders with no session; `backend/archimedes/api/explore_routes.py:24` and `:30` carry no auth dependency. The code is public domain (`LICENSE`). |
| Explore is a FOSS viewer over yfinance streams, not a redistribution product | `TRUE` | The mechanism is true and labelled on the page — `ui/src/components/Explore.jsx:416` tells the visitor which cards are oracle-priced and which come from yfinance, and `ui/src/components/AssetModal.jsx:22` labels the source per asset. The licensing position is stated in `docs/adr/market-data-sourcing.md` (landed with #1627): split sourcing, no commercial redistribution of yfinance data, Tiingo Business named as the mainnet prerequisite. |
| Paid analysis runs on licensed data | `PENDING ADR MERGE` | A statement of policy, not of current state, and the ledger must not launder one into the other. The vendor seam exists on both sides — `analytics-engine/src/archimedes_analytics_engine/market_data.py:96` and `backend/archimedes/services/market_data_provider.py:358` (a real Tiingo provider) — and one `MARKET_DATA_PROVIDER` value selects across both. **The default on both seams is still `yfinance`**, so today the paid path and the free path read the same source. |
| yfinance is an unlicensed commercial dependency on the critical path | `TRUE` | #1218's own finding, unchanged: `analytics-engine/src/archimedes_analytics_engine/market_data.py:32` imports it and `:96` makes it the default, and every strategy pulls its declared universe through it. |

---

## Defects this audit found

Three, all left for a separate change rather than smuggled into a docs PR. The first is the
one worth acting on: it is a live machine-readable contract that under-publishes itself.

0. **`cli/src/archimedes_cli/manifest.py:19` omits exit code `4`.** `exits.py:33` defines
   `INCOMPLETE = 4` and `cli.py:542` exits with it, but the published `EXIT_CODES` table
   stops at `3`. The CLI's whole stated reason for pinning exit codes is that a CI job will
   branch on them, so the one surface a script actually reads is the one that is
   incomplete. Adding the row to `EXIT_CODES` is a two-line fix; it is a `cli/` change, not
   a docs change, so it is not in this PR. `TestPublishedExitCodesStillOmitIncomplete`
   fails the moment it is fixed, which forces this row to move with it.

1. **`ui/src/components/Landing.jsx:78`** — the comment block above `BOARD_FDR` says the
   figure is "served publicly — `GET /api/selection-bias/gate` returns `board_level_fdr`
   ... (`selection_bias_routes.py:535-552`)". That endpoint carries no such key any more:
   #1564 (merged as #1580, commit `131947d7`) moved board-level FDR onto
   `GET /api/leaderboard`, and `backend/archimedes/api/selection_bias_routes.py:105`
   records the move. `test_selection_bias_routes.TestBoardFdrStaysOffThePerStrategyGate`
   fails if the key reappears — so the comment now points a reader at an endpoint that is
   guaranteed *not* to have it. The user-visible copy is unaffected and stays `TRUE`.
2. **The machine surfaces lag the human ones.** `README.md:13`, `ui/public/llms.txt:5`,
   and three `agent.json` fields still carry the vault-execution framing that #1469
   removed from Landing, `/security`, and `ui/index.html`. `ui/test/roadmap-copy.test.js`
   guards the three scrubbed files and only those, so nothing catches the drift.

## Not covered here

- The pitch deck and any grant application text. Those live in the private `docs` repo by
  policy (`CLAUDE.md` § Project); this file covers the surfaces in this repo.
- `/health` field-by-field. It is a live endpoint and the ledger would rot; the rows above
  cite the code that computes each field instead.
- Contract-level claims beyond `ReasoningTraceRegistry`. `contracts/` has its own tests.

<!-- claims-ledger:pending-paths
     Paths cited above that do not exist in this tree yet. The guard asserts each one is
     STILL absent, so the exemption retires itself: when the file lands, the guard goes
     red and the row above must be re-pointed at real evidence rather than a promise.
-->

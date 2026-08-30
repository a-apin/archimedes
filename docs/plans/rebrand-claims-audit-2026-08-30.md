# Rebrand Claims Audit — Landing / Security / PublicLayout

**Working artifact for PR #1469 (v8 Lane 1.1 input).** Read-only claims extraction —
no source edits in this pass. Owner: Dan, who carries the findings into the PR #1469
conversation. Indexed in [`docs/README.md`](../README.md) beside the other #1469 plan
docs — a doc not in the index does not exist, and that holds for working artifacts too.

**Scope:** `ui/src/components/Landing.jsx`, `ui/src/components/Security.jsx`,
`ui/src/components/PublicLayout.jsx` on `origin/feat/calm-precision-product-rebrand`
(commit `ee8a4a42`, "Reconcile the rebrand with current main"). `roadmapCopy.js` is
included where `Landing.jsx` renders it conditionally, since it's a literal string the
component ships.

**Method:** every visitor-testable sentence extracted in file order, then checked
against (a) the verified facts below and (b) a targeted repo grep/read for each
Security.jsx control claim specifically (cited inline). Verification commands are
noted so a reader can re-run them cheaply, per this repo's audit-hygiene convention.

**Baseline facts used to grade every row (2026-08-30 session, independently verified,
not taken from any doc):**

- Corpus = 10,000 papers/abstracts. Retrieval is **lexical + query-time MiniLM rerank**.
  No stored embeddings, no knowledge graph (`corpus_meta`=0, KG=0/0).
- Curated-library pass counts are **UNESTABLISHED** — never quote one. Exactly 3 pass
  the live gate today, and even that number is disputed as an "established" claim.
- Capacity multiplier, if quoted anywhere, is **~67x, not 400x**.
- Payments are real **testnet** USDC, $2 flat per generation.
- Vaults are non-custodial; the DCW fee-collection path is custodial-**interim**.
- Execute and Learnings are flag-off in production (`ROADMAP_SURFACES_ENABLED`, default
  `false` — gates vaults, Marketplace, Publish, Subscriptions, Learnings per
  `ui/src/featureFlags.js`).
- Paper trading works but is barely reachable.
- Commit-reveal anchors a hash of a **structured** decision record — **this bullet
  revises the 2026-08-30 session fact sheet**, which says the anchor covers "one
  free-text `reasoning` field" plus an Arc tx reference. Reading the model shows that
  is wrong: `backend/archimedes/models/trace.py:77-91` pins a canonical 13-field hash
  order — `id`, `vault_address`, `decision_type`, `trigger`, `timestamp`,
  `market_context`, `portfolio_before`, `portfolio_after`, `reasoning`, `confidence`,
  `trades_executed`, `strategies_referenced`, `consulted_paper_hashes` — and
  `canonical_json()` serializes exactly those into the hashed bytes. What the fact
  sheet gets right is the *exclusion*: the debate transcript is not in `_HASH_FIELDS`
  and is not part of the trace. The real residual is reachability, not richness —
  see rows 11 and 22.
- The rigor gate (DSR/PBO/OOS/look-ahead) is real and runs on the live path,
  **but** `look_ahead_safe` on LLM-generated strategies is **self-declared by the
  generating LLM**, gated as pass/fail — not an independent static/AST audit of the
  code. `backend/archimedes/services/fusion_evaluator.py:126-130` says this in the
  code's own comments: *"Honest user-facing label for the look-ahead check ...
  self-attested look_ahead_safe flag was True, NOT ... an independent AST audit."*
- SPF/DMARC are now live (not relevant to these three files — no email claims found).

**Headline finding, ahead of the full table:** the copy is more disciplined than the
baseline facts above would suggest — no capacity multiplier, no curated pass count, no
embeddings/semantic-search claim appears anywhere in these three files. The only
**OVERSTATED** grade left is the leak leg (rows 7 and 21), where "rejects strategy code
that reads data before it existed" describes an independent audit the gate does not
perform. The two reasoning-trace rows (11, 22) survived a first pass as OVERSTATED and
were regraded **CAUTION** after reading the model: the trace payload really is
structured, and the residual is that a prod visitor cannot reach one. Everything else
is accurate, already hedged, or gated behind a flag that's off in production today.

**Truth-assessment key:** `PASS` (verified true, cited) · `PASS (roadmap-gated)` (true,
but only rendered when `VITE_ROADMAP_SURFACES=true`, which is off in prod) · `CAUTION`
(true but could be misread without more context) · `OVERSTATED` (implies more than the
system does today) · `UNBACKED` (Security.jsx-specific: no repo control found) ·
`DYNAMIC` (fetched live from `/api/config/contracts`, not a static string — correctness
depends on the live payload, already fail-soft per the figcaption's own error states).

| # | file:line | Claim (verbatim or closely paraphrased list) | Truth assessment | Suggested fix |
|---|---|---|---|---|
| 1 | Landing.jsx:136 | "One cited candidate. Four ways to reject it." | PASS — 4 rejection checks (DSR/PBO/OOS/LEAK) are real; each generated strategy carries cited paper(s) from the corpus. | None. |
| 2 | Landing.jsx:144 | Default hero lede: "Archimedes turns a plain-language brief into a paper-grounded strategy, then tests it for selection bias against a rigor gate it must pass before anything runs live." | PASS — matches the live path; "before anything runs live" is conditional phrasing, not a claim that Execute is live today. | None. |
| 3 | roadmapCopy.js:42-45 (rendered via Landing.jsx:143 only when `VITE_ROADMAP_SURFACES=true`) | "…then runs accepted methods in a non-custodial vault on Arc." | PASS (roadmap-gated) — Execute/vaults are flag-off in prod, so this string never reaches a default visitor. Confirm it stays gated. | None while the flag stays off. If the flag ever flips on before Execute actually runs strategies unattended, revisit. |
| 4 | Landing.jsx:24 | DSR method: "Corrects for multiple testing and non-normal returns." | PASS — matches `rigor_evaluator.py`'s DSR implementation. | None. |
| 5 | Landing.jsx:30 | PBO method: "Compares many train and test splits, not one lucky cut." | PASS — CPCV is implemented (`rigor_evaluator.py` line ~1158 references Combinatorial Purged CV). | None. |
| 6 | Landing.jsx:36 | OOS method: "Tested on a 30% chronological held-out window it never trained on." | PASS — verified 70/30 chronological holdout, `rigor_verify_routes.py:21,184,201`. | None. |
| 7 | Landing.jsx:42 | LEAK method: "Rejects strategy code that reads data before it existed." | **OVERSTATED** — phrasing implies an active code-level audit that detects leakage. For LLM-generated strategies the flag is self-declared by the generating LLM (`fusion_evaluator.py:126-130`); the gate enforces pass/fail on that self-attestation, it does not itself read the code to find the leak. | Reword to something the code actually does, e.g. "Requires a strategy to declare — and the gate to enforce — that no future data reached a decision," or footnote that this leg is self-attested for LLM-generated candidates today. This is the same "checks presence, not independent value" pattern CLAUDE.md calls out as a repeat defect class. |
| 8 | Landing.jsx:51-54 | Debate step: "Candidate methods are challenged, ranked, and kept beside the alternatives they beat." | PASS — debate society is confirmed the sole live generation pipeline (`docs/adr/debate-society-sole-generation-pipeline.md`, Accepted); rejected alternatives are exposed via `proposals_routes.py` / `debate_engine.py`, not just used internally for the DSR trial count. | None. |
| 9 | Landing.jsx:56-58 | Gate step: "...before sizing diagnostics expose its tradeoffs." | PASS — `strategy_sizer.py` is a real module; sizing diagnostics exist. | None. |
| 10 | Landing.jsx:60-62 | Authorize step (roadmap-only, index 3, excluded by default `slice(0,3)`): "You review the passport and sign any vault action with your linked wallet." | PASS (roadmap-gated) — accurate to the ADR'd non-custodial model; not shown by default since Execute/vault-authorize is out of MVP scope. | None. |
| 11 | Landing.jsx:64-66 | Inspect step (roadmap-only): "Reasoning traces bind context, papers, actions, and an Arc transaction reference into one record." | **CAUTION** (regraded from OVERSTATED), same residual as #22 — the sentence is accurate about the payload, clause by clause: `trace.py:77-91` hashes `market_context` (context), `consulted_paper_hashes` (papers), `trades_executed` / `strategies_referenced` / `portfolio_after` (actions), and `arc_tx_hash` carries the transaction reference; `agent_runner.py:1465-1496` populates every one of them. The residual is **reachability, not structure**: the only producer a user can reach is the vault-rebalance path (`agent_runner.py` `_commit_trace` / `_publish_trace`), and vaults are flag-off in prod, so there is no trace to inspect today. "Context" also does not include the debate transcript — that is not a hashed field. | None. Ships with Execute, and the payload it describes already exists; the thing that has to land first is a reachable trace, not better copy. |
| 12 | Landing.jsx:71-74 | FAQ: "Does a passed rigor gate guarantee returns? No. The gate reduces known sources of false confidence. It cannot remove market risk or guarantee future performance." | PASS — appropriately hedged. | None. |
| 13 | Landing.jsx:76-79 | FAQ: "What happens when a strategy fails? ... does not receive the verified badge and cannot bypass the server-side deployment gate." | PASS — consistent with the live rigor gate / `rigor-gate-unification.md` ADR (server-side, not a client-trusted boolean). | None. |
| 14 | Landing.jsx:81-84 | FAQ: "Do I need a wallet to explore Archimedes? No. ... Link a wallet only when you need proof of on-chain control or want to authorize a vault action." | PASS — matches Better-Auth-first identity model (`docs/security/auth-model.md`). | None. |
| 15 | Landing.jsx:86-90 | FAQ (roadmap-only): "Can Archimedes withdraw from my vault? No. The agent receives rebalance authority within contract rules. Vault ownership and withdrawals stay with the user wallet." | PASS (roadmap-gated) — backed by `Vault.sol`'s `onlyManager`/`onlyOwner` split (agent can't call owner-only oracle/withdraw paths). | None. |
| 16 | Landing.jsx:92-95 | FAQ: "Is this running with real money? No. Archimedes currently runs on Arc public testnet with faucet USDC. It is a research prototype, not a production investment product." | PASS — matches verified facts exactly. This is the strongest sentence on the page; don't weaken it in future edits. | None. |
| 17 | Landing.jsx:184-187 | "Trying enough ideas can manufacture a winner. Archimedes records the search, tests the selected method, and shows weak evidence instead of hiding it." | PASS — consistent with `num-trials-self-containment.md` ADR and the debate society exposing alternatives. | None. |
| 18 | Landing.jsx:189 | "Named papers stay attached." | PASS — lexical retrieval attaches cited papers to a brief; true regardless of the no-embeddings fact (retrieval mechanism isn't claimed here). | None. |
| 19 | Landing.jsx:190 | "Failed values remain visible." | PASS — matches the 4-state gate (`pass`/`fail`/`pending`/`degenerate`) described in `docs/architectural-principles.md`. | None. |
| 20 | Landing.jsx:191 | "Wallet authority stays separate." | PASS — matches the Better-Auth-vs-wallet separation. | None. |
| 21 | Landing.jsx:200-203 | "Four independent checks look for luck, overfitting, weak out-of-sample behavior, and leaked future data." / "Any failed check keeps the candidate unverified." | PASS, with the same caveat as row 7 on the leak leg's self-attestation. | Same fix as row 7 — this is the general statement of the same claim. |
| 22 | Landing.jsx:291-297 | **Default-visible** "is-audit" card: "Audit what the agent saw, cited, decided, and recorded." / "Context and transaction evidence stay in one reviewable trail." | **CAUTION** (regraded from OVERSTATED) — the trace is genuinely structured, so each verb maps to a real hashed field: `trace.py:77-91` fixes the canonical hash order over `market_context` (saw), `consulted_paper_hashes` (cited), `trades_executed` / `strategies_referenced` / `portfolio_after` (decided), and `reasoning` + `arc_tx_hash` (recorded); `agent_runner.py:1465-1496` populates all of them on the live rebalance path. The residual is **reachability, not richness**: the trace pipeline runs only on the vault-rebalance path, vaults are flag-off in prod (`portfolio` / `vault-detail` in `ROADMAP_PAGES`, `ui/src/featureFlags.js`), so a visitor who takes this card at its word and goes looking has nothing to open. And the debate transcript — the part of "what the agent saw" a reader is most likely to expect — is not a hashed field and is not part of the trace. | Not a wording fix; the wording matches the payload. Either gate this card with the rest of Execute until a trace is reachable, or keep it and accept that its subject is real code a prod visitor cannot yet reach. Either way, don't let "saw" be read as "the debate transcript" — that is the one part of the sentence the trace does not carry. |
| 23 | Landing.jsx:286-289 | "is-research" card: "Run quant research without building a quant desk." / "Start in plain language. Inspect papers, backtests, and gates." | PASS. | None. |
| 24 | Landing.jsx:277-282 | "is-custody" card (roadmap-only): "Test idle USDC without surrendering withdrawal authority." / "Arc public testnet keeps the experiment honest and reversible." | PASS (roadmap-gated). | None. |
| 25 | Landing.jsx:310-329 | Tech-stack list: Arc "public testnet settlement" · Circle "native testnet USDC and wallet tooling" · AWS Bedrock "strategy reasoning" · Foundry "contract testing and deployment" · FastAPI + React "agent API and interface." | PASS on all five — matches `CLAUDE.md`'s stack section (`bedrock_converse`/`amazon.nova-micro-v1:0`, Foundry via `contracts-test.yml`, FastAPI+React confirmed in tree). | None. |
| 26 | Landing.jsx:224-227 | Capabilities intro (default): "Every surface answers one question: what was requested, what was rejected, and what survived the rigor gate." | PASS. | None. |
| 27 | Landing.jsx:268-271 | Use-cases intro (default): "Archimedes fits research decisions where sources, rejected candidates, and measured limits must stay visible." | PASS. | None. |
| 28 | Landing.jsx:509-513 | AuthorityBoundary (roadmap-only, whole section gated by `ROADMAP_SURFACES_ENABLED`): "Agent cannot withdraw." / "Autonomy stops at ownership..." | PASS (roadmap-gated) — backed by `Vault.sol` role separation. | None. |
| 29 | Landing.jsx:519-522 | "Agent may": read market conditions/evidence; propose allocations and rebalance within vault rules; commit its reasoning before an enforced trade. | PASS (roadmap-gated) — matches `Vault.sol` `rebalance()`'s commit-before-trade enforcement (issue #589) and `onlyManager` scoping. | None. |
| 30 | Landing.jsx:529-533 | "Only you may": authorize deposits with your wallet; retain withdrawal authority; choose whether a validated strategy receives capital. | PASS (roadmap-gated) — matches ERC-4626 owner-only withdraw/redeem and `onlyOwner` oracle-wiring split. | None. |
| 31 | Landing.jsx:537-539 | "Ownership invariant: You retain vault ownership. Withdrawals stay with your wallet." | PASS (roadmap-gated). | None. |
| 32 | Landing.jsx:370-372 | Final CTA: "Arc public testnet only. Past performance is not a promise. A rigor gate can reject weak evidence; it cannot remove market risk." | PASS. | None. |
| 33 | Landing.jsx:442-446 | EvidenceLedger: "Cited methods / Source papers remain attached." + "Four rejection checks / Failures remain part of the record." | PASS. | None. |
| 34 | Landing.jsx:449-460 | EvidenceLedger 3rd bullet, gated: "You hold authority / Wallet proof appears only for on-chain control." vs. default: "Verdict stays visible / Measured failures remain part of the record." | PASS on both variants. | None. |
| 35 | Landing.jsx:463-473 | Evidence links: "System architecture," "Source code" (GitHub), "Agent API entry point" (`/llms.txt`). | PASS — `ui/public/llms.txt` and the GitHub repo both exist; verified with `find . -iname llms.txt`. | None. |
| 36 | Landing.jsx:493-495 | RigorMatrix rule: "Measured values stay in the record, pass or fail." | PASS. | None. |
| 37 | Landing.jsx:407-427 | ProductWorkspace figcaption: "Live census unavailable" / "Reading Arc contract census" / "Arc census live · ≥N reported instances · X/6 core · Y synths · Z pools." | `DYNAMIC` — fetched live from `GET /api/config/contracts`; the component already fails loud (no cached-count substitution) rather than fabricating a number if the API errors. This is the fail-soft pattern done correctly. | None — this is a model for how the leaderboard/marketplace failures elsewhere in the repo should have behaved. |
| 38 | Landing.jsx:393-398 | Workspace bar: "Brief → gate → authority" (roadmap) / "Brief → debate → gate" (default). | PASS on both. | None. |
| 39 | Landing.jsx:556 | Footer brand: "Research-grounded strategy generation on Arc public testnet." | PASS. | None. |
| 40 | Landing.jsx:566-575 | Footer resource links: `/llms.txt` "Agent API," `/.well-known/agent.json` "Agent manifest," GitHub. | PASS — both files exist (`ui/public/llms.txt`, `ui/public/.well-known/agent.json`); a backend route (`agent_manifest_routes.py`) also serves the manifest. | None. |
| 41 | Landing.jsx:579-589 | Footer project links: "Unlicense" → `LICENSE`, "Arc faucet" → faucet.circle.com, "No privacy or terms page published." | PASS — `LICENSE` file is the actual Unlicense text; `ui/src/routes.js` has no `/privacy` or `/terms` route, so the self-disclosure is accurate. | None. |
| 42 | Landing.jsx:593-594 | Footer base: "Research prototype. No real funds." / "Past performance does not guarantee future results." | PASS. | None. |
| 43 | Security.jsx:9-15 | Hero: "Security is enforced boundaries, not a guarantee." / "...These controls describe current code — not a promise that failure is impossible." | PASS — accurately hedged, and matches every specific control claim checked below. | None. |
| 44 | Security.jsx:18-29 | Status stats: Environment = Arc public testnet; Product status = Research prototype; Value at risk = No real funds. | PASS. | None. |
| 45 | Security.jsx:44-47 | "Signing in, linking a wallet, running an agent, and owning vault shares are separate capabilities." | PASS. | None. |
| 46 | Security.jsx:51-59 | "01 Better Auth account": "Canonical application identity. A connected wallet never creates or replaces the account session." | PASS — verbatim match to `docs/security/auth-model.md` § Canonical identity. | None. |
| 47 | Security.jsx:60-70 | "02 Proof-linked wallet": "A five-minute, single-use EIP-4361 challenge proves control before a wallet is linked. Wallet state is not an app credential." | PASS — `docs/security/auth-model.md` § Wallet proof: "...normalized ... nonce hash, issue time, and five-minute expiry. `POST /api/wallets/verify` atomically consumes challenge before link insertion, preventing replay." | None. |
| 48 | Security.jsx:71-81 | "03 Bounded agent": "...Its role cannot withdraw, redeem, transfer ownership, or install an arbitrary oracle." | PASS — `Vault.sol`: oracle-setting is `onlyOwner` **not** `onlyManager` specifically so "a compromised agent could [not] point a token at a self-serving oracle" (contract comment at the `onlyOwner`/oracle-setter, ~line 543-548); withdraw/redeem are owner/share-owner-gated by ERC-4626 semantics, not manager-gated. | None. |
| 49 | Security.jsx:82-91 | "04 Vault share owner": "...withdraws or redeems directly, or explicitly approves a spender under ERC-4626 allowance rules." | PASS — `contracts/src/interfaces/IVault.sol` is explicitly documented "An ERC-4626 tokenized vault." | None. |
| 50 | Security.jsx:104-107 | "Each control maps to an application, edge, database, or contract boundary in the current repository." | PASS, and demonstrably so — every control below traces to a specific file. | None. |
| 51 | Security.jsx:111-120 | "Session": "Production cookies are HttpOnly and Secure. nginx, the UI route guard, and FastAPI independently protect private surfaces." | PASS — `backend/archimedes/api/auth_siwe.py:371-372` sets `httponly=True, secure=True`; `nginx/nginx.conf` fronts the app; `ui/src/App.jsx:66-71` redirects unauthenticated users to `/sign-in` for gated routes (the "UI route guard"); FastAPI's own session dependency is the third layer. | None. |
| 52 | Security.jsx:122-131 | "Scope": "Profile, strategy, job, and linked-wallet reads resolve through the authenticated Better Auth user — not a client-supplied address." | PASS — matches `auth-model.md` § Canonical identity and the `_siwe_cookies` / user-scoping test helper pattern in `backend/tests/test_user_routes.py`. | None. |
| 53 | Security.jsx:133-141 | "Integrity": "Agent-only writes require a service credential. User sessions cannot forge internal reasoning traces, rebalance events, or other integrity-critical agent records." | PASS — `backend/archimedes/api/auth_guard.py`'s `require_internal_agent_key` (checks `X-Internal-Agent-Key` against `INTERNAL_AGENT_API_KEY`) gates `traces_routes.py`'s `publish_trace` endpoint. | None. |
| 54 | Security.jsx:143-153 | "Edge": "Same-origin rules, a hash-restricted script policy, HSTS, anti-framing headers, limited browser permissions, and separate read/write rate limits..." | PASS on every listed item — `nginx/nginx.conf`: CSP with `script-src 'self' 'sha256-...'` (hash-restricted, line 72); `Strict-Transport-Security` (line 209); `X-Frame-Options: DENY` (line 210, anti-framing); `Permissions-Policy: geolocation=(), microphone=(), camera=()` (line 213); separate `limit_req_zone` for `api_read` (60r/m) and `api_write` (20r/m) (lines 140-141). | None. |
| 55 | Security.jsx:155-164 | "Contract": "Rebalances require an earlier reasoning-trace commitment, bounded target movement, slippage checks, and owner-curated oracle paths." | PASS — `Vault.sol` `rebalance()`: commit-before-trade via `traceRegistry.executeTrade(tradeId)` (#589); "toward target, never overshoot" constraint (#915) for bounded movement; `_oracleMinOut` slippage floor; oracle-setter is `onlyOwner` against an allowlist (comment: "manager may wire oracles only from this allowlist, never an [arbitrary address]"). All four sub-claims independently verified in the same file. | None. |
| 56 | Security.jsx:182-185 | Known limit: "Testnet: Arc public testnet only. No real funds should be used." | PASS — accurate limitation. | None. |
| 57 | Security.jsx:186-189 | Known limit: "Agent risk: Agent may mis-rebalance within its constraints, but its role cannot withdraw user assets." | PASS. | None. |
| 58 | Security.jsx:190-193 | Known limit: "Demo inputs: Some current oracle and risk inputs are mock data and must not support live financial decisions." | PASS as a self-disclosed limitation — consistent with the "oracle strategy: chainlink + back-pocket" note that Chainlink Arc data feeds may not be fully live; this line is the honest counterpart to that gap and should stay as-is. | None — if anything, don't remove this line when Chainlink lands; narrow it instead. |
| 59 | Security.jsx:194-197 | Known limit: "Immutable contracts: A defect requires a new deployment and migration rather than an in-place upgrade." | PASS — matches the non-upgradeable contract pattern implied by the T3.2 redeploy history. | None. |
| 60 | Security.jsx:198-202 | Known limit: "No assurance: No independent security audit, production-readiness, regulatory, or return guarantee is claimed by this page." | PASS — and this is the load-bearing sentence that makes the rest of the page's confident tone acceptable; don't soften it in a future edit. | None. |
| 61 | Security.jsx:219-254 | Evidence links: "System architecture," `docs/security/auth-model.md`, `contracts/src/Vault.sol`, `docs/adr/non-custodial-vault-owner-agent.md`. | PASS — all four targets exist in the repo (`find . -iname auth-model.md`, `find . -iname non-custodial-vault-owner-agent.md`, `contracts/src/Vault.sol` both confirmed present). | None. |
| 62 | PublicLayout.jsx:20-24 | Announcement bar: "Research prototype" / "Arc public testnet" / "No real funds." | PASS — same content as Security.jsx row 44; consistent across pages. | None. |
| 63 | PublicLayout.jsx:29-32 | Brand tagline: "Archimedes" / "Research. Rigor. Custody." | **CAUTION** — "Custody" as a headline pillar word is ambiguous: the actual invariant is *non*-custodial vaults (user retains withdrawal authority), with only the fee-collection DCW path being custodial-interim. A visitor skimming the header could read "Custody" as "we hold your funds," the opposite of the product's real positioning, before ever reaching the FAQ/Security page that clarifies it. | Consider "Research. Rigor. Ownership." (matches the "Ownership invariant" language already used in the gated AuthorityBoundary section and in the Security.jsx role ledger) or "Research. Rigor. Non-custody." if brevity must be preserved. Low urgency since the header alone doesn't assert a fact, but it's the very first three words a visitor reads and it currently points the wrong direction. |

## Notes out of scope for this audit (worth a separate issue)

- **Row 63** (brand tagline word choice) and **rows 7 / 21** (the self-attested
  look-ahead flag described as something the gate "rejects" rather than "requires the
  strategy to declare") are the items worth actually fixing before #1469 ships. **Row
  22** is *not* a copy fix — its wording matches the trace payload; what it needs is a
  reachable trace, which arrives with Execute. Treat rows 11 and 22 as a note on the
  Execute rollout, not a blocker on this PR.
- This audit did not read `Architecture.jsx`, `roadmapCopyApp.js`, or any authenticated-app
  page — those are out of scope per the task but may carry the capacity-multiplier or
  curated-pass-count claims Dan flagged; worth the same pass if they feed into v8 Lane 1.1
  copy too.
- No embeddings/semantic-search claim, no curated-library pass count, and no capacity
  multiplier appear anywhere in the three files audited — confirmed via `grep -in
  "400\|67x\|embed\|semantic\|knowledge graph\|10,000\|10000"` against all three files
  (no matches). Good news, not a gap in this audit.

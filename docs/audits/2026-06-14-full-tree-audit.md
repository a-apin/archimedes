# Archimedes Full-Repo Audit — 2026-06-14

**Scope:** Entire codebase — backend (~235 Python modules), 11 Solidity contracts, the
React/viem UI, the `analytics-engine` package, `infra/` Terraform + CI/CD workflows, and
repo hygiene / docs drift.
**Method:** A 3-domain parallel auditor fan-out (quant rigor · backend-security +
on-chain/contracts · frontend + infra/CI-CD + hygiene), each domain followed by an
**adversarial verification pass** that re-read the cited source and tried to *refute*
every CRITICAL/HIGH finding. 10 agents total. The author then independently re-ran the
load-bearing verification commands for every surviving CRITICAL/HIGH item (house rule:
"verify your own audit claims before acting on them" — `CLAUDE.md`).
**Lineage:** Re-checks `AUDIT_2026-06-10.md` (29 findings) and `AUDIT_2026-06-13.md`. The
two prior CRITICALs in scope — Vault share-theft and `minAmountOut=0` rebalance slippage
— are **genuinely fixed in current source** (see "Verified genuinely fixed"). This pass
surfaces **35 findings**, the most serious of which are concentrated where the two
headline claims meet their live code paths: the **rigor gate** has a second, weaker copy
running on the path a user/judge actually exercises, and the **non-custodial /
on-chain-provenance** story is materially weaker than advertised because the backend agent
*is* the vault owner and the on-chain commit-reveal is never invoked.

---

## Headline

The repo is in strong shape and most prior fixes are real, not cosmetic. There are **no
surviving CRITICALs** — the one CRITICAL candidate (undeflated DSR on the persist path)
was downgraded to MEDIUM under verification because two independent guards stop it from
flipping a "verified" badge. But five HIGH findings are real and live:

1. **Two rigor gates disagree.** The canonical `run_rigor_gate` correctly deflates DSR
   and measures the OOS/IS cliff against the *in-sample slice*. The dataclass property
   `BacktestResult.passes_rigor_gate` (the one that drives the live "verified" badge) and
   the streaming-generation verdict each ship a *weaker* version that divides by the
   full-sample Sharpe / omits the cliff entirely. The stricter, correct gate exists one
   function over.
2. **"Non-custodial / rebalance-only" is weaker than stated.** Every vault is created by
   the backend agent signer, so `creator == owner == agent`. The `onlyOwner` guards added
   on 06-14 (precisely to stop a compromised agent from re-pointing an oracle) are moot
   because the agent *is* the owner.
3. **On-chain provenance is off-chain.** The `ReasoningTraceRegistry.commit()/reveal()`
   functions that would prove "trace existed before the trade" are never called; both
   phases use the legacy `publishTrace`, and the "Temporal Binding ✓ VERIFIED" badge in
   the UI is backed only by a Python boolean in Redis.
4. **A funds-adjacent secret leak is permanent in git history** (Circle entity-secret
   candidates), rotation unverifiable from the repo.

Everything else is MEDIUM/LOW/INFO hardening and drift.

---

## Severity rollup

| Severity | Count | IDs |
| --- | --- | --- |
| CRITICAL | 0 | — (Q1 downgraded under verification) |
| HIGH | 5 | Q2, Q3, B1, B2, I2 |
| MEDIUM | 13 | Q1, Q4, Q5, Q6, Q7, Q11, B3, B4, B5, I1, I3, I4, I5 |
| LOW | 8 | Q8, B6, B7, B8, B9, B10, I6, I7 |
| INFO | 9 | Q9, Q10, Q12, B11, B12, I8, I9, I10, I11 |

Domains: **Q** = quant/rigor · **B** = backend-security/chain/contracts · **I** =
frontend/infra/hygiene. "Verdict" on each finding is the adversarial verifier's result
(confirmed / adjusted / refuted); ★ = the author also re-ran the verify command directly.

---

## HIGH

### Q2 — `passes_rigor_gate` IS/OOS cliff divides by the full-sample Sharpe ★
**Verdict: confirmed (HIGH).** Files: `backend/archimedes/models/backtest.py:174`,
`backend/archimedes/services/portfolio_backtester.py:379,401`

`backtest.py:174`: `if self.sharpe_ratio > 0 and (self.out_of_sample_sharpe /
self.sharpe_ratio < 0.5): return False`. The denominator `self.sharpe_ratio` is the
**full-sample** Sharpe (computed over the entire series at `portfolio_backtester.py:379`),
while `out_of_sample_sharpe` is the last-30% slice. The cliff is meant to detect IS→OOS
degradation, but dividing the OOS Sharpe by a full-sample number that *already blends in
that same OOS tail* makes the 0.5 cliff trivially easy to clear. The sibling gates do it
right — `rigor_evaluator.py:204` and `fusion_evaluator.py:470` divide by the IS-slice
Sharpe, and `compute_in_sample_sharpe` (`_rigor_helpers.py:339`) exists for exactly this
but is unused here. This is the same mistake 06-10 #7 flagged, fixed everywhere except
this dataclass property — which is what `strategies_routes.py:102` reads for the live
"verified" badge.

**Impact:** A strategy grossly overfit in-sample (IS Sharpe 5.0 / OOS Sharpe +0.1) can
clear the cliff. Live-reachable on any `BacktestResult` row with `pbo_score` populated
(curated strategies + the library-level PBO refresh). Freshly-generated rows fail earlier
at PBO (see Q1/Q7), so the exploit window is the curated/refreshed set.
**Fix:** Compare `out_of_sample_sharpe` against the first-70% IS-slice Sharpe (or store an
explicit `in_sample_sharpe` field), matching `run_rigor_gate`.
**Verify:** `sed -n '142,177p' backend/archimedes/models/backtest.py`

### Q3 — Streaming-generation rigor verdict omits the IS/OOS cliff entirely ★
**Verdict: confirmed (HIGH).** Files: `backend/archimedes/agents/generation_pipeline.py:310`,
`backend/archimedes/services/rigor_evaluator.py:200`

`_rigor_verdict_for` computes `passing = dsr_p >= 0.95 and oos > 0.0 and lookahead_passed`
(plus a later PBO patch) and **never computes an in-sample Sharpe or enforces the OOS/IS ≥
0.5 cliff** that `RigorGateResult.passes_all` enforces. So the two generation surfaces
grade on different rules: any strategy with positive OOS and (correctly-deflated, at
`num_trials=len(strategies)`) `p ≥ 0.95` and clean look-ahead passes the streaming verdict
regardless of how far OOS degraded from IS. The verdict's `passing` is persisted into the
passport at `generation_pipeline.py:973`.

**Impact:** An in-sample-overfit strategy reads PASS on the Generate page and in the
persisted passport, while the canonical gate would reject it — directly weakening the
"rigor-gated" headline on a user-facing surface.
**Fix:** Route the streaming verdict through `run_rigor_gate`, or have `_rigor_verdict_for`
compute `compute_in_sample_sharpe` and enforce `oos/is >= 0.5`. One gate definition,
used everywhere.
**Verify:** `sed -n '304,318p' backend/archimedes/agents/generation_pipeline.py`

### B1 — Backend agent signer is the vault owner AND manager; `onlyOwner` protections are moot ★
**Verdict: confirmed (HIGH).** Files: `contracts/src/VaultFactory.sol:51-66`,
`contracts/src/Vault.sol:129,386-404`, `backend/archimedes/chain/executor.py:265-299`,
`backend/archimedes/scripts/bootstrap_vaults.py:381-389`

`VaultFactory.createVault` passes `msg.sender` as `_creator`; `Vault`'s constructor does
`Ownable(_creator)` (`Vault.sol:129`). In production every vault is created via the backend
Circle/agent wallet (`executor.py:280-299`; the agent loop also creates vaults), so
`creator == owner == agent`. `bootstrap_vaults.py:382-384` states this verbatim ("Since
the Circle wallet IS the creator, this is redundant"). The 06-14 hardening made
`setTokenOracles` `onlyOwner` *specifically* so "a compromised agent could [not] point a
token at a self-serving oracle, set minAmountOut ≈ 0, and route swaps that leak vault
value" (`Vault.sol:388-394`) — but because the agent IS the owner, that guard, plus
`setMaxSlippageBps`, `setPlatformFeeRecipient`, and `pause`, are all callable by the agent
key. There is **no `transferOwnership` to the depositing user** anywhere in the tree
(verified: only `SyntheticFactory.sol:57` has an unrelated one).

**Impact:** On a compromised agent key, the attacker re-points a token's oracle to an
attacker-controlled `PriceOracle`, then rebalances with an oracle-derived `minOut` computed
from the manipulated price — the exact economic-drain path the `onlyOwner` split was meant
to block. Pure custody still holds (the agent holds no shares; `withdraw`/`redeem` require
`msg.sender == owner_` of the shares), so this is *economic drain on key compromise*, not
free theft — hence HIGH, not CRITICAL.
**Fix:** Deploy the vault with the user's wallet as creator/owner and only
`setAgent(backendAgent)` (so `owner != agent`), or use a cold owner/governance key distinct
from the hot agent signer. At minimum, correct the `Vault.sol:388-394` comment to admit the
agent is the owner today.
**Verify:** `sed -n '381,389p' backend/archimedes/scripts/bootstrap_vaults.py && grep -rn 'transferOwnership' backend/archimedes contracts/src`

### B2 — On-chain commit-reveal is never invoked; "Temporal Binding ✓" is an off-chain boolean ★
**Verdict: confirmed (HIGH).** Files: `backend/archimedes/chain/agent_runner.py:758-760,846,890-892`,
`backend/archimedes/chain/trace_publisher.py:57-60`, `contracts/src/ReasoningTraceRegistry.sol:108-154`

The agent's main tick runs "Phase 1: COMMIT — anchor on-chain BEFORE trade"
(`agent_runner.py:526`) and "Phase 3: REVEAL" (`:630`) — but `_commit_trace` and
`_reveal_trace` both call `trace_publisher.publish(trace)`, which only ever calls
`publishTrace(address,bytes32,bytes)` (the v1 anchor-after-the-fact path with no
`claimedExecutionTime`, no time-lock, no hash-binding). A grep for backend calls to the
contract's `commit()`/`reveal()` returns **nothing** (verified). The real `commit()`/
`reveal()` (with `require(claimedExecutionTime > block.timestamp)` and
`require(keccak256(fullTraceContent) == c.contentHash)`) are dead from the live path. The
only "temporal binding" is `temporal_binding_valid = commit_block < trade_block` computed
in Python and stored in Redis — and the verifier found this is **surfaced to users as a
green "VERIFIED" badge** labelled "Trace committed before trade executed" at
`ui/src/components/Reasoning.jsx:349-364`.

**Impact:** Headline architectural claim #2 ("trace existed *before* the trade with proven
causal ordering, anchored on-chain") is not enforced on-chain. A chain verifier cannot
reproduce the ordering; a buggy/compromised backend can set the boolean to `true`
arbitrarily. `docs/anti-features.md:224-230` explicitly forbids claiming causation until
commit-reveal ships. Note: the *headline docs are honest* (`CLAUDE.md:808-811` calls this
"the v1.5 hop"; the rubric doc marks it "not live") — the one stale claim is
`AUDIT_2026-06-13.md:96` listing "commit-reveal" under "Verified genuinely fixed," and the
UI badge overstates what is proven.
**Fix:** Wire `_commit_trace`→`commit(...)` and `_reveal_trace`→`reveal(...)`, thread the
returned `traceId`, and call `setVaultAgent` first (see B3). Until wired, relabel the UI
badge and correct `AUDIT_2026-06-13.md:96`.
**Verify:** `grep -rn 'functions.commit(\|functions.reveal(' backend/ || echo 'none'`

### I2 — Live Circle entity-secret candidates remain in git history (`test-secrets.mjs` @ `7940bc5`) ★
**Verdict: confirmed (HIGH).** File: `test-secrets.mjs` (de-tracked; present at blob `7940bc5`)

`git rev-list --all --objects | grep test-secrets` returns blob
`7940bc5aba68375d27600c822cbb54e5517db6e2`; `git show` reveals
`API="https://api.circle.com/v1/w3s"`, `WALLET_ID="81d8797e-d004-5c74-a879-e410ed515aed"`,
and three 64-char hex entity-secret candidates (`0be28ce5…`, `721b87ff…`, `040a4a1a…`) —
the script RSA-encrypts each against Circle's entity public key, probes
`api.circle.com`, and writes the winner into `.env`. The file is correctly no longer
tracked (verified), but the values are permanent in `git log -p` and every clone.

**Impact:** Circle entity secrets are the load-bearing leg of the two-factor signing model
for the oracle/agent's managed wallet. Per the team's own rule, removal ≠ erasure — only
rotation in the Circle console neutralizes it. Flagged HIGH in 06-13 with "rotation still
required (OPEN)"; still unverifiable from the repo.
**Fix (outward, cannot be done in-repo):** Rotate the Circle entity secret + treat
`WALLET_ID` and all three candidates as compromised; confirm the deployed wallet was
regenerated after 2026-06-13.
**Verify:** `git rev-list --all --objects | grep -i test-secrets && git show 7940bc5… | grep -oE '[a-f0-9]{64}' | sort -u`

---

## MEDIUM

### Q1 — Live generated-strategy backtest deflates DSR with `num_trials=1` (zero correction) ★
**Verdict: adjusted CRITICAL→MEDIUM.** Files: `backend/archimedes/services/portfolio_backtester.py:323,400`,
`backend/archimedes/agents/generation_pipeline.py:1053`

`backtest_portfolio` defaults `num_trials_for_dsr: int = 1` and the live persist caller
omits the arg, so `compute_dsr` runs at N=1 where `E_max_N = 0.0` (no deflation). The
author numerically reproduced the divergence: a series passes at N=1 (p≈0.99) but fails at
the real library N=31 (p≈0.62). This is a genuine regression of a known fix — the sibling
streaming path at `generation_pipeline.py:625` correctly passes `num_trials=len(strategies)`.
**Why not CRITICAL:** two guards stop a false "verified" badge — `backtest_portfolio`
always sets `pbo_score=None` (`:451`), so `passes_rigor_gate` short-circuits to `False` at
`backtest.py:168` *before* the DSR check; and the `force_update` re-ingest path
(`passport_loader._update_record`) never copies `dsr_p_value`. Net live damage is a
**wrong (undeflated) `deflated_sharpe_ratio` number displayed on the passport**, not a
flipped gate.
**Fix:** `backtest_portfolio(..., num_trials_for_dsr=max(1, len(strategies)))`; consider
making the arg required so the unsafe default can't be silently inherited.

### Q4 — Fusion gate deflates against hardcoded `num_trials=10` and skips PBO when the LLM omits `parameter_variants`
**Verdict: not separately verified (finder confidence high).** Files:
`backend/archimedes/services/fusion_evaluator.py:387,423`, `backend/archimedes/api/strategies_routes.py:1344`

`apply_rigor_gate` defaults `num_trials=10` and only overrides it when `len(variants_metrics)
>= 2`; the live caller passes nothing, and `parameter_variants` is optional in the DSL, so a
spec without variants deflates against an arbitrary 10 **and** `pbo_score=None` passes
vacuously. The 06-13 "fusion num_trials fixed" claim only holds when a variant grid exists.
Bounded by `ARCHIMEDES_FUSION_ENABLED` (off by default).
**Fix:** Thread a real `num_trials` from the corpus/selection set; when no variants exist,
run an internal parameter grid for a real PBO or report DSR as MISSING rather than deflating
against a placeholder.

### Q5 — "Walk-forward out-of-sample" is a single 70/30 holdout on every live path
**Verdict: not separately verified (drift, finder confidence high).** Files:
`backend/archimedes/services/_rigor_helpers.py:290`, `analytics-engine/.../walk_forward.py:82`

The product names "walk-forward OOS Sharpe" as an admission primitive, but live gates use
`compute_oos_sharpe` — documented in its own docstring as "a single chronological hold-out,
NOT a rolling walk-forward… no per-window refit and no purge/embargo." The genuine rolling
`walk_forward_select` is called only from tests/scripts; the CPCV path always receives
`cv_returns_matrix=None`. For SMA-200/TSMOM-252 strategies the first ~200-252 OOS bars carry
in-sample-warmed signal state. Docstrings are honest, so this is documented drift.
**Fix:** Wire `walk_forward_select`/CPCV into `run_rigor_gate`, or relabel the live primitive
as "single chronological hold-out" in user copy + spec.

### Q6 — Fusion look-ahead primitive is a hardcoded `True` (LLM self-attestation)
**Verdict: not separately verified (finder confidence high).** Files:
`backend/archimedes/services/fusion_evaluator.py:446`, `portfolio_backtester.py:406`

`look_ahead_clean = True` ("guaranteed by the DSL design"); the DSL only rejects specs whose
self-declared `look_ahead_safe` boolean is `False`, and that flag is set by the LLM. The
persist path also hardcodes `look_ahead_passed = True` with an honest comment that the
generated weight matrix is not audited. The real AST audit runs only against cited curated
source, not fusion/DSL output. The closed-enum DSL bounds the real risk.
**Fix:** Run `look_ahead_audit` on the compiled DSL→backtrader output, or surface look-ahead
as "N/A (closed-DSL, not source-audited)" instead of PASS.

### Q7 — Generated passports persist `pbo_score=None` (gate always False) but still surface undeflated DSR / wrong cliff
**Verdict: not separately verified (finder confidence high).** Files:
`backend/archimedes/services/portfolio_backtester.py:451`, `backend/archimedes/models/backtest.py:168`

The binary gate is conservatively `False` for generated strategies (good — this is what
neutralizes Q1/Q2 for fresh rows), but the row still publishes the `num_trials=1` DSR (Q1)
and the full-sample cliff (Q2) numbers, which the UI renders as the strategy's rigor metrics.
**Fix:** Compute a real PBO for generated strategies (as `_patch_pbo` already does for the
streaming verdict) so the gate is meaningful, and fix the Q1/Q2 math so the surfaced numbers
are correct.

### Q11 — Fixture generation path hardcodes a passing rigor verdict (`dsr=0.71, passing=True`)
**Verdict: not separately verified (finder confidence high).** File:
`backend/archimedes/agents/generation_pipeline.py:477`

`_run_fixture_candidate` returns a fully hardcoded passing verdict and skips the real
backtest. This path runs whenever `_llm_available()` is `False` — i.e. any no-API-key
environment, including a judge's cold clone. The live EC2 has a key, so this is bounded to
offline/demo (MEDIUM), but an offline demo shows **fabricated green rigor numbers presented
identically to real ones**.
**Fix:** Emit a clearly-labelled synthetic verdict (`passing=False, reason="fixture mode"`);
never set `passing=True` without running the gate.

### B3 — `publishTrace` authorization is silently coupled to the `agent==owner` topology
**Verdict: not separately verified (couples to B1/B2).** Files:
`contracts/src/ReasoningTraceRegistry.sol:73-105`, `backend/archimedes/chain/trace_publisher.py:57-61`

`publishTrace` requires `isAuthorizedForVault`, which passes only because the agent matches
`agent()/creator()/owner()` (i.e. B1). The backend never calls the owner-only `setVaultAgent`.
Fixing B1 (user becomes owner) without also wiring `setVaultAgent` would make every trace
anchor revert — and the revert is swallowed by the best-effort try/except, leaving traces
silently unanchored.
**Fix:** When fixing B1, have vault creation (or a one-time owner tx) call `setVaultAgent`.

### B4 — Pre-flight liquidity check swallows all exceptions and proceeds with the trade
**Verdict: not separately verified (finder confidence high).** File:
`backend/archimedes/chain/executor.py:147-154`

After the explicit `InsufficientLiquidityError: raise`, a broad `except Exception` logs
"liquidity check failed … (non-fatal, allowing trade)" and returns without aborting. Any RPC
error / ABI decode failure during the probe means the trade is submitted unguarded. The
on-chain oracle slippage floor still applies (not a direct drain), but a thin-pool swap that
should have been skipped can proceed.
**Fix:** Narrow the swallowed set, or fail-closed (skip the leg) on probe error; distinguish
transient RPC errors (retry) from structural ones (skip).

### B5 — `PriceOracle.forceSetPrice` is an unbounded single-key escape hatch
**Verdict: not separately verified (finder confidence high).** File:
`contracts/src/PriceOracle.sol:86-92`

`forceSetPrice` is `onlyOwner`, rejects only zero, and skips the `maxDeviationBps` bound that
`setPrice` enforces (a dedicated test confirms the bypass is intentional). The deviation cap
added for 06-10 #13 is fully bypassable by the owner key in one call; the value flows into
`totalAssets()`, `_oracleMinOut`, and `_liquidateToUsdc`.
**Fix:** Gate behind a timelock or a wider-but-finite emergency bound; require the owner key
to be cold/multisig, distinct from the hot oracle-updater key. Document it as a trusted-owner
override, not a trustless bound.

### I1 — Live EC2 opens 80/443 to the world; WAF is associated only with the ALB ★
**Verdict: adjusted HIGH→MEDIUM.** Files: `infra/main.tf:99,106-121,184-194`,
`infra/alb.tf:142,207-230`, `infra/waf.tf:151-154`

The EC2 SG opens HTTP/HTTPS to `0.0.0.0/0` (verified) with a public EIP, and nginx binds host
80/443; the WAF is associated only with the ALB. A client hitting the EIP directly bypasses
rate-limit/IP-rep/Core/SQLi on the host that holds the signing key. **Downgraded from HIGH
because two of the finder's premises were false:** VPC peering *does* exist
(`aws_vpc_peering_connection.default_to_main`, `infra/aurora.tf:108`, with routes in
`vpc.tf:65-69` — verified), so the ALB→EC2 cross-VPC path and the cross-VPC SG references are
*not* "structurally broken." The split-VPC state is deliberate, documented transitional debt;
no real funds at testnet stage.
**Fix:** Restrict EC2 SG 80/443 ingress to `security_groups = [aws_security_group.alb.id]`
and point DNS at the ALB, or delete the ALB/WAF stack and stop claiming WAF coverage.

### I3 — Tracked `deploy_output.json` is a stale orphan whose addresses don't match the live deploy
**Verdict: not separately verified (finder confidence high).** Files: `deploy_output.json:14-34`,
`ui/src/config.js:617`, `backend/archimedes/chain/client.py:44`

`deploy_output.json` records `sTSLA: 0xE745C0…`, but the live backend (`client.py:44`) and UI
(`config.js:617`) both use `0xd514cd…`. The `0xE745…` set appears only in
`backend/tests/chain/*`. The synth/vault/oracle sets in `deploy_output.json` were last
meaningfully changed 2026-05-22; only `ammRouter`/`vaultFactory`/`traceRegistry`/
`assetRegistry` still happen to match. An operator scripting against this file targets dead
contracts.
**Fix:** Regenerate it from the current deployment, or gitignore it and treat `config.js` +
`client.py` as the single source of truth.

### I4 — WAF Core Rule Set + SQLi managed rules pinned in COUNT (observe-only) mode
**Verdict: not separately verified (finder confidence high).** File: `infra/waf.tf:51,121`

`AWSManagedRulesCommonRuleSet` and `AWSManagedRulesSQLiRuleSet` both have
`override_action { count {} }` — they observe, never block. Only rate-limit and IP-reputation
are in BLOCK mode. Combined with I1 (WAF not in the live path anyway), the live app has no
WAF-layer enforcement; the documented observation window has long elapsed.
**Fix:** Flip both to `none {}` (BLOCK), paired with the I1 fix so the WAF is reachable.

### I5 — `deploy.yml` auto-deploys to the funds-signing EC2 on every push to main, no gate
**Verdict: not separately verified (accepted build-on-deploy model).** File: `.github/workflows/deploy.yml:43-46`

`deploy.yml` triggers on `push: [main]` and runs `git reset --hard` + `docker compose up` on
the host with the agent signing key, with no `needs:` on quality-gate and 0 required reviews.
`quality-gate.yml` runs only on `pull_request`, so a direct push to main (allowed for the
agentic system) deploys with zero tests run. Flagged in 06-10 #10; re-flagged as a standing
operator risk on a funds-adjacent host.
**Fix:** Require quality-gate before merge and/or gate `deploy.yml` on a successful
`workflow_run`; require 1 review on PRs touching `backend/chain/`/`contracts/`.

---

## LOW

| ID | Title | File(s) |
| --- | --- | --- |
| Q8 | OOS Sharpe convention split: analytics-engine uses population stddev (`ddof=0`), backend gate uses sample (`ddof=1`) + different rf — two "OOS Sharpe" numbers aren't directly comparable | `analytics-engine/.../engine.py:179`, `_rigor_helpers.py:336,365` |
| B6 | `withdraw`/`redeem` run `_liquidateToUsdc` (AMM swaps) *before* the share-allowance check — CEI violation; gas-grief only (reverts roll back, no drain) | `contracts/src/Vault.sol:199-213` |
| B7 | `SyntheticVault.previewMint` omits the `synthAmount==0` revert guard present in `mint()` — preview/actual divergence on dust | `contracts/src/SyntheticVault.sol:153-158` |
| B8 | `SyntheticVault.burn` `available = balanceOf - protocolFees` can panic-revert if `protocolFees > balance` (06-13 #4, still unguarded; contrived state) | `contracts/src/SyntheticVault.sol:128,165,175-185` |
| B9 | `AMMPool.swap`/`removeLiquidity` have no `minAmountOut` and no access control — direct pool calls bypass the router's slippage guard (vault routes via router, so vault funds safe) | `contracts/src/AMMPool.sol:72-115,159-186` |
| B10 | SIWE session secret falls back to a random per-boot key when `EMAIL_ENCRYPTION_KEY` unset → multi-worker 401s; also couples session-signing to email-encryption | `backend/archimedes/api/auth_siwe.py:41` |
| I6 | ElastiCache Redis has transit encryption but no AUTH token — any in-VPC host can connect passwordless (SG-scoped, so VPC-internal blast radius) | `infra/elasticache.tf:64-68` |
| I7 | nginx trusts `X-Forwarded-For` from all RFC1918 ranges — a direct-to-EIP client can spoof client IP and split the rate-limit bucket | `nginx/nginx.conf:6-10` |

---

## INFO

| ID | Title | File(s) |
| --- | --- | --- |
| Q9 | Return-IID diagnostics (Ljung-Box / variance-ratio / runs) are correct but **never wired into the gate** — the serial-dependence check that protects the DSR's IID assumption never runs at admission | `backend/archimedes/services/return_diagnostics.py:351` |
| Q10 | Three competing regime classifiers + the regime-conditional rigor functions are all unwired dead code (confirms issue #621) | `regime_detector.py:30`, `statistical_regime.py:47`, `_rigor_helpers.py:622,759` |
| Q12 | PBO uses `omega = rank/N` (not `rank/(N+1)`) — at N=4-6 the logit saturates at the clip bound and is a weak discriminator (documented in the docstring) | `_rigor_helpers.py:275`, `analytics-engine/.../pbo.py:93` |
| B11 | `X-Wallet-Address` still in CORS `allow_headers` + a stale `get_profile` docstring — **no live authz bypass** (code uses SIWE), just drift that could mislead a future reader | `backend/archimedes/main.py:117`, `user_routes.py:81-95` |
| B12 | Generation/chat paid-LLM endpoints unauthenticated by default (`REQUIRE_SIWE_FOR_GENERATION` off) — IP-rotation budget drain; consciously deferred (06-13 #3) | `auth_siwe.py:107-129`, `strategies_routes.py:1168` |
| I8 | `cloudwatch.tf` declares no log groups / no `retention_in_days` — shipped logs retain forever (cost/hygiene); file flagged as never plan/apply-verified | `infra/cloudwatch.tf` |
| I9 | Judge-facing docs say "10 contracts" while 11 `.sol` files exist (StrategyRegistry omitted); README/CLAUDE.md correctly say 11 | `docs/judging-rubric-assessment.md:124,217`, `docs/demo-script-pitch-deck-outline.md:157`, `docs/specs/ecosystem-design-spec.md:6` |
| I10 | Stale test docstring references the deleted `/api/papers/corpus/*` endpoints — docstring only, route is genuinely gone (verified) | `backend/tests/services/test_kb_artifacts.py:8-9` |
| I11 | `environment.yml` vs `requirements.txt`: matplotlib/seaborn + dev tooling in conda env, not in Docker reqs — production deps are aligned (the #1 drift is fixed); confirm no runtime import of plotting libs | `environment.yml:33-34`, `backend/requirements.txt` |

---

## Verified genuinely fixed since the prior audits

Independently re-checked against current source; these prior CRITICAL/HIGH items are real
fixes, not cosmetic:

- **Vault share-theft (06-10 #1, CRITICAL)** — `withdraw`/`redeem` now call
  `_spendAllowance(owner_, msg.sender, shares)` before `_burn` (`Vault.sol:210-213,247-250`). ★
- **`minAmountOut=0` rebalance slippage (06-10 #4, CRITICAL)** — all rebalance/liquidation
  swaps derive an oracle-floored `minOut` (`_oracleMinOut`, `Vault.sol:329/350/538`).
- **DSR `num_trials=1` in the canonical gate (06-10 #2)** — `run_rigor_gate`,
  `_rigor_verdict_for` (streaming), and `selection_bias_routes` now pass
  `num_trials=len(library)`. (The *persist* path regressed — see Q1.) ★
- **Look-ahead audit not executed (06-10)** — `look_ahead_audit` runs a real AST analysis
  (`rigor_evaluator.py:62`); the curated/streaming path invokes it. ★
- **Fusion OOS/IS cliff (06-14 continuation, HIGH)** — `fusion_evaluator.py:470` enforces
  `oos/is < 0.5 → fail`. ★ (The cliff is missing from *other* surfaces — see Q2/Q3.)
- **SIWE domain/chain/expiry binding, port 22 closed, generated DB password, `.dockerignore`,
  CSP/clickjacking headers, viem `parseUnits` amount math, encrypted EBS/Aurora/ElastiCache,
  no XSS sinks / no client-bundle private keys, `pip-audit`/`npm audit` clean, production-dep
  alignment** — all confirmed present (06-13 fixes hold).

---

## Outward actions still required (cannot be completed in-repo)

1. **Rotate the Circle entity secret** and treat `WALLET_ID` + the three candidates in blob
   `7940bc5` as compromised (I2). Removal from tracking is done; rotation is not verifiable
   from the repo.
2. **Redeploy the Vault topology** so the depositing user (or a cold governance key) is the
   owner and the agent is only `setAgent` — then wire `setVaultAgent` for trace anchoring
   (B1 + B3). Contract change → Chuan review.
3. **Wire the on-chain commit-reveal** into `agent_runner` and relabel the UI "Temporal
   Binding ✓ VERIFIED" badge until then (B2). Contract-adjacent → Chuan review.

---

## How to reproduce this audit

Every finding above carries a `verify_command` in the narrative or table. The fastest
cross-checks for the HIGH items:

```bash
# Q2 — cliff divides by full-sample sharpe
sed -n '142,177p' backend/archimedes/models/backtest.py
# Q3 — streaming verdict omits cliff
sed -n '304,318p' backend/archimedes/agents/generation_pipeline.py
# B1 — agent == vault owner, no transferOwnership to user
grep -rn 'transferOwnership' contracts/src backend/archimedes
# B2 — no on-chain commit/reveal calls
grep -rn 'functions.commit(\|functions.reveal(' backend/ || echo 'none (confirms B2)'
# I2 — Circle entity secrets in git history
git rev-list --all --objects | grep -i test-secrets
```

_Audit run 2026-06-14 via a 3-domain parallel auditor fan-out + adversarial verification;
all CRITICAL/HIGH findings independently re-verified by the author (★ = verify command
re-run during this pass)._

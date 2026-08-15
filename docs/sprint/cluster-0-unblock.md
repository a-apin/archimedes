# Cluster 0 — human unblocking + day-0 checks

**Zero code. Bash and `gh` only.** Every item has multi-day human lead time and is already
5 days late. Do this before writing a line.

Read [README](README.md) session rules first.

## Asks to Dan — send all in one message

1. **Contract review, prioritised: #1129 > #1200 > #1153.** One-paragraph exploit statement
   each, explicit Sept-16-gate deadline. Tell him the order rather than leaving him to choose.
   None of the three blocks the payment rail — say that too.
2. **Three decisions needed in writing:**
   - identity key (#1194) — **deadline EOD Aug 17**; default if unsettled: opaque `principal_id`
     derived from wallet today, Better Auth later. Document in an ADR either way.
   - narrow `PAYMENTS_DRY_RUN=false` scope (caller-signed metered-API path only; DCW browser
     subscribe stays dry-run, stays blocked on #975). **Bogdan acknowledges the custody side.**
   - mainnet `PaymentSplitter` owner address (multisig).
3. **Kick off Bedrock Anthropic use-case activation** — multi-day approval. No approval by
   **Aug 24** ⇒ assume it misses, B5 ships against the free model path.
4. **Circle mainnet credentials into SSM** + into the ECS `secrets` block, not just the
   best-effort boot fetch.
5. **`terraform apply` for #1071** (runner relocation). If he can't get to it, **stop the
   runners on the detached EC2 box** — a clean stop beats stale funds-adjacent code running
   unmanaged. Frame as a decision, not a complaint.

## Merges — max 2/day, ≥40 min apart, verify `/health` between

Order: **#1224** (mine, money path) → **#1226** (leaderboard provisional banner — makes the
leaderboard claim honest *today*, highest claim-value-per-token in the sprint) → **#1201**
(stop fabricating vault returns; honesty cover for the re-run window) → **#1095** (commit-reveal
stale-trace; narrows the "anchored on-chain" retraction, so sequence it *before* the claims copy).

Then #1202 (low risk, spare slot). #1096 merge-as-docs or close if #1225 supersedes.
**#1225 lands D1–D2 or after Aug 24 — never mid-sprint.** #1223 lands before or after the
re-run, never during (collides with A7 and B4's `num_trials_source`).

## Day-0 checks (B0)

1. **[BLOCKING] Does the pinned `circlekit` know an Arc *mainnet* chain?** Read the installed
   SDK's chain table. `marketplace/config.py:6` hardcodes `"arcTestnet"`; `payments.py:69`
   passes `GATEWAY_CHAIN` into `create_gateway_middleware(chain=…)`. If there is no mainnet
   entry, **the payment rail cannot reach Arc mainnet and the decision changes.**
2. Arc mainnet chain ID + RPC published? `contracts/foundry.toml` has only `arc-testnet`;
   `ui/src/siwe.js:15` hardcodes `5042002`.
3. **Reserve `archimedes-cli` on PyPI** — 10 minutes, irreversible if lost.
4. Measure real generations/wallet/day from prod before choosing a meter ceiling.
5. Confirm the considered-alternatives modal is broken in prod.

## Two live bug fixes — same PR, ~3 lines total

- [`ui/src/components/RejectedCandidates.jsx:73`](../../ui/src/components/RejectedCandidates.jsx:73)
  — bare `fetch()` with no `credentials: 'include'`. **Verified still broken 2026-08-16.** Sole
  outlier against a universal convention (`api.js` + seven components). With
  `REQUIRE_SIWE_FOR_GENERATION` on this 401/404s, so the visible proof of the K=1-plus-rejects
  architecture is dead on the one surface that converts.
- **`PAYMENTS_DRY_RUN` is unset** in `infra/ecs.tf`, both compose files, and
  `setup-ssm-secrets.sh`. Its prod value is an accident of
  [`main.py:203`](../../backend/archimedes/main.py:203)'s default. Set it explicitly — even to
  `true`. An unset money switch is the bug regardless of which way it points.

## A6 diagnostic — one command, do it here

```bash
aws logs tail /ecs/archimedes-backend --since 7d \
  --filter-pattern '"backtest refresh"' | tail -40
```

- `backtest refresh: skipped (…)` → staleness/backoff (`_last_unresolved_missing`)
- `backtest refresh: cycle failed (…)` → runner raises (likely yfinance egress from Fargate)
- `REFUSING to persist … VolPlausibilityError` (`run_backtests.py:386`) → corrected results
  tripping the vol guard, rejected per-strategy, fail-closed, stale rows left in place

## File two tracking issues

Mainnet gate checklist (16 items + the 8 visibly-deferred, tagged out of scope) · claims ledger.

## Done when

Asks sent · 2 merges landed with `/health` verified · circlekit mainnet answer known · PyPI name
held · both bug fixes in one PR · A6 hypothesis identified by log line.

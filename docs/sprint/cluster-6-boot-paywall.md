# Cluster 6 — boot assertions + the x402 paywall (B0-boot · B3)

**The critical path and the biggest new-code item in the sprint.** `main.py` is 821 lines —
**never read whole.** Four anchors, four windows.

```bash
grep -n "circlekit\|PAYMENTS_DRY_RUN\|_MAX_BODY_BYTES\|GATEWAY_CHAIN" backend/archimedes/main.py
```

Read [README](README.md) session rules first.

## Scope correction — this is new code, not a config flip

`grep -rni "PAYMENT-REQUIRED"` across `backend/` and `ui/src` returns **exactly one hit** —
`marketplace/payments.py:120`, where the platform *consumes* a requirement it generated itself
and signs it with a Circle Developer-Controlled Wallet it controls (`charge()` takes the
subscriber's `wallet_id`). **Re-verified 2026-08-16.**

**No route anywhere emits a 402 an external caller can satisfy.** The two real 402s in the tree
(model entitlement, vault funding) are bare. So there is no cheap "challenge-only" version to
ship first — the challenge and the settlement are the same PR.

## Why this unlocks #975

#975's acceptance criterion is that the platform never holds caller funds. **If the caller signs
the x402 payment from their own key, the platform never holds their funds.** So scope the flip
narrowly: `PAYMENTS_DRY_RUN=false` **only** for the caller-signed metered-API path; the DCW
browser subscribe flow stays in dry-run and stays blocked on #975.

**This needs Dan's explicit sign-off and Bogdan's acknowledgment on the custody side. It must not
be inferred.** See [cluster-0](cluster-0-unblock.md).

## Two startup assertions — ship in this PR

Both convert a silent-degradation path into a refusal to boot.

1. **`GATEWAY_CHAIN` must be explicit and verified.** Never `marketplace/config.py:6`'s
   `arcTestnet` default. Assert the configured chain matches what the RPC actually reports, and
   **refuse to boot when `PAYMENTS_DRY_RUN=false` on an unknown or mismatched chain** — otherwise
   a mainnet deploy silently falls back to testnet or fails settlement.
2. **`circlekit` import failure must be fatal when payments are live.** `main.py:51-57` wraps the
   import in try/except so a broken install degrades to *marketplace absent* rather than failing
   loudly. If circlekit did not import and `PAYMENTS_DRY_RUN=false`, **the process must not serve
   traffic.**

Plus B0's carry-over: set `PAYMENTS_DRY_RUN` **explicitly** in `infra/ecs.tf` — even to `true`.

## The paywall — one tight PR, two at most

Request-path middleware that catches an over-quota call and returns 402 with a real
`PAYMENT-REQUIRED` header → caller retries with `X-PAYMENT` → server verifies, settles, proceeds.
**Payer is the caller's own key; seller is the platform address.**

- **The `accepts` block and the `PAYMENT-REQUIRED` header must come from the existing
  `marketplace/payments.py` middleware. Do not hand-roll an x402 body.**
- **402 body:** `{error, reason, sku, unit_price_usdc, meter:{used,ceiling,resets_at}, accepts:[…]}`
- **Until pay-and-retry is wired, `accepts` must be `[]` with
  `"payment_rail": "not_yet_available"`.** Advertising a payment option you cannot honour violates
  the repo's own honesty convention as badly as advertising an endpoint that 404s.
- When wired, **`Idempotency-Key` is required** — x402 is not crash-retry-idempotent, as
  `service.py:768/805` already learned the hard way.

## Prove it — one real cent, three pieces of evidence

**A log line saying "charged" is not proof:** `_charge_one` returns `(True, None)` in dry-run at
`service.py:834` and logs the same shape. Require all three, recorded in `docs/runbooks/`:

1. a Circle facilitator settlement id from `middleware.settle`
2. an on-chain `PaymentSplitter` transfer tx hash with **non-zero USDC value**
3. publisher balance delta == subscriber balance delta, reconciled to the raw unit

**Prove it end-to-end on testnet with real testnet USDC in non-dry-run mode before this PR
merges.** Only then flip the manifest's `paid` group to `status: "live"`.

## The `/api/v1/` surface

New prefix, new router `backend/archimedes/api/v1_routes.py`. **Do not retrofit `/v1` onto the 24
existing routers** — a breaking change for the SPA with no benefit. Write the policy in
`docs/agent-api.md`: `/api/*` is SPA-internal and may change; `/api/v1/*` is paid, versioned,
additive-only.

In scope this sprint: `POST /api/v1/rigor/verdict` (see
[cluster-8](cluster-8-returns-csv.md)).

**Deferred to buffer, ahead of the CLI** (~1.0d): `GET /api/v1/meter` and
`POST/GET/DELETE /api/v1/keys`. **Consequence to state out loud: the metered API has a paywall
but no key management and no balance endpoint, so the only usable principal in the sprint is a
SIWE session.** That is a coherent launch — browser and `agent_journey.py` both authenticate by
SIWE today — but it is **not** the sellable machine API. The CLI's `login`/`meter` subcommands
depend on keys, so keys come first in the buffer.

When keys land: `Authorization: Bearer ark_live_<key_id>_<secret>`, prefixed so leaked-secret
scanners catch it, compared with `hmac.compare_digest` (the `auth_guard.py:38` precedent), secret
shown once, stored as sha256, SIWE-session-only to mint (a key must never mint another key).
**Leave `INTERNAL_AGENT_API_KEY` completely alone** and say why in the docstring — a shared secret
with no identity is a different concept that must not be conflated.

**x402 is a top-up rail for the meter, not an auth rail.** An x402 header proves only that *a
payer settled*; the payer need not equal the login wallet. Making it the auth rail forces either
treating the payer as `owner_wallet` (breaking `is_strategy_visible`) or forking every check.
The one exception is `POST /api/v1/rigor/verdict` — stateless, no ownership, no persistence — so
bare `402 → pay → retry` with **no account at all** is correct there, and it is the best demo you
have.

## Manifest honesty

`agent_manifest_routes.py` + `.well-known/agent.json` + `llms.txt`:

- add `"APIKey"` and `"x402"` to `auth.schemes`; add a `pricing` block
- **replace the flat `chain_id: 5042002`** with
  `chains: {payments: {…}, execution: {name: "Arc testnet", chain_id: 5042002}}` in all three —
  that is the honest disclosure of the split
- **rewrite `_PENDING_T32`**, whose text still says deploy/marketplace/monitor are "landing with
  the T3.2 contract redeploy (#588)" when T3.2 shipped 2026-07-09
- **delete "Arc has no mainnet yet" from `llms.txt`** — false from Sept 16

Extend `backend/tests/test_agent_manifest.py`: every route string in a group marked `live` must
resolve against `app.routes`, and `chain_id` must appear **nowhere as a bare scalar**.

## Anti-goals

- No OAuth/JWT/npm SDK. No un-pinning `circlekit`.
- Do not retrofit `/v1` onto existing routers.
- Do not advertise an `accepts` option you cannot honour.
- Release marker: **`!minor`** (shared with cluster-5 if they land together). Save
  `!version-release` for the Sept 16 cutover PR.

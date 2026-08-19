# Generation Quote Contract

> **Status: RATIFIED.** Drafted 2026-08-19 by the frontend session as a
> PROPOSED contract; the contracts/backend session implemented and landed
> the real thing in **#1296** (`backend/archimedes/services/generation_payment.py`,
> `backend/archimedes/api/generate_routes.py`) with a *different* shape than
> originally proposed here — no `quote_id`, no `expires_at`, a 409
> wallet-link precondition ahead of the 402, and payer binding to the
> caller's linked wallet. This doc now records **that** ratified shape
> verbatim, not the original proposal. The frontend
> (`ui/src/components/Generate.jsx`, `ui/src/generateQuote.js`) was reworked
> onto it in the PR that flipped this status line.
>
> **Ground truth**: `backend/tests/test_generate_payment_gate.py` pins the
> route-level contract (both endpoints' shapes, the 409/402 ordering, the
> dry-run success path). `backend/tests/services/test_generation_payment.py`
> pins the payment module's internal laws (payer binding, fail-safe price
> parsing, dry-run's unverified-accept). If this doc and either test file
> ever disagree, the tests are correct — update this doc, not them.

Two endpoints, one backend flag (`GENERATION_PAYMENT_REQUIRED`, default
`false`). The frontend has its own independent flag
(`VITE_GENERATION_QUOTE_ENABLED`, `ui/src/featureFlags.js`) that gates
whether the UI *shows* the quote/paywall UI at all — it works fine against
a backend where `GENERATION_PAYMENT_REQUIRED` is still off, since the quote
just reports `payment_required: false` honestly in that case.

## `GET /api/generate/quote`

**Public** — no auth required (`generate_public_router` in
`generate_routes.py`, mounted outside the `require_current_user`
dependency that gates the rest of `/api/generate/*`). A human sees the
price before signing in; an agent plans before paying (#1293).

**Request:** no body, no query params. Pricing is flat
(`GENERATION_PRICE_USD`, default **$0.15**) — there is nothing per-request
to size the quote by.

**Response 200:**

```json
{
  "payment_required": false,
  "pricing_model": "flat_v1",
  "price": "$0.150000",
  "asset": "USDC",
  "chain": "eip155:5042002",
  "recipient": null,
  "dry_run": true,
  "how": "POST /api/generate/start without a Payment-Signature header returns 402 with these requirements in the PAYMENT-REQUIRED header; sign them (x402 / Circle Gateway) and retry with Payment-Signature."
}
```

- `payment_required` — mirrors the backend's `GENERATION_PAYMENT_REQUIRED`
  flag. `false` in every environment until Dan flips it (#834's flip-list).
- `price` — a **decimal string** (`"$0.150000"`, six decimal places), not a
  float — money, no FP rounding. Parses safely to the default on a
  malformed `GENERATION_PRICE_USD`; never free, never absurd.
- `pricing_model` — literal `"flat_v1"` today. #1217's measured
  per-generation budget replaces the pricing internals later and bumps
  this string — the frontend must not hardcode assumptions about what
  `flat_v1` implies beyond "one flat number."
- `asset` — literal `"USDC"`.
- `chain` — the gateway chain id string (`GATEWAY_CHAIN` env, e.g.
  `"eip155:5042002"`).
- `recipient` — the platform wallet address, or `null` when unset
  (flag-off environments; also the fail-closed 503 case below).
- `dry_run` — mirrors `PAYMENTS_DRY_RUN` (backend default: **true**). When
  true, a 402 from `/start` is still returned for a missing payment header,
  but a *present* header is accepted **without verification or
  settlement** — loudly logged `UNVERIFIED` server-side. No real value can
  move while `dry_run` is true, so the frontend must render this
  explicitly (never imply a real charge happened) — see "test mode" below.
- `how` — a literal, backend-owned instruction string. Render it verbatim
  if shown; do not paraphrase it into something that could drift from what
  the 402 actually requires.

**There is no `quote_id` and no `expires_at`.** The original PROPOSED
contract had both (an opaque id to echo back on `/start`, an ISO-8601
expiry the frontend was to treat as a submit-blocking condition). Neither
survived ratification: the price is flat and re-quoted fresh on every
`/start` attempt, so there is nothing to echo back and nothing that goes
stale. **Do not reintroduce quote-id/expiry logic in the frontend** — the
409/402 flow below is the entire approval mechanism.

**Non-2xx:** not expected in normal operation (this endpoint does no I/O
beyond reading env vars) — the frontend still treats any non-2xx as "quote
unavailable" defensively.

## `POST /api/generate/start` — the payment gate

**No new request field.** Unlike the PROPOSED contract (which added an
optional `quote_id` to the body), the ratified `/start` request body is
**unchanged** — still just `GenerateStartRequest` (`brief`, optional
`model`, etc.), with no payment-related field at all. The payment gate
lives entirely in headers and status codes, checked in this order (each
earlier gate protects the caller from a wasted round-trip on the one
after it):

1. **Quota (429)** — the existing account+IP daily cap, unrelated to
   payment, runs first. A quota-blocked caller is refused before ever
   being asked to pay.
2. **Wallet-link precondition (409)** — only when `GENERATION_PAYMENT_REQUIRED`
   is true. If the caller has no linked wallet resolvable from the request
   (`get_linked_wallet_address` — driven by the `X-Wallet-Address` header
   the frontend already sends on every request, or the account's primary
   linked wallet if that header is absent), the response is:

   ```json
   {
     "detail": {
       "reason": "wallet_link_required",
       "message": "Generation requires a linked, funded wallet. Link a wallet to your account (POST /api/wallets/challenge → /api/wallets/verify), fund it with testnet USDC (the faucet currently requires a human), then retry. See GET /api/generate/quote for the price."
     }
   }
   ```

   `409`, not `402` — the blocker is account state (no proven linked
   wallet), not a missing payment. The message includes the faucet caveat
   verbatim (#1294: the faucet is human-only, which now bites agents at
   exactly this gate) — render it as-is, do not re-paraphrase it away.

3. **Paywall (402)** — wallet is linked; no `Payment-Signature` header (or
   one that fails verification/settlement — see below) present:

   ```json
   {
     "detail": {
       "reason": "payment_required",
       "message": "Generation requires payment. Sign the PAYMENT-REQUIRED requirements with your linked wallet and retry with a Payment-Signature header.",
       "quote": { "payment_required": true, "pricing_model": "flat_v1", "price": "$0.150000", "asset": "USDC", "chain": "eip155:5042002", "recipient": "0x...", "dry_run": true, "how": "..." }
     }
   }
   ```

   **`detail.quote` is BYTE-IDENTICAL to the `GET /api/generate/quote`
   response** — both are built by the same `generation_payment.quote()`
   call, so they can never disagree. The frontend parses **one** shape
   (`deriveQuoteView` in `generateQuote.js`) for both surfaces. The
   response also carries the real x402 requirements in a `PAYMENT-REQUIRED`
   header (circlekit-built) — the 402 response body *is* the
   quote-approval flow: sign those requirements and retry with
   `Payment-Signature`.

   Other `reason` values on the same 402 shape: `payment_malformed` (header
   didn't decode), `payer_mismatch` (the signed authorization's payer isn't
   the caller's linked wallet — see "Payer binding" below),
   `payment_invalid` (facilitator verify failed), `payment_settle_failed`
   (verified but settle failed — caller keeps funds). All honest 402s, never
   500s.

4. **Fail-closed config (503)** — flag on with `GENERATION_PAYMENT_RECIPIENT`
   unset is a deliberate outage (`reason: "payment_config_missing"`), never
   a free pass.

**Success — 202**, same body as before this feature
(`GenerateStartResponse`). When a real payment was verified and settled
(live mode, not dry-run), the response additionally carries a
**`PAYMENT-RESPONSE`** header — the facilitator's settlement receipt.
Surface it if present; most successful responses (flag off, or dry-run)
will not have it, and that's normal, not an error.

### Test mode (`PAYMENTS_DRY_RUN`, backend default **true**)

When `dry_run` is true in the quote: a *missing* `Payment-Signature`
header still 402s (so the approval UX is exercisable end to end), but a
*present* one — any non-empty value, not decoded or verified — is accepted
and the request proceeds to enqueue. **No `PAYMENT-RESPONSE` header is set
in this path** (nothing was settled). The frontend must render this
honestly: **"test mode — payment accepted unverified"**, never dressed up
as a real charge. See `PAYMENT_STATUS.DRY_RUN` in `generateQuote.js`.

### Payer binding

The payer named inside a *real, verified* `Payment-Signature` (its signed
authorization's `from` field) must equal the caller's **linked** wallet —
checked before any facilitator round-trip (`payer_mismatch` 402 otherwise).
Because the wallet-link precondition (step 2 above) resolves using the
`X-Wallet-Address` header the frontend already sends with every request
(`api.js`'s `walletHeaders()`, driven by `getAddress()` — the wallet
currently active in the injected provider), reaching the 402 stage at all
already proves that active address is a wallet linked to this account. The
frontend still surfaces a proactive mismatch note
(`describePayerMismatch` in `generateQuote.js`) when the active provider
address is linked to nothing on the account, or differs from the account's
primary linked wallet — signing must use the **linked** wallet's account,
never whatever happens to be active in the provider, and the UI says so
when they diverge rather than silently trusting the injected address.

## Explicitly out of scope here

- **Real x402 signing.** Building and signing the EIP-712 payment
  authorization from the connected wallet isn't wired in this frontend —
  on a live (non-dry-run) 402 it renders an honest "payments aren't
  enabled yet" preview instead of a non-functional pay button. Test mode
  (above) gives a real, functional path end to end while `PAYMENTS_DRY_RUN`
  holds, without needing this.
- **Paper-trading deployment cost.** Always $0, unrelated to this
  contract — the Generate UI states that separately so the two costs
  aren't conflated with the generation quote.

## Ratification

Closed. This doc was PROPOSED by the frontend session; #1296 implemented
and shipped a materially different (and now authoritative) shape on the
backend; this revision brings the doc in line with what actually shipped.
Any further change to the wire contract must land in
`backend/tests/test_generate_payment_gate.py` /
`backend/tests/services/test_generation_payment.py` first — this doc
follows the tests, not the other way around.

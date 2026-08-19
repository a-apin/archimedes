# Generation Quote Contract

> **Status: PROPOSED.** Drafted 2026-08-19 by the frontend session so the
> contracts/backend session (quote endpoint + x402 paywall on
> `/api/generate/start`) has something concrete to converge on or amend.
> Not yet implemented on the backend. Flip this line to **RATIFIED** once
> both sides agree — see "Ratification" below.

Small and deliberately narrow: one GET endpoint, one new optional field on
an existing POST. The frontend (`ui/src/components/Generate.jsx`,
`ui/src/generateQuote.js`) is built against this doc, feature-flagged by
`VITE_GENERATION_QUOTE_ENABLED` (`ui/src/featureFlags.js`) so it ships dark
until both sides are ready.

## `GET /api/generate/quote`

Returns the upfront price to run one generation job, in testnet USDC,
before the caller commits to `/api/generate/start`.

**Request:** no body. Optional query params the frontend already has at
quote time — send them if useful for sizing the quote, otherwise the
backend may return a flat MVP price and ignore them:

- `model` — LLM model id (matches `ui/src/data/modelPricing.json`)
- `max_papers` — requested research depth (int)

**Response 200:**

```json
{
  "quote_id": "qt_2f9a1c...",
  "price_usdc": "0.42",
  "currency": "USDC-testnet",
  "breakdown": [
    { "label": "LLM inference (est.)", "amount_usdc": "0.30" },
    { "label": "Research retrieval", "amount_usdc": "0.12" }
  ],
  "expires_at": "2026-08-19T18:05:00Z"
}
```

- `price_usdc` — **decimal string**, not a float (money, no FP rounding).
- `currency` — literal `"USDC-testnet"`. The frontend renders this verbatim
  as the "testnet USDC" label — do not send `"USDC"` alone, the honesty
  framing depends on the distinction being visible.
- `breakdown` — optional; omit or send `[]` if the backend can't itemize
  yet. The frontend renders it as a plain list when present.
- `quote_id` — opaque string, echoed back on `/start`.
- `expires_at` — ISO-8601 UTC. The frontend treats `now >= expires_at` as
  expired and refetches before letting the user submit.

**Non-2xx:** standard error body. The frontend treats any non-2xx as "quote
unavailable" and disables the submit button — fails closed, no
bypass-by-error path.

## `POST /api/generate/start` — `quote_id` addition

Existing request body (`GenerateStartRequest` in `generate_schemas.py`)
gains one new optional field:

```json
{ "brief": { "...": "..." }, "quote_id": "qt_2f9a1c..." }
```

- Paywall flag **off**: `quote_id` accepted and ignored.
- Paywall flag **on**: request must carry a valid, unexpired `quote_id`
  scoped to the caller, else **402** — the same status code/shape as the
  existing `PREMIUM_MODELS_ENABLED` entitlement gate on this endpoint
  (`generate_schemas.py`'s `model` field docs), so the frontend's existing
  `err.status` handling (`ui/src/api.js`) needs no new plumbing.
- The frontend's payment-step UI activates on **any** 402 from this
  endpoint. If the trigger condition ends up needing to be distinguished
  from other 402s (e.g. a response body discriminator), please add it here
  before ratifying — the frontend currently treats them as the same case.

## Explicitly out of scope here

- **Actual payment execution.** Signing/paying the quote from the
  connected wallet (x402) is not wired in this frontend PR — on a 402 it
  renders an honest "payments aren't enabled yet" preview instead of a
  non-functional pay button. That lands once this contract is ratified and
  the payment rail (amount/address/protocol) is nailed down.
- **Paper-trading deployment cost.** Always $0, unrelated to this
  endpoint — the Generate UI states that separately so the two costs
  aren't conflated with the generation quote.

## Ratification

Contracts session: please confirm or amend field names, the `model`/
`max_papers` query params (or drop them if the MVP quote is flat), and the
402 trigger condition — then flip the status line at the top of this file
to RATIFIED.

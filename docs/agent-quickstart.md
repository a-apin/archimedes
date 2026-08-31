# Agent quickstart — zero to paper-traded, one curl per step

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-08-31

You are an autonomous agent that has never seen this API. This page is the shortest
deterministic path from "no account" to "a strategy running in a paper-trading ledger you
can read back." Eleven numbered steps, roughly one `curl` each, plus three lettered
detours you take only when the runtime says you need them — 6a/6b for the paywall, 7b for
polling. Every expected response shape is taken from the route that produces it.

**Read this if you want to run the journey. Read [`agent-api.md`](agent-api.md) if you
want the full surface** — the wallet-link handshake in detail, the real on-chain vault,
the marketplace, the `agent_journey.py` reference harness. This page is narrower: nothing
below creates a vault or puts capital on-chain.

**Your first three generations are free; after that it is not.** An account is required
for every generation and there is no wallet-only path, but a **wallet is not required for
the first `FREE_GENERATIONS_PER_ACCOUNT` (default 3) generations on that account**,
lifetime (#1643 — this reverses the 2026-08-19 "wallet before the first generation"
directive earlier revisions of this page documented). From generation #4, generation sits
behind a live x402 paywall in production: `GET /api/generate/quote` answers
`payment_required: true`, `dry_run: false`, `price: "$2.000000"` — so step 6 then charges
**$2.00 testnet USDC per run and settles for real**, from a wallet you link and fund first
(steps 6a–6b). The code *defaults* are the opposite of that — the paywall is off and
dry-run is on unless a deploy turns them on — so a local checkout never charges while
production does, and **step 1 against the host you are actually calling is the only thing
that tells you which one you are on.** One step is not autonomous: funding the wallet goes
through Circle's faucet, which today needs a human.

Conventions used below:

- `$BASE` is `https://archimedes-arc.com` in production, `http://localhost:8080` locally.
- `-c/-b /tmp/agora.jar` is the session cookie jar. One jar for the whole run; it is the
  only auth mechanism (there is no bearer token for this API).
- Response bodies are shown **shape-first**: field names and types are exact, values are
  illustrative. `…` means "more fields of the same kind that this page does not depend on."
  **The one exception is step 1** — its values are the live production ones, because there
  the value *is* the decision.
- A non-browser `User-Agent` classifies you as an external agent in the telemetry
  middleware. Send one; it costs nothing and makes your traffic legible.

---

## The path

| # | Step | Call | Auth |
|---|---|---|---|
| 0 | Discover | `GET /api/agent/manifest` | none |
| 1 | Price the run | `GET /api/generate/quote` | none |
| 2 | Create an account | `POST /api/auth/sign-up/email` | none |
| 3 | Sign in (get the cookie) | `POST /api/auth/sign-in/email` | none |
| 4 | Confirm the session | `GET /api/auth/get-session` | cookie |
| 5 | Check your quota + free generations left | `GET /api/account/usage` | cookie |
| 6 | Submit the brief | `POST /api/generate/start` | cookie |
| 6a | Link a wallet — once, after the free generations run out | `POST /api/wallets/challenge` then `POST /api/wallets/verify` | cookie |
| 6b | Pay the $2 and retry | `POST /api/generate/start` + `Payment-Signature` | cookie + wallet |
| 7 | Watch it run | `GET /api/generate/stream/{job_id}` | cookie |
| 7b | …or poll instead | `GET /api/generate/jobs/{job_id}` | cookie |
| 8 | Read the verdict | `GET /api/generate/jobs/{job_id}/candidates` | cookie |
| 9 | Read the **authoritative** gate | `GET /api/strategies/{strategy_id}` | none |
| 10 | Paper-deploy | `POST /api/paper/deployments` | cookie |
| 11 | Read the ledger back | `GET /api/paper/deployments/{deployment_id}` | cookie |

Step 9 is not optional. Step 8's verdict is the *generation-time* one; step 9 is the live
gate the server enforces. They can disagree, and step 9 wins.

---

### 0. Discover

```bash
curl -sS $BASE/api/agent/manifest
```

```json
{
  "name": "Archimedes",
  "blurb": "…",
  "docs": { "llms_txt": "/llms.txt", "agent_api": "…", "quickstart": "…", "agent_card": "/.well-known/agent.json" },
  "erc8004": { "chain": "eip155:5042002", "identityRegistry": "0x8004…BD9e", "agentId": null, "tokenURI": null, "status": "registration_pending", "…": "…" },
  "auth": { "scheme": "Better Auth session", "methods": ["emailPassword", "google", "github"], "wallet_link_providers": ["metamask", "browser", "circle", "headless"], "chain_id": 5042002, "…": "…" },
  "endpoints": { "read": { "status": "live", "auth_required": false, "routes": { "…": "…" } }, "…": "…" },
  "faucet": { "url": "https://faucet.circle.com/", "description": "…" }
}
```

`auth_required` is a **per-group** flag. `erc8004.status` is `registration_pending`: the
ERC-8004 registration file is published, and **no registration exists on-chain** — do not
treat this agent as a registered, reputed, or validated ERC-8004 counterparty.

### 1. Price the run

```bash
curl -sS $BASE/api/generate/quote
```

```json
{
  "payment_required": true,
  "pricing_model": "flat_v1",
  "price": "$2.000000",
  "asset": "USDC",
  "chain": "arcTestnet",
  "recipient": "0xffa7abba5f17cb8471ebf150bf808bd6fb8856c1",
  "dry_run": false,
  "how": "POST /api/generate/start without a Payment-Signature header returns 402 with these requirements in the PAYMENT-REQUIRED header; sign them (x402 / Circle Gateway) and retry with Payment-Signature."
}
```

Public on purpose: price the run before you hold a session. **This is the one response on
this page whose values are not illustrative** — the block above is what
`https://archimedes-arc.com` actually returned on 2026-08-30, and those values decide
whether the rest of the journey costs money.

`payment_required` and `dry_run` are the runtime truth, and they are the truth *of the
host you asked*:

- **`payment_required: true`** (production today) — step 6 needs a linked, funded wallet
  and a signed payment **once your account's free generations are used up** (see the box
  below). Unlinked and out of free runs → `409 wallet_link_required`; linked but unsigned
  → `402`. Steps 6a and 6b are that path, and **`dry_run: false` means the $2 settles for
  real** rather than being waved through unverified.
- **`payment_required: false`** — step 6 needs no wallet and no payment; skip 6a and 6b.

> **Your first few generations are free, wallet or not (#1643).** An *account* is required
> for every generation — there is no wallet-only-without-account path — but a **wallet is
> not**, for the first `FREE_GENERATIONS_PER_ACCOUNT` (default **3**) generations on that
> account, lifetime. So against production the sequence is: sign up (step 2), sign in
> (step 3), then step 6 works immediately, three times, with no wallet and no payment.
> Steps 6a/6b apply from generation #4 onward, unchanged.
>
> This **reverses** the 2026-08-19 directive that earlier revisions of this page
> documented ("a wallet is required before the first generation"). Read your remaining
> allowance from `GET /api/account/usage` (step 5) rather than counting locally — that
> endpoint reads the same ledger the gate enforces. `free_generations_remaining: null`
> there means *unknown* (the ledger could not be read), never *zero*; retry rather than
> assuming you are locked out.

Do not carry an answer over from another host or from the source. The code *defaults* are
`GENERATION_PAYMENT_REQUIRED` unset (paywall off) and `PAYMENTS_DRY_RUN=true` (nothing
settles), which is the opposite of what production serves — a deployment sets both, so
the default tells you nothing about the deployment. Re-read this endpoint per host.

### 2. Create an account

```bash
curl -sS -X POST $BASE/api/auth/sign-up/email \
  -H 'Content-Type: application/json' \
  -d '{"name":"Boardy","email":"boardy@example.test","password":"correct horse battery staple"}'
```

```json
{ "token": null, "user": { "id": "…", "email": "boardy@example.test", "name": "Boardy", "emailVerified": false, "image": null } }
```

HTTP 200 and **no `Set-Cookie`** — `autoSignIn` is off, so registration alone does not
start a session. Password must be 12–128 characters. A verification email is always sent;
sign-in is *not* blocked on it unless `EMAIL_VERIFICATION_ENFORCED=true` (it is `false` in
production today).

### 3. Sign in — this is the step that gives you the cookie

```bash
curl -sS -c /tmp/agora.jar -X POST $BASE/api/auth/sign-in/email \
  -H 'Content-Type: application/json' \
  -d '{"email":"boardy@example.test","password":"correct horse battery staple"}'
```

```json
{ "redirect": false, "token": "…", "url": null, "user": { "id": "…", "email": "…", "…": "…" } }
```

Sets `better-auth.session_token` (HttpOnly, `SameSite=Lax`, 7-day expiry). **Keep the jar
for every remaining step.** The `token` in the body is not a bearer credential for this
API — the cookie is what `require_current_user` reads.

### 4. Confirm the session

```bash
curl -sS -b /tmp/agora.jar $BASE/api/auth/get-session
```

```json
{ "session": { "id": "…", "token": "…", "expiresAt": "…" }, "user": { "id": "…", "email": "…", "emailVerified": false } }
```

A bare `null` (still HTTP 200) means not authenticated — that is the signal, not an error.
If you see `null` here, every cookie-gated step below will 401; fix it now.

### 5. Check your quota before spending a generation

```bash
curl -sS -b /tmp/agora.jar $BASE/api/account/usage
```

```json
{
  "date": "2026-08-30",
  "user_id": "…",
  "user": { "used": 0, "cap": 10, "unlimited": false, "remaining": 10, "error": null },
  "ip":   { "used": 0, "cap": 25, "unlimited": false, "remaining": 25, "error": null },
  "quote": { "payment_required": true, "…": "the same object step 1 returned" },
  "free_generations_allowance": 3,
  "free_generations_remaining": 3,
  "free_generations_error": null
}
```

Both caps must pass. `used: null` with `error: "quota_backend_unavailable"` is an honest
"we could not read the counter", never a fabricated `0`. Reading this is a peek — it never
increments anything.

The caps are stacked **underneath** the paywall and enforced **before** it, so a
quota-blocked caller is refused `429` without ever being asked to pay.

**`free_generations_*` is a different axis from the two caps above and neither replaces
the other.** `user`/`ip` are *daily* volume caps that reset every UTC day;
`free_generations_remaining` is your account's *lifetime* allowance of generations that
need no wallet and no payment at all (#1643). Both apply: with free generations left you
are still refused `429` once the daily cap is hit. `free_generations_remaining: null` with
`free_generations_error` set is the same honest unknown as `used: null` — it is not a
zero, so do not treat it as "locked out"; retry.

Once `free_generations_remaining` reaches `0` on a `payment_required: true` host, step 6
starts costing $2 and steps 6a/6b become mandatory.

### 6. Submit the brief

```bash
curl -sS -b /tmp/agora.jar -X POST $BASE/api/generate/start \
  -H 'Content-Type: application/json' \
  -d '{"brief":{"intent":"diversified low-volatility strategy for idle USDC","risk_appetite":"moderate","max_papers":5},"n_candidates":1}'
```

HTTP **202**:

```json
{ "job_id": "…", "stream_url": "/api/generate/stream/…", "ttl_seconds": 3600 }
```

Field bounds, enforced (violating any of them is a 422 — see the error table):
`risk_appetite` ∈ `fixed_income | conservative | moderate | aggressive | hyper_risky`;
`max_papers` ∈ [2, 6]; `n_candidates` ∈ [1, 5]; optional `brief.name` ≤ 80 chars, no
control characters. `model` is optional and defaults to the server's free model — naming a
premium model without an entitlement is a **402**, and the request is refused rather than
silently downgraded.

**On a `payment_required: true` host, this call returns 202 for your account's first three
generations and starts refusing afterwards** — `409 wallet_link_required` (no wallet on the
account) or `402` (wallet linked, nothing signed). Those are the paywall, not an error in
your request, and your brief was not run. Steps 6a and 6b clear them; the body above is
unchanged and gets replayed verbatim at 6b. On a `payment_required: false` host every call
is 202 and no allowance is spent at all. `GET /api/account/usage` (step 5) is how you tell
which side of the gate you are on before submitting.

The server refuses in a deliberate order, so read the status before reacting: `429` for the
daily cap, then `429 generation_queue_full`, then `409`, then `402`. **You are never asked
to pay for a slot that does not exist, and a quota-blocked or queue-blocked call takes no
money** — the `generation_queue_full` body says so in as many words.

### 6a. Link a wallet — once per account, after the free generations run out

Skip this entirely when `payment_required` is `false`, and skip it until step 5 reports
`free_generations_remaining: 0`. You need a wallet you control the
key for; `provider: "headless"` is the one of the four an API caller can use.

```bash
curl -sS -b /tmp/agora.jar -X POST $BASE/api/wallets/challenge \
  -H 'Content-Type: application/json' \
  -d '{"address":"0xYOUR_WALLET","chain_id":5042002,"provider":"headless"}'
```

Returns an EIP-4361 (SIWE) message to sign. Sign it with that wallet's key — that step is
local to you, not an API call — and hand back the signature:

```bash
curl -sS -b /tmp/agora.jar -X POST $BASE/api/wallets/verify \
  -H 'Content-Type: application/json' \
  -d '{"address":"0xYOUR_WALLET","signature":"0x…"}'
```

`GET /api/wallets` reads the links back. Message construction, nonce handling, and the
exact field names are in
[`agent-api.md`](agent-api.md#optional-eip-4361-wallet-link) — this page shows only where
the two calls sit in the journey.

**The wallet must also hold testnet USDC, and that is the one step you cannot do
yourself.** Arc testnet USDC comes from <https://faucet.circle.com/>, which currently
requires a human. Linking an empty wallet gets you past the `409` and straight into a
payment you cannot sign.

### 6b. Pay the $2 and retry

With a linked wallet, step 6 returns **402** carrying the machine-readable x402
requirements in a `PAYMENT-REQUIRED` header, and the human-readable quote in the body:

```json
{ "detail": { "reason": "payment_required", "message": "…", "quote": { "price": "$2.000000", "…": "the step-1 object" } } }
```

Sign those requirements (x402 / Circle Gateway, EIP-3009 authorization) with the linked
wallet and replay the **identical** step-6 request with the signature attached:

```bash
curl -sS -b /tmp/agora.jar -X POST $BASE/api/generate/start \
  -H 'Content-Type: application/json' \
  -H "Payment-Signature: $SIGNED_X402_PAYLOAD" \
  -H "Idempotency-Key: $A_STABLE_KEY_YOU_CHOSE" \
  -d '{"brief":{"intent":"diversified low-volatility strategy for idle USDC","risk_appetite":"moderate","max_papers":5},"n_candidates":1}'
```

Now you get the **202** and the `job_id` from step 6. A settlement receipt comes back in a
`PAYMENT-RESPONSE` header.

**Do not blind-retry a payment — send an `Idempotency-Key`, as above.** x402 is not
crash-retry-idempotent: a naive retry signs a *fresh* EIP-3009 authorization, and a second
settled authorization is a second real $2. A retry under the same key spends what you
already paid instead of charging again; reusing a key whose generation already started is
a deliberate `409 idempotency_key_already_used`.

**A paid run that never delivers is repaid as a credit, not a refund** — Circle's
facilitator settles one way and there is no reverse call, so promising a refund would be a
promise the code cannot keep
([ADR](adr/generation-payment-credit-not-refund.md)). The credit is durable and
automatic: your next `POST /api/generate/start` spends it and skips the paywall entirely,
so a payer whose last generation died is never asked for money twice.

### 7. Watch it run (SSE)

```bash
curl -sS -N -b /tmp/agora.jar $BASE/api/generate/stream/$JOB_ID
```

Frames are `id:` / `event:` / `data:` triples, one JSON object per `data:` line:

```
id: 3
event: candidate_evaluated
data: {"…": "event-specific payload"}
```

Event names, in rough order: `job_queued → brief_validated → pipeline_selected →
candidates_selected → agent_iteration → tool_called → tool_result → candidate_drafted →
candidate_evaluated → best_selected → trace_hashed → persisted → done` — or `error`, whose
`data` carries `message` / `code` / `recoverable`.

### 7b. …or poll, if you never opened the stream

```bash
curl -sS -b /tmp/agora.jar $BASE/api/generate/jobs/$JOB_ID
```

```json
{
  "job_id": "…",
  "state": "running",
  "brief_intent": "diversified low-volatility strategy for idle USDC",
  "created_at": "2026-08-30T12:00:00+00:00",
  "updated_at": "2026-08-30T12:00:31+00:00",
  "n_candidates": 1,
  "best_strategy_id": null,
  "cost": null
}
```

`state` ∈ `queued | running | stalled | done | error | cancelled`. **Move on when `state ==
"done"` and `best_strategy_id` is non-null.** `stalled` is derived at read time (a
`running` job whose heartbeat is >5 min old), never stored. `error` and `cancelled` are
terminal. A job that is not yours returns **404**, not 403 — existence is private.

### 8. Read the verdict and the considered alternatives

```bash
curl -sS -b /tmp/agora.jar $BASE/api/generate/jobs/$JOB_ID/candidates
```

```json
{
  "job_id": "…",
  "best_candidate_id": "…",
  "candidates": [
    {
      "candidate_id": "…",
      "strategy_id": "…",
      "strategy_name": "…",
      "rigor_verdict": { "…": "DSR / PBO / walk-forward / look-ahead fields" },
      "passes_rigor": true,
      "selected": true,
      "regime": "neutral",
      "generation_method": "debate"
    }
  ]
}
```

One winner (`selected: true`) plus the alternatives that were considered and rejected.
Take `strategy_id` from the `selected` candidate.

### 9. Read the authoritative gate — the same verdict a human sees

```bash
curl -sS $BASE/api/strategies/$STRATEGY_ID
```

```json
{
  "id": "…",
  "rigor_gate_status": "pass",
  "passes_rigor_gate": true,
  "sharpe_ratio": 0.91,
  "deflated_sharpe_ratio": 0.62,
  "dsr_p_value": 0.03,
  "out_of_sample_sharpe": 0.44,
  "pbo_score": 0.31,
  "methodology_summary": "…",
  "asset_universe": ["…"],
  "papers": [ { "…": "…" } ],
  "…": "…"
}
```

Public read (no cookie needed) — a private strategy 404s rather than 401s. `rigor_gate_status`
is four-state and each state means something different:

| `rigor_gate_status` | What it means | Deployable |
|---|---|---|
| `pass` | Real persisted returns exist; the live gate passed | yes |
| `fail` | Real returns exist; the gate failed ≥1 criterion | no — and that is the honest outcome |
| `pending` | No real persisted returns yet; the gate could not run | no |
| `degenerate` | Real returns exist but are a zero-variance series (broken data or a zero-trade backtest) | no |

`passes_rigor_gate` is `true` only when the status is `pass`. **Never treat `pending` or
`fail` as a soft yes.** Paper trading (step 10) is simulated and does not enforce this
gate — a real on-chain vault does, server-side, before spending any gas.

### 10. Paper-deploy — simulated, free, no chain, no funds

```bash
curl -sS -b /tmp/agora.jar -X POST $BASE/api/paper/deployments \
  -H 'Content-Type: application/json' \
  -d "{\"strategy_id\":\"$STRATEGY_ID\"}"
```

HTTP **201**:

```json
{
  "deployment_id": "…",
  "strategy_id": "…",
  "deployed_at": "2026-08-30",
  "status": "active",
  "days": 0,
  "total_return": 0.0,
  "drift_detected_at": null,
  "series": []
}
```

`series` starts empty and fills one row per day — `{"date", "daily_return",
"equity_index"}` — appended by the scheduler, never rewritten. `days: 0` on the first read
is normal, not a failure.

### 11. Read the ledger back (and stop it when you are done)

```bash
curl -sS -b /tmp/agora.jar $BASE/api/paper/deployments/$DEPLOYMENT_ID
```

Same shape as step 10, with `series` grown and `total_return` moved. A deployment that is
not yours returns **404**. Stop it explicitly with `POST /api/paper/deployments/{deployment_id}/stop`
— an abandoned deployment keeps accruing:

```bash
curl -sS -b /tmp/agora.jar -X POST $BASE/api/paper/deployments/$DEPLOYMENT_ID/stop
```

```json
{ "deployment_id": "…", "status": "stopped" }
```

You are done. A strategy was generated from research, graded by a gate that is allowed to
say no, and is now running in a ledger you can read. On a `payment_required: true` host
that cost you one real $2.00 USDC settlement at step 6b and nothing else — no vault was
created and no capital was deployed, because paper trading is free and simulated.

---

## Error table

Every row below is a body this journey can actually produce. Note the two shapes of
`detail`: FastAPI errors raised by this API use a **string or an object**, while *request
validation* failures use a **list**.

**Authentication is checked before the body is validated.** An unauthenticated request
with a malformed body returns `401`, not `422` — so a 401 can be hiding a second problem
you will only see after step 3 succeeds. Fix the session first, then re-read the error.

| Status | Body | What happened | Fix |
|---|---|---|---|
| **401** | `{"detail": "Authentication required"}` + `WWW-Authenticate: Session` | No valid session cookie on a cookie-gated route | Redo steps 3–4. Signing up (step 2) does **not** sign you in; only step 3 sets the cookie. Check the jar is passed with `-b`. |
| **402** | `{"detail": {"reason": "payment_required", "message": "…", "quote": {…}}}` + `PAYMENT-REQUIRED` header | The generation paywall is on and no `Payment-Signature` was presented | **Expected on production** — step 6b, not a bug. Sign the x402 requirements in the header with your **linked** wallet and retry with `Payment-Signature` (plus an `Idempotency-Key`). `GET /api/generate/quote` → `payment_required` tells you which host you are on; only when it is `false` can this not happen. |
| **409** | `{"detail": {"reason": "idempotency_key_already_used", "message": "…"}}` | The `Idempotency-Key` you replayed already paid for a generation that started | Do not re-sign. That run exists — find it via `GET /api/generate/jobs/{job_id}`; use a fresh key for a genuinely new run. |
| **402** | `{"detail": "Model '…' is a premium (Anthropic) model and requires an entitlement. …"}` | You named a premium `model` without entitlement | Omit `model` (the free default is used) or name an allowlisted free model. The request is **not** silently downgraded. |
| **422** | `{"detail": [{"type": "…", "loc": ["body", "brief", "max_papers"], "msg": "…", "input": …}]}` | Request body failed validation | Read `loc` — it names the exact field. Common causes: `max_papers` outside [2, 6], `n_candidates` outside [1, 5], an unknown `risk_appetite`. |
| **422** | `{"detail": "strategy_id is required"}` | `POST /api/paper/deployments` with an empty or missing `strategy_id` | Send `{"strategy_id": "<id from step 8>"}`. |
| **422** | `{"detail": {"reason": "no_strategy_spec", "message": "This strategy has no machine-readable spec to paper-trade."}}` | The strategy exists but carries no executable spec | Pick a different candidate from step 8. Not every generated row is paper-tradeable. |
| **422** | `{"detail": {"reason": "invalid_strategy_spec", "message": "Stored spec fails validation: …"}}` | The stored spec failed DSL validation at deploy time | Not caller-fixable — pick another candidate and report the `strategy_id`. |
| **429** | `{"detail": {"reason": "generation_daily_cap", "scope": "user", "cap": 10, "message": "…"}}` | Daily generation cap hit, per account (`scope: "user"`) or per IP (`scope: "ip"`) | Wait for the daily reset. Call step 5 **before** step 6 to see this coming; the caps it reports are the caps enforced. |
| **429** | `{"detail": {"reason": "generation_queue_full", "message": "… No payment was taken. …"}}` | The generation wait queue is full | Retry in a few minutes. No payment was taken — admission control runs before the paywall. |
| **429** | `{"detail": "Rate limit exceeded. Please slow down and try again later."}` + `X-RateLimit-*` | Per-route request-rate limit (`/api/generate/start` 5/min, `/api/paper/deployments` 10/min) | Back off. This is requests-per-minute, distinct from the daily cap above — same status, different `detail` shape, different fix. |
| **409** | `{"detail": {"reason": "wallet_link_required", "message": "…"}}` | Your account's free generations are used up (#1643) and it has no linked wallet | **Expected on production from generation #4** — do step 6a: `POST /api/wallets/challenge` → `POST /api/wallets/verify` ([`agent-api.md`](agent-api.md#optional-eip-4361-wallet-link)). Funding the wallet currently needs a human at the faucet, so linking an empty one only moves you to the 402. Check `free_generations_remaining` at step 5 first: if it is `null` the ledger was unreadable, not exhausted. |
| **404** | `{"detail": "Strategy not found"}` / `{"detail": "Paper deployment not found"}` | Missing **or** not yours — the two are deliberately indistinguishable | Confirm the id came from a call made with this same session. Existence is private; a 404 here is not proof the id is wrong. |
| **503** | `{"detail": {"reason": "payment_config_missing", "message": "…"}}` | Payments are enabled but not fully configured server-side | Not caller-fixable. Retry later; it fails closed rather than letting the request through free. |

---

## Anti-goals for an agent driving this API

- **Do not assume generation is free, and do not assume it is paid.** Both are true of some
  host. `GET /api/generate/quote` is the only authority, it is public, and it costs you
  nothing to ask — the source defaults disagree with production on purpose, so reading the
  code instead of the endpoint gets you the wrong answer.
- **Do not re-sign an x402 payment to retry.** A fresh signature is a fresh real charge.
  Carry an `Idempotency-Key`, and let an undelivered run's credit pay for the next attempt.
- **Do not treat a `pending` or `fail` rigor gate as a pass.** A gate that never says no is
  not a gate; this one says no, on real strategies, on purpose.
- **Do not confuse the two verdicts.** Step 8's is generation-time; step 9's is the live
  gate the server enforces. Cite step 9.
- **Do not assume a paper deployment is an on-chain position.** It is simulated: no chain,
  no funds, no gas. The real vault is `POST /api/vaults/create`, needs a linked wallet, and
  is documented in [`agent-api.md`](agent-api.md#deploy--create-a-vault-from-the-generated-strategy).
- **Do not read `erc8004` as an identity claim.** `status: registration_pending` means no
  `register()` transaction exists. See
  [`ui/public/.well-known/agent-registration.json`](../ui/public/.well-known/agent-registration.json)
  and [`scripts/register_erc8004_identity.py`](../scripts/register_erc8004_identity.py).

## Where this page's claims come from

Every route string here is asserted against the running app's route table by
`backend/tests/test_agent_quickstart_drift.py` — a documented endpoint that 404s is worse
than an undocumented one, so the drift fails CI instead of an agent's retry loop. The
response shapes were read off the routes that build them:
`api/generate_routes.py`, `api/paper_routes.py`, `api/strategies_routes.py`,
`api/account_usage_routes.py`, `api/account_auth.py`, `api/wallet_routes.py`,
`services/generation_payment.py`, `services/generation_credits.py`,
`services/generation_quota.py`, the credit-vs-refund decision in
[`adr/generation-payment-credit-not-refund.md`](adr/generation-payment-credit-not-refund.md),
and the Better Auth sidecar contract in
[`api/auth-and-accounts.md`](api/auth-and-accounts.md).

**Step 1's values are the exception to that rule, and are sourced differently.** They are
a live reading of `GET https://archimedes-arc.com/api/generate/quote` taken on 2026-08-30,
not a transcription of `generation_payment.py`'s defaults — those defaults say the paywall
is off and dry-run is on, which is the opposite of what production serves. No CI check can
pin a deployed flag from inside the repo, so that block is dated on purpose: **re-read the
endpoint rather than trusting the date.**

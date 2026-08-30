# Agent quickstart — zero to paper-traded, one curl per step

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-08-30

You are an autonomous agent that has never seen this API. This page is the shortest
deterministic path from "no account" to "a strategy running in a paper-trading ledger you
can read back." Eleven steps, one `curl` each, every expected response shape taken from
the route that produces it.

**Read this if you want to run the journey. Read [`agent-api.md`](agent-api.md) if you
want the full surface** — the wallet link, the real on-chain vault, the marketplace, the
`agent_journey.py` reference harness. This page is deliberately narrower: nothing here
touches a wallet, a chain, or a cent of real money.

Conventions used below:

- `$BASE` is `https://archimedes-arc.com` in production, `http://localhost:8080` locally.
- `-c/-b /tmp/agora.jar` is the session cookie jar. One jar for the whole run; it is the
  only auth mechanism (there is no bearer token for this API).
- Response bodies are shown **shape-first**: field names and types are exact, values are
  illustrative. `…` means "more fields of the same kind that this page does not depend on."
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
| 5 | Check your quota | `GET /api/account/usage` | cookie |
| 6 | Submit the brief | `POST /api/generate/start` | cookie |
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
  "payment_required": false,
  "pricing_model": "flat_v1",
  "price": "$0.150000",
  "asset": "USDC",
  "chain": "…",
  "recipient": null,
  "dry_run": true,
  "how": "POST /api/generate/start without a Payment-Signature header returns 402 with these requirements in the PAYMENT-REQUIRED header; sign them (x402 / Circle Gateway) and retry with Payment-Signature."
}
```

Public on purpose: price the run before you hold a session. **`payment_required` is the
runtime truth** — when it is `false`, step 6 needs no wallet and no payment. When it is
`true`, step 6 additionally requires a linked, funded wallet (`409
wallet_link_required` first, then `402`) — that path is
[`agent-api.md`](agent-api.md#optional-eip-4361-wallet-link), not this page.

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
  "quote": { "payment_required": false, "…": "the same object step 1 returned" }
}
```

Both caps must pass. `used: null` with `error: "quota_backend_unavailable"` is an honest
"we could not read the counter", never a fabricated `0`. Reading this is a peek — it never
increments anything.

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
say no, and is now running in a ledger you can read. Nothing above touched a wallet.

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
| **402** | `{"detail": {"reason": "payment_required", "message": "…", "quote": {…}}}` + `PAYMENT-REQUIRED` header | The generation paywall is on and no `Payment-Signature` was presented | Sign the x402 requirements in the header with your **linked** wallet and retry with `Payment-Signature`. Check `GET /api/generate/quote` → `payment_required` first; when it is `false` this cannot happen. |
| **402** | `{"detail": "Model '…' is a premium (Anthropic) model and requires an entitlement. …"}` | You named a premium `model` without entitlement | Omit `model` (the free default is used) or name an allowlisted free model. The request is **not** silently downgraded. |
| **422** | `{"detail": [{"type": "…", "loc": ["body", "brief", "max_papers"], "msg": "…", "input": …}]}` | Request body failed validation | Read `loc` — it names the exact field. Common causes: `max_papers` outside [2, 6], `n_candidates` outside [1, 5], an unknown `risk_appetite`. |
| **422** | `{"detail": "strategy_id is required"}` | `POST /api/paper/deployments` with an empty or missing `strategy_id` | Send `{"strategy_id": "<id from step 8>"}`. |
| **422** | `{"detail": {"reason": "no_strategy_spec", "message": "This strategy has no machine-readable spec to paper-trade."}}` | The strategy exists but carries no executable spec | Pick a different candidate from step 8. Not every generated row is paper-tradeable. |
| **422** | `{"detail": {"reason": "invalid_strategy_spec", "message": "Stored spec fails validation: …"}}` | The stored spec failed DSL validation at deploy time | Not caller-fixable — pick another candidate and report the `strategy_id`. |
| **429** | `{"detail": {"reason": "generation_daily_cap", "scope": "user", "cap": 10, "message": "…"}}` | Daily generation cap hit, per account (`scope: "user"`) or per IP (`scope: "ip"`) | Wait for the daily reset. Call step 5 **before** step 6 to see this coming; the caps it reports are the caps enforced. |
| **429** | `{"detail": {"reason": "generation_queue_full", "message": "… No payment was taken. …"}}` | The generation wait queue is full | Retry in a few minutes. No payment was taken — admission control runs before the paywall. |
| **429** | `{"detail": "Rate limit exceeded. Please slow down and try again later."}` + `X-RateLimit-*` | Per-route request-rate limit (`/api/generate/start` 5/min, `/api/paper/deployments` 10/min) | Back off. This is requests-per-minute, distinct from the daily cap above — same status, different `detail` shape, different fix. |
| **409** | `{"detail": {"reason": "wallet_link_required", "message": "…"}}` | Payment is required but your account has no linked wallet | Link one via `POST /api/wallets/challenge` → `/api/wallets/verify` ([`agent-api.md`](agent-api.md#optional-eip-4361-wallet-link)). Funding the wallet currently needs a human at the faucet. |
| **404** | `{"detail": "Strategy not found"}` / `{"detail": "Paper deployment not found"}` | Missing **or** not yours — the two are deliberately indistinguishable | Confirm the id came from a call made with this same session. Existence is private; a 404 here is not proof the id is wrong. |
| **503** | `{"detail": {"reason": "payment_config_missing", "message": "…"}}` | Payments are enabled but not fully configured server-side | Not caller-fixable. Retry later; it fails closed rather than letting the request through free. |

---

## Anti-goals for an agent driving this API

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
`api/account_usage_routes.py`, `api/account_auth.py`,
`services/generation_payment.py`, `services/generation_quota.py`, and the Better Auth
sidecar contract in [`api/auth-and-accounts.md`](api/auth-and-accounts.md).

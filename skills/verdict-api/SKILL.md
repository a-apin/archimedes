---
name: verdict-api
description: How an agent (or a human, via curl/HTTPie) requests a strategy generation and reads its rigor verdict over Archimedes' HTTP API — the /api/generate/* endpoints, the SSE event shape, real auth requirements on main today, and how to read DSR/PBO/OOS numbers without over-claiming.
triggers:
  - calling POST /api/generate/start or GET /api/generate/stream
  - "how do I generate a strategy from the API"
  - "what does the SSE stream from Archimedes send"
  - reading a strategy's passes_rigor_gate / DSR / PBO / out_of_sample_sharpe fields
  - integrating an external agent against Archimedes' generation endpoint
  - "what auth does generation need — account session, wallet, or both"
---

# Requesting a strategy verdict over HTTP

This skill grounds every claim in the working tree. File:line citations refer to
`backend/archimedes/` at the commit this skill shipped with — re-run the greps in
"Verify" if the code has moved.

## The endpoint family

Defined in [`backend/archimedes/api/generate_routes.py`](../../backend/archimedes/api/generate_routes.py)
(module docstring, lines 1–13) and mounted at prefix `/api/generate`:

| Method | Path | Line | What it does |
|---|---|---|---|
| POST | `/api/generate/start` | 106 | Create a generation job; returns `job_id` + `stream_url` immediately (202) and runs the pipeline in the background |
| GET | `/api/generate/stream/{job_id}` | 250 | Server-Sent Events (SSE) stream of the job's progress and final verdict |
| POST | `/api/generate/jobs/{job_id}/cancel` | 331 | Best-effort cancel of a running job |
| GET | `/api/generate/jobs` | 381 | List recent jobs (status table) |
| GET | `/api/generate/jobs/{job_id}/candidates` | 428 | N candidates considered, including rejected ones, once the job is `done` |

This router deliberately lives outside `api/routes.py` — "no new endpoints go into
`api/routes.py`" (generate_routes.py:11-12) — so don't look there for these.

## Step 1 — start a job

```bash
curl -sX POST http://localhost:8000/api/generate/start \
  -H 'Content-Type: application/json' \
  --cookie-jar /tmp/cj.txt --cookie /tmp/cj.txt \
  -d '{
        "brief": {
          "intent": "momentum tilt on large-cap tech, moderate risk",
          "risk_appetite": "moderate",
          "max_papers": 5
        },
        "n_candidates": 1
      }'
```

Request/response schema: `GenerateStartRequest` / `GenerateStartResponse` in
[`generate_schemas.py`](../../backend/archimedes/api/generate_schemas.py) (lines 27–86).
Notable fields:

- `brief.intent` — free text, required.
- `brief.risk_appetite` — one of `fixed_income | conservative | moderate | aggressive | hyper_risky` (generate_schemas.py:31).
- `brief.max_papers` — 1–20, default 5 (generate_schemas.py:34).
- `n_candidates` — 1–5, default 1 (generate_schemas.py:63). This is **not** the DSR
  multiple-testing count — see "Reading DSR/PBO honestly" below.
- `mode` — accepted for API compatibility but **ignored**: "the debate society is
  the sole generation pipeline" (generate_schemas.py:66-69, generate_routes.py's
  `_run_with_cleanup` never branches on it besides passing it through unused by
  the active pipeline).
- `model` — optional model id; gated server-side, see "Model gating" below.

Response is `202` with `{job_id, stream_url, ttl_seconds}` (generate_routes.py:218-222).
`ttl_seconds` is `EVENT_LOG_TTL = 900` (15 minutes) from
[`services/job_queue.py`](../../backend/archimedes/services/job_queue.py):26 — the
event log a reconnecting client can replay from expires that long after the job
reaches a terminal state.

## Step 2 — stream the verdict

```bash
curl -N http://localhost:8000/api/generate/stream/<job_id> \
  --cookie /tmp/cj.txt
```

This is a real `text/event-stream` response (`StreamingResponse`,
generate_routes.py:312-320), not a polling endpoint. Behavior worth knowing before
you write a client:

- **First byte is a comment**, not an event: `: stream opened\n\n` (generate_routes.py:271),
  sent immediately so `EventSource.onopen` fires fast. A spec-compliant SSE parser
  ignores `:`-prefixed lines; a hand-rolled line-splitter must not choke on them.
- **Heartbeats.** If no real event fires for 15s (`_HEARTBEAT_INTERVAL_SECONDS`,
  generate_routes.py:71), the server emits `: heartbeat\n\n` so intermediaries with
  a shorter idle-read timeout (CloudFront, corporate proxies) don't drop the
  connection while a long debate/backtest step is still computing
  (generate_routes.py:61-71, 302-305). Also invisible to a spec-compliant parser.
- **Resume with `Last-Event-ID`.** Each real event carries a numeric `id:` field;
  send it back as the `Last-Event-ID` header on reconnect and the server resumes
  after that cursor (generate_routes.py:264-266, 273).
- **Hard timeout.** One connection is capped at 300s (`_STREAM_TIMEOUT_SECONDS`,
  generate_routes.py:60); past that it sends `: stream timeout\n\n` and closes —
  reconnect with `Last-Event-ID` to keep tailing a still-running job.
- **Terminal events** are `done` and `error` (`_TERMINAL_EVENTS`, generate_routes.py:58);
  the stream closes right after either.

### Event names on the wire

The full `EventName` literal (generate_schemas.py:92-107):

```
job_queued, brief_validated, pipeline_selected, candidates_selected,
agent_iteration, tool_called, tool_result, candidate_drafted,
candidate_evaluated, best_selected, trace_hashed, persisted, done, error
```

Each SSE frame is built by `_format_sse` (generate_routes.py:323-328) as:

```
id: <int>
event: <one of the names above>
data: <json>

```

`data` is an arbitrary JSON object (`GenerateEvent.data: dict[str, Any]`,
generate_schemas.py:114-128) — the event name tells you how to interpret it; there
is no single fixed schema across all fourteen event types. The verdict itself
arrives inside the `done` event's payload (and via the separate
`/api/generate/jobs/{job_id}/candidates` and `/api/strategies/passports/{id}`
endpoints once the job is `done` — see `skills/strategy-passport/SKILL.md` for the
full passport field list).

## Auth requirements — as they actually are on `main` today

There is **no API key** and **no wallet requirement to generate**. Canonical
identity is a **Better Auth account session** (#1194): every generation
endpoint takes `user: CurrentUser = Depends(require_current_user)`
(generate_routes.py:27, 112) and returns **401** with no session. Wallets
are *linked to* an account, never a login by themselves.

**Getting a session, in short:** `POST /api/auth/sign-up/email
{name, email, password}` (password minimum 12 chars) → `POST
/api/auth/sign-in/email` → the response sets the session cookie; send it on
every subsequent call (`credentials: include` / curl `-b`). Verify with
`GET /api/auth/get-session`. `scripts/agent_journey.py` at the repo root is
the reference implementation of the full account → wallet-link → generate →
paper-deploy flow for an external agent — read it before hand-rolling your
own client.

**Job access is account-scoped.** The stream/jobs/candidates endpoints
resolve the session first, then check `_require_job_access(job, user.id,
job_id, linked_wallet)` (generate_routes.py:84, 261, 354): a job you don't
own returns **404, not 403** — a mismatched caller must not learn the job
exists.

**Where a wallet still matters** — two places, both *optional* until they
aren't:

- **Payment.** `GET /api/generate/quote` is public and always tells the
  truth about whether payment is required (generate_routes.py:96-103;
  contract spec: [`docs/specs/generation-quote-contract.md`](../../docs/specs/generation-quote-contract.md)).
  When the backend's payment flag is on, `POST /start` without a
  `Payment-Signature` returns **402 with the quote in `detail`**, and the
  signed payer must equal the account's **linked wallet**
  (generate_routes.py:132-154). Link a wallet via `POST
  /api/wallets/challenge` → sign the EIP-4361 message with your key → `POST
  /api/wallets/verify` — one round-trip, `cast`-signable.
- **Premium models.** See "Model gating" below — entitlement is checked
  against the linked wallet.

**Rate limiting and quotas**, stacked and fail-closed:

- `POST /api/generate/start` is capped at `5/minute` via slowapi
  (`@limiter.limit("5/minute")`, generate_routes.py:107).
- `enforce_generation_quota(request, user.id)` (generate_routes.py:124,
  [`services/generation_quota.py`](../../backend/archimedes/services/generation_quota.py):212)
  enforces **two stacked daily caps**: per-account
  (`GENERATION_DAILY_CAP_PER_USER`, default 10) and per-IP
  (`GENERATION_DAILY_CAP_PER_IP`, default 20), user bucket checked first,
  `<= 0` disables either (generation_quota.py:63-86). Hitting one returns
  **429**. Quota runs **before** the payment check — you cannot pay your
  way past the cap.

## Model gating (`model` field on the start request)

Passing a **premium** (Anthropic-on-Bedrock) model id requires entitlement on
the account's **linked wallet**, checked *before* the job is enqueued
(`enforce_model_entitlement`, generate_routes.py:156-162,
[`services/model_gate.py`](../../backend/archimedes/services/model_gate.py)):
a non-entitled caller gets **HTTP 402**, never a silent downgrade
(model_gate.py:18-20). A **free** model id is always allowed. Anything that
isn't on the free allowlist and wasn't entitled falls back to the env default
(generate_routes.py:164-174) — check `is_allowed_model` in
`services/llm_backend.py` for the current free-tier list.

## Reading DSR/PBO/holdout numbers honestly

Once a job is `done`, the winning strategy's verdict fields live on its passport
(`StrategyPassportRecord` / `/api/strategies/passports/{id}` — full field-by-field
guide in `skills/strategy-passport/SKILL.md`, read that skill before quoting a
number from this response to a user). The headline honesty traps specific to
*this* endpoint family:

1. **`n_candidates` on the request is not the DSR `num_trials`.** The Deflated
   Sharpe Ratio's multiple-testing correction uses `_society_num_trials(pool_size)`
   — the *search pool the winning candidate was actually selected from* —
   computed independently in
   [`agents/generation_pipeline.py`](../../backend/archimedes/agents/generation_pipeline.py):645
   and threaded through `_backtest_and_persist` (generation_pipeline.py:1520,
   1591-1599). It is deliberately **never** the size of the curated strategy
   library (generation_pipeline.py:334-335, "NEVER the curated library's count").
2. **Curated strategies are graded at `num_trials=1` — always.** For a
   hand-curated single-paper strategy (no generation search of ours produced
   it), the self-contained trial count is 1: it's judged purely on its own return
   series, not deflated by how many *other* strategies sit in the library
   ([`api/selection_bias_routes.py`](../../backend/archimedes/api/selection_bias_routes.py):313-321,
   the hardcoded `num_trials = 1` at line 321). This is a deliberate 2026-07-09
   decision (decouple #2) that **reverses** an earlier library-size-deflation
   scheme and needed Önder's sign-off precisely because it raises curated pass
   rates by removing a cross-strategy penalty (selection_bias_routes.py:319-320).
   A guard (`assert_self_contained_cohort_correlation`,
   `services/_rigor_helpers.py`:577) raises loudly if a future edit ever
   re-couples curated DSR to the library's cross-strategy correlation — so this
   invariant is enforced, not just documented. **When you report a curated
   strategy's DSR p-value, say "graded at num_trials=1 against its own series" —
   do not imply it was tested against the whole library.**
3. **`passes_rigor_gate` is strictness-level-dependent for the ladder, but the
   badge is always the strictest level.** `/api/selection-bias/gate` accepts a
   `strictness` query param (1 = strictest/badge … 5 = loosest,
   selection_bias_routes.py:186-200); the badge shown as `passes_rigor_gate` on a
   strategy object is *always* evaluated at the strictest level regardless of
   what a caller queries for (see `services/rigor_profiles.py` docstring, line 22).
   `min_passing_level` on `StrategyRigorResult` tells you the loosest level the
   strategy *would* pass at, if you need that nuance.
4. **`status: "live"` is not a rigor claim.** See `skills/strategy-passport/SKILL.md`
   ("passes_rigor_gate vs status") — this trap is common enough on this endpoint's
   output that it's worth flagging here too: a strategy's lifecycle `status`
   field means "active in at least one portfolio" (`models/strategy.py`:40), not
   "currently passes the gate." Read `passes_rigor_gate` /
   `rigor_gate_status` for the actual verdict, never `status`.
5. **CPCV is honestly reported as `NOT_RUN`, not silently absent.** The
   combinatorial-purged-CV criterion needs a rolling multi-split re-backtest
   pipeline that isn't wired into this route yet; rather than omit the field
   (which would look like a passing/neutral result), every strategy gets an
   explicit `NOT_RUN` status with a reason (selection_bias_routes.py:207-213).

## Verify (re-run these before trusting this document)

```bash
# Endpoint list + line numbers still match:
grep -n "@generate_router\.\(post\|get\)" backend/archimedes/api/generate_routes.py

# Generation is still account-session-gated with stacked daily caps:
grep -n "require_current_user\|enforce_generation_quota" backend/archimedes/api/generate_routes.py

# EVENT_LOG_TTL:
grep -n "EVENT_LOG_TTL = " backend/archimedes/services/job_queue.py

# The curated num_trials=1 caveat is still live and still guarded:
grep -n "num_trials = 1" backend/archimedes/api/selection_bias_routes.py
grep -n "num_trials.*!= 1" backend/archimedes/services/_rigor_helpers.py

# Reference client for the full account + wallet-link + stream flow:
sed -n '1,40p' scripts/agent_journey.py
```

## What this skill deliberately does not cover

- The `archimedes` CLI (`cli/`) does **not** wrap this API — it's a `0.0.1`
  name-reservation stub whose subcommands all exit `NOT_IMPLEMENTED`
  (`cli/src/archimedes_cli/cli.py`:3-5, 50). Don't reach for it; use `curl`/HTTPie
  or `scripts/agent_journey.py` directly against `/api/*`.
- Full passport field semantics — see `skills/strategy-passport/SKILL.md`.
- The x402 marketplace payment flow (a *separate* money seam, unrelated to
  generation) — see `skills/x402-payment/SKILL.md`.

---
name: verdict-api
description: How an agent (or a human, via curl/HTTPie) requests a strategy generation and reads its rigor verdict over Archimedes' HTTP API — the /api/generate/* endpoints, the SSE event shape, real auth requirements on main today, and how to read DSR/PBO/OOS numbers without over-claiming.
triggers:
  - calling POST /api/generate/start or GET /api/generate/stream
  - "how do I generate a strategy from the API"
  - "what does the SSE stream from Archimedes send"
  - reading a strategy's passes_rigor_gate / DSR / PBO / out_of_sample_sharpe fields
  - integrating an external agent against Archimedes' generation endpoint
  - "is REQUIRE_SIWE_FOR_GENERATION on, and generation auth questions in general"
---

> **⚠️ Auth model transition in flight (2026-08):** this skill documents `main`
> as of 2026-08-19, where generation auth is SIWE-based
> (`REQUIRE_SIWE_FOR_GENERATION`, secure-by-default). **PR #1194 replaces this
> with Better Auth accounts as canonical identity** (`require_current_user` +
> per-account/per-IP generation caps). When #1194 merges, update this skill's
> auth section before trusting it — the endpoints stay, the identity model
> changes.


# Requesting a strategy verdict over HTTP

This skill grounds every claim in the working tree. File:line citations refer to
`backend/archimedes/` at the commit this skill shipped with — re-run the greps in
"Verify" if the code has moved.

## The endpoint family

Defined in [`backend/archimedes/api/generate_routes.py`](../../backend/archimedes/api/generate_routes.py)
(module docstring, lines 1–13) and mounted at prefix `/api/generate`:

| Method | Path | Line | What it does |
|---|---|---|---|
| POST | `/api/generate/start` | 114 | Create a generation job; returns `job_id` + `stream_url` immediately (202) and runs the pipeline in the background |
| GET | `/api/generate/stream/{job_id}` | 227 | Server-Sent Events (SSE) stream of the job's progress and final verdict |
| POST | `/api/generate/jobs/{job_id}/cancel` | 315 | Best-effort cancel of a running job |
| GET | `/api/generate/jobs` | 371 | List recent jobs (status table) |
| GET | `/api/generate/jobs/{job_id}/candidates` | 418 | N candidates considered, including rejected ones, once the job is `done` |

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

Response is `202` with `{job_id, stream_url, ttl_seconds}` (generate_routes.py:197-201).
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
generate_routes.py:296-304), not a polling endpoint. Behavior worth knowing before
you write a client:

- **First byte is a comment**, not an event: `: stream opened\n\n` (generate_routes.py:255),
  sent immediately so `EventSource.onopen` fires fast. A spec-compliant SSE parser
  ignores `:`-prefixed lines; a hand-rolled line-splitter must not choke on them.
- **Heartbeats.** If no real event fires for 15s (`_HEARTBEAT_INTERVAL_SECONDS`,
  generate_routes.py:63), the server emits `: heartbeat\n\n` so intermediaries with
  a shorter idle-read timeout (CloudFront, corporate proxies) don't drop the
  connection while a long debate/backtest step is still computing
  (generate_routes.py:53-63, 280-289). Also invisible to a spec-compliant parser.
- **Resume with `Last-Event-ID`.** Each real event carries a numeric `id:` field;
  send it back as the `Last-Event-ID` header on reconnect and the server resumes
  after that cursor (generate_routes.py:248-250, 257).
- **Hard timeout.** One connection is capped at 300s (`_STREAM_TIMEOUT_SECONDS`,
  generate_routes.py:52); past that it sends `: stream timeout\n\n` and closes —
  reconnect with `Last-Event-ID` to keep tailing a still-running job.
- **Terminal events** are `done` and `error` (`_TERMINAL_EVENTS`, generate_routes.py:50);
  the stream closes right after either.

### Event names on the wire

The full `EventName` literal (generate_schemas.py:92-107):

```
job_queued, brief_validated, pipeline_selected, candidates_selected,
agent_iteration, tool_called, tool_result, candidate_drafted,
candidate_evaluated, best_selected, trace_hashed, persisted, done, error
```

Each SSE frame is built by `_format_sse` (generate_routes.py:307-312) as:

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

There is **no API key**. Authentication is a SIWE (EIP-4361) wallet-signature
session cookie, and whether it's *required* is a runtime flag:

- `_generation_auth_required()` in
  [`api/auth_siwe.py`](../../backend/archimedes/api/auth_siwe.py):189-210 — **secure
  by default**: unless `REQUIRE_SIWE_FOR_GENERATION` is explicitly set to a falsy
  value (`0|false|no|off`), generation requires a verified session. Set it to a
  truthy value to force the gate on explicitly; leave it unset for the default-on
  behavior. Under `TESTING=1` with the var unset, the gate is off (the hermetic
  suite exercises the open path) — an explicit true/false always overrides that
  carve-out.
- `gate_generation` (auth_siwe.py:213-225) is the FastAPI dependency actually
  wired onto `POST /api/generate/start` (generate_routes.py:120). When the gate is
  on it behaves like `require_verified_wallet` — **401** with no session. When
  explicitly off, it's best-effort: returns whatever wallet the session cookie
  carries (possibly `None`) without enforcing.
- The **stream/cancel/jobs/candidates** endpoints use a *different*, narrower
  dependency: `get_verified_wallet` (never raises on its own) plus an explicit
  `_require_job_auth` / `_require_job_access` check
  (generate_routes.py:76-111). When gating is on: an anonymous caller gets **401
  before any job lookup** (existence-oracle avoidance, generate_routes.py:81-83);
  a caller whose wallet doesn't own the job gets **404, not 403** — a mismatched
  caller must not be able to tell the job exists (generate_routes.py:94-98).
  Ownerless jobs (created while gating was off) stay readable by any
  *authenticated* caller.

**Getting a session, in short:** `GET /api/auth/nonce` → sign the returned SIWE
message with your key → `POST /api/auth/verify {message, signature}` → the
response sets an `httponly`+`secure`+`samesite=strict` cookie
(`api/auth_siwe.py`:231-464). A production `PUBLIC_DOMAIN`/chain-id mismatch fails
closed with 401/503 (auth_siwe.py:96-113, 320-333) — the message must be signed
for *this* domain and Arc chain id, not replayed from elsewhere. `scripts/agent_journey.py`
at the repo root is the reference implementation of this exact flow for an
external agent — read it before hand-rolling your own signer.

**Rate limiting**, independent of the auth gate:

- `POST /api/generate/start` is capped at `5/minute` via slowapi
  (`@limiter.limit("5/minute")`, generate_routes.py:115).
- A **wallet-less** caller (gate off, or gate on but genuinely anonymous — only
  possible when the gate is off) is additionally capped at a small **daily**
  quota per IP by `enforce_generation_quota`
  (`services/generation_quota.py`:1-40, called at generate_routes.py:131-132);
  a SIWE-authenticated caller bypasses this cap entirely. Hitting it returns 429
  with a "connect a wallet" steering payload — it's deliberately also a
  conversion prompt, not just a rate limit.

## Model gating (`model` field on the start request)

Passing a **premium** (Anthropic-on-Bedrock) model id requires wallet-connected
entitlement, checked *before* the job is enqueued
(`enforce_model_entitlement`, generate_routes.py:139,
[`services/model_gate.py`](../../backend/archimedes/services/model_gate.py)):
a non-entitled caller gets **HTTP 402**, never a silent downgrade
(model_gate.py:18-20). A **free** model id is always allowed. Anything that
isn't on the free allowlist and wasn't entitled falls back to the env default
(generate_routes.py:142-151) — check `is_allowed_model` in
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
   and threaded through `_backtest_and_persist` (generation_pipeline.py:1511,
   1582-1590). It is deliberately **never** the size of the curated strategy
   library (generation_pipeline.py:334-335, "NEVER the curated library's count").
2. **Curated strategies are graded at `num_trials=1` — always.** For a
   hand-curated single-paper strategy (no generation search of ours produced
   it), the self-contained trial count is 1: it's judged purely on its own return
   series, not deflated by how many *other* strategies sit in the library
   ([`api/selection_bias_routes.py`](../../backend/archimedes/api/selection_bias_routes.py):313-320,
   the hardcoded `num_trials = 1` at line 320). This is a deliberate 2026-07-09
   decision (decouple #2) that **reverses** an earlier library-size-deflation
   scheme and needed Önder's sign-off precisely because it raises curated pass
   rates by removing a cross-strategy penalty (selection_bias_routes.py:314-319).
   A guard (`assert_self_contained_cohort_correlation`,
   `services/_rigor_helpers.py`:615) raises loudly if a future edit ever
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

# Auth gate default is still secure-by-default:
grep -n "_generation_auth_required\|REQUIRE_SIWE_FOR_GENERATION" backend/archimedes/api/auth_siwe.py

# EVENT_LOG_TTL:
grep -n "EVENT_LOG_TTL = " backend/archimedes/services/job_queue.py

# The curated num_trials=1 caveat is still live and still guarded:
grep -n "num_trials = 1" backend/archimedes/api/selection_bias_routes.py
grep -n "num_trials.*!= 1" backend/archimedes/services/_rigor_helpers.py

# Reference client for the full SIWE + stream flow:
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

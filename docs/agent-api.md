# Agent API — driving the Archimedes journey programmatically

> Status: slice 1 (READ + GENERATE, no signing). Tracks [issue #788](https://github.com/a-apin/archimedes/issues/788).
> Slice 2 (agent-auth + programmatic signer for DEPLOY + MONITOR) is pending.

Archimedes ships one human interface: a passkey/wallet React SPA. AI agents — the
"new citizens" of the Agora thesis — can't drive a browser passkey, so today they
**can't use the product or convert**. This document is the agent-facing contract:
the exact `/api/*` calls that exercise the same journey a human does. The reference
client is [`scripts/agent_journey.py`](../scripts/agent_journey.py).

Two reasons this matters:
1. **Dogfooding** — running the journey as code surfaces the bugs and rough edges a
   human would hit, fast and repeatably.
2. **The agent-user segment** — an agent that sends a non-browser `User-Agent` is
   classified as an external agent by the telemetry middleware and its
   `generation_started` is attributed in the conversion funnel ([#787](https://github.com/a-apin/archimedes/issues/787)).

The **READ** and **GENERATE** paths need **no authentication**
(`REQUIRE_SIWE_FOR_GENERATION` is off), so slice 1 runs against prod today.

## Quick start

```bash
# read-only smoke (no LLM spend):
python scripts/agent_journey.py --base https://archimedes-arc.com --read-only

# full journey (starts a real generation — Amazon Nova Micro, sub-cent):
python scripts/agent_journey.py --base https://archimedes-arc.com \
  --intent "diversified low-volatility strategy for idle USDC" --risk moderate
```

The client identifies itself with an agent `User-Agent`, so its traffic shows up
as an external agent in `/api/metrics`.

## The journey

### 1. READ — public surfaces (no auth)

| Call | Returns |
| --- | --- |
| `GET /health` | `{ "status": "ok", ... }` — liveness |
| `GET /api/metrics` | `{ human_count, agent_count, total_requests, timestamp }` — cumulative human/agent **traffic** counters (#428). These are request tallies, **not users** — mostly crawlers. |
| `GET /api/metrics/funnel` | distinct-visitor conversion funnel (`landed → generation_started → wallet_connected → vault_deployed`) with `pct_of_landed` + `step_conversion` per stage (#787). Add `?day=YYYY-MM-DD` for one day. |
| `GET /api/strategies/` | the curated/example strategy library |

### 2. GENERATE — start + stream (no wallet)

```http
POST /api/generate/start
Content-Type: application/json

{
  "brief": {
    "intent": "diversified low-volatility strategy for idle USDC",
    "risk_appetite": "moderate",          // fixed_income | conservative | moderate | aggressive | hyper_risky
    "max_papers": 5                        // 1..20
  },
  "n_candidates": 1,                       // 1..5 considered internally (K=1 winner is emitted)
  "model": null                            // optional; allowlisted free model id, else env default. Premium → HTTP 402 without entitlement.
}
```

Response `202`:
```json
{ "job_id": "…", "stream_url": "/api/generate/stream/…", "ttl_seconds": 3600 }
```

Then tail the **SSE** stream:
```http
GET /api/generate/stream/{job_id}
```
Events (`event:` name + `data:` JSON), in rough order:
`job_queued → brief_validated → pipeline_selected → candidates_selected →
agent_iteration → tool_called → tool_result → candidate_drafted →
candidate_evaluated → best_selected → trace_hashed → persisted → done` (or `error`).

### 3. RIGOR — the externalized verdict + considered alternatives

```http
GET /api/generate/jobs/{job_id}/candidates
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
      "rigor_verdict": { "...": "DSR / PBO / walk-forward / look-ahead fields" },
      "passes_rigor": true,
      "selected": true,
      "regime": "neutral"
    }
  ]
}
```
This is the K=1-generation + externalized-rigor-gate shape from the architecture
principles: one winner (`selected: true`) plus the considered-and-rejected
alternatives, each carrying the rigor verdict the user reviews before deploy.

### 4. LIVE GATE — deployability (the SAME verdict a human sees)

The `candidates` payload above carries the *generation-time* rigor verdict. The
**authoritative deployability verdict** — the one the server-side `create_vault`
gate (#829) enforces and the human sees on the passport — is the **live rigor gate**
(#833, compute-on-read from the strategy's real persisted returns). Read it per
candidate from its persisted strategy:

```
GET /api/strategies/{strategy_id}
→ { "rigor_gate_status": "pass" | "fail" | "pending",
    "passes_rigor_gate": <bool>,          # true ⇒ deployable
    "sharpe_ratio": <float|null>,          # real backtest Sharpe (null while pending)
    "deflated_sharpe_ratio": <float|null>,
    "dsr_p_value": <float|null>,
    "out_of_sample_sharpe": <float|null>,
    "pbo_score": <float|null>, ... }
```

- **`pass`** — real returns exist and the live gate passed → `passes_rigor_gate: true`, deployable.
- **`fail`** — real returns exist and the gate failed ≥1 criterion → not deployable (honest).
- **`pending`** — no real persisted returns yet → the gate cannot run; not deployable.

> Fusion/debate candidates emit a DSL spec (`weights={}`) scored by
> `evaluate_fusion_spec`, so they skip the static buy-and-hold backtester. Until the
> #788/#818 fix, their real backtest returns weren't persisted → the live gate read
> `pending` forever and nothing was deployable. The fix persists those returns so a
> fusion/debate winner reads `pass`/`fail` like any other strategy. **Never weaken
> the gate to force a `pass` — `pending`/`fail` are honest, deployable-only-on-`pass`.**

## Slice 2 (pending) — DEPLOY + MONITOR

Deploying a vault and reading it back needs a **programmatic signer** (agents
can't do browser passkeys). The flow:
- **Agent-auth (not a passkey):** an agent-held EOA — a testnet dev key the agent
  controls, never a funded/mainnet key — signs the **SIWE** challenge from
  `GET /api/auth/nonce` and posts it to `POST /api/auth/verify`, which establishes
  the session cookie.
- **Deploy:** with that session, call the wallet-gated `POST /api/vaults/create`.
- **Monitor:** read the vault back via `GET /api/vaults/{address}/health`.

Tracked in [#788](https://github.com/a-apin/archimedes/issues/788). Do not land any
contract-touching agent work before the #588 contract-redeploy keystone.

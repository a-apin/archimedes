# Generation cost instrumentation

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-08-20

Every generation now carries a measurement record: how many tokens the debate
society actually consumed, how long each pipeline phase took in wall and CPU
seconds, the process peak RSS, and how many rows the run wrote. The record is
persisted on the job, **written durably to the database keyed to the strategy the
run produced**, and readable over the API and on the strategy passport.

This exists because the only figure ever quoted for a generation was a Bedrock
inference estimate — the language-model term alone, excluding the walk-forward
and CSCV backtests the debate society runs across every candidate. Issue #1217:
*"until this number exists, 'near-zero marginal cost' is an assumption wearing
the clothes of a finding."*

**This layer records counts and seconds. It does not price anything.** The
paywall quote seam (`generation_payment.quote()`, `GET /api/generate/quote`)
remains `pricing_model: "flat_v1"` and is untouched. Converting the counts into
dollars happens off-server against the current Bedrock and Fargate rate cards;
the point of the instrumentation is that the conversion stops being applied to
a guess.

## Where it lives

| Piece | File |
|---|---|
| The meter — accumulation, guards, snapshot shape | [`backend/archimedes/services/cost_meter.py`](../backend/archimedes/services/cost_meter.py) |
| Token capture at the provider boundary | [`backend/archimedes/services/llm_backend.py`](../backend/archimedes/services/llm_backend.py) (every `complete()`) — plus an inert call in [`backend/archimedes/agents/portfolio_agent.py`](../backend/archimedes/agents/portfolio_agent.py), see "What is not measured" below |
| Stage timing + write tallies | [`backend/archimedes/agents/generation_pipeline.py`](../backend/archimedes/agents/generation_pipeline.py), [`backend/archimedes/agents/debate_engine.py`](../backend/archimedes/agents/debate_engine.py) |
| Persistence onto the job (expires with `JOB_TTL`) | `JobStore.merge_result` in [`backend/archimedes/services/job_queue.py`](../backend/archimedes/services/job_queue.py) |
| Durable persistence (`generation_costs`) | [`backend/archimedes/models/generation_cost.py`](../backend/archimedes/models/generation_cost.py) + migration `e2b7f4c81d93`, written from `run_generation`'s `finally` |
| Read surfaces | `GET /api/generate/jobs/{job_id}/cost`, the `cost` field on `GET /api/generate/jobs`, `generation_cost` on `GET /api/strategies/{id}` and `GET /api/strategies/generated` |
| UI | passport card + library column via [`ui/src/generationCost.js`](../ui/src/generationCost.js) |
| Tests | [`backend/tests/test_generation_cost_meter.py`](../backend/tests/test_generation_cost_meter.py), [`backend/tests/test_generation_cost_persistence.py`](../backend/tests/test_generation_cost_persistence.py), [`ui/test/generation-cost.test.js`](../ui/test/generation-cost.test.js) |

The meter is bound to a `contextvars` context for the duration of one job, so
the LLM boundary records usage without threading a parameter through the debate
engine and the fusion proposer. `asyncio.to_thread` and `asyncio.create_task`
both copy the current context, so the society's parallel proposal and backtest
workers write into the same meter; mutations are lock-guarded.

### What is not measured

A recorder is a no-op when no meter is bound to the context. That is the right
behaviour outside a job, but it has a consequence worth stating plainly:
**instrumenting a code path does not by itself put that path in a job's
snapshot.** `run_generation` is the only thing that binds a meter.

One instrumented call is therefore **inert today**:
`propose_portfolio_with_tools()` in `portfolio_agent.py` carries a
`record_llm_call`, and its raw-SDK tool-use loop genuinely does bypass
`LLMBackend.complete()` — but that method is not on the generation pipeline's
call graph. Its only caller is `GET /api/strategies/advisor`, which never opens
a `cost_meter.measure()` scope, and the pipeline's two runners
(`_run_debate_leaderboard`, `_run_fixture_candidate`) both accept an `agent`
argument for signature parity and ignore it. So no generation reaches this loop,
and the call cannot contribute to any job's cost snapshot as the code stands.

It is kept rather than removed because it is correct the moment that changes: if
this loop is ever joined to a metered job, the coverage is already in place
instead of being discovered missing after the fact. It closes no gap today, and
the snapshots in this document do not include anything from it.

## Snapshot shape (`cost_v1`)

```json
{
  "schema": "cost_v1",
  "job_id": "9f2c…",
  "wall_seconds": 47.9312,
  "cpu_seconds": 31.4407,
  "cpu_attribution": "process_wide_delta",
  "peak_rss_bytes": 812939264,
  "rss_attribution": "process_high_water",
  "llm": {
    "calls": 17,
    "calls_missing_usage": 0,
    "usage_complete": true,
    "input_tokens": 41234,
    "output_tokens": 5120,
    "total_tokens": 46354,
    "by_model": {
      "amazon.nova-micro-v1:0": {
        "calls": 17, "calls_missing_usage": 0,
        "input_tokens": 41234, "output_tokens": 5120
      }
    }
  },
  "stages": {
    "brief_validation":      {"wall_seconds": 1.02,  "cpu_seconds": 0.01, "runs": 1},
    "pipeline_select":       {"wall_seconds": 0.31,  "cpu_seconds": 0.28, "runs": 1},
    "corpus_load":           {"wall_seconds": 2.44,  "cpu_seconds": 2.31, "runs": 1},
    "debate_propose":        {"wall_seconds": 12.80, "cpu_seconds": 1.10, "runs": 1},
    "debate_transcript":     {"wall_seconds": 6.02,  "cpu_seconds": 0.40, "runs": 1},
    "debate_backtest":       {"wall_seconds": 21.55, "cpu_seconds": 20.90,"runs": 1},
    "candidate_generation":  {"wall_seconds": 43.10, "cpu_seconds": 24.9, "runs": 1},
    "rigor_gate":            {"wall_seconds": 0.90,  "cpu_seconds": 0.88, "runs": 1},
    "persist_winner":        {"wall_seconds": 0.42,  "cpu_seconds": 0.05, "runs": 1},
    "backtest_persist":      {"wall_seconds": 2.10,  "cpu_seconds": 1.90, "runs": 1}
  },
  "writes": {
    "strategy_store": 1, "strategy_passports": 2,
    "backtest_results": 1, "strategy_proposals": 4
  },
  "meta": {
    "n_candidates_requested": 1, "model_requested": null,
    "candidates_considered": 4, "candidates_passing_rigor": 0,
    "pipeline": "debate", "served_model": "amazon.nova-micro-v1:0",
    "outcome": "done"
  }
}
```

### Reading it honestly

* **`candidate_generation` is the outer stage; `corpus_load` / `debate_propose` /
  `debate_transcript` / `debate_backtest` are its sub-phases.** They overlap by
  construction — do not sum all stages and expect `wall_seconds`.
* **`cpu_seconds` is a `time.process_time()` delta, which is process-wide.**
  Under concurrent traffic a stage's CPU figure includes work other requests did
  in the same worker. Labelled `cpu_attribution: "process_wide_delta"` rather
  than presented as isolated per-job CPU. For an isolated figure, measure a
  single generation on a quiet task.
* **`peak_rss_bytes` is a process high-water mark** (`ru_maxrss`, unit-corrected
  per platform), so it says what the worker has ever reached, not what this job
  caused. It is the number to size an ECS task from, not to attribute.
* **`writes` counts write operations the pipeline issued**, upserts counting
  once — not a measured row delta in Aurora.
* **`usage_complete: false` means at least one call's tokens were not readable.**
  Do not treat the totals as the run's full token consumption when this is false.
* **`meta.candidates_passing_rigor: 0` is the common case, not a failure to
  measure.** A run that fails the gate spends the same backtest compute; the
  snapshot is written on the error and cancelled paths too, so the rejected path
  is measurable rather than assumed to be cheaper.

## Where it is stored, and for how long (#1326)

The snapshot is written **twice, from the same object**, in `run_generation`'s
`finally` — so the two copies can never be two different readings:

1. **Onto the Redis job record** via `merge_result`. Expires with `JOB_TTL`
   (3600s). This is what `GET /api/generate/jobs/{job_id}/cost` serves.
2. **Into the `generation_costs` table**, keyed to the strategy the run
   produced. This is what the passport card and the library column read, an
   hour or a year later.

One row per `(job_id, strategy_id)` pair, with two payload columns:

| Column | What it holds |
|---|---|
| `measurement_json` | The literal `cost_v1` snapshot. Screened by `cost_meter.assert_measurement_only` on the way in — a pricing-shaped key at any depth raises `PricingLeakError` rather than landing in the column. |
| `quote_json` | The literal `generation_payment.quote()` payload in force when the job **started**. NULL when the seam could not be read, which means "not recorded", never "$0.00". |

**Two columns is the design, not normalization.** Quote-vs-measured has to be a
pairing of two independently recorded facts. Merging the quote into the snapshot
— the obvious shortcut — would produce a priced `cost_v1` record, and the screen
above exists to make that raise instead.

**The measurement is the JOB's.** Generation is K=1 (one `strategy_store` row per
job), so the pairing is one-to-one today. If a job ever persists more than one
strategy again, each gets a row carrying the *same* job-level measurement: read a
row as "the run that produced this strategy consumed this", never as "this
strategy's private share", and never sum across rows.

**Nothing is written when no strategy row exists.** A run that bails before
persistence — an invalid brief, an early crash — leaves the job record as the
only copy. There is nothing to key a durable record to, and inventing an id to
hang one on would be worse than the gap.

**No backfill, and none is possible.** Every generation before the meter was
never measured. Those strategies render as *not measured* on the passport and as
an em-dash in the library. That is the honest state, and manufacturing a figure
for them is precisely the failure this instrumentation exists to end.

### Reading the surfaced record honestly

`GET /api/strategies/{id}` serves `generation_cost` as
`{schema, job_id, recorded_at, measurement, quote}`, or `null`. The UI's rules
(all in `ui/src/generationCost.js`, all covered by `ui/test/generation-cost.test.js`):

* **`null` renders as "not measured", never as zero.** A count that arrives as a
  string, a boolean, `NaN`, `Infinity` or a negative is *not measured* either —
  the same refusal `_coerce_count` makes server-side.
* **A genuinely measured zero stays zero.** The fixture path makes no LLM calls
  and honestly reports `total_tokens: 0`; that must remain distinguishable from
  "we don't know".
* **`usage_complete: false` renders the totals with a `≥`.** They are a floor,
  not a total, and the card says how many calls were unreadable. An *absent*
  `usage_complete` fails closed to the same treatment — completeness is claimed
  only when the record literally says `true`.
* **The dominant stage excludes `candidate_generation`.** It is the umbrella that
  contains `corpus_load` / `debate_propose` / `debate_transcript` /
  `debate_backtest`, so a plain maximum names it every time and says nothing.
* **The library column shows total tokens.** Design call: it is the term that
  scales with the model and the one #1217 exists to pin down, while wall time is
  dominated by backtests and moves with whatever else the worker is doing. Wall
  time and the dominant stage ride in the cell's tooltip.
* **No `$`-conversion anywhere client-side.** The only money on the card is the
  price string the server recorded from the quote seam.

## Guarantees the code enforces

1. **A missing measurement is never a zero.** A provider response with no usable
   `usage` block increments `calls_missing_usage` and sets `usage_complete:
   false`; it does not bank a 0-token call. A half-readable block banks neither
   half. (`CLAUDE.md` § fail-soft — the correct degraded state is a loud, visible
   absence, never a plausible substitute.)
2. **An implausible count is refused, not accumulated.** Negative, `NaN`,
   infinite, stringly-typed, boolean, non-integral, or absurd (`>` 10M) counts
   are recorded as missing.
3. **No pricing math server-side.** Every caller-chosen label — stage name,
   write-counter name, meta key — is screened at write time, and a pricing-shaped
   one (`cost_usd`, `price_per_call`, `bedrock_spend`, …) raises
   `PricingLeakError`, whether or not a meter is bound. That write-time screening
   is the enforcement: the remaining keys in a snapshot are the meter's own fixed
   literals, so a caller label is the only route a pricing-shaped key could take
   into one. The test complements it from the other side by walking a
   representative snapshot's keys at every depth and asserting none contains
   `usd` / `dollar` / `price` / `cost` / `fee` / `revenue` — a check on one
   instance, not a proof over all of them; the screening is what makes it
   general. `quote()["pricing_model"]` stays `flat_v1`.
4. **The measurement never conjures a job.** `merge_result` refuses to write to a
   job id that does not exist, and undoes a write that raced an expiry —
   otherwise a bare `HSET` would materialise a TTL-less phantom hash that then
   lists forever as a statusless job.
5. **Instrumentation cannot fail a generation.** `record_llm_call` swallows
   unexpected errors, and both snapshot persists — the job record and the durable
   row — are suppressed-and-logged on failure. A deliberate pricing-label
   violation is the one exception: it raises, because it is a code bug with a
   fixed literal label, not a data-dependent condition.
6. **The durable write is deliberately untallied.** The pipeline does not call
   `cost_meter.record_write` for its own `generation_costs` row. The snapshot is
   already sealed by then — a tally cannot appear inside the document it counts —
   and the label is pricing vocabulary inside a measurement record, so the meter
   would refuse it anyway. The table's *name* is fine; a counter label inside a
   `cost_v1` snapshot is not.

## Measurement procedure

Both runs below are `n_candidates=1` and `n_candidates>1`, so the scaling in N is
observed rather than assumed. Run against a live LLM path — the fixture runner
makes no LLM calls and reports `llm.calls: 0`, which is honest and useless for
this purpose.

```bash
# 1. Start a generation (session cookie required; paywall per environment).
curl -s -X POST https://archimedes-arc.com/api/generate/start \
  -H 'content-type: application/json' -b "$COOKIES" \
  -d '{"brief":{"intent":"momentum on liquid majors","risk_appetite":"moderate"},"n_candidates":1}'
# → {"job_id": "...", "stream_url": "...", "ttl_seconds": 900}

# 2. Wait for a terminal state on the SSE stream (or poll /api/generate/jobs).
curl -s -N -b "$COOKIES" https://archimedes-arc.com/api/generate/stream/$JOB_ID

# 3. Read the measurement.
curl -s -b "$COOKIES" https://archimedes-arc.com/api/generate/jobs/$JOB_ID/cost | jq .

# 4. Repeat with "n_candidates": 5 and compare llm.total_tokens,
#    stages.debate_backtest.cpu_seconds, and writes.strategy_proposals.
```

Record at minimum: one `outcome: "done"` run that passed the gate, one that
failed it (`meta.candidates_passing_rigor: 0`), and one multi-candidate run. The
`meta` block makes each snapshot self-describing, so the three are comparable
after the fact.

### Task sizing, and what this does not measure

`peak_rss_bytes` and `cpu_seconds` come from inside the worker process. The
allocated task size — what Fargate actually bills — is the ECS task definition's
`cpu`/`memory`, and per-task utilisation is CloudWatch's `CpuUtilized` /
`MemoryUtilized`:

```bash
aws ecs describe-tasks --cluster <cluster> --tasks <task> \
  --query 'tasks[0].{cpu:cpu,memory:memory}'
```

Turning `cpu_seconds` + task size into a per-generation Fargate figure, and
`llm.total_tokens` + the model's rate card into a per-generation inference
figure, is deliberately left outside the server. Related:
[`bedrock-model-cost-comparison.md`](bedrock-model-cost-comparison.md) for the
rate card, and [`cost-estimates/generate-llm-costs.md`](cost-estimates/generate-llm-costs.md)
for the estimate this instrumentation exists to replace.

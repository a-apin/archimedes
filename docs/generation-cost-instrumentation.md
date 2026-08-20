# Generation cost instrumentation

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-08-20

Every generation now carries a measurement record: how many tokens the debate
society actually consumed, how long each pipeline phase took in wall and CPU
seconds, the process peak RSS, and how many rows the run wrote. The record is
persisted on the job and readable over the API.

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
| Token capture at the provider boundary | [`backend/archimedes/services/llm_backend.py`](../backend/archimedes/services/llm_backend.py) (every `complete()`), [`backend/archimedes/agents/portfolio_agent.py`](../backend/archimedes/agents/portfolio_agent.py) (raw-SDK tool-use loop) |
| Stage timing + write tallies | [`backend/archimedes/agents/generation_pipeline.py`](../backend/archimedes/agents/generation_pipeline.py), [`backend/archimedes/agents/debate_engine.py`](../backend/archimedes/agents/debate_engine.py) |
| Persistence onto the job | `JobStore.merge_result` in [`backend/archimedes/services/job_queue.py`](../backend/archimedes/services/job_queue.py) |
| Read surfaces | `GET /api/generate/jobs/{job_id}/cost`, and the `cost` field on `GET /api/generate/jobs` |
| Tests | [`backend/tests/test_generation_cost_meter.py`](../backend/tests/test_generation_cost_meter.py) |

The meter is bound to a `contextvars` context for the duration of one job, so
the LLM boundary records usage without threading a parameter through the debate
engine, the fusion proposer, and the portfolio agent. `asyncio.to_thread` and
`asyncio.create_task` both copy the current context, so the society's parallel
proposal and backtest workers write into the same meter; mutations are
lock-guarded.

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

## Guarantees the code enforces

1. **A missing measurement is never a zero.** A provider response with no usable
   `usage` block increments `calls_missing_usage` and sets `usage_complete:
   false`; it does not bank a 0-token call. A half-readable block banks neither
   half. (`CLAUDE.md` § fail-soft — the correct degraded state is a loud, visible
   absence, never a plausible substitute.)
2. **An implausible count is refused, not accumulated.** Negative, `NaN`,
   infinite, stringly-typed, boolean, non-integral, or absurd (`>` 10M) counts
   are recorded as missing.
3. **No pricing math server-side.** Stage names, write-counter names, and meta
   keys are screened; a pricing-shaped label (`cost_usd`, `price_per_call`,
   `bedrock_spend`, …) raises `PricingLeakError`. The snapshot carries no
   pricing-shaped key at any depth, and `quote()["pricing_model"]` stays
   `flat_v1`.
4. **The measurement never conjures a job.** `merge_result` refuses to write to a
   job id that does not exist, and undoes a write that raced an expiry —
   otherwise a bare `HSET` would materialise a TTL-less phantom hash that then
   lists forever as a statusless job.
5. **Instrumentation cannot fail a generation.** `record_llm_call` swallows
   unexpected errors, and the snapshot persist is suppressed on failure. A
   deliberate pricing-label violation is the one exception: it raises, because it
   is a code bug with a fixed literal label, not a data-dependent condition.

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

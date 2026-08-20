# Scale projection — cost and performance, ahead of MVP launch

> **status:** current (point-in-time)
> **owner:** Dan Browne
> **updated:** 2026-08-20
> **superseded-by:** —

**TL;DR.** At today's real usage (`real_users=4`, $262.67/mo AWS forecast), **99.9%+ of the
bill is a flat infrastructure floor that does not move with user count.** The only
genuinely usage-linear cost — Bedrock nova-micro tokens — is $0.06/month today and stays
under $150/month even at 1,000 active users on the stated profile below. **The thing that
actually changes with scale is not the bill, it's what breaks first**: the backend runs as
a *single* Fargate task (1 vCPU) with *no concurrency cap* on generation jobs, so 2+
concurrent Generate presses contend for the same core that's also serving the leaderboard,
auth, and every open SSE stream. And **SES production access was already requested and
denied** (case `178714790000802`) — the account is sandboxed today, which is a live
functional gap, not a future scaling risk.

Everything below is either read from AWS (Cost Explorer, Price List API, CloudWatch,
ECS/RDS/ElastiCache/SES describe calls, all 2026-08-20) or read from the code path that
spends the money (cited by file). Every number that is an *assumption* rather than a
measurement is labeled as one.

---

## 1. Cost model — drivers and assumptions

### 1.1 Generation (the debate society, `backend/archimedes/agents/debate_engine.py`)

The generation pipeline is the debate society (`docs/adr/debate-society-sole-generation-pipeline.md`)
— there is no other path. One `POST /api/generate/start` call does, in order
(`generation_pipeline.py` → `debate_engine._run_debate_leaderboard`):

| Phase | LLM calls | What it costs |
|---|---:|---|
| Brief validation (`_validate_brief`) | 1 | small classification call |
| Proposer pool (`_propose_pool`, one call per steer) | `_pool_max()` — **prod default 10** (`DEBATE_POOL_MAX` is **unset** in the live task def → falls back to the code default of 10, clamped to [2,24] of an 18-steer regime×mechanism grid) | the dominant token cost — each call carries corpus-grounded evidence (paper abstracts) |
| Debate transcript (`_debate_round`) | 4 — bull round 1, bear round 1, bull round 2 (rebuttal), bear round 2 (rebuttal) | fixed, independent of pool size |
| Critics (`_critic_prov`, `_critic_rigor`, `_critic_regime`) | **0** — all deterministic Python | $0 in tokens; this is the design's stated budget trick |
| Synthesizer (`build_leaderboard`) | **0** — deterministic ranking, not an LLM call in the shipped code (the design doc's "0 or 1 LLM call" collapsed to always-0 in `debate_engine.py`) | $0 |

**→ 15 nova-micro calls per generation** (1 + 10 + 4), confirmed by reading the live ECS
task definition's environment (`LLM_PROVIDER=bedrock_converse`, `LLM_BEDROCK_MODEL=amazon.nova-micro-v1:0`,
no `DEBATE_POOL_MAX` override).

**Rate card** (AWS Price List API, `us-east-1`, live-verified): Nova Micro = **$0.035 /
1M input, $0.140 / 1M output**.

**Real measured tokens** (Cost Explorer, `USE1-NovaMicro-{input,output}-tokens`, July 2026
— a full month at `real_users≈4`): **819,166 input / 190,668 output tokens**, billing
**$0.0554** for the month. Cross-checked against CloudWatch app + nginx logs: only **2–3**
`POST /api/generate/start` calls landed in that same 30-day window. That puts real observed
cost at **≈$0.018–0.028 per generation** — this repo's *first actual measured figure* for
what §generation-cost-instrumentation.md calls "the number nobody had." (`docs/generation-cost-instrumentation.md`
ships the raw per-job measurement layer — `cost_meter.py` — but nothing has queried its
`generation_costs` table in bulk yet; this doc's per-generation figure comes from the
account-level Bedrock usage total divided by the observed call count, not from that table.)

**Assumption used below:** **$0.03/generation** (rounds the $0.018–0.028 observed range up
for margin — new corpus content, longer briefs, or a `DEBATE_POOL_MAX` bump would raise it).

**Fargate CPU, not tokens, is the real compute cost of a generation.** The shipped example
snapshot in `docs/generation-cost-instrumentation.md` (`cost_v1` schema) shows
`cpu_seconds: 31.44` against `wall_seconds: 47.93` for one job — i.e. a single generation
holds the task's **one vCPU ~65% busy for ~48 seconds**, and the `debate_backtest` stage
(real `backtrader` backtests over all 10 pooled candidates, not the LLM calls) is the
stated dominant term. At the live Fargate on-demand rate (**$0.04048/vCPU-hr**, AWS
published `us-east-1` Linux/x86 rate — the Price List API's `AmazonECS` product list did
not return a page containing the Fargate compute SKU on this pull, so this figure is the
public rate card, not a fresh API read), ~31 CPU-seconds costs **≈$0.0003** — negligible in
dollars. The risk this creates is *latency/concurrency*, not spend — see §4.

**yfinance during a generation:** `debate_engine._critic_rigor` fetches real 2-year daily
history for the pooled candidates' asset universe **once per generation, cached by ticker
set** — the code's own comment: *"the fetch is cached per ticker-set, so a pool sharing a
universe costs one yfinance round-trip, not N"* (`debate_engine.py:387-388`). Confirmed live:
`ARCHIMEDES_FUSION_REAL_DATA` is unset in the task def → defaults to `"1"` (on), so this is
a real fetch in prod, not the synthetic fallback. **Marginal cost: $0** (yfinance has no
metered API charge); the risk is reliability, not dollars — see §4.5.

### 1.2 Paper trading — the daily advance loop (`backend/archimedes/chain/agent_runner.py`)

Runs as its own always-on process on the `archimedes-runner` EC2 box (`t3.small`, **not**
Fargate — its cost is already inside the flat EC2 floor, §2), one tick every
`AGENT_INTERVAL_SECONDS` (**default 300s**, unoverridden in prod). Each tick:

1. Fetches signals for the curated + generated strategy pool once.
2. Loops **sequentially** — `for vault_addr in vaults:` (`agent_runner.py:543`, plain `for`,
   no `asyncio.gather`) — over every managed vault: scope its strategies, aggregate
   signals, construct target weights, then (non-dry-run) commit/reveal on-chain.

**Marginal $ cost of one more paper-trading deployment ≈ $0.** Price data is fetched once
per tick and cached (`strategy_signal_evaluator.py`'s module-level dict cache, TTL 600s) —
adding vaults does not multiply yfinance calls, since they all read the same cached
universe. The real cost of more deployments is **time inside the sequential loop**, which
is a latency/saturation risk, not a spend risk — see §4.3.

### 1.3 Signals / leaderboard / passport reads (Aurora + Redis)

`GET /api/leaderboard` (`backend/archimedes/api/leaderboard_routes.py`) opens a fresh
`get_session()` and queries Aurora on **every request — there is no Redis cache-aside on
this path** (confirmed by reading the full route body; the only Redis use nearby is for job
queues, quota buckets, and funnel/visitor tracking, not for read-serving). Cost is Aurora
Serverless v2 ACU-seconds — see §2 for why this is mostly absorbed by the always-billed
floor until read QPS pushes the cluster above its 0.5 ACU minimum.

### 1.4 Auth / email (SES)

`auth/mailer.js` sends real mail via `SESv2Client` in prod (`EMAIL_MAILER=ses` in the live
task def). **Live SES account state, read 2026-08-20:**

| Field | Value |
|---|---|
| `ProductionAccessEnabled` | **false** |
| Last review (`ReviewDetails`) | **`Status: DENIED`**, case `178714790000802` |
| `Max24HourSend` (sandbox) | 200 |
| `MaxSendRate` | 1/sec |
| Verified identities | `archimedes-arc.com` (sending **domain**; sandbox restricts by **recipient** verification, which this does not grant) |

**This is a live gap, not a future one.** SES sandbox mode only delivers to individually
verified recipient addresses (plus AWS's mailbox simulator) — a real user's inbox is not a
verified recipient, so every verification/reset email SESv2 attempts to send them fails
today. It has not visibly broken anything yet only because `EMAIL_VERIFICATION_ENFORCED=false`
in the live task def — signup does not currently gate on the email actually arriving. Cost
itself is trivial ($0.10/1,000 emails) at any scale considered here; the constraint is
**functional (200/day cap + recipient-verification block), not financial.** See §5.

### 1.5 Corpus (static)

10,000 papers + abstracts, lexical retrieval (no embeddings — `paper-corpus-empty.md`).
Reads are a keyword-hit-count rank over an in-memory/DB-loaded corpus
(`strategy_fusion.select_candidates`) — no external API, no per-read metered cost. Storage
is a few thousand rows; effectively **$0 marginal, flat**.

---

## 2. What the current $262.67/mo actually is (Cost Explorer, July 2026 full month, `real_users≈4`)

| Service | $/mo (July, actual) | Shape |
|---|---:|---|
| Amazon RDS (Aurora Serverless v2, min 0.5 ACU) | $47.57 | **flat floor**, tapers to step |
| Amazon ECS (1 Fargate task, 1024 CPU / 3072 MB) | $41.59 | **flat** at desired=1, **step** on scale-out |
| EC2 – Compute (2× NAT `t4g.nano` + 1× runner `t3.small`, all always-on) | $39.14 | **flat** |
| EC2 – Other (EBS + data transfer) | $28.61 | **flat + thin linear tail** (egress) |
| CloudWatch (logs, alarms, Container Insights) | $18.96 | **flat + linear tail** (log volume) |
| VPC (interface endpoints) | $18.61 | **flat** |
| ALB | $16.76 | **flat LCU floor + linear** (requests/bytes) |
| WAF (web ACL + 4 managed rule groups) | $16.20 | **flat** (+ negligible per-request fee) |
| ElastiCache (1× `cache.t3.micro` Redis) | $12.65 | **flat**, step on node resize |
| Amazon Bedrock (nova-micro) | **$0.06** | **linear** — the only real usage-driven line |
| Everything else (Route53, S3, CloudFront, Cost Explorer) | <$1 | flat |
| **Total** | **$241.41** (Aug/Sep forecast: $262.67 / $262.52) | |

**The headline: Bedrock is 0.02% of the bill.** Going from 4 → 1,000 users does not scale
this floor smoothly — it eventually trips a small number of **step functions** (a second
Fargate task, Aurora ACU climbing off its floor, a Redis node resize), while every genuinely
linear-with-usage term (tokens, SES sends, egress bytes) stays small in absolute dollars
even at the top of the range modeled here.

Real rate-card confirmations pulled from the AWS Price List API today: Aurora PostgreSQL
Serverless v2 = **$0.12/ACU-hr** (0.5 ACU × 730h × $0.12 = $43.80 — matches the $47.57
actual within backup/IO variance); Redis `cache.t3.micro` = **$0.017/node-hr** ($12.41/mo —
matches the $12.65 actual).

---

## 3. Projection at 10 / 100 / 1,000 active users

**Stated usage profile per active user/month — assumptions, not measurements** (today's
real behavior is far below this: 2–3 generations total across 4 users in 30 days). This
profile represents an *engaged, post-launch* user, deliberately generous so the projection
is a ceiling, not a best case:

- **4 generations/user/month** (§1.1: 15 nova-micro calls each, $0.03 marginal)
- **0.3 new paper-trading deployments/user/month** (most users browse; a minority deploy)
- **~40 leaderboard/signal/passport page reads/user/month**
- **1 signup email/new user/month** (SES — see the sandbox caveat, §1.4)

| | 10 users | 100 users | 1,000 users |
|---|---:|---:|---:|
| Generations/mo (4/user) | 40 | 400 | 4,000 |
| **Bedrock (LLM)** | $1.20 | $12 | $120 |
| New paper deployments/mo (0.3/user) | 3 | 30 | 300 |
| Signup emails/mo | 10 | 100 | 1,000 |
| Infra floor (§2's $241–263) | **~$260** (unchanged — today's number) | **~$260–280** (occasional 2nd Fargate task during bursts; Aurora still near its floor) | **~$260–450** (see step-function note below) |
| **Projected total** | **≈$262** | **≈$275–300** | **≈$500–750** |

**Why 1,000 users is a range, not a point:** this is exactly where the step functions in §2
start tripping, and *when* depends on burstiness (10 generations spread evenly vs. 10 in
one demo hour), which this doc cannot observe from 4-user data. The components:

- **Bedrock:** $120/mo — precise, linear, small in absolute terms even here.
- **Fargate:** the autoscaling policy (§4.2) already targets 60% CPU with min 1 / max 4
  tasks. 4,000 generations/month is ~133/day; each holds the vCPU busy ~48s, so on its own
  that's nowhere near saturating a task — but demo-day clustering could push a 2nd (or 3rd)
  task up for hours at a time. Modeled range: **+$0 to +$120/mo** (0 to 3 extra tasks'
  worth of the $41.59 baseline, prorated).
- **Aurora:** 1,000 users' leaderboard/signal/passport reads (all uncached, §1.3) plus
  generation-driven writes will very plausibly push the cluster's average ACU above its 0.5
  floor. Modeled range at an assumed 1–3 ACU average: **$88–263/mo** (vs. $47.57 today) —
  this is the single largest source of uncertainty in the 1,000-user number, and the
  reason to actually watch the ACU alarm (§4.4) rather than trust this range.
- **Redis:** likely still fine on `t3.micro` at 1,000 users' session/cache/quota-bucket
  load, but this is the item most likely to need a reactive resize — modeled at $0 extra
  (watch the eviction alarm, §4.6).
- **SES:** cost itself stays under $1/mo even at 1,000 sends — but functionally, this is
  the scale at which the sandbox's 200/day cap and recipient-verification block stop being
  a curiosity and start being a launch-blocking wall on day one of any real growth. See §5.

---

## 4. Performance watchlist — saturation order

Ordered by how *little* traffic it takes to hit each one, read from the code and the live
AWS config, not guessed.

### 4.1 SES sandbox — already broken, not a future risk

**Symptom:** users report verification or password-reset emails never arriving; Dan sees
signups with no follow-through. **Why:** `ProductionAccessEnabled: false`, prior request
`DENIED` (case `178714790000802`); sandbox delivers only to individually verified
recipients. **Leading indicator:** none is wired today — there's no SES event destination
or CloudWatch metric on send/bounce/reject, which is itself a gap worth closing. Until then,
the leading indicator is a support message. **Pre-arranged fix:** file a fresh SES
production-access request now (typically a 1–3 business-day AWS review) — a dial to turn,
not a redesign. Do this *before* day-1, not after the first "I never got my email" report.

### 4.2 Single Fargate task's shared vCPU — first thing a real traffic spike hits

**Symptom:** during a demo or any moment with 2+ concurrent Generate presses, the
leaderboard, auth, and every open SSE stream visibly slow down at the same time. **Why:**
the live task definition packs `backend` + `auth` + `nginx` into **one** Fargate task
(`cpu=1024`, i.e. 1 vCPU, shared unpartitioned across the three containers — no per-container
CPU reservation is set). `generate_routes.py`'s `/start` handler has **no semaphore or
concurrency cap** — every accepted job gets its own `asyncio.create_task`, and each holds
that single vCPU ~65% busy for ~48 seconds (§1.1). Two concurrent generations approach 100%
CPU contention on the one core serving everything else. **Leading indicator:**
`AWS/ECS ServiceAverageCPUUtilization` for `archimedes-cluster/archimedes-backend` — the
existing target-tracking alarms already fire at 60% (scale-out) / 54% (scale-in, currently
in `ALARM` state, i.e. below threshold — expected at today's near-zero load). **Pre-arranged
fix:** the autoscaling policy already exists (target 60% CPU, min 1 / max 4 tasks,
60s/300s cooldowns) — this is a scale-out that already happens automatically. The one dial
worth turning *before* a known spike (a demo, a YC moment, a press mention): temporarily set
`MinCapacity` to 2 so scale-out isn't reactive-and-late — a cold Fargate task takes 30–90s
to become healthy, which is most of the window a demo-day burst would need it.

### 4.3 `agent_runner`'s sequential per-vault tick — the mid-term watch item

**Symptom:** deployed paper-trading strategies stop reflecting current prices/allocations
in a timely way; rebalances lag behind the market. **Why:** one process on one `t3.small`
EC2 box, one 5-minute tick (`AGENT_INTERVAL_SECONDS=300`), and inside each tick a **plain
sequential `for vault_addr in vaults:` loop** (`agent_runner.py:543`) — no `asyncio.gather`,
no batching. Price data is shared/cached across vaults (§1.2), so this scales with **vault
count × (on-chain commit/reveal latency per vault)**, not with yfinance load. At the
volumes in §3 (≤300 new deployments/month even at 1,000 users) this is unlikely to trip,
but it's the first place raw deployment *count* — as opposed to generation *rate* — becomes
a latency risk rather than a dollar one. **Leading indicator:** no CloudWatch metric times a
full tick today; the closest existing signal is the `archimedes-chain-disconnected` alarm.
Until a real tick-duration metric exists, watch wall-clock gaps between consecutive
`"[tick %s]"` log lines in `/archimedes/runners`. **Pre-arranged fix:** `REVEAL_RECONCILE_MAX_PER_TICK`
(default 5) already caps how much reconciliation work one tick absorbs; if the vault loop
itself becomes the bottleneck, that's a genuine code change (parallelizing the loop), not a
knob — flagged here as the one item on this list that isn't purely dial-turning.

### 4.4 Aurora Serverless v2 under uncached leaderboard/signal reads

**Symptom:** leaderboard/passport pages slow to load under concurrent visitor traffic.
**Why:** `GET /api/leaderboard` has no Redis cache (§1.3) — every page view is a live Aurora
query. The backend's SQLAlchemy pool is `pool_size=5, max_overflow=10` per worker
(`db.py:108`), and the container runs a **single** uvicorn process (no `--workers` flag in
`Dockerfile`) — so **15 concurrent DB operations is the hard ceiling per Fargate task**
before requests queue on the pool. Even at 4 tasks (§4.2's max), that's 60 possible
connections, still under Aurora's own alarm threshold. **Leading indicator:** two alarms
already wired — `archimedes-aurora-connections-high` / `-pct-high` (threshold 80) and
`archimedes-aurora-acu-at-max` (threshold 15.5 of max 16). **Pre-arranged fix:** Aurora
Serverless v2 already auto-scales 0.5→16 ACU — this is the intended dial. If the SQLAlchemy
pool binds first (unlikely before Aurora does, per the connection math above), it's a
one-line config bump, not a redesign.

### 4.5 yfinance fragility — a reliability risk, not a scale risk

**Symptom:** a generation's `debate_backtest` stage silently drops a candidate
(`logger.info("debate C-rigor: dropped a candidate on backtest error…")`), or an oracle
price update fails-closed on a delisted/renamed ticker — already known and already logged
(per team memory: "the runner already logs delisted-ticker errors"). **Why:** Yahoo Finance
has no rate-limit SLA and intermittently blocks or delists tickers; this is orthogonal to
Archimedes' own traffic. **Mitigations already in code:** a 12-hour Postgres read-through
cache (`asset_daily_bars`, `market_data_provider.py`), a 10-minute in-process cache in
`strategy_signal_evaluator.py`, and the one-fetch-per-generation sharing in §1.1 — together
these mean the fetch rate is bounded by **(universe size) / (cache TTL)**, essentially flat
regardless of user count. **Leading indicator:** none wrapped in CloudWatch today — this is
a log-only signal. **Pre-arranged fix:** none needed for the 10–1,000 user range modeled
here (it doesn't get worse with more users); worth a CloudWatch Logs metric filter on
`"backtest error"` / `"delisted"` if it needs to become watchable rather than just logged.

### 4.6 Redis single node — the quiet one

**Symptom:** SSE event delivery or the generation daily-cap buckets start behaving oddly
under memory pressure. **Why:** one `cache.t3.micro` node, no cluster mode, shared by the
job-queue/SSE event log, the generation quota buckets (§1.1's daily caps), and
funnel/visitor tracking. **Leading indicator:** already alarmed —
`archimedes-redis-evictions` (threshold 100). **Pre-arranged fix:** a node-class bump
(`t3.micro` → `t3.small`/`medium`) is a single `ModifyCacheCluster` call — no redesign.

---

## 5. Day-1 launch checklist — the dials to pre-set or watch

1. **File a fresh SES production-access request now.** The prior one was denied
   (case `178714790000802`); until approved, treat every verification/reset email to a real
   user as non-functional, not "probably fine." §4.1 — this is the highest-priority item
   because it's already true today, not a threshold to watch for.
2. **Pre-set Fargate `MinCapacity` to 2 before any known traffic spike** (demo, launch
   announcement, press). The 60%-CPU target-tracking policy already exists (min 1 / max 4)
   — this just removes the 30–90s cold-start lag from the first spike's critical path.
   §4.2.
3. **Watch, don't touch, the two Aurora alarms**: `archimedes-aurora-acu-at-max` (15.5 of
   16 ACU) and `archimedes-aurora-connections-high`/`-pct-high` (80 connections). These are
   the two numbers that say "the database, not the app, is now the bottleneck." §4.4.
4. **Leave `GENERATION_DAILY_CAP_PER_USER=10` / `GENERATION_DAILY_CAP_PER_IP=20` as-is.**
   Already the right shape of anti-abuse dial for a launch. Note in passing:
   `GENERATION_PAYMENT_REQUIRED=false` today, so Dan is subsidizing 100% of generation cost
   — per §3, that stays trivial (≤$120/mo) even at 1,000 users on the stated profile, so
   there's no cost pressure to flip it before launch, only an abuse-pressure one.
5. **Watch the WAF rate-limit rule (1,000 req/5min/IP) and its blocked-request alarm
   (threshold 6,000).** Already wired (`archimedes-waf-blocked-spike`); this is the number
   that tells launch-day whether a traffic spike is real users or a bot/scraper wave.

---

## Sources

All AWS reads performed 2026-08-20 under `AWS_PROFILE=ArchimedesDanAdmin`, account
`037613907429` / `us-east-1`, read-only (no resources modified): Cost Explorer
(`get-cost-and-usage`, `get-cost-forecast`, by `SERVICE` and by `USAGE_TYPE` filtered to
Amazon Bedrock), Price List API (`get-products` for `AmazonRDS`, `AmazonElastiCache`),
`ecs describe-services` / `describe-task-definition` (task def `archimedes-backend:80`),
`application-autoscaling describe-scalable-targets` / `describe-scaling-policies`,
`rds describe-db-clusters` / `describe-db-instances`, `elasticache describe-cache-clusters`,
`ec2 describe-instances` / `describe-nat-gateways`, `elbv2 describe-load-balancers` /
`describe-load-balancer-attributes`, `wafv2 get-web-acl`, `cloudwatch describe-alarms`,
`sesv2 get-account` / `list-email-identities`, `logs start-query` / `get-query-results`
against `/archimedes/app` and `/archimedes/nginx`.

Code read (this repo, 2026-08-20): `backend/archimedes/agents/debate_engine.py`,
`backend/archimedes/agents/generation_pipeline.py`,
`backend/archimedes/api/generate_routes.py`, `backend/archimedes/services/llm_backend.py`,
`backend/archimedes/services/cost_meter.py`, `backend/archimedes/services/generation_quota.py`,
`backend/archimedes/chain/agent_runner.py`, `backend/archimedes/services/market_data_provider.py`,
`backend/archimedes/services/strategy_signal_evaluator.py`,
`backend/archimedes/api/leaderboard_routes.py`, `backend/archimedes/db.py`, `auth/mailer.js`,
`backend/Dockerfile`, plus `docs/generation-cost-instrumentation.md`,
`docs/cost-estimates/generate-llm-costs.md` (superseded by the debate-society cutover — kept
as history, not current), `docs/bedrock-model-cost-comparison.md`,
`docs/adr/debate-society-sole-generation-pipeline.md`, `docs/adr/k1-generation-external-rigor-gate.md`.

**Note on `docs/cost-estimates/generate-llm-costs.md`:** that doc's per-generation figures
(~$0.076–$0.150) describe the pre-cutover `portfolio_agent` multi-turn tool-use pipeline on
Sonnet-class pricing. The debate society replaced that path in the T1.1 Phase-3 cutover
(2026-07-14) and runs on nova-micro at ~1/85th the per-token rate with a different call
shape entirely — that doc is retained for history but should not be quoted as current.

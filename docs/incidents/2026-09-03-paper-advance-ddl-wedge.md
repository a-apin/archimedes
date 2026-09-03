# Incident 2026-09-03 — paper-advance DDL wedge

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-09-03
> **superseded-by:** —

Tracked on [#1818](https://github.com/aprin-labs/archimedes/issues/1818). The timeline and
mechanism below are copied from that issue verbatim — they are evidence, and the timestamps
are the only record of what the fleet was doing while `/health` said 200.

## Summary

Production was unreachable for the first user of the day (Dan) from **13:29Z to 15:03Z on
2026-09-03** (ALB 504s, both targets unhealthy). Root cause: the paper-advance child runs
`init_db()` (hand-rolled `ALTER TABLE … ADD COLUMN IF NOT EXISTS` schema patches) **on every
cycle, in both ECS tasks at once**. At the 03:32Z cycle the two children interleaved so that
one child's patch transaction queued an `AccessExclusiveLock` on `papers` behind the other
child's open cycle transaction; the ticking child then needed `papers` from a second session
and stalled behind its own sibling. PostgreSQL cannot see that cycle (the blocker is not
waiting), so the fleet sat wedged for 10 hours while `/health` kept returning 200 from
`stale_cached` probes. The first real page load hit the same lock queue and froze both web
processes; the replacement tasks wedged at boot on the same DDL (`init_db()` at import,
`main.py:800`) for 91 minutes. The wedge broke only when one old task was OOM-killed.

Not CPU starvation: ECS CPU was ~3% and Aurora CPU ~4% for the whole 11.5 h. This is a DDL
lock chain.

## Timeline (UTC, 2026-09-03; app log `/archimedes/app`, ECS events, Aurora log, CloudWatch)

| time | evidence |
|---|---|
| 03:31:19 | task 9b58b8b9's child: `init_db: papers schema patches applied` → takes the fleet lock → replay running (dsl_to_backtrader warnings 03:31:59–03:32:33) |
| 03:32:08–03:32:19 | FIRST `HEALTH_PROBE_TIMEOUT` on **both** tasks (`corpus`, `corpus_db`, `corpus_meta`, `paper_rag`, then every probe); they stay `state=stale_cached` until the tasks die (cached_age reaches 35,797 s) |
| 03:31–03:33 | Aurora `DatabaseConnections` 16 → 33 and flat at 33 for 11.5 h; CPU ~4% (idle = waiting, not working) |
| 03:32:33 | last line ever from the ticking child (no `paper advance: {...}` summary, no `skipping this cycle` line from the sibling) |
| 03:32→13:28 | `/health` answers 200 in ~1.05 s (= the probe budget) every 10–30 s on both tasks; ECS + ALB stay green |
| 13:28:47–13:28:50 | first user requests of the day (`/api/features`, `whoami`, `/health`, then `GET /api/strategies/generated`) — one per task |
| 13:28:50→15:02:58 | `GET /api/strategies/generated` on acc8e19e takes **5,648,772 ms**; the sibling task logs nothing at all after 13:28:48 |
| 13:29–13:33 | ALB: `UnHealthyHostCount=2`, `HealthyHostCount=0`, `HTTPCode_ELB_504_Count` 2/1/1 per minute, `TargetResponseTime` max 30 s (the health-check timeout) |
| 13:30:32 | ECS: "replaced 2 tasks due to an unhealthy status" → tasks c0c07250 + e6ce460d created 13:30:31, images pulled by 13:31:25 |
| 13:31:20 | new tasks' `auth` containers listening; their **backend containers log nothing until 15:02:58** (91 min) — `init_db()` at import blocked on the same lock queue; Aurora connections 33 → 35 |
| 13:31→15:01 | ECS `MemoryUtilization` (max) ramps linearly 39% → 100% on the old tasks (mechanism not established; see open questions) |
| 15:02:52–15:02:57 | Aurora log: 16 connections from 10.0.11.39 (task 9b58b8b9) reset "with an open transaction" — ECS later records its backend container as `OutOfMemoryError: container killed due to memory usage`, exit 137 |
| 15:02:58 | Aurora log: **`ERROR: deadlock detected`** — Process 40057 (`SELECT … FROM papers WHERE papers.arxiv_id IN (…)`) waits for AccessShareLock on `papers`, blocked by process 76751 (`ALTER TABLE strategy_store ADD COLUMN IF NOT EXISTS is_example BOOLEAN NOT NULL DEFAULT FALSE`), which waits for AccessExclusiveLock on `strategy_store`, blocked by 40057. PostgreSQL kills 40057. |
| 15:02:58.689 | acc8e19e: `slow request: GET /api/strategies/generated status=200 duration_ms=5648772.0` (the victim handler returns) |
| 15:02:58.736 | new tasks: `Started server process [1]` — boot unblocked the same second |
| 15:03:02 | acc8e19e: aiohttp/web3 `TimeoutError` traceback → `Fatal Python error: Aborted` → container exit 139 (SIGSEGV) |
| 15:03:13–15:03:41 | ECS registers the new targets, stops both old tasks as "failed ELB health checks" |
| 15:07:52 | new fleet's first cycle completes cleanly: `{'deployments': 4, 'ok': 4, 'appended': 5, 'drift': 7, 'decisions': 1, 'traces_published': 1}` — the race did not fire this time |

## Mechanism (code, main @ 9cb868eb)

1. `backend/archimedes/services/paper_trading.py:1229` — the child calls `init_db()` **every
   cycle**, before asking for the fleet lock. Two tasks → two children → two concurrent DDL
   transactions, 24 h apart forever.
2. `backend/archimedes/db.py:170-192` — `init_db()` runs all patches in **one**
   `engine.begin()` transaction: three `ALTER TABLE papers …` first, then four `ALTER TABLE
   strategy_store …`, then `chat_messages`. Even a no-op `ADD COLUMN IF NOT EXISTS` takes
   `AccessExclusiveLock` on the table for the rest of the transaction. No `lock_timeout`. A
   waiting `AccessExclusiveLock` request queues **every** later reader of that table behind it.
3. The winning child's cycle transaction (`_run` → `advance_all`, one session, one commit at
   the end) holds `AccessShareLock`s on `strategy_store`/`papers` for the whole replay. The
   trace step opens a **second** session (`services/paper_trace.py:442`,
   `resolve_paper_hashes` → `SELECT … FROM papers WHERE arxiv_id IN (…)`) while the first is
   still open. Session 2 queues behind the sibling's DDL; the sibling's DDL waits on session 1;
   session 1 waits on session 2 outside PostgreSQL. No PostgreSQL deadlock, no timeout, no log
   line.
4. `backend/archimedes/main.py:800` — the web process also runs `init_db()` at import, so every
   replacement task inherits the wedge at boot (the 91-minute gap).
5. `backend/archimedes/api/strategies_routes.py:764-797` — `list_generated_strategies` is
   `async def` and runs `session.query(...)` (and `compute_pbo`, 13.5 s on a healthy task
   today) **on the event loop**, so a blocked query freezes `/health` → the ALB sees a dead
   target rather than a slow endpoint.
6. `/health` served 200 from `stale_cached` probe values for 10 hours (`HEALTH_PROBE_TIMEOUT …
   state=stale_cached`). Liveness was correct (the loop was alive); readiness was not
   represented anywhere.

## What did NOT happen

- No deploy that day (task-def 230 since 2026-09-02 03:25Z), no migration task, no NAT/RPC
  outage (chain probes were healthy until 13:28:50), no CPU saturation (ECS 3%, Aurora 4%).
- The kill switch was not pulled; the fleet healed itself only by an OOM kill.

## Open questions

- The linear memory ramp 39% → 100% between 13:31 and 15:01 on the old tasks (~20 MB/min) —
  not explained by the lock chain; candidates are retry pile-up of blocked handlers or an
  allocation loop behind the blocked query. Needs the next occurrence's `docker stats`-level
  data or Container Insights.
- Which session held `papers` at 03:32:07 (the winner's cycle session, inferred from probe
  timing + the 15:02:58 DETAIL) — reproduce locally with two children and `log_lock_waits=on`.

## What the P1 PR changes

This is mechanism items 1 and 2 — the two ends of the DDL lock chain — and nothing else.

1. **The paper-advance child never runs DDL.** `paper_advance_loop` no longer imports or calls
   `init_db()`. Schema is the migrate task's job (`alembic upgrade head`, see
   `migrations/README.md`) plus the web process's single boot-time call; a 24-hourly ticker has
   no business asserting it. Two guards in
   `backend/tests/services/test_paper_advance_kill_switch.py`: an AST walk over
   `paper_advance_loop` (so the docstring can keep explaining *why* without satisfying a
   substring search), and a behavioural one — `archimedes.db.init_db` is armed to raise in the
   shared `_drive_one_tick` harness, which makes every driven tick in that file a standing
   guard.
2. **Boot schema patches take a `lock_timeout`, one transaction per statement.**
   `_apply_patch_statement` in `backend/archimedes/db.py` runs each patch in its own
   `engine.begin()`, issuing `SET LOCAL lock_timeout = '5s'` first on the Postgres dialect only
   (`DB_PATCH_LOCK_TIMEOUT` overrides the value). A patch that cannot take its lock is a
   WARNING and boot continues — the patches were already declared non-fatal (#1028), and this
   makes that declaration true under contention. The statements behind a blocked one still run,
   which matters because a missing `strategy_store.is_example` is a 500 on every Generate. The
   same treatment covers `_ensure_ownership_columns()`, including its `CREATE INDEX IF NOT
   EXISTS` — which takes a lock on `strategy_store` too and used to ride in the same transaction
   as the ALTERs above it.

Together these mean: nothing on a cycle path issues DDL, and the DDL that does run (once, at
boot, in the web process) can no longer wait longer than five seconds for a lock or hold two
tables at once. Tests are hermetic — `backend/tests/test_db_boot_patches.py` drives a recording
fake engine for the Postgres cases (transaction boundaries are not observable through a real
connection) and a real tmp-file SQLite engine for the unchanged-behaviour case.

## What it does not change (P2–P5, tracked on #1818)

- **P2** — the second session opened while the cycle transaction is still open
  (`resolve_paper_hashes` in `services/paper_trace.py`). That is the other half of mechanism
  item 3: with P1 in, no DDL queues between the two sessions, but the two-open-sessions pattern
  is still there.
- **P3** — readiness. `/health` still answers 200 from `stale_cached` probe values, so a wedged
  task can stay in the target group indefinitely (10 hours, here). Mechanism item 6.
- **P4** — `list_generated_strategies` and siblings still run `session.query(...)` on the event
  loop, so a slow query still freezes `/health` in the same process. Mechanism item 5.
- **P5 (ops)** — there are still no CloudWatch alarms on `UnHealthyHostCount ≥ 1`,
  `HTTPCode_ELB_5XX_Count`, or ECS `MemoryUtilization > 85%`. The owner found out by using the
  site.
- The memory ramp under Open questions is not explained or addressed here.

Related: #1632 (the C-abort class, seen again at 15:03:02), #1778 (tourniquet lift), #1802
(decision A: move the tick off the web tier), #1799 (task-def drift), #1740.

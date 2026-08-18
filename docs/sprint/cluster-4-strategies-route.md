# Cluster 4 — strategies_routes.py + live_rigor_gate (A3 · A7-cohort · meter hole)

`strategies_routes.py` is **2357 lines ≈ 30k tokens. Never read whole.** Three anchors, three
windows.

```bash
grep -n "metrics_source\|_live_rigor_results_for_strategies\|_run_fusion_job\|pbo_score" \
  backend/archimedes/api/strategies_routes.py
```

Read [README](README.md) session rules first.

## 1. A3 — kill the fixture fallback (#1187) ⭐ re-run prerequisite

Live contamination is measurable. Bucketing `pbo_score` on the production leaderboard:

```
pbo=0.112821  n=14   <- live-computed cohort value
pbo=None      n=14
pbo=0.299456  n=3    <- fixture constant
pbo=0.065113  n=2    <- fixture constant
pbo=0.083838  n=1    <- fixture constant
```

`strategies_routes.py` ~154-179 falls back to DB columns migrated from
`backend/tests/fixtures/backtest_fixtures_snapshot.json`. If the live cohort compute fails for
any strategy post-re-run, this serves **fixture constants next to fresh live numbers** — exactly
"publishing biased numbers with more authority."

**Do:** replace every `s.<field> ?? bt.<field>` rigor fallback with `None` plus an explicit
`metrics_source: "live_gate" | "persisted_backtest" | "unavailable"`. Keep the columns for now;
drop them next sprint.

## 2. A3 — `DEGENERATE` status

Add a fourth status to `live_rigor_gate` (285 lines — readable whole), gated on the **same test**
`_rigor_helpers.py:130` uses — `float(np.ptp(arr)) == 0.0` — so the two agree by construction.

Five strategies have a mathematically constant 5,659-point return series reported as "pending"
(#1184); eight rows show Sharpe of exactly 0.0 (zero-trade cross-sectional artifacts). **These
are almost certainly the same population** — a zero-trade run yields a flat curve, which is both.
The before/after report must show that overlap explicitly.

## 3. A7-cohort — note the divergence, fix in buffer

The badge and the numbers beside it are computed on **different cohorts today**:
`_live_rigor_results_for_strategies` (`:270`) filters zero-variance series;
`verdicts_for_strategies` (`live_rigor_gate.py:232`) filters only `len >= 10`. So
`avg_correlation` and `pbo_scores` differ between the badge and the figures next to it.

One `cohort_results(strategies) -> dict[str, RigorGateResult]`, from which both derive, deletes
~120 lines and this divergence together. **Buffer work** — but leave a `# TODO(A7)` at both
sites naming the divergence so the next session doesn't rediscover it.

## 4. The unmetered budget hole — 2 lines

`:1962 POST /api/strategies/generate` → `_run_fusion_job` is a **second live, SIWE-gated,
LLM-spending generation endpoint with no UI consumer**, rate-limited at 20/minute vs 5/minute on
the real one. Once [cluster-5](cluster-5-meter.md)'s meter lands it must go through the same
meter or it is an unmetered budget hole. **Route it through the meter; defer deletion.**

If cluster-5 hasn't landed yet, leave a `# TODO(B2)` at the site and do it there instead.

## Test

```bash
pytest backend/tests/test_strategies_routes.py backend/tests/test_live_rigor_gate.py -q
```

Add: a test asserting no response field ever falls back to a persisted column silently — every
value carries a `metrics_source`; a test asserting a zero-variance series returns `DEGENERATE`,
not `pending` and not a 0.0 Sharpe.

## Verify on prod after deploy

```bash
curl -s https://archimedes-arc.com/api/leaderboard | python3 -c "
import sys,json,collections
e=json.load(sys.stdin)['entries']
print(collections.Counter(round(x['pbo_score'],6) if x.get('pbo_score') is not None else None for x in e))"
# 0.299456 / 0.065113 / 0.083838 must be absent
```

## Anti-goals

- Do **not** drop the persisted columns this sprint — switch the read path only.
- Do not build `cohort_results` here.
- Never weaken a threshold.

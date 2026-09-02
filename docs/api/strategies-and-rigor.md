# Strategies & Rigor API

This surface covers the strategy library (curated seed strategies plus
fusion/architect-generated ones, unified through the `strategy_passports`
table), stress testing, and the rigor/selection-bias
gate that grades every strategy on DSR (Deflated Sharpe Ratio), PBO
(Probability of Backtest Overfitting), and chronological out-of-sample Sharpe
before it is allowed to be promoted from `candidate` to `validated` or bound
into a vault's `strategy_ids`. The badge (`passes_rigor_gate` /
`rigor_gate_status`) and the numeric rigor fields served by `GET
/api/strategies/` and `GET /api/strategies/{id}` are the **stored verdict of
record** — graded once at backtest time and persisted on the strategy's
passport row with its provenance (`gate_version`, `graded_at`, `cohort_n`;
see the ADR `rigor-verdict-of-record`). `GET /api/selection-bias/gate/{id}`
is different: it **recomputes** the gate live on every call (it backs the
Deploy ladder and the vault deploy gate). The two can therefore differ in
vintage for the same strategy until the row is explicitly re-graded; when
they do, the passport row is the verdict of record and the live route is a
fresh opinion. Curated strategies currently serve `pending` on the stored
path until they are graded (PR-B of the same program).

**Auth model.** Reads are anonymous by default — the library, passports,
stress testing, and every `/api/selection-bias/*` route are public.
Ownership-scoped reads (`GET /api/strategies/generated`) and mutations
(`PATCH /api/strategies/{id}`)
require a Better Auth account session (the `better-auth.session_token`
cookie). Several anonymous reads additionally consult an *optional* linked- or
SIWE-verified-wallet header/cookie purely to decide whether an unpublished row
is visible to *this* caller (private-until-published, 404-hides-existence) —
absence of that proof is a normal anonymous request, never an error. Examples
needing a session assume an authenticated cookie jar at `/tmp/session.jar`.

## Library & strategy endpoints

### GET /api/strategies/
List strategies in the library, backed by `LocalStrategyProvider`; the badge
and every numeric rigor field come from a single live-gate run over the FULL
library, before pagination. | **Auth**: anonymous

Request: query `status: "candidate"|"validated"|"live"|"retired"|null, limit: int(1..100)=20, offset: int(>=0)=0`.
Response (`StrategyListResponse`): `{strategies: [StrategyResponse], total: int, degraded: bool=false, degraded_reason: str=""}`. `degraded` is `true` when the strategy provider raised or the library came back empty for a reason other than a legitimate filter (e.g. `"strategy corpus not found in build"`) — a loud, specific unavailable state instead of the false claim "no strategies" (#1356). `StrategyResponse` carries `id, papers[], methodology_summary, asset_universe, universe_source, position_sizing, rebalance_frequency, status, sharpe_ratio, sortino_ratio, cagr, max_drawdown, win_rate, calmar_ratio, correlation_to_spy, deflated_sharpe_ratio, dsr_p_value, pbo_score, out_of_sample_sharpe, kelly_fraction, passes_rigor_gate: bool, rigor_gate_status: "pass"|"fail"|"pending"|"degenerate", is_backtest_placeholder: bool, sharpe_ci_lower/upper, backtest_start/end, regime_tag, return_source: "risk_premium"|"mispricing"|"productive_growth"|"noise", return_source_note, generation_cost, can_publish`.
Errors: 422 on invalid `status`/`limit`/`offset`.

```bash
curl -s "https://archimedes-arc.com/api/strategies/?status=validated&limit=20&offset=0"
```

### GET /api/strategies/generated
List fusion/architect-generated strategies from the `strategy_store` table —
private-until-published (visible when published, or owned by the caller). |
**Auth**: account-session

Request: query `limit: int(1..200)=50`.
Response: `{strategies: [StrategyRecord.to_dict() + can_publish: bool + generation_cost + the rigor verdict of record], total: int, degraded: bool=false, degraded_reason: str=""}`.
Each row carries the STORED verdict, overlaid from that strategy's `strategy_passports` row in one `IN` query per page:
`passes_rigor_gate: bool|null`, `rigor_gate_status: "pass"|"fail"|"pending"|"degenerate"`, `graded_at: str|null`, and the
four rigor numbers the same grading event produced (`deflated_sharpe_ratio`, `dsr_p_value`, `pbo_score`,
`out_of_sample_sharpe`). A strategy with no passport row has never been graded: `passes_rigor_gate: null`,
`rigor_gate_status: "pending"`, all four numbers `null` — never `false`, which would claim a gate ran and the strategy
lost. `rigor_verdict` on the same row is the GENERATION-TIME fusion verdict (the debate record) and is **not** a rigor
grade; likewise `status`, which is written from it. See [`docs/adr/rigor-verdict-of-record.md`](../adr/rigor-verdict-of-record.md).
`degraded` is `true` when the strategy store raised (`degraded_reason: "strategy store unavailable"`) — the same
honest-degradation contract `GET /api/strategies/` carries (#1356 review round 2): a swallowed DB failure used to
render as a measured `total: 0`, indistinguishable on the wire from a genuinely-empty store.
Errors: none explicit — a DB failure degrades to an empty list plus `degraded: true` (logged warning), never a 500.

```bash
curl -s -b /tmp/session.jar "https://archimedes-arc.com/api/strategies/generated?limit=50"
```

### GET /api/strategies/signals
Evaluate all strategies against live market data and return signals. |
**Auth**: anonymous

Request: none.
Response (`StrategySignalsResponse`): `{strategy_count: int, regime: str (legacy name for the ensemble-consensus bucket), ensemble_consensus: str|null, confidence: float, target_weights: dict[str,float], strategies: [StrategySignalResponse{strategy_id, paper_title, signals: [SignalResponse{asset, signal: "long"|"flat"|"scaled", weight, reason, strategy_name}]}], timestamp: str}`.
Errors: none explicit.

```bash
curl -s https://archimedes-arc.com/api/strategies/signals
```
Note: `regime` is the `flat_pct`-derived ensemble-consensus bucket, not a true market-regime detector output.

### GET /api/strategies/stress/scenarios
List the available stress scenarios with descriptions. | **Auth**: anonymous

Request: none.
Response: `{scenarios: [...]}` from `stress_engine.list_scenarios()`.
Errors: none.

```bash
curl -s https://archimedes-arc.com/api/strategies/stress/scenarios
```

### POST /api/strategies/stress/run
Apply a stress scenario to a caller-supplied portfolio. | **Auth**: anonymous
| **Flags**: rate limit `20/minute` (disabled under `TESTING`)

Request: `{allocations: [{symbol: str, weight: number|null, ...}], scenario: str="all", usdc_weight: number=0.0}`.
Response: `{results: [{scenario, label, description, portfolio_pnl, portfolio_value_after, per_asset_pnl}]}`.
Errors: 400 `allocations[] is required` (missing/empty list); 422 `allocations[i] must be an object` / `allocations[i].symbol is required and must be a non-empty string` / `allocations[i].weight must be a number` / `usdc_weight must be a number`; 404 `Unknown scenario: {scenario}`.

```bash
curl -s -X POST https://archimedes-arc.com/api/strategies/stress/run \
  -H "Content-Type: application/json" \
  -d '{"allocations": [{"symbol": "sTSLA", "weight": 0.3}], "scenario": "all", "usdc_weight": 0.2}'
```

### GET /api/strategies/passports
List strategies from the unified `strategy_passports` table —
private-until-published exactly as `/generated`. | **Auth**: anonymous

Request: query `status: str|null, regime_tag: str|null, limit: int(1..200)=50`.
Response: `{passports: [dict] (owner_wallet redacted unless caller owns it), total: int, source: "strategy_passports"}`.
Each dict has the same shape as the single-passport read below, verdict and provenance included.
Errors: none explicit.

```bash
curl -s "https://archimedes-arc.com/api/strategies/passports?limit=50"
```

### GET /api/strategies/passports/{strategy_id}
Get a single passport in its native dict shape from `strategy_passports`;
unpublished non-example passports 404 for non-owners, never 403. | **Auth**:
anonymous

Request: path `strategy_id`.
Response: dict — passport record's `to_dict()` with `owner_wallet` redacted unless caller owns it. A **pure read** of the
stored rigor verdict, never a recompute: `passes_rigor_gate: bool`, `rigor_gate_status: "pass"|"fail"|"pending"|"degenerate"`,
plus its provenance — `graded_at: str|null` (ISO-8601; `null` ⟺ never graded), `gate_version: str|null` (which gate
produced it; the literal `"legacy-derived"` marks a verdict INFERRED by the verdict-of-record migration from pre-existing
columns rather than produced by a gate run), `cohort_n: int|null` (cohort size behind the grade's cohort-scoped inputs;
`1` = graded self-contained). Curated passports currently serve `"pending"` — every curated row's stored
`passes_rigor_gate` is the #821 fail-closed placeholder, not a gate result, and grading them is tracked separately; the
curated badge on `GET /api/strategies/{id}` is unaffected and still comes from the live gate.
See [`docs/adr/rigor-verdict-of-record.md`](../adr/rigor-verdict-of-record.md).
Errors: 404 `Passport not found` (missing, or not visible to caller).

```bash
curl -s https://archimedes-arc.com/api/strategies/passports/<strategy_id>
```

### GET /api/strategies/{strategy_id}/returns
Return persisted real daily returns for a strategy; never synthesizes from
fixture metrics — only real persisted run data. | **Auth**: anonymous

Request: path `strategy_id`.
Response (`StrategyReturnsResponse`): `{strategy_id, source: "persisted_backtest", start: str|null, end: str|null, n: int, daily_returns: [float]}` — `owner_wallet` intentionally absent (PII redaction).
Errors: 404 `Strategy not found` (nonexistent, or private and caller is not the owner); 404 `no persisted returns` (strategy exists but has no `BacktestResultRecord` row); 500 `Failed to load returns` (DB read failure).

```bash
curl -s https://archimedes-arc.com/api/strategies/<strategy_id>/returns
```

### GET /api/strategies/{strategy_id}/debate
Return the persisted bull/bear debate transcript the society produced while
generating this strategy (4 turns: bull-r1, bear-r1, then a visible round-2
rebuttal of each other's round-1 claims). Debate-path strategies only — a
curated strategy, or a generated one from before this table existed, genuinely
has none; never fabricates a transcript. | **Auth**: anonymous
(mirrors `GET /api/strategies/{strategy_id}` exactly — curated strategies are
always public, a generated strategy's transcript is 404 unless the caller owns
the row)

Request: path `strategy_id`.
Response: `{strategy_id: str|null, generation_id: str, candidate_id: str, created_at: str, transcript: [{role: "bull"|"bear", round: int, verdict: str, claims: [str]}]}` — `strategy_id` is `null` for a Considered-Alternative row (K=1: only the society's winner is ever persisted to `strategy_store`).
Errors: 404 `Strategy not found` (nonexistent, or private and caller is not the owner — existence stays hidden, same as the plain detail route); 404 `no debate transcript` (strategy exists and is visible, but nothing was persisted for it); 500 `Failed to load debate transcript` (DB read failure).

```bash
curl -s https://archimedes-arc.com/api/strategies/<strategy_id>/debate
```

### GET /api/strategies/{strategy_id}
Get a single strategy by ID — tries the curated `LocalStrategyProvider` first,
falls through to the `strategy_passports` table for fusion/architect-generated
strategies. | **Auth**: anonymous

Request: path `strategy_id`.
Response: `StrategyResponse` (same shape as the list endpoint's items).
Errors: 404 `Strategy not found` (nonexistent, or private and caller is not the owner — a 404 here prevents existence probing).

```bash
curl -s https://archimedes-arc.com/api/strategies/<strategy_id>
```

### PATCH /api/strategies/{strategy_id}
Rename an owned, generated strategy — owner-gated; curated examples
(`is_example`) are not renamable. | **Auth**: account-session

Request: `{name: str (1..80 chars after trim)}`.
Response: `{strategy: StrategyRecord.to_dict()}`.
Errors: 422 `'name' (string) is required` / `name must be 1–80 characters after trimming`; 404 `Strategy not found` (missing row, curated example, or unpublished + not owner); 403 `Not authorized to rename this strategy.` (published row, caller not owner).

```bash
curl -s -X PATCH https://archimedes-arc.com/api/strategies/<strategy_id> \
  -b /tmp/session.jar -H "Content-Type: application/json" -d '{"name": "Momentum v2"}'
```

## Generation is not served here

`POST /api/strategies/generate` and its poll partner
`GET /api/strategies/generate/{job_id}` — the flag-gated direct-fusion bypass —
were **removed on 2026-08-31**. They were a second live, account-gated,
LLM-spending generation path that survived the Phase-3 cutover, contradicting
the [debate-society-sole-generation-pipeline
ADR](../adr/debate-society-sole-generation-pipeline.md).

Generation lives entirely on `POST /api/generate/start` — see
[`generation.md`](generation.md) — with job status at
`GET /api/generate/jobs/{job_id}`. `backend/tests/test_sole_generation_route_guard.py`
fails if a generation route reappears under `/api/strategies`.

## Rigor gate (selection-bias) endpoints

### GET /api/selection-bias/gate
Evaluate the rigor gate for all strategies in the library — DSR, PBO,
chronological OOS Sharpe, plus the look-ahead static audit per strategy. |
**Auth**: anonymous

Request: query `strictness: int(1..5)=1` (1 = strictest/badge level).
Response (`RigorGateResponse`): `{strategies: [StrategyRigorResult{strategy_id, strategy_name, passes_all: bool, gate_details: RigorGateDetail{dsr,pbo,oos_sharpe,look_ahead,cpcv,dsr_convention,rf_convention,iid,regime_robustness}, deflated_sharpe, dsr_p_value, pbo_score, oos_sharpe, in_sample_sharpe, library_pbo: LibraryPbo{value,data_vintage,selection_set_size,source,rf_convention}, strictness_level, min_passing_level, blocked_by_floor, num_trials_scope}], total, passing, failing, library_pbo, strictness_level}`. `rf_convention` (`excess_tbill_series` | `excess_flat_fallback` | `MISSING`, #1409) rides both `RigorGateDetail` and `LibraryPbo` — `MISSING` means no excess-return metric (or, for `LibraryPbo`, no PBO value) was computed at all, distinct from `excess_flat_fallback`'s "a flat-rate computation genuinely ran" — see [`rigor-methods.md` §1a](../rigor-methods.md) for what it discloses.
Errors: none explicit — strategies with fewer than 10 persisted daily returns report every `gate_details` field as an explicit `"MISSING (no backtest data)"` string rather than erroring. `cpcv` is always an explicit `NOT_RUN` status (the combinatorial-split OOS matrix isn't wired yet), never a bare `MISSING`.

```bash
curl -s "https://archimedes-arc.com/api/selection-bias/gate?strictness=1"
```

### GET /api/selection-bias/gate/{strategy_id}
Evaluate the rigor gate for a single strategy at `strictness` (default =
badge level); tries the curated cohort first, falls through to grading a
DB-persisted (fusion/architect-generated) strategy on its own context. |
**Auth**: anonymous | **Flags**: rate limit `10/minute` (disabled under
`TESTING`)

Request: path `strategy_id`; query `strictness: int(1..5)=1`.
Response: `StrategyRigorResult` (same shape as the `/gate` list item).
Errors: 404 `Strategy not found` (unknown to both the curated provider and the generated-strategy path, or not visible to caller); 404 `Strategy not found in gate results` (fallthrough after the full curated gate run).

```bash
curl -s "https://archimedes-arc.com/api/selection-bias/gate/<strategy_id>?strictness=1"
```

### GET /api/selection-bias/strictness-ladder
Disclose the full 1–5 strictness ladder plus the always-on floors — single
source of truth for the UI's strictness slider. | **Auth**: anonymous

Request: none.
Response (`StrictnessLadderResponse`): `{levels: [StrictnessLevelInfo{level,label,dsr_p_min,pbo_max,oos_is_ratio_min,description}], strictest_level, loosest_level, badge_level, default_level, floors: {dsr_p_floor, oos_abs_floor, cpcv_min_positive_fraction}}`.
Errors: none.

```bash
curl -s https://archimedes-arc.com/api/selection-bias/strictness-ladder
```

### POST /api/selection-bias/pbo
Compute PBO (Probability of Backtest Overfitting) across a caller-supplied
set of strategy return series — a library-level metric. | **Auth**: anonymous
| **Flags**: rate limit `20/minute` (disabled under `TESTING`)

Request (`PBORequest`): `{returns_matrix: dict[str, list[float]], s_partitions: int=16}`.
Response (`PBOResponse`): `{pbo_scores: dict[str, float], interpretation: str}`.
Errors: none explicit.

```bash
curl -s -X POST https://archimedes-arc.com/api/selection-bias/pbo \
  -H "Content-Type: application/json" \
  -d '{"returns_matrix": {"strat_a": [0.001, -0.002, 0.003]}, "s_partitions": 16}'
```

## Rigor gate status semantics (honesty note)

The gate reports a **multi-state status** — `rigor_gate_status` on
`StrategyResponse`; `passes_all` + `gate_details` on `StrategyRigorResult` —
never a plain pass/fail boolean pretending the underlying evidence is always
conclusive:

| Status | Meaning |
|---|---|
| `pass` | The live gate ran on persisted real returns and every enabled criterion (DSR significance, PBO, chronological OOS Sharpe, the look-ahead audit, plus the always-on floors) cleared at the requested strictness level. |
| `fail` | The live gate ran and at least one criterion did not clear. |
| `pending` | No live verdict could be computed — no, or fewer than 10, persisted daily returns, or a batch/DB failure. Rendered as `"MISSING (no backtest data)"` per criterion, never a fabricated pass or fail. |
| `degenerate` | A persisted return series exists but is zero-variance (flat/placeholder). It is still graded on its own row, excluded from cohort correlation/PBO context so it cannot dilute other strategies' numbers, and reported honestly rather than silently passing or silently disappearing. |

`passes_rigor_gate` (`StrategyResponse`) is the fail-closed boolean derived
from this status — `True` only when the status is exactly `pass`.

**Never quote a curated-library pass count anywhere** — not in this doc, not
in UI copy, not in pitch material. A pass count is a snapshot of a live,
per-request computation over whichever cohort and strictness level happened
to be in view; it is not a static fact about the library. Three strategies
once reported "passing" turned out to be grading a substitute data feed
(equity-like ~18.5% annual vol) rather than the strategy's own series — the
corrected count is **not established**, and restating any remembered number
here would repeat exactly that mistake. If a surface needs to say something
quantitative, point at the live `GET /api/selection-bias/gate` response — its
`total`/`passing`/`failing` fields as of *this* call — rather than a cached or
remembered figure.

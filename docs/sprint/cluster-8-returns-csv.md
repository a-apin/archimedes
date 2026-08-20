# Cluster 8 — returns-CSV → rigor verdict (B4)

The strategy-import surface. Mostly a new pure module — cheap to write, cheap to test, no big
file reads.

Read [README](README.md) session rules first.

## Transport — zero new deps

**Do not add `python-multipart`.** It is absent from `requirements.txt`, and per CLAUDE.md a new
pip dep must land in both that and `environment.yml`.

`POST /api/v1/rigor/verdict` accepts either `Content-Type: text/csv` with the raw body, or
`application/json` `{returns, dates, num_trials, units, meta}`. The browser does
`<input type="file">` → `FileReader.readAsText` → POST as `text/csv`. **One endpoint serves
browser, CLI, and agents.**

**Size is already bounded:** `main.py:426 _MAX_BODY_BYTES = 1 MB` and nginx has no override —
1 MB is ~40k daily rows ≈ 160 years. **Verify and document rather than change.**

## `services/returns_import.py` — pure, therefore testable

- **Units are the highest-risk validation.** Someone will submit `1.2` meaning 1.2%.
  `max(|r|) > 1.0` → **reject** with "looks like percent; pass `units=percent` or divide by 100."
  **Never auto-guess.** Silently-wrong units is exactly where a fake Sharpe comes from.
- **Two length floors.** Reuse `live_rigor_gate._MIN_RETURNS_FOR_GATE = 10` as the hard floor —
  **do not invent a second constant.** DSR/PBO on 10 points is meaningless, so add an advisory
  `_MIN_RETURNS_FOR_MEANINGFUL = 252` below which the verdict carries `confidence: "low"` with a
  named reason. **Never silently return `pass` on 30 observations.**
- **Do not interpolate gaps** — interpolation manufactures autocorrelation and would poison
  `return_diagnostics.ljung_box_test`. Flag >5 consecutive missing days and >10% total.
- ISO-8601 dates only, strictly increasing. Reject `MM/DD` vs `DD/MM` ambiguity outright. Reject
  non-finite values (with row numbers), zero-variance series, >20,000 observations.
- **`num_trials` is user-disclosed and adversarial.** Default to `1` matching
  `_default_num_trials()`, and add `num_trials_source: user_disclosed | undisclosed_default_1 |
  engine_measured` **now** — with `undisclosed_default_1` the response must state that DSR runs
  **undeflated** and the verdict is an *upper bound* on rigor. Because #1223 reworks `num_trials`
  by provenance, making this a field today means **#1223 lands as a value change rather than a
  schema change.** Coordinate with Dan.

## Reuse — do not rebuild

`verdict_from_returns` (`live_rigor_gate.py:117` — bare list of floats → four-state plus
`min_passing_level` / `blocked_by_floor`) · `vol_plausibility` (`compute_vol_stats`,
`assess_strategy`) · `return_diagnostics.diagnose` · `rigor_cache` (600s TTL + single-flight, so a
resubmitted identical CSV is free).

Return a shape **byte-compatible with what `StrategyPassport.jsx` already renders.**

**`data_quality.verify_universe` does NOT apply** — it needs tickers and a yfinance call, and the
user chose "no market data." Say so in the docstring rather than implying reuse.

## The look-ahead decision — make it explicitly

An imported return series has no code, so the AST look-ahead audit cannot run. Passing
`strategy_code=None` and `look_ahead_audit_passed=None` forces the always-on look-ahead floor to
fail at every strictness level.

**The honest answer is not to accept a self-attestation the way the DSL path does.** Report the
look-ahead leg as `NOT_RUN` and **cap the verdict accordingly**, stating that no code was supplied.

## Persistence — default to not persisting

Compute, return, forget. Smallest correct thing, and it dodges the storage/abuse/PII surface
entirely. Optional `?save=true` (auth required) writes a `StrategyRecord` with
`owner_wallet=<principal.wallet>` and a new `source='imported_returns'` — and `is_strategy_visible:10`
then gives owner-only visibility with **zero new code**.

## Hard rule — enforced in code, not docs ⭐

`docs/anti-features.md` already says allowing arbitrary third-party strategies into Tier 1
"dilutes the badge to meaninglessness."

An imported series can **never** carry Archimedes Verified, appear on `/leaderboard`, be published
to the marketplace, or be deployed. **Enforce it in the publish path and the leaderboard query,
and test both.** The badge is what the product's credibility rests on.

## Run the gate in `asyncio.to_thread`

CSCV PBO over 20k points is seconds of numpy. On the event loop it would stall
`/api/generate/stream`'s 15-second heartbeat and break SSE for every concurrent user. **This is a
real availability bug if skipped.**

## UI — a card on `/library`, not a new route

Drop zone plus the existing `RigorExplainer.jsx`. **No `routes.js` / sitemap / nav /
OnboardingTour churn.** (UI card may slip to buffer; the endpoint is the deliverable.)

## Test

```bash
env -i HOME=$HOME PATH=$PATH PYTHONPATH=backend python -m pytest backend/tests/test_returns_import.py -q
```

Cases: percent-units rejection · <10 rejected · 10–251 returns `confidence: "low"` · gap flagging
without interpolation · DD/MM ambiguity rejected · non-finite with row numbers · zero-variance →
`DEGENERATE` · `num_trials_source` present on every response · **imported series cannot reach the
leaderboard or the publish path** (two separate tests).

## Anti-goals

No `python-multipart` · no server-side user Python · no DSL-JSON upload · no second minimum-length
constant · never auto-guess units.

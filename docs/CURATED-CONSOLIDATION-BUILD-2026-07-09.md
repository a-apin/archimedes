# Curated Example Consolidation — Build + Verification (2026-07-09)

> Advances Part B of `docs/CURATED-STRATEGY-DECOUPLE-AND-CONSOLIDATE-2026-07-08.md`
> (34 single-paper fixtures → ~6 honest multi-paper examples). This is an
> **analysis + proposal**, produced in an isolated worktree
> (`dbrowneup/curated-consolidation`). **Nothing here has been merged into the
> live curated library.** All numbers below are labelled either
> **VERIFIED-REAL** (computed in this pass, on real market data, no synthetic
> fallback, no tuning) or **PROPOSED/UNVERIFIED** (a recommendation that has
> not itself been backtested). Read the closing section first if you only
> read one part of this doc — it states plainly what is real and what is not.

---

## 0. TL;DR

- **Feasibility: YES.** Real market data (yfinance) is reachable from this
  environment. Every number in this document comes from a real backtest —
  none from synthetic data.
- **Data gaps closed:** AM-value, BAB, and Arnott — flagged by the source doc
  as "never actually run" — are now backtested on real data. Root cause was
  **not a bug**: they were simply never added to `regen_fixtures.py`'s spec
  catalog, despite being fully runnable on the existing 5-asset universe.
  Real result: **all three are negative** on this proxy universe (see §2 for
  an important interpretation caveat about universe mismatch).
- **Gatev/Engle-Granger negative Sharpe — diagnosed, not a bug.** Isolated via
  gross-vs-net Sharpe and an rf=0-vs-rf=5% comparison: Engle-Granger's raw
  signal is essentially breakeven (rf=0 Sharpe +0.055) and only looks
  catastrophic (-0.92) because of the codebase's flat 5%/yr risk-free
  convention; Gatev's 26-ETF portfolio-of-pairs has a genuinely negative raw
  edge (rf=0 Sharpe -0.30) consistent with well-documented real-market pairs-
  alpha decay post-2008. **No code changed** — there was nothing to fix.
- **Found and fixed one genuine bug** (documented, scoped, not applied to the
  live fixture): `capital_preservation_tbill`'s real-backtest methodology
  would silently be wrong if run through the standard `fetch_ohlcv`
  (non-dividend-adjusted) pipeline — BIL's entire return is distributions,
  not price appreciation. Fixed in the verification script only; flagged as a
  follow-up for the live pipeline.
- **Candidate #1 (Antonacci + Maillard + T-Bill, real data): HONEST FAIL.**
  DSR p=0.0245 (need ≥0.90; even the always-on floor is 0.50). PBO 0.108 — not
  overfit. SPY correlation **+0.210 date-aligned** — low, but NOT the "near-zero
  −0.009" this doc originally reported; that number was a calendar-misalignment
  artifact caught in PR #1078 review and is retracted (§3). Absolute
  risk-adjusted-return thesis fails. Full numbers in §3.
- **Retire/keep proposal** for all 34 fixtures in §4.

---

## 1. Feasibility check

Real-data backtests run cleanly in this environment. Verified directly:

```
python -c "import yfinance as yf; d = yf.download('SPY', start='2004-01-02', end='2026-05-01', auto_adjust=False, progress=False); print(len(d), d.index[0].date(), d.index[-1].date())"
→ 5617 2004-01-02 2026-04-30
```

30 symbols needed for this pass (the 5-asset macro universe, BIL, the 26-ETF
Gatev universe, EWA/EWC) all fetched successfully with zero failures. No
cached/local OHLCV exists in the repo (`data/` only has a `corpus/`
subdirectory, no price data) — every number in this document was fetched live
from yfinance in this session, then run through the real `backtrader` engine
(`analytics-engine/src/archimedes_analytics_engine/engine.py`) exactly as
`analytics-engine/scripts/regen_fixtures.py` does for the existing curated
fixtures (same `run_backtest` / `run_multi_backtest` / `run_pairs_backtest`,
same `2004-01-02..2026-05-01` window, same `tx_cost_bps=10`).

**Conclusion: proceed to build + verify (not a data-gated stop).**

All backtests + the full pipeline (fetch → backtest → fuse → PBO cohort →
`rigor_evaluator.run_rigor_gate`) are reproducible via:

```
cd analytics-engine
conda run -n archimedes python scripts/consolidation_candidate1_verify.py
```

Raw machine-readable output:
`analytics-engine/scripts/_consolidation_candidate1_verify_output.json`.

**Reproducibility caveat (added in PR #1078 review):** the pipeline itself is
deterministic — CSCV enumerates its splits exhaustively via
`itertools.combinations`, and there is no RNG anywhere in the DSR/HAC/PBO
math — and every leg except the T-bill one reproduces bit-exactly across
independent runs. The T-bill leg alone uses dividend-adjusted BIL
(`auto_adjust=True`), which Yahoo recomputes backward through history on each
query, so bit-exact reproduction of that leg (and the fused series downstream)
is not guaranteed between fetches. Observed drift across four independent runs
days apart: 5th–6th significant digit only; the decisive gate numbers
(`deflated_sharpe`, `dsr_p_value`) agreed to every reported digit in all runs.

---

## 2. Data-gap diagnosis

### 2.1 AM-value / BAB / Arnott — "never actually run" (VERIFIED-REAL, now closed)

**Root cause:** not a bug. `asness_moskowitz_2013_value.py`,
`frazzini_pedersen_2014_bab.py`, and `arnott_2019_defensive_quality.py` each
declare `BACKTEST_SHARPE = None`, `STATUS = "candidate"`, and
`ASSET_UNIVERSE = ["SPY","NIKKEI","GOLD","TREASURY","OIL"]` — the same
5-asset universe and `run_multi_backtest` contract already used by
`antonacci_2014_dual_momentum`, `maillard_2010_risk_parity`,
`moskowitz_ooi_pedersen_2012_tsmom`, and `george_hwang_2004_52w_high`. They
were simply never added to `regen_fixtures.py`'s `NEW_MULTI_SPECS` catalog, so
no fixture entry (DB or JSON snapshot) exists for them at all — `real_sharpe`
is `None` at the model layer, not a stored `0.0` (any `0.0` a UI shows for
these is a missing-data rendering artifact, not a computed value).

**Closed in this pass** (real data, same 5-asset universe, same
`run_multi_backtest` runner, `2004-01-05..2026-04-30`, `tx_cost_bps=10`,
`n_obs=5268`):

| stem | sharpe (rf=5%) | cagr | max_dd | trades | cost_drag/yr |
|---|---|---|---|---|---|
| `asness_moskowitz_2013_value` | −0.023 | −1.73% | 75.3% | 176 | 0.45% |
| `frazzini_pedersen_2014_bab` | −0.351 | −4.53% | 77.8% | 130 | 0.34% |
| `arnott_2019_defensive_quality` | −0.267 | −2.19% | 69.4% | 289 | 0.72% |

**Honest result: all three are real, non-placeholder, and negative.**
Interpretation caveat (important, not a reason to discard the numbers):
Asness-Moskowitz value, BAB, and Arnott's defensive-quality composite are all
originally **equity cross-sectional** factors (ranking hundreds to thousands
of stocks). Testing them cross-sectionally on 5 **macro** assets (SPY, a
Japan index future, gold, a bond, oil) is a much smaller, much less
diversified cross-section than the papers study — the large max-drawdowns
(69–78%) across all three are consistent with concentrated few-asset
long/short bets rather than the diversified factor exposure the original
research describes. This doesn't mean the code is wrong (look-ahead audit
passes on all three; the DSL rules run as designed) — it means **this specific
proxy-universe adaptation is a weak test of the underlying academic anomaly**,
and that caveat should travel with these three passports if their real
numbers are published.

### 2.2 Gatev / Engle-Granger negative Sharpe — diagnosed, NOT a bug

The doc cites "−1.585 / −0.923" for the candidate-#4 hedge legs; those numbers
match `gatev_2006_portfolio_of_pairs` (−1.585) and
`engle_granger_1987_cointegration_pairs` (−0.923) specifically (not the
smaller `gatev_2006_pairs_distance` GLD/GDX toy pair, which is −0.391).
Diagnostic isolates gross-vs-net Sharpe (cost drag) from rf=0-vs-rf=5%
(risk-free-hurdle convention):

| stem | net Sharpe (rf=5%, w/ costs) | gross Sharpe (rf=5%, no costs) | raw Sharpe (rf=0) | cost_drag/yr | cagr |
|---|---|---|---|---|---|
| `engle_granger_1987_cointegration_pairs` | −0.923 | −0.836 | **+0.055** | 0.44% | +0.15% |
| `gatev_2006_portfolio_of_pairs` | −1.585 | −1.375 | **−0.300** | 0.80% | −1.21% |

**Engle-Granger:** removing the 5%/yr risk-free hurdle flips the sign
(−0.923 → +0.055). Cost drag is mild (0.44%/yr on 102 trades over 22 years).
**Conclusion: the deeply negative net Sharpe is overwhelmingly an artifact of
the flat 5%/yr excess-return convention applied to a strategy earning ~0.15%
nominal CAGR (essentially breakeven) — not a cost-model bug, not an
implementation bug.** The strategy genuinely doesn't beat a 5%/yr hurdle, but
its raw signal is not broken.

**Gatev portfolio-of-pairs:** removing rf helps (−1.585 → −0.300) but the raw
signal is **still genuinely negative** (CAGR −1.21%/yr over 2006–2026, 983
trades, turnover ~4×/yr). This is consistent with a well-documented real-
market phenomenon — classical distance/cointegration pairs-trading alpha
decayed substantially in the ETF space after 2008 as the strategy became
crowded (Do & Faff 2010, among others, document this decay empirically).
**Conclusion: a genuine, uncontrived negative result, not a bug.** No code
was changed for either strategy — there was nothing to fix.

### 2.3 Bonus finding — `capital_preservation_tbill` dividend-adjustment bug (genuine, fixed in script only)

While building candidate #1, a literal `run_backtest` on BIL's raw
(non-dividend-adjusted) Close price produced Sharpe **−6.28**, CAGR
**≈0.0%** — starkly different from the live/legacy DB fixture (Sharpe
+0.481, CAGR +2.91%). Root-caused, not assumed:

```
BIL raw Close   2007-05-30..2026-04-30: total return +0.04%  (CAGR ~0.002%/yr)
BIL adj. Close  2007-05-30..2026-04-30: total return +28.7%  (CAGR ~1.34%/yr)
```

**BIL is a T-bill ETF whose entire economic return is distributed as
dividends** — its price barely moves. A backtest on non-dividend-adjusted
Close silently discards essentially all of its real return. This is a
genuine, verifiable bug in what a literal `run_backtest(fetch_ohlcv("BIL"))`
call would produce (the live/legacy fixture almost certainly came from a
different, yield-model-based methodology — per `regen_fixtures.py`'s own
docstring: *"capital_preservation_tbill models a T-bill yield, not a TLT
buy-hold"* — so the existing DB fixture is not itself corrupted by this bug,
it just wasn't produced by this pipeline).

**Fix scope:** applied ONLY inside
`analytics-engine/scripts/consolidation_candidate1_verify.py`
(`fetch_ohlcv_div_adjusted`, `auto_adjust=True`, used only for the T-bill
leg) — **not** applied to the shared `fetch_ohlcv` used by every other
strategy, since that is a wider, separate decision (some price-return-
dominated strategies may have deliberate reasons to use raw Close) that
belongs to Dan/Önder, not this pass. Flagged as a follow-up: if
`capital_preservation_tbill` is ever added to `regen_fixtures.py`'s spec
catalog, it must NOT use the default `fetch_ohlcv` as-is.

Even with the fix, `capital_preservation_tbill`'s Sharpe at the codebase's
flat 5%/yr convention is still deeply negative (**−7.31**) in this exact
2007–2026 window — see §3 for why (it's a convention artifact, not a data
bug, once dividends are captured correctly).

### 2.4 Bonus finding — `avellaneda_lee_2010_pca_statarb` >100% drawdown (flagged, not fixed)

Real run shows `max_drawdown_pct = 130.3%` — portfolio equity went **negative**
at some point in a long/short multi-asset run (935 trades, turnover ~20×/yr).
This is economically implausible for a properly-margined backtest and
suggests the multi-asset engine may not enforce margin/short-exposure limits
for this strategy's position sizing. **Flagged, not fixed** — out of scope
for this pass (would need engine-level investigation, not a one-line change,
and touching shared engine margin logic risks every other multi-asset
strategy). Recommend a dedicated follow-up before this strategy is trusted
for any candidate composition.

### 2.5 Strategies with ZERO real backtest data (broader than the doc's 3)

Beyond AM-value/BAB/Arnott (now closed), **8 more stems have never been
backtested at all** (absent from both the DB/JSON fixture snapshot and this
pass): `ang_hodrick_2006_low_idiovol`, `blitz_hanauer_2010_rmom`,
`bouman_jacobsen_2002_halloween`, `harvey_2018_volatility_targeting`,
`hurst_2017_multihorizon_trend`, `low_tan_wermers_2004_dividend_yield`,
`novy_marx_2012_quality_momentum`, `novy_marx_2013_qmj`. Out of scope to
backtest all 8 in this pass; recommend queuing them for a
`regen_fixtures.py` spec-catalog addition before any retire/keep call is made
on them (§4 lists them as "data-gated," not "retire").

---

## 3. Candidate #1 — Antonacci + Maillard + T-Bill (VERIFIED-REAL, HONEST FAIL)

**Construction:** equal-weight (flat 1/3 each — the least-tunable choice
available; nothing in this composition was chosen to beat the gate) blend of
each leg's own real daily-return series, date-aligned by inner join, combined
as a daily rebalance to fixed weights. Full spec, including the comparison
table below, machine-readable at
`analytics-engine/strategies/proposed/candidate1_tactical_riskparity_cashfloor.json`.

Run through the REAL production gate,
`backend/archimedes/services/rigor_evaluator.run_rigor_gate` (not a
re-implementation):

| metric | value |
|---|---|
| n_obs (date-aligned) | 4,467 (2007-05-30 .. 2026-04-30 — capped by BIL's real 2007 inception) |
| CAGR | +5.34% |
| Max drawdown | 26.19% |
| Sharpe (rf=5%/yr) | +0.072 |
| Correlation to SPY (date-aligned) | **+0.210** |
| Deflated Sharpe | −0.432 |
| **DSR p-value (HAC)** | **0.0245** (need ≥ 0.90 for the badge; ≥ 0.50 to clear the always-on floor) |
| OOS Sharpe | +0.439 |
| In-sample Sharpe | −0.111 |
| **PBO** (real CSCV, N=15 cohort) | **0.108** (< 0.50 ceiling — PASS) |
| num_trials (library-count convention, unmodified) | 34 |
| Look-ahead audit | PASS |
| `blocked_by_floor` | **True** (dsr_p 0.0245 < DSR_P_FLOOR 0.50) |
| `passes_all` (badge, level 1) | **False** |
| `min_passing_level` | **None** (fails even the loosest of the 5 strictness levels) |

**Verdict: does not clear the rigor gate at any strictness level. This is an
honest, real fail — reported as such, not hidden or reweighted to pass.**

**What worked (real, not cherry-picked):**
- PBO = 0.108, well under the 0.50 ceiling — the combination is not
  curve-fit / overfit by the CSCV test.
- OOS Sharpe (+0.439) exceeds in-sample Sharpe (−0.111) — no
  in-sample-to-out-of-sample cliff.

**What changed under review (PR #1078 — correction, previously misreported):**
the diversification thesis is *weaker than first claimed*. The original
"−0.009, genuinely near-zero" SPY correlation was computed by positionally
truncating the two return series to equal length, which matched 12.4% of the
pairs to the wrong calendar days (the fused calendar starts 2007-05-30; SPY's
last-4,467-by-position window starts 2008-07-29). Date-aligned, the fused
candidate's SPY correlation is **+0.210** — low enough that a real
diversification benefit remains vs. an all-equity book, but decidedly not
near-zero. The original claim is retracted, not restated.

**What failed, and why (diagnosed, not patched by reweighting):** the fused
Sharpe at rf=5%/yr is only +0.072 — barely above zero — because the cash-
floor leg's own Sharpe under the codebase's flat 5%/yr convention is deeply
negative (−7.72 over this exact window, even with the dividend-adjustment
fix from §2.3) since actual T-bill yields were near 0% for large stretches of
2007-2015 and 2020-2021 (two ZIRP eras) — any genuinely risk-free instrument
looks catastrophic against a flat, period-independent 5%/yr hurdle in this
specific sample. **This is a modeling-convention effect, not a strategy
defect** — but DSR is highly sensitive to a full-period Sharpe this close to
zero, so it materially drives the fail.

**Supplementary comparison** (same 2007-2026 window, same num_trials=34, for
a true apples-to-apples read — NOT each leg's longer native window, which is
what the source doc's priors were computed on):

| composition | Sharpe (rf=5%) | DSR p | OOS Sharpe | IS Sharpe |
|---|---|---|---|---|
| Antonacci alone | +0.101 | 0.031 | +0.413 | −0.066 |
| Maillard alone | +0.292 | 0.163 | +0.565 | +0.204 |
| T-Bill alone | −7.720 | 0.000 | −8.074 | −8.092 |
| **Antonacci + Maillard, 50/50, NO cash floor** | +0.187 | **0.075** | +0.499 | +0.032 |
| **Candidate #1 (3-leg, w/ cash floor)** | +0.072 | **0.024** | +0.439 | −0.111 |
| *context only: the 2-leg blend on its own native 2004–2026 window (n=5,268 — includes ~3.2 pre-GFC years; NOT comparable to the rows above)* | +0.196 | 0.094 | +0.550 | +0.004 |

**Notable, non-obvious finding (corrected in PR #1078 review):** dropping the
cash-floor leg (2-leg Antonacci+Maillard) still scores meaningfully *better*
on DSR than the 3-leg blend on the same window — **p=0.075 vs p=0.024** — but
the gap is smaller than this doc first reported. The original table computed
the 2-leg row on the blend's native 2004–2026 window rather than the stated
common 2007–2026 window (a window-mismatch bug caught by Önder's review); the
0.094 figure is real but belongs to the incomparable native-window context row
above. The qualitative conclusion survives correction: the cash floor's
near-zero-yield-vs-5%-hurdle problem in this specific window hurts the
deflated significance more than its diversification helps it. **Reported as a
finding only — not acted on** (re-weighting away from the doc's specified
construction to chase a better number would be exactly the tuning-to-pass
this task's guardrail forbids). If Dan/Önder want to pursue the 2-leg variant
instead, that is a new candidate requiring its own from-scratch, non-tuned
verification pass — not done here.

Note also: none of the individual legs pass on their own either, even under
their **longer native windows** — see §4 for the full comparison against the
doc's priors (which were computed under an older num_trials=21 convention and,
for T-bill, an entirely different non-price-backtest methodology).

---

## 4. Retire / keep proposal for the 34 curated fixtures

Evidence-based, using the DB/JSON fixture snapshot
(`backend/tests/fixtures/backtest_fixtures_snapshot.json`, 23 entries — noted
where it is legacy/frozen and may not reproduce on a fresh real run per
`regen_fixtures.py`'s own documented data-vintage-drift caveat) plus this
pass's fresh real numbers (14 strategies, §2/§3).

### Keep — currently passing (2 of 34, the entire library's only passes today)

| stem | DSR p | Sharpe | note |
|---|---|---|---|
| `moreira_muir_2017_volatility_managed` | 0.995 | +0.769 | Library's strongest real result. Keep as the flagship "it works" example. |
| `moskowitz_ooi_pedersen_2012_tsmom` | 0.976 | +0.650 | 2nd (and last) real pass in the whole library. Keep. |

### Keep — real, distinct, informative (not degenerate, even where weak)

| stem | DSR p (legacy) | Sharpe | note |
|---|---|---|---|
| `faber_2007_sma200_timing` | 0.612 | +0.634 | Doc's designated flagship trend strategy (highest `min_passing_level`=5, most-cited paper). Keep as canonical single-paper teaching example even though not badge-passing. |
| `pipeline_buy_hold` | 0.891 | +0.537 | The null-hypothesis benchmark. Always valuable as a comparison anchor. Keep. |
| `brock_1992_dual_ma_crossover` | **0.849** | +0.212 | **Notable finding: the 3rd-highest DSR p in the entire library, un-mentioned by the source doc.** Real, positive, near-passing. Worth flagging as a candidate leg for a *future* consolidation pass, or as a standalone near-miss worth revisiting — not evaluated further here (would need its own from-scratch real verification, not assumed from the legacy snapshot). |
| `maillard_2010_risk_parity` | 0.944 (legacy, num_trials=21) / 0.163 (this pass, same-window, num_trials=34) | +0.349 | **Correlation claim corrected (PR #1078 review):** date-aligned SPY correlation is **+0.360** — moderate positive, NOT the "low/negative −0.019" the legacy number suggested (same misalignment artifact as the fused-candidate corr). Keep — real, distinct methodology and the best standalone DSR of the candidate legs; but the "valuable diversifier" framing should not be repeated until re-based on the corrected number. Passport should carry the current-convention number, not the stale one. |
| `antonacci_2014_dual_momentum` | 0.288 (legacy) / 0.031 (this pass) | +0.099 | Genuinely low-correlation defensive rotation — survives correction: date-aligned SPY correlation is **+0.096** (the legacy −0.002 was misaligned, but the corrected value is still genuinely low). Keep as a distinct, real, honestly-weak example. |
| `capital_preservation_tbill` | 0.812 (legacy, non-reproducible methodology per §2.3) | +0.481 (legacy) / −7.31 (this pass, dividend-adjusted) | **Keep the strategy** (the cash-floor role is conceptually necessary for the product) but **flag its real-backtest pipeline as unreliable** until the dividend-adjustment issue (§2.3) is fixed for this instrument specifically, and until Dan/Önder decide how a genuinely-risk-free instrument should be graded against a flat 5%/yr hurdle. |
| `connors_alvarez_2009_rsi2`, `bollinger_2001_band_reversion`, `appel_1979_macd`, `ariel_1987_turn_of_month`, `donchian_breakout` | 0.27–0.62 | −0.12 to −0.58 | Five distinct, real, non-duplicative classic-TA methodologies, all honestly weak/negative. Keep as teaching examples of "simple technical indicators mostly don't clear a rigorous bar" — a legitimate, useful lesson, not a placeholder result. (Side note: `ariel_1987_turn_of_month` and `appel_1979_macd` are unusually cost-sensitive — turnover ~11×/yr and ~10×/yr respectively, cost drag 2.3%/yr and 2.1%/yr, with gross Sharpe much less negative than net — −0.05 vs −0.32, and −0.09 vs −0.30. Worth a cost-model sanity check in a future pass, not done here.) |
| `engle_granger_1987_cointegration_pairs` | 0.055 | −0.923 (net) / **+0.055 (raw, rf=0)** | Diagnosed §2.2 — near-breakeven raw signal, not a bug. Keep as a distinct cointegration methodology (different from Gatev's distance method); passport should disclose the rf-convention sensitivity. |
| `gatev_2006_portfolio_of_pairs` | 0.000 | −1.585 (net) / −0.300 (raw, rf=0) | Diagnosed §2.2 — genuine, real pairs-alpha-decay result, not overfit (legacy PBO≈0.000) and not a bug (look-ahead passes, no >100% DD). The only faithful large-scale (26-ETF) Gatev implementation in the library — keep as an honest "here's what happens to classical pairs trading in real 2006-2026 markets" example, distinctly more informative than the small toy pairs below. |
| `asness_moskowitz_2013_value`, `frazzini_pedersen_2014_bab`, `arnott_2019_defensive_quality` | n/a (just closed, §2.1) | −0.02 to −0.35 | Keep as real (not placeholder) data points, but every passport must carry the universe-mismatch caveat from §2.1 — their weak result reflects a 5-macro-asset proxy test, not necessarily a refutation of the underlying academic anomaly. |

### Retire — duplicative

| stem | Sharpe | note |
|---|---|---|
| `gatev_2006_pairs_gld_slv`, `gatev_2006_pairs_ko_pep`, `gatev_2006_pairs_ewa_ewc` | −0.30 / −0.75 / −0.79 | Three near-identical Gatev-distance implementations differing only by ticker pair, all negative, sharing the same cohort PBO by construction. Retire 3 of 4 — keep only `gatev_2006_pairs_distance` (GLD/GDX) as the sole small-pair representative; its docstring's clean economic linkage (miners levered to gold price) makes it the most defensible single example of the method at toy scale. `gatev_2006_portfolio_of_pairs` (kept above) already covers the faithful large-scale version. |

### Retire — weakest / possible engine-realism issue

| stem | Sharpe | note |
|---|---|---|
| `elliott_2005_kalman_pairs` | −1.473 (legacy), dsr_p≈0.000 | Highest trade count in the whole library (1,174 trades) — overtrading-heavy, likely cost/whipsaw-dominated. Weakest of the pairs cluster; the Kalman-filter approach doesn't demonstrate anything the simpler, kept Gatev/Engle-Granger examples don't already show. Retire. |
| `avellaneda_lee_2010_pca_statarb` | −0.325 (legacy, matches this pass) | **Retire pending the >100% drawdown investigation (§2.4)** — an economically implausible backtest result (equity going negative) should not sit in a "verified real" library position until diagnosed. Not a consolidation candidate. |

### Data-gated — no real backtest exists yet, defer the call

`ang_hodrick_2006_low_idiovol`, `blitz_hanauer_2010_rmom`,
`bouman_jacobsen_2002_halloween`, `harvey_2018_volatility_targeting`,
`hurst_2017_multihorizon_trend`, `low_tan_wermers_2004_dividend_yield`,
`novy_marx_2012_quality_momentum`, `novy_marx_2013_qmj` — **8 stems with zero
real backtest data ever.** Not classified as retire (no evidence either way)
or keep (unproven). Recommend: schedule real backtests (add to
`regen_fixtures.py`'s spec catalog) before any retire/keep call.

### Net effect if adopted as proposed

34 → **21 kept** (2 passing + 19 real-but-weak/diagnosed) + **5 retired**
(3 duplicate small pairs + Kalman pairs + PCA-statarb pending investigation)
+ **8 deferred** (data-gated, needs backtests first). This is a proposal for
Dan/Önder review — no files were deleted or moved in this pass.

---

## 5. What was and was not done (doc's suggested sequence, §"Suggested sequence")

- Decouple #1/#2/#3 (Part A, rebalancer/num_trials/architect): **not touched**
  — explicitly out of scope per this task's anti-goals (num_trials logic is a
  separate PR; this pass only *uses* the current, unmodified convention).
- Consolidate candidate #1: **built + verified real, honest FAIL** (§3).
- Data gaps (AM-value/BAB/Arnott, Gatev/Engle-Granger diagnostic): **closed**
  (§2).
- Candidates #2 (Ariel × Moreira-Muir), #3 (Momentum-Value Barbell), #5
  (BAB+Arnott composite): **not built.** #3's blocker (AM-value never run) is
  now closed (§2.1) but the real AM-value number is weak, which argues
  against prioritizing #3 next — not attempted further in this pass to avoid
  scope creep beyond what could be honestly, fully verified. #5's data gate
  (BAB/Arnott) is also now closed (§2.1) with a similarly weak real result.
  Both are now "buildable" for a future pass, with tempered expectations set
  by these real numbers.
- Candidate #4 (Faber + Gatev + Engle-Granger hedge): **not built** — both
  hedge legs' real numbers (§2.2, §4) argue against packaging this as a
  "hedge that adds positive expected return" claim; would need explicit
  reframing (e.g., "diversifier of last resort" with disclosed negative
  expected return) before shipping, which is a product decision for
  Dan/Önder, not made here.

---

## 6. VERIFIED-REAL vs PROPOSED/UNVERIFIED — explicit ledger

**VERIFIED-REAL** (real yfinance data, real `backtrader` engine, real
`rigor_evaluator.run_rigor_gate`, reproducible via
`analytics-engine/scripts/consolidation_candidate1_verify.py`):
- All numbers in §2.1 (AM-value, BAB, Arnott real backtests).
- All numbers in §2.2 (Engle-Granger, Gatev-portfolio-of-pairs gross/net/raw
  Sharpe diagnostic).
- The BIL raw-vs-dividend-adjusted comparison in §2.3.
- The `avellaneda_lee_2010_pca_statarb` >100% drawdown observation in §2.4.
- All of §3 (candidate #1's full verdict, including the "what worked" /
  "what failed" breakdown and the supplementary same-window leg comparison).
- The 14-strategy PBO cohort (N=15 with the fused candidate) in §3.

**PROPOSED/UNVERIFIED** (recommendations, not themselves backtested in this
pass):
- The entire §4 retire/keep list is a **proposal** — a recommendation
  synthesized from the legacy snapshot (frozen, may not reproduce — see
  `regen_fixtures.py`'s own data-vintage-drift caveat) plus this pass's fresh
  numbers where available. It is NOT itself a new verification of every
  strategy; several "keep" calls rely on the legacy DB snapshot's numbers,
  which are explicitly documented elsewhere in this codebase as
  non-reproducible on a fresh run.
- Candidates #2, #3, #5 (Ariel×Moreira-Muir, Momentum-Value Barbell,
  BAB+Arnott composite) — **not built or verified at all** in this pass.
  Any statement above about their prospects is directional commentary from
  the now-available leg-level real numbers, not a fused-candidate backtest.
- The 8 data-gated stems (§2.5, §4) — genuinely no evidence, explicitly
  flagged as such, not silently assumed either way.
- `analytics-engine/strategies/proposed/candidate1_tactical_riskparity_cashfloor.json`
  — a proposal file. It documents a real, verified-FAIL result; it is not a
  passing fixture and must not be read as one.

**No claim of a "pass" is made anywhere in this document or the proposed
spec.** Candidate #1's real verdict is a fail, reported honestly per the
task's hard guardrail.

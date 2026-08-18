# Universe Experiment: does a bigger universe rescue the second-wave strategies?

> **Status:** Findings note, 2026-06-11 (Önder, quant lane). Companion to
> [`second-wave-multi-asset-strategies.md`](../plans/second-wave-multi-asset-strategies.md).
> **TL;DR:** No. All nine second-wave strategies are admitted as `CANDIDATE`
> (none clears the rigor gate). The natural hypothesis — "they fail only because
> the demo universe is too small (5 assets)" — was tested directly and is **false**.
> Expanding and re-composing the universe does **not** flip any verdict, and in
> several cases makes performance *worse*. The strategies genuinely underperform
> on real data after costs; the gate is doing its job. One legitimate calibration
> question (the DSR `num_trials` penalty) is split out to its own issue.

## Why this note exists

A fair challenge after the second wave landed: *"these are real, heavily-cited
papers (Jegadeesh-Titman, Engle-Granger, Avellaneda-Lee, …) — why is **every**
new strategy denied? Is the validation system too harsh, or is the 5-asset demo
universe unfair to them?"*

Good question, and testable. So we re-ran the four multi-asset strategies on
larger and more appropriate universes, using the **identical** rigor machinery
(DSR / PBO / OOS / gate) so the comparison is apples-to-apples.

## First: the gate is not a black hole

Of the full 22-strategy library, **two pass** the rigor gate today —
`moreira_muir_2017_volatility_managed` (DSR p = 0.995) and
`moskowitz_ooi_pedersen_2012_tsmom` (p = 0.976) — and a third comes within 0.006.
So the gate discriminates; it is not rejecting everything reflexively. The
question is only why *these* fail.

The dominant reason is blunt: **7 of the 9 new strategies posted a *negative*
Sharpe on real 2004–2026 data** — they lost money. No validation system should
bless a money-losing backtest, regardless of how cited the source paper is. The
only two non-negative new strategies are dual momentum (+0.10, too weak) and
risk parity (+0.35, the one genuine near-miss).

## The experiment

| Universe | Members | Rationale |
|---|---|---|
| **Original 5** | SPY, ^N225, GC=F, TLT, CL=F | the as-shipped demo universe (cross-asset, mixed markets) |
| **Diversified 10 ETF** | SPY, QQQ, IWM, EFA, EEM, TLT, IEF, GLD, XLE, XLF | bigger, US-listed (synchronous closes) |
| **9 sector ETFs** | XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY | a *homogeneous cross-section* — the fair test for ranking momentum / PCA |
| **8 asset classes** | SPY, EFA, EEM, TLT, IEF, GLD, DBC, VNQ | diversified low-correlation classes — the fair test for risk parity |
| **GEM 5** | SPY, EFA, EEM, TLT, GLD | relative + absolute momentum with a bond defensive leg |

All runs: 2004–2026, 10 bps transaction costs, same engine (`run_multi_backtest`),
same gate.

### Results (Sharpe ratio; **none** clears the gate in any column)

| Strategy | Original 5 | Diversified 10 | Sector 9 | Asset-class 8 | GEM 5 |
|---|---|---|---|---|---|
| Cross-sectional momentum | −0.21 | −0.24 | **−0.89** | — | — |
| Dual momentum | +0.10 | +0.14 | — | — | +0.15 |
| Risk parity | **+0.35** | +0.10 | — | −0.09 | — |
| PCA stat-arb | −0.33 | −1.22 | −1.53 | — | — |

## What this tells us

1. **Universe size is not the binding constraint.** Bigger did not help; the
   diversified-10 and sector-9 sets left every verdict unchanged or worse.
2. **Composition matters far more than count.** Risk parity's best result is on
   the *original 5* (+0.35), because that set is genuinely low-correlation
   (equities + gold + bonds + oil). The "bigger" 10-ETF set is mostly equities,
   so it is *less* diversified in risk terms and risk parity does worse (+0.10).
   Adding broad commodities (DBC) in the 8-asset set dragged it negative.
3. **Sector-rotation momentum has decayed.** On the textbook homogeneous sector
   cross-section it is the *worst* (−0.89): post-2009 momentum crashes plus
   monthly-rebalance costs. Consistent with McLean & Pontiff (2016) on
   post-publication factor decay.
4. **PCA needs scale we don't have.** Even on 9–10 names it over-trades
   (1700–1800 round-trips) and the factor hedge can't neutralize idiosyncratic
   risk at this scale → large drawdowns. AL's method is built for *hundreds* of
   names.
5. **The papers are not "wrong."** They report results at their intended scale
   (broad stock cross-sections, diversified pair portfolios, hundreds of names).
   Our small/medium-universe adaptations are honest demos, not those designs —
   "from a cited paper" and "survives DSR/PBO on this universe today after costs"
   are different claims. Surfacing that gap is the product (rigor-as-wedge).

**Conclusion:** keep the strategies as honest `CANDIDATE`s. Do **not** force a
larger universe onto them — for risk parity that would *degrade* the best
configuration we have. The expanded symbol set added to
`instruments.OPERATION_TO_SYMBOL` is useful infrastructure for *future* strategies
(and for these experiments), not a reason to rewrite the shipped ones.

## The one calibration caveat (own issue)

The single near-miss — risk parity on the original 5 assets (Sharpe +0.35,
max-DD 27%) — fails the DSR significance bar *only* because of how conservatively
we set `num_trials_in_selection`. Sweeping it on that strategy's real returns:

| `num_trials` | DSR p-value | passes p ≥ 0.95? |
|---|---|---|
| 1–13 | 0.999 → 0.963 | **yes** |
| 22 (full library) | 0.941 | no |
| 50 | 0.896 | no |

We currently set `num_trials` = the full library size (22), which penalizes each
strategy as if it were cherry-picked as the best of 22 independent trials. For an
*individually-specified, paper-grounded* strategy that was **not** data-mined from
the library, that is arguably too harsh. This is a genuine design question about
the gate's calibration — written up separately for a team decision (it touches
the shared `rigor_evaluator`, so it is not a unilateral change). Note it is **not**
what blocks the other eight: they fail on performance, not on the penalty.

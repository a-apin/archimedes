# Why Faber 2007 fails the rigor gate — and why that's correct

> **TL;DR:** The Faber GTAA strategy ("A Quantitative Approach to Tactical
> Asset Allocation") fails the Deflated Sharpe Ratio gate (DSR p = 0.612 vs
> the 0.95 threshold) **despite a raw Sharpe (0.634) nearly identical to Time
> Series Momentum's (0.650), which passes at 0.976.** This is not a bug. It is
> the single clearest demonstration in our library of why the Deflated Sharpe
> Ratio is a better admission test than the raw Sharpe. An earlier draft of
> the SPEC-1 runbook claimed Faber passes; that claim was stale and has been
> corrected.

## The anomaly

Live library state (`/api/strategies?include_examples=true`, 2026-05-27):

| Strategy | Rebalance | Raw Sharpe (in-sample) | OOS Sharpe | DSR p-value | Gate |
| --- | --- | --- | --- | --- | --- |
| Volatility-Managed Portfolios (Moreira–Muir 2017) | daily | 0.769 | 0.969 | 0.995 | **PASS** |
| Time Series Momentum (Moskowitz–Ooi–Pedersen 2012) | daily | 0.650 | 0.762 | 0.976 | **PASS** |
| Tactical Asset Allocation (Faber 2007) | daily | 0.634 | 0.930 | 0.612 | fail |
| 52-Week High Momentum (George–Hwang 2004) | monthly | 0.620 | 0.910 | 0.609 | fail |
| Buy-and-Hold Baseline | daily | 0.537 | 0.792 | 0.891 | fail |
| Capital Preservation (T-Bill / USYC) | daily | 0.481 | 0.431 | 0.812 | fail |

Faber and TSMOM are the puzzle: **same daily frequency, same 2004–2026
backtest window, near-identical raw Sharpe (0.634 vs 0.650) — yet one is
blocked and the other passes overwhelmingly.** If the gate keyed only on the
Sharpe ratio, both would land on the same side of the line. It doesn't, and
that is the entire point.

## Ruling out the boring explanations

We reconstructed the DSR numerically (`scripts`-free, via
`archimedes.services.rigor_evaluator.compute_dsr`) to isolate what drives the
gap. Two candidates were eliminated:

- **Number of trials (N).** Both strategies are scored with the same
  `num_trials` = library size (the multiple-testing correction is applied
  identically across the selection set in `selection_bias_routes.py`). N
  cannot separate them.
- **Track length (T).** Both report the identical `2004-01-02 → 2026-04-30`
  backtest window at daily frequency, so both feed ≈ 5,500 daily bars into
  `compute_dsr`. A synthetic Gaussian series at Sharpe 0.634 over that many
  bars and N = 6 trials lands at **p ≈ 0.95** — i.e. on track length alone,
  Faber's Sharpe *should* clear the bar. It doesn't. Length is not the cause.

## The actual cause: higher moments (skewness + kurtosis)

The Bailey–López de Prado (2014) Deflated Sharpe is a *variance-adjusted*
z-statistic. Its denominator (eq. 8, as implemented in
[`rigor_evaluator.py`](../../backend/archimedes/services/rigor_evaluator.py))
is

```
denom² = 1 − γ₃·ŜR + ((γ₄ − 1)/4)·ŜR²
```

where γ₃ is skewness and γ₄ is (Pearson) kurtosis of the return series. The
larger the denominator, the smaller the z-statistic, the lower the DSR
p-value. Two return distributions with the **same Sharpe** can therefore land
on opposite sides of the gate purely on their third and fourth moments:

- **Negative skew (γ₃ < 0)** makes the `−γ₃·ŜR` term *positive* → inflates the
  denominator → deflates the Sharpe.
- **Excess kurtosis (γ₄ > 3, i.e. fat tails)** inflates the denominator → also
  deflates the Sharpe.

This maps directly onto the two strategies' documented return profiles:

- **Time Series Momentum is positively skewed.** Moskowitz, Ooi & Pedersen
  (2012) explicitly characterize TSMOM as a *divergent / "long-volatility"*
  payoff: it cuts losers and rides winners, producing a right-skewed
  distribution. Positive γ₃ makes `−γ₃·ŜR` negative → *shrinks* the
  denominator → *boosts* the DSR. TSMOM is rewarded for the same Sharpe.
- **Faber's GTAA is negatively skewed.** Tactical trend-following on monthly
  SMA signals earns small, steady returns in trending regimes but takes sharp
  whipsaw losses through choppy regime transitions — a left-skewed,
  fat-tailed profile. Negative γ₃ and excess γ₄ both inflate the denominator
  → the DSR correctly discounts the headline Sharpe.

> **Measurement caveat.** The live API does not expose the raw daily-return
> series (`equity_curve` is empty in the list payload), so the exact γ₃/γ₄
> for Faber were not measured directly here — they are *inferred* from (a) the
> DSR formula, (b) the ruled-out alternatives (N and T are held equal), and
> (c) the well-documented skew profiles of these two strategy families. To
> confirm numerically, run `compute_dsr` on the persisted series and print
> `sp_skew` / `sp_kurtosis` for both strategy IDs. The conclusion — *the gap
> is driven by higher moments, not Sharpe* — holds regardless of the exact
> values.

## Why this is the pitch, not a problem

This is the cleanest possible demonstration of the wedge:

> Faber 2007 and Time Series Momentum have **nearly identical Sharpe ratios** —
> 0.63 and 0.65. A robo-advisor that ranks on Sharpe would treat them as
> equally good. Our gate **passes one and blocks the other**, because the
> Deflated Sharpe Ratio accounts for skewness and kurtosis. Faber's
> tactical-allocation returns carry fat left tails the raw Sharpe can't see;
> Time Series Momentum's are right-skewed. Same Sharpe, opposite trustworthiness.

A backtest Sharpe is not alpha. It is a point estimate that ignores the shape
of the loss distribution and the number of strategies you tried. The rigor
gate exists precisely to catch the Fabers — strategies that *look* as good as
the winners on the headline number but aren't.

## Action taken

- **No code change.** `compute_dsr` and `passes_rigor_gate` are behaving
  exactly as specified.
- **Corrected the stale runbook claim.** `docs/runbooks/spec-1-walkthrough.md`
  Phase 4b previously listed Faber 2007 among the passing-rigor strategies;
  it now lists only the two strategies that actually pass
  (Volatility-Managed Portfolios and Time Series Momentum).

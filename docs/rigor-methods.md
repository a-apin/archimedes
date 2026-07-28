# Rigor Methods — How Archimedes Stress-Tests Every Strategy

> **Status:** Shipped — all four gates (DSR, PBO, walk-forward OOS, look-ahead audit) are live in [`services/rigor_evaluator.py`](../backend/archimedes/services/rigor_evaluator.py) (canonical) and gate every Tier-1 strategy. How many strategies pass all four is not recorded in this document — the live rigor gate is the only authority on which strategies currently pass; see the PASS/CANDIDATE badges in the app and `backend/archimedes/services/live_rigor_gate.py`.
> Faber 2007 does *not* pass — see [`analysis/faber-dsr-finding.md`](analysis/faber-dsr-finding.md).
>
> **Audience:** Judges, team members, and anyone reading a strategy passport who is not a quant.
> **Author:** Önder Akkaya (Lead Quant)
> **Date:** 2026-05-19

Every Tier-1 strategy in Archimedes must pass four quantitative gates before it can be
promoted from `candidate` → `validated`. This page explains each one in plain English,
why it matters, and what the number shown in the UI actually means.

---

## Why this matters at all

Most AI finance tools backtest a strategy, see a high Sharpe ratio, and call it done.
The problem: a high Sharpe on historical data is easy to manufacture accidentally.
You can search through hundreds of parameter combinations — moving average windows,
lookback periods, thresholds — until one "works." If you do that, the backtest has
essentially memorized the past rather than discovered a real edge. The strategy will
likely fail going forward.

This is called **overfitting**, and academic research quantifies how common it is.
Bailey et al. (2015, "The Probability of Backtest Overfitting") showed that for a
strategy with 45 trials, over half of seemingly-positive backtests are pure luck.

Archimedes surfaces three metrics — DSR, PBO, and OOS Sharpe — specifically to catch
this. If a strategy looks good on all three, the Sharpe ratio is not an accident.

---

## 1. Deflated Sharpe Ratio (DSR)

**What the gate actually states.** This is the wording the app publishes on the
Architecture page, and it is the only defensible form — docs match the app, not the other
way round:

> Over 20+ years of backtested returns net of realistic commission, a strategy's excess Sharpe
> must be positive at 90% one-sided confidence under standard errors robust to non-normality
> and autocorrelation, and must stay positive on a 30% chronological holdout. On the generated
> path, the Sharpe is additionally deflated against that strategy's own candidate pool.
>
> **On the curated library, `num_trials=1` — DSR runs undeflated (no multiple-testing
> correction).**

**The question it answers:** "Once the standard errors account for the fat tails and
autocorrelation in these returns, is the excess Sharpe distinguishable from zero?"

**The idea in one sentence:** A raw Sharpe ratio is misleading when returns are skewed,
kurtotic or autocorrelated; the Deflated Sharpe Ratio widens the standard error to
account for that, and — *where a search actually happened* — additionally deflates by the
expected best-of-`N` under the null.

**What the number means:**
- DSR is displayed as the **p-value** of the test (range 0–1).
- A p-value ≥ 0.90 clears the bar: the excess Sharpe is positive at 90% one-sided
  confidence under non-normality- and autocorrelation-robust standard errors.
- Below 0.90 means the strategy has not cleared the bar — it may still be a good
  strategy, but we cannot distinguish it from noise with this sample.

**Where the deflation does and does not apply.** On the **generated** path the Sharpe is
deflated against that strategy's own candidate pool — the search we ran is the search we
pay for. On the **curated library** `num_trials = 1`, so `E[max_N] = 0` and DSR runs
**undeflated**: there was no search of ours to charge for, and a published strategy's
score must not change because the library around it grew (see
[`adr/num-trials-self-containment.md`](adr/num-trials-self-containment.md)).
`rigor_evaluator.py` logs this verbatim when it fires. Do not describe the curated gate as
correcting for multiple testing; on that path it does not.

**Disclosure is not correction.** The product *discloses* the board-level selection bias a
user incurs by choosing the best of N displayed strategies; it does not *correct* it.
Benjamini–Hochberg helpers exist in
[`_rigor_helpers.py:1199`](../backend/archimedes/services/_rigor_helpers.py) with **zero
non-test callers** — a written-down, unimplemented decision. Saying otherwise would claim
a control the live path does not run.

**Why it's better than raw Sharpe:** The standard Sharpe assumes returns are normally
distributed, serially independent, and that you only ran one backtest. None of those is
reliably true. DSR relaxes the first two always, and the third when a candidate pool
exists (Bailey & López de Prado 2014).

---

## 2. Probability of Backtest Overfitting (PBO)

**The question it answers:** "If we had used a different slice of history to pick this
strategy, would it still look good on the remainder?"

**The idea in one sentence:** Split the historical data into 16 equal chunks, try all
possible ways to divide those chunks into in-sample and out-of-sample, and count how
often the in-sample winner also wins out-of-sample.

**What the number means:**
- PBO is the fraction of splits where the in-sample winner loses out-of-sample.
- PBO = 0% means the strategy won out-of-sample every time (no overfitting detected).
- PBO = 50% means it's a coin flip — essentially random.
- Archimedes requires PBO < 50% for the Tier-1 gate. Lower is better.

**Why it matters:** A genuinely predictive strategy should win on any slice of data,
not just the one it was tuned on. The CSCV method (Bailey et al. 2014) formalizes this
intuition with C(16,8) = 12,870 different IS/OOS splits.

---

## 3. Out-of-Sample Sharpe (Walk-Forward)

**The question it answers:** "Does the strategy still work on data it has never seen?"

**The idea in one sentence:** Train on the first 70% of history, test on the final 30%
without touching the parameters — chronological order preserved, no peeking.

**What the number means:**
- The OOS Sharpe is just the Sharpe ratio computed on the last 30% of the data.
- Archimedes requires OOS Sharpe ≥ 50% of the in-sample Sharpe.
- A ratio near 1.0 means the strategy held up perfectly out-of-sample.
- A ratio near 0 or negative means the edge evaporated the moment the model stopped
  seeing the training data — a textbook overfit.

**Why it complements DSR/PBO:** DSR and PBO are statistical tests; OOS Sharpe is an
economic test. A strategy can pass the statistics but fail if the Sharpe drops 90%
out-of-sample. The trio together catches more failure modes than any one alone.

---

## 4. Kelly Fraction (f*)

**The question it answers:** "What fraction of your capital should you bet on this
strategy, assuming you want to maximize long-run growth without going broke?"

**The idea in one sentence:** Kelly (1956) derived the mathematically optimal bet size
for repeated positive-expectancy bets; applying it to a continuous return stream gives
the maximum-growth position size.

**Formula used:**
```
f* = 0.5 × (annualized_excess_return) / (annualized_variance)
```

The 0.5 multiplier is **half-Kelly** — a standard risk-management convention because
full Kelly is very aggressive and highly sensitive to parameter estimation error.

**What the number means:**
- Kelly f* = 1.0 means: bet up to your full account (already half-Kelly capped).
- Kelly f* = 0.5 means: deploy 50% of capital into this strategy.
- Kelly f* = 0.0 means: the strategy has negative excess return — do not allocate.
- Values are clipped to [0, 1] since we do not use leverage.

**How it's used:** Kelly fractions drive the MVO (Mean-Variance Optimization)
portfolio construction step. Strategies with higher Kelly fractions receive larger
allocations in the optimizer, subject to diversification and drawdown constraints.

---

## 5. Regime-conditional risk aversion (γ scaling)

**The question it answers:** "Should my portfolio be more defensive when markets are
stressed, even if my declared risk profile hasn't changed?"

**The idea in one sentence:** Your stated risk profile (moderate, aggressive, etc.)
should determine how you invest in *normal times* — but when the regime is stressed,
the optimizer's risk aversion should rise automatically, pulling allocations toward
minimum-variance without requiring you to change your declared preference.

**The math:** The Kelly/MVO objective is `max wᵀ(μ−rf) − ½γwᵀΣw`. The parameter γ
is the risk-aversion coefficient. We map risk profiles to γ baseline values, then
multiply by a regime-conditional factor:

| Risk profile   | Baseline γ | Description |
|---|---|---|
| `fixed_income` | 12.0 | Extreme preservation; dominated by minimum-variance |
| `conservative` | 6.0  | Capital preservation first |
| `moderate`     | 3.0  | Balanced growth and protection |
| `aggressive`   | 2.0  | Growth-oriented, accepts drawdown |
| `hyper_risky`  | 1.5  | Near-full-Kelly; maximum growth intent |

These baseline γ values are then scaled by a **regime multiplier**:

| Regime       | Multiplier | Effective interpretation |
|---|---|---|
| `risk_on`    | 1.0×       | Declared profile is appropriate; full allocation budget |
| `transition` | 1.0×       | Uncertain regime; do not overreact |
| `risk_off`   | 2.0×       | Double effective γ ≈ halve effective Kelly fraction |
| `crisis`     | 4.0×       | Quadruple effective γ ≈ minimum-variance leaning |

So a `moderate` investor in a `risk_off` regime has effective γ = 3.0 × 2.0 = 6.0 —
the same as a `conservative` investor in a normal regime. In a `crisis` regime that
same moderate investor operates at γ = 12.0 — equivalent to `fixed_income`.

**The research grounding:** Ang & Bekaert (2002, "International Asset Allocation With
Regime Shifts", *Review of Financial Studies*) demonstrated that regime-conditioned
portfolio weights strictly dominate static weights across a range of γ specifications.
Their two-state Markov-switching model produces exactly this pattern: risk aversion
should be higher in the bear/crisis state than the bull/calm state, and the magnitude
of the increase is large enough to matter for allocation decisions.

**Calibration note:** The multipliers here (1×/2×/4×) are deliberately conservative
relative to the research-paper values (which sometimes go 6–10× in tail regimes) to
avoid producing wildly different allocations every time the regime detector flips.
This is an engineering judgment, not a claim of optimality — it reflects the
hackathon-stage calibration status of the regime detector.

**What the number means in the UI:** The Portfolio Advisor's allocation table shows
the effective γ applied to each recommendation. When the regime is `risk_off` or
`crisis`, the advisor notes the multiplier applied and which paper it cites.

---

## The four-primitive admission gate

A strategy is promoted to `validated` only if all four conditions hold simultaneously:

| Condition | Threshold |
|---|---|
| DSR p-value | ≥ 0.95 |
| PBO | < 0.50 |
| OOS Sharpe / IS Sharpe | ≥ 0.50 |
| Total trades in backtest | ≥ 10 (avoid sparse-trade illusions) |

The UI displays each number openly for every strategy — including candidates that
have not yet passed. If a strategy fails a gate, you can see exactly which one and
why. This is the design: rigor as transparency, not as a hidden score.

---

## References

- Ang, A., Bekaert, G. (2002). "International Asset Allocation With Regime Shifts."
  *Review of Financial Studies*, 15(4), 1137–1187. *(Regime-conditional γ scaling — §5)*
- Bailey, D.H., Borwein, J., López de Prado, M., Zhu, Q.J. (2014). "Pseudo-Mathematics
  and Financial Charlatanism: The Effects of Backtest Overfitting on Out-of-Sample
  Performance." *Notices of the AMS*, 61(5), 458–471.
- Bailey, D.H., López de Prado, M. (2014). "The Deflated Sharpe Ratio: Correcting for
  Selection Bias, Backtest Overfitting and Non-Normality." *Journal of Portfolio
  Management*, 40(5), 94–107.
- Kelly, J.L. (1956). "A New Interpretation of Information Rate." *Bell System Technical
  Journal*, 35(4), 917–926.
- McLean, R.D., Pontiff, J. (2016). "Does Academic Research Destroy Stock Return
  Predictability?" *Journal of Finance*, 71(1), 5–32.

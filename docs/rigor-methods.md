# Rigor Methods — How Archimedes Stress-Tests Every Strategy

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

**The question it answers:** "After accounting for the fat tails in returns and the
fact that we tested multiple strategy variants, is this Sharpe ratio statistically
significant?"

**The idea in one sentence:** A raw Sharpe ratio is misleading when returns are
skewed or when you've tried many versions of a strategy. DSR corrects both at once.

**What the number means:**
- DSR is displayed as the **p-value** of the test (range 0–1).
- A p-value ≥ 0.95 means we have 95% confidence the Sharpe is not luck.
- Below 0.95 means the strategy has not yet cleared the statistical bar — it may still
  be a good strategy, but we cannot distinguish it from noise with this sample.

**Why it's better than raw Sharpe:** The standard Sharpe assumes returns are normally
distributed and that you only ran one backtest. Neither is true in practice. DSR
applies corrections from Bailey & López de Prado (2014) to penalize both.

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

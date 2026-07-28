# RFB Alignment

> **Date:** 2026-05-12 (Day 2)
> **Audience:** Archimedes hackathon team
> **Purpose:** Map Archimedes against the six [Agora RFBs](https://luma.com/7i50p2r9)
> explicitly, with judging-criteria coverage. This is the doc to cite in the pitch deck
> and during judging Q&A.

## Summary mapping

| RFB                                          | Alignment        | Notes                                                                                |
| -------------------------------------------- | ---------------- | ------------------------------------------------------------------------------------ |
| **01 — Perpetual Futures Trading Agent**     | Skip             | High regulatory risk + demo-fragile. Cited only for "what we deliberately didn't do." |
| **02 — Prediction Market Trader Intelligence** | Math primitive | Kelly Criterion / +EV / Bayesian position sizing — Önder's portfolio math module     |
| **03 — Prediction Market Verticals**         | Skip             | Different product shape (creating markets vs. managing portfolios).                  |
| **04 — Adaptive Portfolio Manager**          | **Primary**      | The bullseye. Every "What the AI decides" + "What builders create" item maps.        |
| **05 — Cross-Platform Arbitrage Agent**      | Skip             | Latency-sensitive; demo would be a recorded video. Not our wedge.                    |
| **06 — Social Trading Intelligence**         | Adjacent showcase | Strategy leaderboard + reasoning-trace-as-the-product = "copy the thinking."         |

## RFB 04 — Adaptive Portfolio Manager (primary)

Direct quotes from the [Agora RFB 04 page](https://luma.com/7i50p2r9), with our coverage:

### "What the AI decides"

| RFB 04 item                                                                       | Archimedes coverage                                                                                                       |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Asset allocation based on market regime (risk-on vs risk-off)                     | Regime detection (VIX, MA crossovers, credit spreads, BTC dominance, correlations, USYC yields). See design.md § 4.3.3.   |
| When to rebalance vs let winners run                                              | Drift thresholds + strategy decay + regime change + calendar — see design.md § 4.3.4. Cost-benefit check before execution.|
| Yield allocation — park capital in USYC during risk-off periods                   | **USYC floor per risk profile**. Conservative 40–60%, Hyper-Risky 0–5%. Risk-off raises the floor automatically.          |
| Tax-loss harvesting opportunities and execution timing                            | v1.5 — flagged in design.md but not on the critical path for demo. We acknowledge but don't ship in v1.                    |
| Correlation-based diversification across DeFi and TradFi                          | Strategy selection optimizes `Sharpe × (1 − correlation_to_portfolio)`. Cross-asset + cross-strategy diversification.       |
| Risk management — reduce exposure during high volatility                          | CRISIS regime → deleverage to USYC floor; TRANSITION → tighten stops, reduce position sizes.                              |

### "What builders create"

| RFB 04 item                                                                       | Archimedes coverage                                                                                                       |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Goal-based portfolio management interfaces                                        | Risk-profile selection (Conservative / Moderate / Aggressive / Hyper-Risky) → portfolio constructed for the profile.       |
| Cross-chain rebalancing infrastructure with CCTP / Gateway                        | RWA token acquisition flow (design.md § 5.3): CCTP for cross-chain USDC, Gateway for RWA tokens back to Arc.              |
| Tax-loss harvesting automation tools                                              | v1.5 (see above).                                                                                                          |
| Regime detection models with automatic allocation adjustment                      | Continuous regime monitoring + auto-rebalance triggers.                                                                    |

### "Example builds" comparison

- **AdaptiveFolio** — "set goals, AI manages everything." Archimedes does this, plus academic
  provenance for every strategy.
- **RegimeShift** — "detects market regime changes." Archimedes does this **with the
  detection rule itself sourced from regime-detection literature, traceable to specific
  papers**.

### Traction metrics

We can credibly target:

| RFB 04 metric                                  | Our target                                                |
| ---------------------------------------------- | --------------------------------------------------------- |
| Number of users                                | 30–50+ (per design.md § 9 traction strategy)              |
| Assets under management                        | Demonstrable testnet USDC AUM                             |
| Returns vs benchmark (Bitcoin, S&P 500)        | Curated strategies selected for backtest outperformance   |
| Portfolio turnover and rebalancing frequency   | 10+ live autonomous rebalances in Week 2 demo period      |

### Judging-criteria mapping

| Agora criterion (weight)              | RFB 04 coverage in Archimedes                                                                                                     |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Agentic Sophistication (30%)          | Full autonomy: regime detection, autonomous rebalancing, strategy rotation, **on-chain reasoning traces** for every decision.       |
| Traction (30%)                        | Pre-curated strategies enable day-1 portfolios. Simple onboarding. Strategy leaderboard creates discovery loop.                    |
| Circle Tool Usage (20%)               | Wallets, USYC, CCTP, Gateway, Paymaster, Contracts — **deep integration across the Circle stack**.                                |
| Innovation (20%)                      | Paper-grounded provenance for every strategy + **on-chain reasoning traces** = academic accountability for autonomous trading.    |

## RFB 02 — Prediction Market Trader Intelligence (math primitive)

Direct citation in the pitch deck, even though we're not building a prediction-market
product. The Kelly Criterion / +EV / Bayesian position sizing primitives that drive
Archimedes' portfolio math are structurally the RFB 02 mechanic applied inside RFB 04.

Önder's module covers:

- **Kelly Criterion sizing** for each strategy's allocation within a risk profile —
  preventing over-concentration even when a strategy looks great.
- **Bayesian posterior on strategy alpha** — given each strategy's backtest plus its
  rolling live performance, update belief about its forward-looking edge. Underperformers
  decay in weight; outperformers earn weight, capped by Kelly half-fraction.
- **Information-source credibility weighting** — RFB 02 calls this out explicitly. In
  Archimedes, this becomes "paper credibility weighting" — how heavily we trust a
  strategy's claimed alpha based on the paper's source venue, citation count, and our
  re-validated backtest delta vs. the paper's claimed Sharpe.

The pitch deck cites RFB 02 explicitly when we describe Önder's math module. This positions
Archimedes as taking the best primitive from two RFBs.

## RFB 06 — Social Trading Intelligence (adjacent showcase)

Archimedes is not a copy-trading platform, but the strategy-performance leaderboard
[design.md § 4.2](../design.md) is structurally a "follow top-performing strategies" surface.
The differentiation from RFB 06's example builds:

- **SmartMirror** picks top 5 traders by risk profile. **Archimedes** picks top N strategies
  with paper-grounded provenance.
- **SignalAggregator** combines signals from many traders. **Archimedes** combines
  paper-grounded signals with correlation-aware portfolio construction.
- **CopyProtect** copies winning strategies with AI risk limits. **Archimedes** allocates to
  strategies with Kelly-bounded risk + USYC floor + regime-aware position adjustment.

The pitch deck mentions RFB 06 in the "platform extensibility" slide — "the strategy
leaderboard could be productized as a social trading surface in v2."

## RFBs we explicitly do not pursue

### RFB 01 — Perpetual Futures Trading Agent

**Why not:** High regulatory exposure, demo-fragile (liquidation events look bad on stage),
requires real-time risk infrastructure we can't ship cleanly. The RFB 04 framing achieves
similar agentic sophistication without the leverage failure modes.

**What we say if a judge asks:** "Perp trading agents are exciting but the reputation
problem is hardest there — leaderboard rank doesn't persist out-of-sample (as Canteen's
own slash-bond RFB iii in the cold-email examples acknowledges). Archimedes addresses the
same agentic-portfolio thesis with spot + RWA + USYC, where verifiable history is more
defensible than predicted performance."

### RFB 03 — Prediction Market Verticals

**Why not:** Different product shape — creating new markets vs. managing portfolios. We'd
use the prediction-market translation primitive (Canteen RFB iv from the cold email) if
we built a translation agent, but our team's load-bearing skills point at portfolio
management.

### RFB 05 — Cross-Platform Arbitrage Agent

**Why not:** Latency-sensitive; demo would be a recorded video or stale screenshot.
Doesn't showcase paper-grounded reasoning (arbitrage strategies don't come from academic
research the way portfolio-construction strategies do).

## How to use this doc

- **In the pitch deck:** Slide 2 ("Why this fits the hackathon") cites RFB 04 primary
  + RFB 02 math + RFB 06 adjacent, with one-line justifications. The judging-criteria
  table goes on Slide 6 ("Why we'll score well").
- **In the live judging Q&A:** When asked "which RFB are you?", answer "RFB 04 primary,
  with RFB 02's math primitive and RFB 06's leaderboard adjacent."
- **When red-teaming Archimedes' fit:** Use the "what builders create" / "what the AI
  decides" tables above — if Archimedes doesn't credibly cover an RFB 04 line, that's a
  scope gap to address.

## Open data points

- Whether the Agora judges weight "Adjacent RFB coverage" positively. **Default
  assumption:** primary RFB + one strong adjacent reads better than primary RFB alone.
- Whether traction is measured separately for each RFB or in aggregate. **Default
  assumption:** aggregate, judged across the full submission.

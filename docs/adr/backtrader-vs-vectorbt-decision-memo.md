# Backtesting Library — Decision Memo

> **Audience:** Archimedes hackathon team (decision owners: Dan + Önder + Chuan)
> **Status:** Open decision. Surface for the team to ratify in Day 3 sync.
> **Question being decided:** Which backtesting library/engine for v1?

## TL;DR

**Recommendation: ship with backtrader for v1.** Faster path to working strategies given
Dan's familiarity, mature ecosystem, well-documented Strategy API. Migrate to vectorbt for
v2 if we hit speed limits. Custom numpy is overkill for the hackathon timeline.

## The open question

Chuan's [`../design.md` § 6](../archive/agora-2026-05/design.md) names "vectorbt / custom numpy engine" for
backtesting. Dan independently suggested [backtrader](https://github.com/mementum/backtrader).
Both are credible Python backtesting libraries with different tradeoffs. We need to pick
one before strategy implementation starts in earnest (per the Week-1 roadmap, that's
Day 3–4).

## The three candidates

### backtrader

[backtrader](https://github.com/mementum/backtrader) (Daniel Rodriguez, 2015–present;
~15k GitHub stars) is the dominant Python event-driven backtesting library.

**API shape:**

```python
import backtrader as bt

class MomentumStrategy(bt.Strategy):
    def __init__(self):
        self.sma = bt.indicators.SimpleMovingAverage(period=15)

    def next(self):
        if self.sma[0] > self.data.close[0]:
            if not self.position:
                self.buy()
        elif self.sma[0] < self.data.close[0]:
            if self.position:
                self.close()

    def notify_order(self, order):
        if order.status in [order.Completed]:
            pass

cerebro = bt.Cerebro()
cerebro.addstrategy(MomentumStrategy)
cerebro.adddata(data_feed)
cerebro.run()
```

**Pros:**

- **Mature, stable, well-documented.** [Strategy docs](https://www.backtrader.com/docu/strategy/)
  are comprehensive. Lifecycle is well-defined: `__init__`, `start`, `prenext`,
  `nextstart`, `next`, `stop`, `notify_order`, `notify_trade`.
- **Rich built-in indicators (122)** plus TA-Lib integration if needed.
- **Realistic execution model.** Order types, slippage, commission schemes built in.
- **Analyzers** (TimeReturn, Sharpe, SQN) and Observers handle metrics out of the box.
- **Dan is familiar with it.** Lower learning curve for the team's strategy curator.
- **Strategy class lends itself to LLM-generated code.** A Strategy subclass is a
  bounded API contract; the arxiv-paper-to-strategy pipeline outputs a Strategy subclass
  that we can validate by static analysis.
- **Event-driven model matches the live agent.** The agent's runtime decisions look like
  Strategy.next() — easy to bridge backtest to live.

**Cons:**

- **Speed.** Event-driven loop runs bar-by-bar; 10-year daily backtests on 100 assets
  take seconds-to-minutes, not milliseconds. For our scale (5–10 strategies × 10
  years × ~100 assets), this is fine. For a future "run 10,000 candidate strategies"
  pipeline, it gets slow.
- **No native vectorization.** numpy under the hood but not pandas-vectorized end-to-end.
- **Active maintenance level is low.** Last significant release was years ago (project
  is stable; not "abandoned" but not actively evolving). Bug fixes happen; new features
  don't. For our use case, this is acceptable.

### vectorbt

[vectorbt](https://github.com/polakowo/vectorbt) (Oleg Polakow, 2019–present; ~5k stars)
is a vectorized backtesting library focused on speed and pandas-native workflow.

**API shape:**

```python
import vectorbt as vbt

# Vectorized: compute signals across the entire price series at once
close = price_data['close']
fast_ma = close.rolling(window=20).mean()
slow_ma = close.rolling(window=50).mean()
entries = fast_ma > slow_ma
exits = fast_ma < slow_ma

# Run portfolio simulation
portfolio = vbt.Portfolio.from_signals(close, entries, exits)
print(portfolio.stats())
```

**Pros:**

- **Fast.** Vectorized end-to-end; 10-year daily backtests on hundreds of assets in
  seconds.
- **Parameter sweeps are first-class.** Easy to backtest 1000 parameter combinations in
  parallel.
- **Pandas-native.** Natural for data scientists already in the pandas/numpy world.
- **Strong portfolio analytics.** Built-in Sharpe, Sortino, max drawdown, drawdown
  duration, etc.
- **Active maintenance.** Modern Python idioms; type hints; PyPI releases.

**Cons:**

- **Vectorized API is harder to translate from academic paper.** Most quant papers describe
  strategies as "at each time t, do X" — event-driven thinking. Translating that to
  vectorized signals/exits is non-trivial for complex strategies (regime switches,
  multi-leg positions, conditional logic).
- **Less natural for live trading.** The live agent's decision loop is event-driven; a
  vectorized backtest is structurally different from the live runtime, which means
  backtest results may not match live behavior exactly.
- **LLM code generation is harder.** Generating vectorized pandas code that's correct is
  trickier than generating an event-driven Strategy subclass.
- **Smaller community + fewer tutorials.** The ecosystem is real but narrower.

### Custom numpy engine

A bespoke event-driven engine built on numpy/pandas for our specific needs.

**Pros:**

- **Tailored to our schema** (strategy passport, paper-claim binding, etc.).
- **No external dependency surface.**
- **Can be optimized exactly for our use case.**

**Cons:**

- **Maintenance burden.** We're writing infrastructure instead of strategies.
- **Hackathon timeline.** Building a backtesting library is a side project; we have 12
  days.
- **Bug surface.** Event-driven backtesting has well-known gotchas (look-ahead bias,
  survivorship bias, transaction-cost handling); we'd reinvent them.

**Verdict on custom numpy: NO.** Too much engineering for a hackathon. Use a library.

## Decision criteria

What matters in the next 12 days:

1. **Time to first working strategy.** We need 5–10 strategies implemented and backtested
   by Day 4–5. The library with the lowest learning-curve overhead for *Dan* wins —
   because Dan is the strategy curator + implementer.
2. **LLM-generated strategy code feasibility.** The arxiv ingest pipeline (demo-only, but
   real) needs to extract strategies from papers and generate runnable code. Which API is
   easier for an LLM to target?
3. **Backtest realism.** Transaction costs, slippage, realistic order execution matters
   for trustworthy backtest claims.
4. **Path to live trading.** The live agent's decision loop should structurally match the
   backtest engine — otherwise backtest claims don't transfer to live.

| Criterion                                     | backtrader  | vectorbt   | custom-numpy |
| --------------------------------------------- | ----------- | ---------- | ------------ |
| Dan's familiarity                             | High        | Medium     | n/a          |
| Time to first strategy (Days 3–4)             | **Fastest** | Medium     | Slow         |
| LLM-friendly code generation                  | **Yes**     | Harder     | n/a          |
| Backtest realism (execution model, costs)     | **Strong**  | Adequate   | DIY          |
| Match to live agent loop                      | **Strong**  | Weaker     | Tunable      |
| Speed (5–10 strategies × 10 years × ~100 assets) | OK       | **Fastest**| Tunable      |
| Parameter sweep capability                    | OK          | **Strong** | DIY          |
| Community + docs + tutorials                  | **Mature**  | Real but smaller | n/a    |

**Backtrader wins on 5 of 7 criteria for the v1 hackathon timeline.** vectorbt wins on
speed and parameter sweeps, which matter for v2 ("run a thousand candidate strategies")
but not for v1 ("ship a curated 5–10 strategies").

## Recommendation

**Use backtrader for v1.**

- **Day 3–4:** Dan implements 5–10 strategies as `bt.Strategy` subclasses. Each has the
  paper-claim binding (arxiv_id, methodology_text, methodology_hash). Each strategy's
  `__init__`, `next`, and `notify_order` are well-commented to make the methodology
  inspection straightforward.
- **Day 5:** Önder wires the backtest runner to produce `BacktestResult` dataclass output
  per Chuan's design.md § 4.2, with the additional fields from
  [`strategy-passport-spec.md`](../specs/strategy-passport-spec.md) (paper_claimed comparison,
  out_of_sample_sharpe, look_ahead_audit_passed, etc.).
- **Day 8–9:** The live agent's decision loop is implemented to structurally match the
  backtrader Strategy lifecycle (regime check → strategy selection → next() →
  notify_order()). Decisions get a `reasoning_traces` row per the passport spec.

### Architectural tradeoff we're accepting

If we hit a "want to backtest 10,000 candidate strategies for the arxiv pipeline" scaling
problem, backtrader will be too slow. **The mitigation:** the arxiv pipeline is v1 demo-only,
running on 2–3 papers manually. Scale becomes a v2 problem when we productize the pipeline,
at which point we can migrate to vectorbt without disrupting the live agent (because the
live agent runs on the live engine, not the candidate-screening engine).

### Migration path if we pick wrong

Wrap the Strategy interface in our own dataclass (which we're doing anyway via
`strategies` table). If we later need vectorbt, we write an adapter that converts our
canonical strategy definition into a vectorbt signal/entry/exit pattern. Backtrader
strategies can be expressed in vectorbt form for most cases, just with effort. The
abstraction makes future migration tractable without locking us in.

### What if the team disagrees

Counter-arguments worth considering:

- **"vectorbt's speed pays for itself."** True for parameter sweeps in v2. Not true for
  the v1 demo's 5–10 strategies.
- **"Dan should learn vectorbt — it's the modern way."** Reasonable but the hackathon's
  12-day timeline doesn't reward a learning curve. Curate v1 with backtrader; learn
  vectorbt for v2.
- **"Custom numpy is more powerful."** True in the limit; not relevant for 12 days. We're
  not in the business of writing a backtesting library.

If the team strongly disagrees, the cost of the disagreement is 0.5–1 day of Dan ramping
on vectorbt. Acceptable. But the recommendation stands.

## Decision

**Use backtrader for v1.** Confirm in Day 3 sync; if no objections, lock and move on.

If team chooses vectorbt instead, that's fine — same passport schema works; the
`BacktestResult` fields are engine-agnostic; the `backtest_engine` field on
`backtest_results` tracks which engine produced the result.

## Open questions

1. **Multi-leg strategies (long X, short Y).** Backtrader handles this via multiple
   `bt.feeds` and `self.datas` indexing. Document the pattern for Dan's reference.
2. **Live data feeds.** Backtrader supports IB, Oanda, etc. natively; we may not need a
   live data feed for v1 if we're using stored daily prices for portfolio decisions.
   Decide based on demo cadence.
3. **Cross-strategy correlation computation.** Backtrader doesn't natively run multiple
   strategies in one Cerebro to compute correlation; we do that in our wrapper after
   running each strategy separately. Document the pattern.

> **ARCHIVED 2026-07-28 — historical. Current: [`docs/architecture.md`](../../architecture.md)**
>
> The original single-vault architecture spec. Superseded end to end by the architecture map.

# Archimedes — Design Document

**Fund-of-Funds Portfolio Agent Powered by Academic Research**

> **Status:** Architecture lineage doc — **superseded for product framing** by [`user-stories.md`](../../user-stories.md); the architecture for everything below the product layer remains authoritative. Component-level shipped state in [`chuan-architecture-survey.md`](chuan-architecture-survey.md). Retained so reviewers can trace the design history.
>
> ⚠️ **Superseded for product framing.** This is the original single-vault design
> document, retained for architecture lineage. The **canonical product spine** — and
> the retirement of the "connect wallet → pick a risk profile" flow — is
> [`user-stories.md`](../../user-stories.md). Read that first for what the product *is*.
>
> 📌 **Day-10 update (2026-05-22).** Component-level shipped state for everything
> described below is in [`chuan-architecture-survey.md`](chuan-architecture-survey.md);
> the corpus deep-dive supersedes § 4 on the strategy engine for what's actually
> live ([`corpus-architecture.md`](../../corpus-architecture.md)). The four selection-bias
> primitives in § 4.2 are live and have 2 Tier-1 strategies passing today. Sections
> 4.3 (Portfolio Agent) and 4.4 (Reasoning Trace) describe the *direction*; the
> shipped state has gone past — see the agentic advisor (`portfolio_agent.py`,
> 850 lines) and the live trace publisher / Registry contract for what's running.

Agora Agents Hackathon | Canteen x Circle x Arc | May 11–25, 2026

---

## 1. Vision

Archimedes is an autonomous portfolio agent that turns quantitative finance research into investable strategies. It scans arxiv q-fin papers, extracts trading strategies, backtests them, and constructs personalized portfolios of RWA tokens (equities, ETFs, commodities, bonds) and yield instruments — all settled on Arc with USDC.

The name references the mathematician who said "give me a lever long enough and I shall move the world." The lever here is academic research; the fulcrum is autonomous AI; the world is your portfolio.

### Core Loop

```
arxiv q-fin papers
    → AI extracts strategy logic
    → backtest against historical data
    → rank strategies by risk-adjusted return
    → user specifies risk profile
    → AI constructs portfolio from RWA tokens on Arc
    → AI autonomously rebalances, rotates strategies, detects regime changes
    → reasoning traces hashed to Arc for transparency
```

---

## 2. Hackathon Fit

### Primary RFB: 04 — Adaptive Portfolio Manager

| RFB 04 Requirement | Archimedes Coverage |
|---|---|
| Asset allocation based on market regime | Regime detection model switches risk-on/risk-off allocations |
| When to rebalance vs let winners run | AI decides rebalance triggers based on drift thresholds and strategy decay |
| Yield allocation — park capital in USYC during risk-off | USYC as the risk-off anchor in every portfolio tier |
| Tax-loss harvesting | Harvest underperforming positions when replacement strategies are available |
| Correlation-based diversification | Strategy selection optimizes for low cross-correlation |

### Adjacent: RFB 06 — Social Trading Intelligence

Strategy performance leaderboard lets users follow top-performing strategies. Reasoning traces make the "why" transparent — users copy thinking, not just trades.

### Judging Criteria Alignment

| Criterion | Weight | How We Score |
|---|---|---|
| **Agentic Sophistication** | 30% | Full autonomy: regime detection, autonomous rebalancing, strategy rotation, live reasoning traces. AI decides after deployment, not just before. |
| **Traction** | 30% | Pre-curated strategies let users trade day 1. Simple onboarding: connect wallet → pick risk profile → deploy. |
| **Circle Tool Usage** | 20% | Wallets, CCTP, Gateway, USYC, Paymaster, Contracts — deep integration across the stack. |
| **Innovation** | 20% | Arxiv-to-strategy pipeline is novel. On-chain reasoning traces tie to Research #01 (Trading-R1). Academic provenance for every strategy. |

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ARCHIMEDES PLATFORM                         │
│                                                                     │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────────────┐  │
│  │   Strategy    │    │  Backtesting  │    │   Portfolio Agent     │  │
│  │   Engine      │───▶│  Engine       │───▶│   (Live Autonomous)  │  │
│  │              │    │              │    │                       │  │
│  │  arxiv scan  │    │  historical  │    │  regime detection    │  │
│  │  paper parse │    │  data replay │    │  rebalancing         │  │
│  │  code gen    │    │  metrics     │    │  strategy rotation   │  │
│  └──────────────┘    └──────────────┘    │  reasoning traces    │  │
│         │                   │            └───────────┬───────────┘  │
│         │                   │                        │              │
│         ▼                   ▼                        ▼              │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Strategy Database                        │   │
│  │  strategies, backtest results, performance metrics,         │   │
│  │  reasoning traces, regime state                             │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                     Web Interface                           │   │
│  │  risk profiling, portfolio dashboard, strategy explorer,    │   │
│  │  reasoning trace viewer, performance charts                 │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
└──────────────────────────────┼──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          ARC BLOCKCHAIN                             │
│                                                                     │
│  ┌────────────┐  ┌────────────┐  ┌─────────┐  ┌────────────────┐  │
│  │  Portfolio  │  │  Reasoning │  │  USYC   │  │  RWA Tokens    │  │
│  │  Contracts │  │  Trace     │  │  Yield  │  │  (via Gateway/ │  │
│  │            │  │  Registry  │  │         │  │   CCTP bridge) │  │
│  └────────────┘  └────────────┘  └─────────┘  └────────────────┘  │
│                                                                     │
│  Settlement: USDC  │  Fees: ~$0.01 via Paymaster  │  Finality: <1s │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Component Design

### 4.1 Strategy Engine

**Purpose:** Extract trading strategies from academic papers and convert them into executable code.

**MVP approach:** Ship with 5–10 pre-curated strategies that have proven backtests. The arxiv pipeline runs as a demo feature on 2–3 papers to show the concept.

#### Pipeline

```
1. Paper Ingestion
   - Fetch recent papers from arxiv q-fin (RSS + API)
   - Filter by categories: PM, TR, CP, RM (Portfolio Management,
     Trading, Computational Finance, Risk Management)
   - Extract: abstract, methodology, claimed returns, asset universe

2. Strategy Extraction (LLM)
   - Parse paper methodology into structured strategy definition
   - Output: entry/exit signals, position sizing, asset universe,
     rebalance frequency, risk constraints
   - Validate: reject strategies requiring unavailable data or
     unrealistic assumptions

3. Code Generation
   - Convert strategy definition into executable backtest code
   - Target framework: vectorbt or custom numpy-based engine
   - Include paper citation and methodology hash for provenance

4. Validation Gate
   - Static analysis of generated code (no network calls, bounded loops)
   - Sandbox execution on small data slice
   - Reject if: runtime errors, unrealistic returns (>1000% annual),
     look-ahead bias detected
```

#### Strategy Schema

```python
@dataclass
class Strategy:
    id: str                          # deterministic hash of paper + methodology
    paper_arxiv_id: str              # e.g. "2509.11420"
    paper_title: str
    methodology_summary: str         # 2-3 sentence plain english
    asset_universe: list[str]        # ticker symbols
    signals: dict                    # entry/exit signal definitions
    position_sizing: str             # equal_weight | risk_parity | kelly
    rebalance_frequency: str         # daily | weekly | monthly
    risk_constraints: dict           # max_drawdown, max_leverage, etc.
    backtest_results: BacktestResult
    status: str                      # candidate | validated | live | retired
    reasoning_trace: str             # full LLM reasoning for extraction
```

### 4.2 Backtesting Engine

**Purpose:** Validate strategies against historical data and produce standardized performance metrics.

#### Metrics

```python
@dataclass
class BacktestResult:
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    cagr: float
    calmar_ratio: float              # CAGR / max_drawdown
    win_rate: float
    profit_factor: float
    total_trades: int
    avg_holding_period_days: float
    correlation_to_spy: float        # diversification signal
    correlation_to_btc: float
    equity_curve: list[float]        # daily equity values
    monthly_returns: list[float]
    backtest_start: date
    backtest_end: date
    paper_claimed_sharpe: float | None  # what the paper said vs reality
```

#### Backtest Integrity

- Walk-forward validation: train on 70%, test on 30%, no peeking
- Transaction costs: 10bps per trade (conservative estimate for RWA tokens)
- Slippage model: volume-based, reject strategies needing >5% of daily volume
- No survivorship bias: include delisted assets in historical data
- Paper claim comparison: flag strategies where backtest Sharpe < 50% of paper's claimed Sharpe

### 4.3 Portfolio Agent (Live Autonomous)

This is the core agentic component — where the AI makes real decisions post-deployment.

#### 4.3.1 Risk Profiling

User selects a risk profile on onboarding:

| Profile | Target Vol | Max Drawdown | USYC Floor | Strategy Types |
|---|---|---|---|---|
| **Conservative** | 5–8% | 10% | 40–60% | Low-vol momentum, mean reversion, bond-heavy |
| **Moderate** | 10–15% | 20% | 20–40% | Balanced factor exposure, trend following |
| **Aggressive** | 20–30% | 35% | 5–15% | High-conviction momentum, concentrated bets |
| **Hyper-Risky** | 30%+ | 50% | 0–5% | Leveraged strategies, sector concentration |

USYC floor = minimum allocation to USYC yield at all times, ensuring a baseline yield even in drawdown.

#### 4.3.2 Portfolio Construction

```
Input: risk_profile, available_strategies[], current_market_regime

1. Filter strategies by risk profile compatibility
2. Rank by risk-adjusted return (Sharpe * (1 - correlation_to_portfolio))
3. Select top N strategies (N = 3–8 depending on profile)
4. Optimize weights:
   - Minimize portfolio variance subject to target return
   - Constraint: max 30% in any single strategy
   - Constraint: USYC floor per risk profile
   - Constraint: max sector/asset concentration
5. Map strategy weights to RWA token allocations
6. Output: {token: weight} portfolio + reasoning trace
```

#### 4.3.3 Regime Detection

The agent continuously monitors market conditions and classifies the current regime:

```
Regimes:
  RISK_ON        — low VIX, positive momentum, tight credit spreads
  RISK_OFF       — high VIX, negative momentum, widening spreads
  TRANSITION     — mixed signals, increasing uncertainty
  CRISIS         — extreme VIX, correlation spike, flight to safety

Signals monitored:
  - VIX level and rate of change
  - S&P 500 50/200 day MA crossover
  - Credit spread (IG and HY)
  - BTC dominance (crypto-specific risk gauge)
  - Cross-asset correlation (rising = risk-off)
  - USYC yield changes (rate environment signal)

Actions per regime:
  RISK_ON     → increase equity/crypto weight, reduce USYC
  RISK_OFF    → increase USYC weight, shift to defensive strategies
  TRANSITION  → tighten stops, reduce position sizes, hold
  CRISIS      → emergency deleverage to USYC floor + max USYC
```

#### 4.3.4 Autonomous Rebalancing

Triggers (any one triggers a rebalance evaluation):
- **Drift threshold:** any position drifts >5% from target weight
- **Regime change:** regime classification changes
- **Strategy decay:** any strategy's rolling 30d Sharpe drops below 0.5
- **Calendar:** weekly check regardless of other triggers

Decision logic:
```
on_rebalance_trigger(trigger):
  current = get_current_portfolio()
  target = construct_portfolio(risk_profile, strategies, regime)
  
  trades = diff(current, target)
  cost = estimate_trade_cost(trades)  # fees + slippage
  benefit = expected_improvement(current, target)
  
  if benefit > 2 * cost:
    execute_rebalance(trades)
    publish_reasoning_trace(trigger, current, target, trades)
  else:
    log("rebalance skipped: cost {cost} > benefit {benefit}")
```

#### 4.3.5 Strategy Rotation

When a live strategy underperforms:
```
1. Monitor rolling 30-day Sharpe for each active strategy
2. If Sharpe < 0.5 for 7 consecutive days → flag for review
3. AI evaluates:
   - Is underperformance regime-specific? (expected in current regime → hold)
   - Is the strategy's thesis broken? (structural change → rotate out)
   - Is there a better-performing strategy with low correlation? → rotate in
4. Execute rotation as part of next rebalance
5. Publish reasoning trace explaining the decision
```

### 4.4 Reasoning Trace Publisher

Every agent decision produces a structured reasoning trace that gets hashed to Arc.

```python
@dataclass
class ReasoningTrace:
    id: str
    timestamp: datetime
    decision_type: str           # rebalance | rotation | regime_change | construction
    trigger: str                 # what caused this decision
    market_context: dict         # regime, key metrics at decision time
    reasoning: str               # LLM-generated explanation
    action_taken: dict           # trades executed
    expected_outcome: str        # what the agent expects to happen
    confidence: float            # 0-1
    trace_hash: str              # SHA-256 of the full trace
    arc_tx_hash: str | None      # Arc transaction recording this trace
```

**On-chain publishing flow:**
1. Agent produces reasoning trace
2. Hash the full trace (SHA-256)
3. Store full trace in off-chain DB (IPFS or own storage)
4. Record hash + metadata on Arc via smart contract (~$0.01 per trace)
5. Users can verify any historical decision against the on-chain hash

This directly implements Research #01 (Trading-R1) — the reasoning trace as the product.

---

## 5. Arc / Circle Integration

### 5.1 Integration Map

| Circle Tool | Usage | Priority |
|---|---|---|
| **Wallets** | User wallet creation on signup. Agent-managed wallets for portfolio execution. Programmatic key management for autonomous trading. | P0 — Core |
| **USYC** | Risk-off anchor in every portfolio. Idle capital parks in USYC between trades. Yield accrues automatically. | P0 — Core |
| **Gateway** | Bridge RWA tokens from source chains (Ethereum, Polygon) to Arc. Sub-500ms cross-chain transfers for rebalancing. Unified USDC balance across chains. | P0 — Core |
| **CCTP** | Cross-chain USDC movement for acquiring RWA tokens on their native chains before bridging. Multi-venue collateral management. | P0 — Core |
| **Paymaster** | All transaction fees paid in USDC. Users never need to acquire gas tokens. Clean UX for non-crypto-native users. | P1 — UX |
| **Contracts** | Portfolio management smart contracts on Arc. Reasoning trace registry. Rebalance execution logic. | P0 — Core |
| **App Kit** | Bridge component for cross-chain RWA acquisition. Swap component for on-chain token exchanges. Send for portfolio distributions. | P1 — UX |
| **USDC/EURC** | Native settlement currency. Multi-currency support for international users (EURC for EU-based users). | P0 — Core |

### 5.2 Smart Contract Architecture

```
ArchimedesVault.sol
├── deposit(amount: uint256)           — user deposits USDC
├── withdraw(amount: uint256)          — user withdraws USDC
├── setRiskProfile(profile: uint8)     — user sets risk preference
├── rebalance(trades: Trade[])         — agent executes rebalance (agent-only)
├── getPortfolio(user: address)        — view current holdings
└── getPerformance(user: address)      — view historical returns

ReasoningTraceRegistry.sol
├── publishTrace(hash: bytes32, metadata: bytes)  — record decision hash
├── verifyTrace(id: uint256, fullTrace: bytes)     — verify against hash
└── getTraces(agent: address, from: uint, to: uint) — query history

StrategyRegistry.sol
├── registerStrategy(id: bytes32, metadata: bytes) — register new strategy
├── updatePerformance(id: bytes32, metrics: bytes) — update live metrics
├── retireStrategy(id: bytes32)                     — mark strategy as retired
└── getStrategy(id: bytes32)                        — query strategy info
```

### 5.3 RWA Token Acquisition Flow

```
User deposits USDC on Arc
    → Agent determines target portfolio (e.g., 30% TSLA, 20% SPY, 20% GLD, 30% USYC)
    → For each RWA token:
        1. CCTP: move USDC from Arc to source chain (e.g., Ethereum for tokenized TSLA)
        2. Swap USDC for RWA token on source chain DEX/protocol
        3. Gateway: bridge RWA token back to Arc (sub-500ms)
    → USYC: mint directly on Arc (native)
    → Portfolio is live on Arc, all holdings visible in user dashboard
    → Rebalancing follows same flow in reverse as needed
```

---

## 6. Tech Stack

| Layer | Technology | Rationale |
|---|---|---|
| **Backend** | Python (FastAPI) | Fastest iteration for 2-week hackathon. Rich quant ecosystem (numpy, pandas, scipy). |
| **LLM** | Claude API | Strategy extraction, reasoning trace generation, user interaction. |
| **Backtesting** | vectorbt / custom numpy engine | Vectorized backtesting for speed. |
| **Database** | PostgreSQL + Redis | Postgres for strategies/backtests. Redis for live market state and regime cache. |
| **Frontend** | Next.js + TailwindCSS | Fast UI development. Chart.js or Recharts for portfolio visualization. |
| **Smart Contracts** | Solidity (Arc EVM-compatible) | Portfolio vault, reasoning trace registry, strategy registry. |
| **Market Data** | yfinance + CoinGecko + custom feeds | Historical + live price data for backtesting and regime detection. |
| **On-chain** | Circle SDK (Wallets, CCTP, Gateway, Paymaster) | Full Circle developer platform integration. |
| **Deployment** | Docker + fly.io / Railway | Quick deployment for hackathon demo. |

---

## 7. Data Model

```
┌────────────────────┐     ┌────────────────────┐
│     strategies      │     │   backtest_results  │
├────────────────────┤     ├────────────────────┤
│ id (PK)            │────▶│ strategy_id (FK)    │
│ arxiv_id           │     │ sharpe_ratio        │
│ paper_title        │     │ sortino_ratio       │
│ methodology        │     │ max_drawdown        │
│ asset_universe[]   │     │ cagr                │
│ signals (JSON)     │     │ equity_curve[]      │
│ rebalance_freq     │     │ backtest_start      │
│ status             │     │ backtest_end        │
│ reasoning_trace    │     │ paper_claimed_sharpe│
│ created_at         │     └────────────────────┘
└────────────────────┘
         │
         ▼
┌────────────────────┐     ┌────────────────────┐
│     portfolios      │     │  reasoning_traces   │
├────────────────────┤     ├────────────────────┤
│ id (PK)            │     │ id (PK)             │
│ user_address       │     │ portfolio_id (FK)   │
│ risk_profile       │     │ decision_type       │
│ strategy_weights   │     │ trigger             │
│ token_allocations  │     │ market_context      │
│ current_regime     │     │ reasoning_text      │
│ total_value_usdc   │     │ action_taken        │
│ created_at         │     │ trace_hash          │
│ last_rebalance     │     │ arc_tx_hash         │
└────────────────────┘     │ created_at          │
                           └────────────────────┘
```

---

## 8. Two-Week Roadmap

### Week 1: Foundation (May 11–18)

| Day | Milestone |
|---|---|
| **1–2** | Project setup: repo structure, smart contract scaffolding, DB schema, API skeleton. Deploy to Arc testnet. |
| **3–4** | Pre-curated strategies: implement 5–10 proven quant strategies manually. Run full backtests. Populate strategy DB. |
| **5** | Portfolio construction engine: risk profiling, strategy selection, weight optimization. |
| **6** | Smart contracts: deploy ArchimedesVault + ReasoningTraceRegistry to Arc testnet. Circle Wallets integration. |
| **7** | Frontend MVP: landing page, risk profile selector, portfolio dashboard with charts. |

**Week 1 deliverable:** Users can connect wallet, pick a risk profile, and see a constructed portfolio with backtest performance data.

### Week 2: Agent Intelligence + Polish (May 19–25)

| Day | Milestone |
|---|---|
| **8–9** | Live agent: regime detection, autonomous rebalancing, strategy rotation. Reasoning trace publishing to Arc. |
| **10** | RWA bridge flow: CCTP/Gateway integration for cross-chain RWA token acquisition. USYC integration. |
| **11** | Arxiv demo: show the pipeline working end-to-end on 2–3 papers. Strategy extraction → backtest → portfolio inclusion. |
| **12** | Paymaster integration: all fees in USDC. Polish UX, fix bugs. |
| **13** | Traction push: onboard users, seed strategy leaderboard, gather feedback. |
| **14** | Final demo: record product demo + pitch video. Clean up repo. Submit. |

**Week 2 deliverable:** Fully autonomous portfolio agent with live rebalancing, on-chain reasoning traces, and real users.

---

## 9. Traction Strategy

Traction is 30% of the score. Plan to demonstrate:

| Metric | Target | How |
|---|---|---|
| **Users onboarded** | 50+ | Share in hackathon Discord, crypto Twitter, quant communities |
| **Portfolios created** | 30+ | Frictionless onboarding: connect wallet → pick profile → go |
| **Total AUM (testnet)** | Demonstrate meaningful deposit volume | Testnet USDC faucet for easy onboarding |
| **Rebalance events** | 10+ autonomous rebalances | Run the agent continuously from day 9 onward |
| **Reasoning traces on-chain** | 20+ | Every agent decision is published |
| **Strategy performance** | Positive risk-adjusted returns vs benchmark | Pre-curated strategies selected for proven backtests |

### User Acquisition Channels
1. **Canteen Discord** — Post strategy performance leaderboard daily
2. **Arc Builder Discord** — Share technical updates and integration progress
3. **Crypto Twitter** — Thread the arxiv-to-alpha pipeline concept
4. **Quant communities** — r/algotrading, QuantConnect forums

---

## 10. Risk & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| RWA tokens unavailable on source chains | Can't build RWA portfolio | Fallback: use crypto tokens + USYC only. Demonstrate the bridge flow with available tokens. |
| Arxiv pipeline generates bad strategies | Demo fails, trust breaks | MVP uses pre-curated strategies. Pipeline is demo only, with human validation gate. |
| Regime detection produces false signals | Unnecessary rebalancing, costs | Conservative thresholds. Require 2+ signals to confirm regime change. Cost-benefit check before every rebalance. |
| Smart contract bugs | Fund loss on testnet | Focused scope: vault is simple deposit/withdraw + agent-controlled rebalance. Thorough testing. |
| Gateway/CCTP integration delays | Can't bridge RWA tokens | Build the portfolio logic chain-agnostic. Bridge is one adapter; can swap for direct DEX execution on source chain. |
| Two weeks isn't enough | Incomplete product | Strict MVP scoping. Pre-curated strategies eliminate the riskiest dependency. Daily milestone tracking. |

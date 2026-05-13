# Archimedes Ecosystem Design Spec

> **Date:** 2026-05-13 (Day 3)
> **Author:** Chuan Bai (via design session)
> **Status:** Draft — pending team review
> **Supersedes:** Original single-vault architecture in [`design.md` § 5.2](../design.md)
> **Scope:** Hackathon MVP of the full ecosystem vision

## Executive Summary

Archimedes evolves from a single-agent portfolio manager into a **closed strategy/portfolio
marketplace ecosystem** with four interlocking components:

1. **Synthetic Protocol** — tokenize any asset with a price feed (sTSLA, sSPY, sGLD, sBTC)
2. **AMM Exchange** — trade synthetic assets and vault tokens against USDC
3. **Vault Factory** — anyone can create managed portfolios; two tiers (paper-grounded + community)
4. **Agent-as-a-Service** — AI manages Tier 1 vaults fully; Tier 2 vaults opt into agent features

Copy-trading is native: buying a vault token on the AMM = investing in that manager's portfolio.
The ecosystem is self-contained on Arc with USDC settlement.

---

## 1. Design Decisions (Resolved)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Tradeable units | **Two layers:** Layer 1 = individual synthetic/bridged assets; Layer 2 = vault tokens (ERC-20 shares of portfolios). Exchange trades both. |
| 2 | Vault creation model | **Two-tier marketplace:** Tier 1 = Archimedes-curated, paper-grounded, "Verified" badge. Tier 2 = community freestyle, any assets/weights, no paper required. |
| 3 | Asset tokenization | **Hybrid:** bridge existing RWA tokens where available (CCTP/Gateway); mint synthetics (oracle-priced, USDC-collateralized) for everything else. |
| 4 | Exchange type | **CLOB (vision) → AMM (hackathon MVP).** Uniswap V2-style (x·y=k) for hackathon. CLOB is the post-hackathon upgrade. |
| 5 | Copy-trade mechanism | **Buy vault tokens.** The vault token IS the copy-trade primitive. No separate mechanism needed. |
| 6 | Fee structure | **Management + performance fee (2-and-20 style).** Vault creators set mgmt fee (e.g. 1–2% annual) + perf fee (e.g. 20% above HWM). Platform takes 10% of all fees. |
| 7 | Synthetic collateral | **Protocol-level shared pool.** A single `SyntheticFactory` contract holds all USDC collateral. Any vault or user mints/redeems synthetics from this pool. |
| 8 | Timeline | **Hackathon MVP** with strategic cuts: simplified AMM (not CLOB), no liquidation engine, 100% collateral ratio, 3–5 synth assets, basic fees. |
| 9 | Agent role | **Agent-as-a-service.** Full agent (regime detection, strategy rotation, reasoning traces) for Tier 1 vaults. Opt-in agent features (auto-rebalance, drift alerts, basic regime response) for Tier 2. |
| 10 | AMM scope | **ETF model — both layers trade on AMM.** Vault tokens can trade at premium/discount to NAV. Direct mint/redeem at NAV provides the arbitrage mechanism. |
| 11 | Initial asset set | **sTSLA** (tech equity), **sSPY** (index), **sGLD** (commodity), **sBTC** (crypto), **USYC** (yield) + **USDC** (settlement). 5 synth assets + 5 AMM pools. |
| 12 | Contract architecture | **Replace + extend.** VaultFactory replaces ArchimedesVault. ReasoningTraceRegistry unchanged. StrategyRegistry → AssetRegistry. New: SyntheticFactory, AMMRouter, AMMPool. ~6 contracts. |
| 13 | Build order | **Bottom-up:** Days 1–3 synthetics → Days 4–6 AMM → Days 7–9 vaults → Days 10–12 polish. |
| 14 | User journey | **Explore → Invest → Create → Trade.** Marketplace is the landing page. Lowest friction to first investment. Vault creation for power users. |
| 15 | Social layer location | **On-platform chat** with wallet-native identity. Chat identity = on-chain identity = portfolio performance as social proof. |
| 16a | Chat access control | **Fully open.** Any connected wallet can read and write in any vault chat. No token-gating. Maximum participation. |
| 16b | Social scope (hackathon) | **Minimal chat.** Per-vault text chat, wallet address as name, message persistence. No profiles, DMs, reactions, or formatting. ~4–6 hours build. |
| 17 | AI in chat | **Active AI in Tier 1 vault chats.** Auto-posts on rebalance/regime events. Responds to user @mentions via Claude API. "Talk to your fund manager" differentiator. |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     ARCHIMEDES ECOSYSTEM                                │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                     MARKETPLACE UI                                │  │
│  │  Vault leaderboard │ Asset prices │ Swap UI │ Vault creator       │  │
│  │  Reasoning traces  │ Performance charts │ LP dashboard            │  │
│  └───────────────────────────────┬───────────────────────────────────┘  │
│                                  │                                      │
│  ┌───────────────┐  ┌────────────┴────────────┐  ┌──────────────────┐  │
│  │  VAULT LAYER  │  │     EXCHANGE LAYER      │  │  AGENT LAYER     │  │
│  │               │  │                         │  │                  │  │
│  │ VaultFactory  │  │  AMMRouter + AMMPool    │  │  Archimedes AI   │  │
│  │ Vault (4626)  │◀─┤  USDC/sTSLA pool        │  │  (manages T1,    │  │
│  │ Fees (2&20)   │  │  USDC/sSPY pool         │  │   assists T2)    │  │
│  │ Rebalance     │  │  USDC/sGLD pool         │  │                  │  │
│  │               │  │  USDC/sBTC pool         │  │  Regime detect   │  │
│  │ Tier 1: 🏆    │  │  USDC/USYC pool         │  │  Rebalance       │  │
│  │ Tier 2: 👥    │  │  USDC/vaultToken pools  │  │  Strategy rotate │  │
│  └───────┬───────┘  └────────────┬────────────┘  │  Reasoning trace │  │
│          │                       │                └────────┬─────────┘  │
│          ▼                       ▼                         │            │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                   SYNTHETIC PROTOCOL                              │  │
│  │                                                                   │  │
│  │  SyntheticFactory.sol                                             │  │
│  │  ├─ Collateral Pool (shared USDC)                                 │  │
│  │  ├─ sTSLA (oracle: TSLA/USD)                                      │  │
│  │  ├─ sSPY  (oracle: SPY/USD)                                       │  │
│  │  ├─ sGLD  (oracle: XAU/USD)                                       │  │
│  │  ├─ sBTC  (oracle: BTC/USD)                                       │  │
│  │  └─ USYC  (Circle native — pass-through, not synthetic)           │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                   IDENTITY LAYER                                  │  │
│  │  ReasoningTraceRegistry.sol  │  AssetRegistry.sol                 │  │
│  │  (on-chain hash anchoring)   │  (synth + vault metadata)          │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          ARC BLOCKCHAIN                                 │
│  Settlement: USDC  │  Fees: ~$0.01 via Paymaster  │  Finality: <1s     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Smart Contract Specifications

### 3.1 SyntheticFactory.sol

The protocol-level contract that manages synthetic asset creation, collateral, and minting.

```solidity
// SPDX-License-Identifier: Unlicense
pragma solidity ^0.8.24;

interface ISyntheticFactory {
    // --- Admin ---
    function createSynthetic(
        string calldata name,       // e.g. "Synthetic Tesla"
        string calldata symbol,     // e.g. "sTSLA"
        address oracle              // price feed address
    ) external returns (address token);

    // --- User / Vault ---
    function mint(
        address synthetic,          // which synth to mint
        uint256 usdcAmount          // USDC to deposit as collateral
    ) external returns (uint256 synthAmount);

    function redeem(
        address synthetic,          // which synth to burn
        uint256 synthAmount         // amount to redeem
    ) external returns (uint256 usdcAmount);

    // --- Views ---
    function getPrice(address synthetic) external view returns (uint256);
    function totalCollateral() external view returns (uint256);
    function totalSynthValue() external view returns (uint256);
    function healthRatio() external view returns (uint256); // collateral / synth value
    function getSynthetics() external view returns (address[] memory);
}
```

**Hackathon simplifications:**
- Collateral ratio: 100% (1:1 USDC backing, no over-collateralization)
- No liquidation engine
- Oracle: mock `PriceOracle.sol` that owner updates via API (upgrade to Pyth/Chainlink post-hackathon)
- Each synthetic is a standard ERC-20 minted by the factory

### 3.2 VaultFactory.sol + Vault.sol (ERC-4626)

Factory produces individual vault contracts. Each vault is an ERC-4626 tokenized vault.

```solidity
interface IVaultFactory {
    function createVault(
        string calldata name,           // e.g. "Momentum Alpha"
        string calldata symbol,         // e.g. "vMOMENTUM"
        uint16 managementFeeBps,        // e.g. 150 = 1.50%
        uint16 performanceFeeBps,       // e.g. 2000 = 20%
        bool agentAssisted              // opt-in to agent rebalancing
    ) external returns (address vault);

    function getVaults() external view returns (address[] memory);
    function getVaultsByCreator(address creator) external view returns (address[] memory);
}

interface IVault {
    // --- ERC-4626 standard ---
    function deposit(uint256 assets, address receiver) external returns (uint256 shares);
    function withdraw(uint256 assets, address receiver, address owner) external returns (uint256 shares);
    function redeem(uint256 shares, address receiver, address owner) external returns (uint256 assets);

    // --- Management ---
    function rebalance(
        address[] calldata tokensIn,
        uint256[] calldata amountsIn,
        address[] calldata tokensOut,
        uint256[] calldata amountsOut
    ) external;  // restricted to creator OR agent

    function setTargetAllocations(
        address[] calldata tokens,
        uint256[] calldata weightsBps   // basis points, must sum to 10000
    ) external;  // creator only

    // --- Views ---
    function totalAssets() external view returns (uint256);  // NAV in USDC terms
    function getHoldings() external view returns (address[] memory tokens, uint256[] memory amounts);
    function getTargetAllocations() external view returns (address[] memory tokens, uint256[] memory weights);
    function creator() external view returns (address);
    function tier() external view returns (uint8);  // 1 = paper-grounded, 2 = community
    function managementFeeBps() external view returns (uint16);
    function performanceFeeBps() external view returns (uint16);
    function highWaterMark() external view returns (uint256);
    function isAgentAssisted() external view returns (bool);
}
```

**Fee mechanics:**
- Management fee: accrues per-second, deducted from NAV calculation
- Performance fee: charged on profits above high-water mark at redemption
- Platform cut: 10% of all fees routed to platform treasury
- Fees reduce share price (like real fund accounting)

**Tier distinction:**
- Tier 1 vaults: created by the platform agent address, carry "Archimedes Verified" metadata
- Tier 2 vaults: created by any address, community tier
- Both use the same contract code; tier is set at creation based on `msg.sender`

### 3.3 AMMRouter.sol + AMMPool.sol

Uniswap V2-style constant-product AMM. Minimal viable exchange.

```solidity
interface IAMMRouter {
    function createPool(
        address tokenA,     // e.g. USDC
        address tokenB      // e.g. sTSLA or vault token
    ) external returns (address pool);

    function addLiquidity(
        address tokenA, address tokenB,
        uint256 amountA, uint256 amountB,
        uint256 minLPTokens
    ) external returns (uint256 lpTokens);

    function removeLiquidity(
        address tokenA, address tokenB,
        uint256 lpTokens,
        uint256 minAmountA, uint256 minAmountB
    ) external returns (uint256 amountA, uint256 amountB);

    function swap(
        address tokenIn, address tokenOut,
        uint256 amountIn, uint256 minAmountOut
    ) external returns (uint256 amountOut);

    function getAmountOut(
        address tokenIn, address tokenOut,
        uint256 amountIn
    ) external view returns (uint256 amountOut);
}

interface IAMMPool {
    function token0() external view returns (address);
    function token1() external view returns (address);
    function reserve0() external view returns (uint256);
    function reserve1() external view returns (uint256);
    function totalSupply() external view returns (uint256);  // LP tokens
    function swapFee() external view returns (uint16);       // e.g. 30 = 0.30%
}
```

**Hackathon parameters:**
- Swap fee: 0.3% (standard Uniswap V2)
- Fee distribution: 80% to LPs, 20% to platform
- All pools paired against USDC
- Initial pools: USDC/sTSLA, USDC/sSPY, USDC/sGLD, USDC/sBTC, USDC/USYC
- Vault token pools created on-demand when vault AUM exceeds threshold

### 3.4 ReasoningTraceRegistry.sol (Unchanged)

Survives from original design. Every agent decision (Tier 1 vaults + agent-assisted Tier 2)
publishes reasoning traces on-chain.

```solidity
interface IReasoningTraceRegistry {
    function publishTrace(bytes32 hash, bytes calldata metadata) external;
    function verifyTrace(uint256 id, bytes calldata fullTrace) external view returns (bool);
    function getTraces(address agent, uint256 from, uint256 to) external view returns (bytes32[] memory);
}
```

### 3.5 AssetRegistry.sol (Evolves from StrategyRegistry)

Tracks all assets and vaults in the ecosystem.

```solidity
interface IAssetRegistry {
    // --- Synthetic assets ---
    function registerSynthetic(address token, bytes32 oracleId, bytes calldata metadata) external;
    function getSynthetic(address token) external view returns (bytes memory metadata);

    // --- Bridged assets ---
    function registerBridged(address token, uint256 sourceChainId, address sourceToken) external;

    // --- Vaults ---
    function registerVault(address vault, uint8 tier, bytes calldata metadata) external;
    function updateVaultMetrics(address vault, bytes calldata metrics) external;
    function getLeaderboard(uint8 tier, uint256 limit) external view returns (address[] memory);
}
```

### 3.6 PriceOracle.sol (Hackathon Mock)

Owner-updatable oracle for the hackathon. Replaced by Pyth/Chainlink integration post-hackathon.

```solidity
interface IPriceOracle {
    function setPrice(address token, uint256 price) external;  // owner only
    function getPrice(address token) external view returns (uint256 price, uint256 updatedAt);
    function batchSetPrices(address[] calldata tokens, uint256[] calldata prices) external;
}
```

**Off-chain oracle updater:** A backend service fetches real prices from yfinance/CoinGecko
every ~60 seconds and calls `batchSetPrices`. This gives the demo real price movement
without needing a decentralized oracle integration.

---

## 4. Social Layer

### Architecture

On-platform chat with wallet-native identity. Every user's chat presence is tied to their
on-chain address — your reputation in chat is your portfolio performance.

```
┌────────────────────────────────────────────┐
│           SOCIAL LAYER                     │
│                                            │
│  Per-Vault Chat Rooms (WebSocket + Redis)  │
│  ┌──────────────────────────────────────┐  │
│  │ 🏆 Momentum Alpha Chat              │  │
│  │                                      │  │
│  │ 🤖 [Archimedes AI] 10:32 AM         │  │
│  │ Rebalanced: -15% sTSLA, +15% USYC.  │  │
│  │ Regime → RISK_OFF. Trace: #42        │  │
│  │                                      │  │
│  │ [0x1a..f3] 10:35 AM                  │  │
│  │ Why not hold through the dip?        │  │
│  │                                      │  │
│  │ 🤖 [Archimedes AI] 10:35 AM         │  │
│  │ VIX>25 + negative momentum →         │  │
│  │ 12% avg drawdown historically.       │  │
│  │ Confidence: 0.78.                    │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  Access: fully open (any connected wallet) │
│  Identity: wallet address                  │
│  Persistence: Postgres                     │
│  Real-time: WebSocket + Redis pub/sub      │
└────────────────────────────────────────────┘
```

### Access Model

- **Fully open:** any connected wallet can read and write in any vault's chat
- No token-gating for hackathon MVP (post-hackathon: tiered access as upgrade)
- Wallet address displayed as identity (ENS/profile names post-hackathon)

### AI Participation (Tier 1 Vaults)

The Archimedes AI is an active participant in Tier 1 vault chat rooms:

1. **Auto-posts** on every agent action:
   - Rebalance events (what changed, why, link to reasoning trace)
   - Regime change detection (new regime, implications)
   - Strategy rotation (what was rotated, why)
2. **Responds to @mentions** via Claude API:
   - Users can ask the AI about its reasoning
   - AI has full context: current holdings, recent traces, market data
   - Responses reference specific reasoning traces for verifiability

**Tier 2 vaults:** Human curators chat directly. AI is not present unless
the curator explicitly enables agent-assisted chat (post-hackathon feature).

### Hackathon Scope (Minimal)

| Feature | Hackathon | Post-Hackathon |
|---------|-----------|----------------|
| Per-vault chat rooms | ✔ | ✔ |
| Wallet address identity | ✔ | + ENS/profiles |
| Message persistence | ✔ | ✔ |
| AI auto-post on actions | ✔ | ✔ |
| AI responds to @mentions | ✔ | + richer context |
| User profiles (PnL, holdings) | ✘ | ✔ |
| DMs between users | ✘ | ✔ |
| Global marketplace feed | ✘ | ✔ |
| Reactions / emoji | ✘ | ✔ |
| Thread replies | ✘ | ✔ |
| Token-gated access tiers | ✘ | ✔ |
| Push notifications | ✘ | ✔ |

### Backend Implementation (Hackathon)

```
Tech stack:
  WebSocket server (FastAPI WebSocket or Socket.IO)
  Redis pub/sub (real-time message fan-out)
  Postgres (message persistence)
  Claude API (AI responses to @mentions)

Endpoints:
  WS /chat/{vault_address}     — join vault chat room
  POST /chat/{vault_address}   — send message (REST fallback)
  GET  /chat/{vault_address}   — message history (paginated)

AI trigger:
  Message contains "@archimedes" or "@ai"
  → Claude API call with context:
    - Recent chat messages
    - Vault current holdings
    - Latest reasoning traces
    - Current market regime
  → Response posted as 🤖 [Archimedes AI]
```

---

## 5. Two-Tier Marketplace

### Tier 1: Archimedes Verified 🏆

- Created by the platform's agent address
- Strategies trace back to published arxiv papers
- Full AI agent: regime detection, strategy rotation, autonomous rebalancing
- Reasoning traces published on-chain for every decision
- Carry "Archimedes Verified" badge in the marketplace
- Higher default trust signal

### Tier 2: Community 👥

- Created by any wallet address (permissionless)
- Freestyle asset allocation — any available synth/bridged asset
- Opt-in agent features:
  - Auto-rebalance to target weights (drift threshold trigger)
  - Drift alerts
  - Basic regime response (shift to USYC in risk-off)
- Performance tracked on-chain
- Reputation built from verifiable track record
- No paper-grounding required

### Marketplace Features

- **Leaderboard:** Sort vaults by Sharpe ratio, total return, AUM, age
- **Vault detail page:** holdings breakdown, performance chart, fee structure, reasoning traces (if agent-managed)
- **Asset prices:** live synthetic asset prices with charts
- **Swap UI:** trade any asset or vault token against USDC
- **Vault creator:** step-by-step vault creation flow (pick assets → set weights → set fees → deploy)
- **LP dashboard:** provide liquidity, track fees earned

---

## 6. Agent-as-a-Service Architecture

```
┌──────────────────────────────────────────────┐
│            ARCHIMEDES AI AGENT               │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │  TIER 1 — Full Autonomy               │  │
│  │                                        │  │
│  │  • Paper-grounded strategy selection   │  │
│  │  • Regime detection (4 regimes)        │  │
│  │  • Autonomous rebalancing              │  │
│  │  • Strategy rotation                   │  │
│  │  • Reasoning trace publication         │  │
│  │  • Cost-benefit analysis per trade     │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │  TIER 2 — Opt-in Tools                │  │
│  │                                        │  │
│  │  • Auto-rebalance to target weights    │  │
│  │    (when drift > threshold)            │  │
│  │  • Drift threshold alerts              │  │
│  │  • Basic regime response               │  │
│  │    (risk-off → increase USYC)          │  │
│  │  • Reasoning traces for agent actions  │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │  SHARED INFRASTRUCTURE                 │  │
│  │                                        │  │
│  │  • Market data ingestion               │  │
│  │  • Oracle price updates                │  │
│  │  • AMM interaction (swap execution)    │  │
│  │  • Vault rebalance execution           │  │
│  └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

---

## 7. User Journey (Demo Script)

### 1️⃣ EXPLORE (first 30 seconds of demo)

```
User lands on Archimedes marketplace
  → Sees vault leaderboard:
    🏆 Momentum Alpha (Archimedes)  +12.3%  Sharpe 1.8  $500k AUM
    🏆 Yield Optimizer (Archimedes)  +8.1%  Sharpe 2.1  $300k AUM
    👥 DeFi Degen (community)       +18.7%  Sharpe 0.9  $50k AUM
    👥 Safe Haven (community)        +5.2%  Sharpe 1.5  $80k AUM
  → Clicks "Momentum Alpha"
  → Sees: holdings (30% sTSLA, 25% sSPY, 15% sGLD, 30% USYC)
  → Sees: reasoning trace — "Regime: RISK_ON. Increased equity weight..."
  → Sees: performance chart, fee structure (1.5% mgmt, 20% perf)
```

### 2️⃣ INVEST (next 30 seconds)

```
User connects wallet (Circle SDK)
  → Deposits 1000 USDC
  → Buys Momentum Alpha vault tokens on AMM
  → Dashboard shows: "You hold 95.2 vMOMENTUM tokens ($1000 value)"
  → Real-time: agent rebalances → NAV changes → user sees update
  → Can view every reasoning trace the agent published
  → Joins vault chat: sees AI explaining latest rebalance decision
  → Asks @archimedes "why did you reduce TSLA?" → gets real-time answer
```

### 3️⃣ CREATE (power user demo)

```
User clicks "Create Vault"
  → Names it: "AI & Defense"
  → Picks assets: 40% sTSLA, 30% sSPY, 30% USYC
  → Sets fees: 1% mgmt, 15% performance
  → Opts in to agent-assisted rebalancing
  → Deploys → vault appears on leaderboard
  → Other users can now buy "vAIDEF" tokens
```

### 4️⃣ TRADE (advanced demo)

```
User swaps 100 USDC → sTSLA on the AMM
  → Instant execution, 0.3% fee
  → sTSLA in wallet, tracking real Tesla price
User provides liquidity to USDC/sBTC pool
  → Earns trading fees from swaps
```

---

## 8. Hackathon Build Plan

### Days 1–3: Synthetic Layer (Foundation)

| Task | Owner | Deliverable |
|------|-------|-------------|
| `PriceOracle.sol` — mock oracle with batch price updates | Chuan | Contract deployed, owner can set prices |
| `SyntheticFactory.sol` — create synths, mint/redeem, collateral pool | Chuan | Factory deployed, 5 synths created |
| Oracle updater service — fetches prices from yfinance/CoinGecko, calls `batchSetPrices` | Backend eng | Prices updating every 60s |
| `AssetRegistry.sol` — register synths + metadata | Chuan | Registry deployed, synths registered |
| Foundry test suite for synthetic layer | Chuan | Full coverage: mint, redeem, price updates |

**Milestone:** Can deposit USDC → mint sTSLA → redeem sTSLA → get USDC back at oracle price.

### Days 4–6: Exchange Layer (AMM)

| Task | Owner | Deliverable |
|------|-------|-------------|
| `AMMPool.sol` — constant-product pool (x·y=k) | Chuan / Marten | Pool contract with swap, add/remove liquidity |
| `AMMRouter.sol` — pool creation, multi-hop routing | Chuan / Marten | Router deployed, 5 pools created |
| Seed initial liquidity — platform deposits USDC + synths into pools | Backend eng | All 5 pools liquid |
| Swap integration test — end-to-end: mint synth → swap on AMM | Chuan | Passing tests |

**Milestone:** Can swap USDC ↔ any synthetic asset on the AMM.

### Days 7–9: Vault Layer (Marketplace)

| Task | Owner | Deliverable |
|------|-------|-------------|
| `VaultFactory.sol` + `Vault.sol` (ERC-4626) | Chuan | Factory deployed, can create vaults |
| Fee mechanics — management fee, performance fee, HWM, platform cut | Chuan / Önder | Fees accruing correctly |
| Rebalance integration — vault rebalances via AMM swaps | Chuan / Marten | Agent can rebalance vault holdings |
| `ReasoningTraceRegistry.sol` — unchanged from original design | Chuan | Deployed, traces publishing |
| Agent integration — Tier 1 vault management, Tier 2 opt-in features | Dan / Backend eng | Agent managing at least 1 Tier 1 vault |
| Vault token AMM pools — create USDC/vaultToken pools for active vaults | Marten | Vault tokens tradeable |

**Milestone:** Can create vault, deposit, rebalance, see reasoning traces. Vault tokens trade on AMM.

### Days 10–12: Frontend + Polish + Demo

| Task | Owner | Deliverable |
|------|-------|-------------|
| Marketplace landing page — vault leaderboard, asset prices | Daniel | Working UI |
| Vault detail page — holdings, performance, traces, fees | Daniel | Working UI |
| Swap UI — trade synths and vault tokens | Daniel | Working UI |
| Vault creator flow — step-by-step vault deployment | Daniel | Working UI |
| Demo script rehearsal | Dan / full team | Polished 3-minute demo |
| Traction push — onboard users, seed community vaults | Full team | 30+ portfolios |
| Social layer — per-vault chat, WebSocket, AI auto-post + @mention | Daniel / Backend eng | Chat working in vault pages |

**Milestone:** Full demo-ready ecosystem.

---

## 9. Contract Dependency Graph

```
PriceOracle.sol          (no dependencies)
    │
    ▼
SyntheticFactory.sol     (depends on: PriceOracle, USDC token)
    │
    ├──▶ sTSLA (ERC-20)
    ├──▶ sSPY  (ERC-20)
    ├──▶ sGLD  (ERC-20)
    ├──▶ sBTC  (ERC-20)
    └──▶ USYC  (pass-through)
          │
          ▼
AMMRouter.sol            (depends on: synth tokens, USDC)
    │
    ├──▶ AMMPool (USDC/sTSLA)
    ├──▶ AMMPool (USDC/sSPY)
    ├──▶ AMMPool (USDC/sGLD)
    ├──▶ AMMPool (USDC/sBTC)
    └──▶ AMMPool (USDC/USYC)
          │
          ▼
VaultFactory.sol         (depends on: AMMRouter, SyntheticFactory)
    │
    ├──▶ Vault (Tier 1 — Archimedes Managed)
    ├──▶ Vault (Tier 2 — Community)
    └──▶ AMMPool (USDC/vaultToken) per vault
          │
          ▼
ReasoningTraceRegistry.sol  (depends on: nothing — standalone)
AssetRegistry.sol           (depends on: nothing — standalone)
```

---

## 10. Hackathon Simplifications (What Gets Cut)

| Full Vision | Hackathon MVP |
|-------------|---------------|
| CLOB order book | Uniswap V2 AMM |
| Over-collateralized synthetics (110%+) | 100% collateral (1:1 USDC backing) |
| Liquidation engine | No liquidation (demo environment) |
| Pyth/Chainlink oracles | Mock oracle updated by backend service |
| Bridge integration (CCTP/Gateway) | All synthetic, no bridging in v1 |
| Concentrated liquidity (V3) | Constant product (V2) |
| KYC/permissioning | Fully permissionless |
| Multi-chain support | Arc only |

---

## 11. Revenue Model

```
Platform revenue streams:

1. Fee cut from vault management
   Vault fees (mgmt + perf) × 10% platform cut
   Example: $10M ecosystem AUM × 1.5% avg mgmt fee = $150k
   Platform cut: $15k/year

2. AMM swap fees
   0.3% swap fee × 20% platform share
   Example: $1M daily volume × 0.3% × 20% = $600/day

3. Synthetic mint/redeem spread (future)
   Small fee on mint/redeem from protocol
   Not implemented in hackathon MVP

4. Premium agent features (future)
   Advanced agent capabilities for Tier 2 vaults
   Tiered pricing based on AUM
```

---

## 12. Post-Hackathon Upgrade Path

| Phase | Component | Upgrade |
|-------|-----------|---------|
| 1 | Exchange | AMM → CLOB with off-chain matching + on-chain settlement |
| 2 | Oracles | Mock → Pyth/Chainlink decentralized feeds |
| 3 | Collateral | 100% → 110%+ with liquidation engine |
| 4 | Bridging | Pure synthetic → hybrid (bridge real RWAs where available) |
| 5 | Agent | Basic opt-in → full strategy marketplace + custom agent configs |
| 6 | Governance | Platform-controlled → DAO governance for protocol parameters |

---

## 13. Relationship to Existing Design Docs

| Existing Doc | Status |
|---|---|
| [`design.md`](../design.md) | **Partially superseded.** Strategy engine (§4.1), backtesting (§4.2), regime detection (§4.3.3) survive. Smart contract architecture (§5.2) replaced by this spec. |
| [`mvp-scope-memo.md`](../mvp-scope-memo.md) | **Scope expanded.** The three locked decisions (RFB 04, both on-chain stories, curated library) still hold but are now embedded in a larger ecosystem. |
| [`architectural-principles.md`](../architectural-principles.md) | **Survives intact.** Paper-grounded provenance = Tier 1. Reasoning traces = all agent-managed vaults. Non-custodial = vault architecture. Verifiable history = ReasoningTraceRegistry. |
| [`strategy-passport-spec.md`](specs/strategy-passport-spec.md) | **Survives for Tier 1 vaults.** Strategy passports apply to Archimedes-curated strategies. Community vaults don't require paper backing. |
| [`anti-features.md`](../anti-features.md) | **Needs update.** "No third-party strategy onboarding" is now reversed — community vaults ARE third-party strategies. |

---

## 14. Open Questions (Deferred)

1. **Vault token naming convention** — Auto-generated or creator-chosen? (Leaning: creator-chosen with uniqueness check)
2. **Minimum vault AUM for AMM pool creation** — Threshold to avoid empty pools? (Leaning: 1000 USDC minimum)
3. **Vault abandonment** — What happens if a creator disappears? (Leaning: holders can vote to migrate to a new manager)
4. **Cross-vault composability** — Can a vault hold another vault's tokens? (Leaning: yes, enables fund-of-funds)
5. **LP incentives** — How to bootstrap initial AMM liquidity? (Leaning: platform seeds pools with protocol-owned liquidity)

---

_This spec is the result of a 14-question design session. When the team disagrees with any
decision, discuss in Discord, agree, and update this doc. Date substantive changes._

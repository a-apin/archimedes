# Component Interfaces & Test Cases — Team Work Split

> **Status:** Day-10 update (2026-05-22). The original Day-3 (2026-05-13) frozen
> interface contracts in this doc are **architecturally still correct** — every
> `I*` Protocol in [`backend/archimedes/interfaces/`](../../backend/archimedes/interfaces/)
> is alive and consumed by shipped code. **What's changed:** the strict per-person
> ownership column below has been replaced in practice by a "lead + coverage" model
> ([`CLAUDE.md`](../../CLAUDE.md) § "Lead + coverage by load-bearing component").
> Cross-lane contributions are now the norm; the agentic system (`t2o2`) holds the
> plurality of commits across the backend. The table below documents the *original*
> ownership intent; the actual current authorship per file is in
> [`../chuan-architecture-survey.md`](../archive/agora-2026-05/chuan-architecture-survey.md).

## Original team ownership intent (Day-3 — historical context)

| Lead   | Component | Implements | Depends On |
|--------|-----------|------------|------------|
| **Chuan** | Smart contracts | `contracts/src/*.sol` behind `contracts/src/interfaces/I*.sol` | — |
| **Chuan** | Backend API | `backend/archimedes/api/routes.py` | other interfaces |
| **Chuan** | Agent orchestrator | `IAgentOrchestrator` | All other interfaces |
| **Marten** | On-chain backend | `IOracleUpdater`, `IChainExecutor`, `ITracePublisher` | Contract ABIs |
| **Önder** | Portfolio math | `IRegimeDetector`, `IPortfolioConstructor`, `IBacktestEvaluator` | Shared data models only |
| **Dan** | Strategy library | `IStrategyProvider` | Shared data models only |
| **Daniel R.** | Frontend | React + Vite against REST API + contract ABIs | REST schemas + contract addresses |

---

## File Layout

```
backend/archimedes/
├── __init__.py
├── models/                          # SHARED — everyone reads these
│   ├── __init__.py                  #   All exports
│   ├── asset.py                     #   AssetInfo, AssetPrice, MarketSnapshot
│   ├── strategy.py                  #   Strategy, StrategyStatus, SignalDefinition
│   ├── backtest.py                  #   BacktestResult
│   ├── regime.py                    #   Regime, RegimeSignals, RegimeClassification
│   ├── portfolio.py                 #   RiskProfile, Portfolio, TargetAllocation, TradeOrder, RebalanceDecision
│   ├── vault.py                     #   VaultInfo, VaultTier, VaultMetrics
│   └── trace.py                     #   ReasoningTrace, DecisionType
│
├── interfaces/                      # FROZEN — the contracts between people
│   ├── __init__.py
│   ├── math.py                      #   Önder:  IRegimeDetector, IPortfolioConstructor, IBacktestEvaluator
│   ├── strategy.py                  #   Dan:    IStrategyProvider
│   ├── chain.py                     #   Marten: IChainExecutor, IOracleUpdater, ITracePublisher
│   └── agent.py                     #   Chuan:  IAgentOrchestrator
│
├── api/                             # Daniel depends on these schemas
│   ├── __init__.py
│   ├── schemas.py                   #   Pydantic response models (JSON shapes)
│   └── routes.py                    #   FastAPI endpoint definitions
│
└── services/                        # Implementation goes here (not yet written)
    └── (each person adds their impl)

contracts/src/
├── interfaces/                      # FROZEN — Marten + Daniel code against these
│   ├── IPriceOracle.sol
│   ├── ISyntheticFactory.sol
│   ├── IAMMPool.sol
│   ├── IAMMRouter.sol
│   ├── IVaultFactory.sol
│   ├── IVault.sol
│   ├── IReasoningTraceRegistry.sol
│   └── IAssetRegistry.sol
│
└── (implementations go here)

tests/flows/                         # User flow test cases
├── conftest.py                      #   Stubs for all interfaces (swap for real impls)
├── test_flow_1_synthetic_mint_redeem.py    # MANDATORY
├── test_flow_2_amm_swap.py                 # MANDATORY
├── test_flow_3_vault_lifecycle.py          # MANDATORY
├── test_flow_4_reasoning_traces.py         # MANDATORY
├── test_flow_5_agent_management.py         # MANDATORY
├── test_flow_6_marketplace_ui.py           # MANDATORY
└── test_flow_7_11_aspirational.py          # OPTIONAL
```

---

## How to Work Against This

### Önder (math modules)
1. Read `backend/archimedes/interfaces/math.py`
2. Read `backend/archimedes/models/regime.py`, `portfolio.py`, `backtest.py`
3. Create `backend/archimedes/services/regime_detector.py` implementing `IRegimeDetector`
4. Create `backend/archimedes/services/portfolio_constructor.py` implementing `IPortfolioConstructor`
5. Create `backend/archimedes/services/backtest_evaluator.py` implementing `IBacktestEvaluator`
6. Run `tests/flows/test_flow_5_agent_management.py::TestRegimeDetection` — all tests should pass
7. Swap the stub in `conftest.py` for your real implementation

### Dan (strategy library)
1. Read `backend/archimedes/interfaces/strategy.py`
2. Read `backend/archimedes/models/strategy.py`
3. Create `backend/archimedes/services/strategy_provider.py` implementing `IStrategyProvider`
4. Seed 5-10 curated strategies with real arxiv paper IDs
5. Run `tests/flows/test_flow_5_agent_management.py::TestStrategyProvider` — all tests should pass
6. Swap the stub in `conftest.py` for your real implementation

### Marten (on-chain backend)
1. Read `backend/archimedes/interfaces/chain.py`
2. Read `contracts/src/interfaces/*.sol` (the ABIs you'll call)
3. Create `backend/archimedes/services/oracle_updater.py` implementing `IOracleUpdater`
4. Create `backend/archimedes/services/chain_executor.py` implementing `IChainExecutor`
5. Create `backend/archimedes/services/trace_publisher.py` implementing `ITracePublisher`
6. Run `tests/flows/test_flow_1_*`, `test_flow_4_*` — oracle and trace tests should pass
7. Extend `wallet-setup/` scripts from smart-contracts branch

### Daniel (frontend)
1. Read `backend/archimedes/api/schemas.py` — these are your JSON response shapes
2. Read `backend/archimedes/api/routes.py` — these are the endpoints
3. Read `contracts/src/interfaces/IVault.sol`, `IAMMRouter.sol`, `ISyntheticFactory.sol` — for direct wallet signing
4. GET `/api/config/contracts` at startup to get all contract addresses
5. Use the REST API for reads, call contracts directly for writes (deposit, withdraw, swap)
6. Run `tests/flows/test_flow_6_marketplace_ui.py` — API contract tests should pass

### Chuan (contracts + backend + orchestrator)
1. Implement Solidity contracts behind the `I*.sol` interfaces
2. Implement FastAPI routes in `backend/archimedes/api/routes.py`
3. Implement `IAgentOrchestrator` in `backend/archimedes/services/agent.py`
4. Wire everything together: orchestrator calls Önder → Dan → Marten interfaces
5. All 6 mandatory flow tests should pass end-to-end

---

## Change Policy

- **Models (`models/`)**: Announce in Discord before changing. Everyone depends on these.
- **Interfaces (`interfaces/`, `contracts/src/interfaces/`)**: Frozen. Change requires team alignment.
- **API schemas (`api/schemas.py`)**: Announce to Daniel before changing.
- **Test fixtures (`conftest.py`)**: Anyone can swap stubs for real implementations.
- **Implementations (`services/`)**: Your own code — change freely.

---

## Commitment Levels

| Flow | Test File | Status |
|------|-----------|--------|
| 1. Synthetic mint/redeem | `test_flow_1_synthetic_mint_redeem.py` | **MANDATORY** |
| 2. AMM swap | `test_flow_2_amm_swap.py` | **MANDATORY** |
| 3. Vault lifecycle | `test_flow_3_vault_lifecycle.py` | **MANDATORY** |
| 4. Reasoning traces | `test_flow_4_reasoning_traces.py` | **MANDATORY** |
| 5. Agent management | `test_flow_5_agent_management.py` | **MANDATORY** |
| 6. Marketplace UI | `test_flow_6_marketplace_ui.py` | **MANDATORY** |
| 7. Community vaults | `test_flow_7_11_aspirational.py` | Aspirational |
| 8. Vault token trading | `test_flow_7_11_aspirational.py` | Aspirational |
| 9. Per-vault chat | `test_flow_7_11_aspirational.py` | Aspirational |
| 10. LP dashboard | `test_flow_7_11_aspirational.py` | Aspirational |
| 11. Strategy explorer | `test_flow_7_11_aspirational.py` | Aspirational |

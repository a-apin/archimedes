---
name: archimedes-fork-1__ui
description: React/Vite frontend with 30+ components — strategy generation, portfolio, vaults, marketplace, SIWE auth
triggers: [archimedes-fork-1 ui, React, Vite, archimedes-fork-1 frontend, archimedes-fork-1 components, JSX]
---

# UI — Archimedes Fork

**Source**: `archimedes-fork-1`
**Category**: Core

## When to use this skill
Working on the React frontend — UI components, pages, styling, API client, wallet connection (SIWE), Circle wallet integration, or Vite build configuration.

## Key files and folders
- `/home/ricardo/github/archimedes-fork-1/ui/src/main.jsx` — Entrypoint
- `/home/ricardo/github/archimedes-fork-1/ui/src/App.jsx` — Root component with routing
- `/home/ricardo/github/archimedes-fork-1/ui/src/api.js` — API client
- `/home/ricardo/github/archimedes-fork-1/ui/src/siwe.js` — SIWE auth integration
- `/home/ricardo/github/archimedes-fork-1/ui/src/circle-wallet.js` — Circle wallet integration
- `/home/ricardo/github/archimedes-fork-1/ui/src/circle-tx-executor.js` — Circle transaction executor
- `/home/ricardo/github/archimedes-fork-1/ui/src/components/` — 30+ React components:
  - Strategy: `Generate.jsx`, `GenerationStream.jsx`, `GenerationStatus.jsx`, `Strategies.jsx`, `StrategyPassport.jsx`
  - Portfolio: `Portfolio.jsx`, `PortfolioAdvisor.jsx`, `PortfolioAdvisorPanels.jsx`
  - Vaults: `VaultDetail.jsx`, `CreateVaultModal.jsx`, `DepositFlow.jsx`, `VaultChat.jsx`
  - Market: `MarketTab.jsx`, `Explore.jsx`, `Marketplace.jsx`
  - Research: `CorpusExplorer.jsx`, `CorpusKG.jsx`, `CorpusGraph.jsx`
  - Auth/Wallet: `WalletConnect.jsx`, `WalletGate.jsx`
  - Analysis: `RiskAnalysis.jsx`, `RegimePanel.jsx`, `BacktestVisualizer.jsx`, `StressScenarioPanel.jsx`
  - Misc: `Landing.jsx`, `Layout.jsx`, `FusionResult.jsx`, `Reasoning.jsx`, `RigorExplainer.jsx`, etc.
- `/home/ricardo/github/archimedes-fork-1/ui/vite.config.js` — Vite configuration
- `/home/ricardo/github/archimedes-fork-1/ui/uno.config.js` — UnoCSS styling config
- `/home/ricardo/github/archimedes-fork-1/ui/package.json` — React 19, viem 2.53, @circle-fin/modular-wallets-core

## Key concepts
- **React 19** with Vite 8, UnoCSS for styling
- **SIWE auth**: Sign-In With Ethereum via `siwe.js` + `WalletConnect.jsx`
- **Circle integration**: `circle-wallet.js` + `circle-tx-executor.js` for modular wallet operations
- **SSE streaming**: `GenerationStream.jsx` for real-time LLM response streaming
- **3-step deposit flow**: `DepositFlow.jsx` (approve → deposit → allocate)
- **Wallet gating**: `WalletGate.jsx` route wrapper for authenticated pages
- **EIP-6963**: Multi-injected-provider wallet connect

## Constraints and rules
- UnoCSS for styling — not Tailwind or plain CSS
- ESLint 10 for linting (`eslint.config.js`)
- `make ui-dev` for Vite dev server (from project root)
- API client in `api.js` — all backend calls routed through it

## Related skills
- See `.agents/skills/archimedes-fork-1__backend` (APIs the UI consumes)

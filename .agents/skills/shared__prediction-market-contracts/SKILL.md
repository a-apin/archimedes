---
name: shared__prediction-market-contracts
description: Shared knowledge about the AuctionManager, PredictionMarket, MarketFactory, and PublishingRightsNFT contract interfaces, lifecycle, and ABIs — consumed by both on-chain (auction-prediction-market) and off-chain (cypherlexicon-offchain) sources
triggers: [prediction market contracts, auction manager abi, market factory abi, prediction market lifecycle, publishing rights nft]
---

# Shared — Prediction Market Contracts

**Source**: shared (auction-prediction-market, cypherlexicon-offchain)
**Category**: Shared

## When to use this skill
Understanding the contract interface between the on-chain Solidity contracts (auction-prediction-market) and the off-chain backend (cypherlexicon-offchain). Needed when modifying contract interfaces, ABIs, or the lifecycle between contracts and backends.

## Key files and folders
- **On-chain source**: `/home/ricardo/github/auction-prediction-market/src/`
- **Off-chain ABIs (inline in source)**: `/home/ricardo/github/CypherLexicon-offchain/backend/blockchain.js` and `/home/ricardo/github/CypherLexicon-offchain/backend/core/blockchain.js` (ABI arrays defined directly in JS)
- **Off-chain blockchain layer**: `/home/ricardo/github/CypherLexicon-offchain/backend/blockchain.js`
- **Deployment guide**: `/home/ricardo/github/auction-prediction-market/GUIDE.md`

## Contract Lifecycle
```
AuctionManager.createAuction()
  → Users submit proposals (off-chain: CypherLexicon auction routes)
  → AI scoring (off-chain: CypherLexicon AI agents)
  → AuctionManager.resolveAuction(winner)
  → MarketFactory.createMarket(...) creates PredictionMarket
  → PredictionMarket.buyShares(YES/NO) (off-chain: CypherLexicon market routes)
  → Market resolves → YES holders win → PublishingRightsNFT minted
```

## Key interfaces shared across repos
| Contract | On-chain (auction-prediction-market) | Off-chain (cypherlexicon-offchain) |
|---|---|---|
| `AuctionManager` | `/home/ricardo/github/auction-prediction-market/src/AuctionManager.sol` | `/home/ricardo/github/CypherLexicon-offchain/backend/blockchain.js` (inline ABI array) |
| `PredictionMarket` | `/home/ricardo/github/auction-prediction-market/src/PredictionMarket.sol` | `/home/ricardo/github/CypherLexicon-offchain/backend/blockchain.js` (inline ABI array) |
| `MarketFactory` | `/home/ricardo/github/auction-prediction-market/src/MarketFactory.sol` | `/home/ricardo/github/CypherLexicon-offchain/backend/blockchain.js` (inline ABI array) |
| `PublishingRightsNFT` | `/home/ricardo/github/auction-prediction-market/src/PublishingRightsNFT.sol` | `/home/ricardo/github/CypherLexicon-offchain/backend/blockchain.js` (inline ABI array) |

## Critical Rule
**When contract interfaces change**: (1) Update the Solidity source, (2) Re-deploy, (3) Update inline ABI arrays in `/home/ricardo/github/CypherLexicon-offchain/backend/blockchain.js` and `/home/ricardo/github/CypherLexicon-offchain/backend/core/blockchain.js`, (4) Update event listeners in `/home/ricardo/github/CypherLexicon-offchain/backend/blockchain.js`.

## Related skills
- See `.agents/skills/auction-prediction-market__contracts` — full contract implementation details
- See `.agents/skills/cypherlexicon-offchain__blockchain-layer` — off-chain contract interaction
- See `.agents/skills/cypherlexicon-offchain__auction-service` — auction business logic
- See `.agents/skills/cypherlexicon-offchain__prediction-market-backend` — market business logic
- See `.agents/skills/shared__arc-blockchain` — Arc testnet deployment config

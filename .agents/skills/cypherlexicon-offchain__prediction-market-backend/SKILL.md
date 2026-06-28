---
name: cypherlexicon-offchain__prediction-market-backend
description: Prediction market backend — betting, market creation, resolution, balance management, fee claims
triggers: [cypherlexicon market, cypherlexicon prediction, cypherlexicon betting, cypherlexicon resolve]
---

# CypherLexicon Prediction Market Backend

**Source**: cypherlexicon-offchain
**Category**: Core

## When to use this skill
Working on CypherLexicon's prediction market logic: market CRUD, betting, resolution, balance queries, fee claiming.

## Key files and folders
- **Market routes**: `/home/ricardo/github/CypherLexicon-offchain/backend/market/routes.js`
- **Blockchain (on-chain calls)**: `/home/ricardo/github/CypherLexicon-offchain/backend/core/blockchain.js`
- **Agents (market resolution logic)**: `/home/ricardo/github/CypherLexicon-offchain/backend/core/agents.js`
- **Tests**: `/home/ricardo/github/CypherLexicon-offchain/tests/market/routes.test.js`

## Key concepts
- Markets are created for term sets with binary outcomes (YES/NO)
- Betting by purchasing outcome shares
- Resolution based on auction results (AI scoring determines the winning term)
- Fee claims allow protocol fee withdrawal

## Related skills
- See `.agents/skills/cypherlexicon-offchain__blockchain-layer` — on-chain market contract interaction
- See `.agents/skills/cypherlexicon-offchain__auction-service` — auctions determine market resolution
- See `.agents/skills/shared__prediction-market-contracts` — PredictionMarket/MarketFactory contract interfaces

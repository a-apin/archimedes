---
name: cypherlexicon-offchain__auction-service
description: Auction backend — CRUD auctions, proposal encoding, AI-driven scoring, rewards distribution
triggers: [cypherlexicon auction, cypherlexicon proposals, cypherlexicon ai scoring, lexicon auction]
---

# CypherLexicon Auction Service

**Source**: cypherlexicon-offchain
**Category**: Core

## When to use this skill
Working on CypherLexicon's auction logic: creating/managing auctions, encoding proposals, AI-scoring submissions, distributing rewards.

## Key files and folders
- **Auction routes**: `/home/ricardo/github/CypherLexicon-offchain/backend/auction/routes.js`
- **Auction service**: `/home/ricardo/github/CypherLexicon-offchain/backend/auction/service.js`
- **AI agents (scoring)**: `/home/ricardo/github/CypherLexicon-offchain/backend/core/agents.js`
- **Blockchain (on-chain calls)**: `/home/ricardo/github/CypherLexicon-offchain/backend/core/blockchain.js`
- **Tests**:
  - `/home/ricardo/github/CypherLexicon-offchain/tests/auction/service.test.js`
  - `/home/ricardo/github/CypherLexicon-offchain/tests/auction/routes.test.js`

## Key concepts
- Auctions manage term proposals with AI-powered quality scoring
- Proposals are encoded into structured format for on-chain submission
- Three-phase lifecycle: Open → Submitting → Resolving
- Rewards distributed based on ranking/scores after resolution

## Constraints and rules
- Routes follow Express conventions — see `backend/server.js` for mount points
- All async errors must use `next(err)` for the Express error handler
- Tests: Vitest with `pnpm test`

## Related skills
- See `.agents/skills/cypherlexicon-offchain__ai-agents` — the LLM agents that define evaluation criteria
- See `.agents/skills/cypherlexicon-offchain__blockchain-layer` — on-chain auction contract interaction
- See `.agents/skills/shared__prediction-market-contracts` — AuctionManager contract interface

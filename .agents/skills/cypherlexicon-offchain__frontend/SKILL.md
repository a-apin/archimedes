---
name: cypherlexicon-offchain__frontend
description: Vanilla JS browser frontend — app shell, auction UI, market UI, leaderboard, web3 wallet connection
triggers: [cypherlexicon ui, cypherlexicon frontend, cypherlexicon web3, cypherlexicon app]
---

# CypherLexicon Frontend

**Source**: cypherlexicon-offchain
**Category**: Core

## When to use this skill
Working on the CypherLexicon browser frontend: HTML pages, vanilla JS controllers in `js/core/`, `js/auction/`, `js/market/`, web3 wallet connection via ethers.js, or UI styling.

## Key files and folders
- **Public root**: `/home/ricardo/github/CypherLexicon-offchain/public/`
- **App entry**: `/home/ricardo/github/CypherLexicon-offchain/public/index.html`
- **JS app**: `/home/ricardo/github/CypherLexicon-offchain/public/js/app.js`
- **Core modules**: `/home/ricardo/github/CypherLexicon-offchain/public/js/core/`
  - `api.js`, `ui.js`, `web3.js`, `leaderboard.js`, `agents.js`
- **Auction UI**: `/home/ricardo/github/CypherLexicon-offchain/public/js/auction/auction.js`
- **Market UI**: `/home/ricardo/github/CypherLexicon-offchain/public/js/market/market.js`
- **Web3 integration**: `/home/ricardo/github/CypherLexicon-offchain/public/js/core/web3.js`, `public/js/web3.js`
- **Stylesheets**: `/home/ricardo/github/CypherLexicon-offchain/public/css/`

## Key concepts
- Vanilla JavaScript (no framework) served as static files from Express
- Web3 wallet connection via `ethers.js` (browser build)
- SPA-like navigation with hash-based routing
- Connects to backend at same origin (or configured API URL)

## Related skills
- See `.agents/skills/cypherlexicon-offchain__auction-service` — the API the frontend calls
- See `.agents/skills/cypherlexicon-offchain__prediction-market-backend` — the market API
- See `.agents/skills/cypherlexicon-offchain__blockchain-layer` — shared ethers.js patterns for web3 integration

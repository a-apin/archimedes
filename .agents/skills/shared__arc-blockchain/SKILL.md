---
name: shared__arc-blockchain
description: Arc testnet chain configuration, RPC endpoints, USDC/CCTP addresses, explorer, and deployment patterns shared across all ecosystem repos
triggers: [arc testnet, arc chain id, arc rpc, arc usdc, arc cctp, arc explorer]
---

# Shared — Arc Blockchain Configuration

**Source**: shared (archimedes, cypherlexicon-offchain, auction-prediction-market, arc-nanopayments)
**Category**: Shared

## When to use this skill
Setting up Arc testnet connections in any source repo, configuring RPC endpoints, looking up USDC/CCTP contract addresses, or understanding Arc-specific deployment patterns.

## Key files and folders
- **Arc docs**: `/home/ricardo/.arc-canteen/context/docs/docs.arc.network/arc/`
- **Connect to Arc**: `/home/ricardo/.arc-canteen/context/docs/docs.arc.network/arc/references/connect-to-arc.md`
- **Chain config**: `/home/ricardo/.arc-canteen/context/docs/docs.arc.network/arc/references/`
- **Archimedes chain layer**: `/home/ricardo/github/archimedes/backend/archimedes/chain/`
- **CypherLexicon blockchain**: `/home/ricardo/github/CypherLexicon-offchain/backend/blockchain.js`
- **Auction-PM foundry.toml**: `/home/ricardo/github/auction-prediction-market/foundry.toml`

## Key concepts
- **Chain ID**: 5042002 (Arc testnet)
- **RPC**: `https://rpc.arcchain.io` (configurable — see each repo's config)
- **Native gas token**: USDC (6 decimal places, NOT ETH)
- **Explorer**: Escrows canary explorer (testnet explorer URL in docs)
- **CCTP**: Circle Cross-Chain Transfer Protocol for bridging USDC to/from Arc

## Decision Points
| Decision | Recommendation |
|---|---|
| Local vs Arc testnet | Use Arc testnet for integration tests; `anvil` for unit tests |
| Private key vs Circle signer | Archimedes/CypherLexicon: Circle signer; Auction-PM: Foundry keystore for dev |
| USDC amount precision | Always 6 decimal places — `1_000_000` = 1 USDC |

## Per-Source Paths
- **Archimedes**: chain config in `/home/ricardo/github/archimedes/backend/archimedes/chain/`
- **CypherLexicon**: provider in `/home/ricardo/github/CypherLexicon-offchain/backend/blockchain.js`
- **Auction Prediction Market**: network config in `/home/ricardo/github/auction-prediction-market/foundry.toml`
- **ARC Nanopayments**: chain config in `/home/ricardo/github/arc-nanopayments/lib/x402.ts`

## Related skills
- See `.agents/skills/arc-canteen-context__arc-docs` — full Arc chain reference docs
- See `.agents/skills/arc-canteen-context__circle-docs` — Circle wallet and CCTP docs
- See `.agents/skills/archimedes__smart-contracts` — deployed on Arc
- See `.agents/skills/auction-prediction-market__deployment-testing` — Arc testnet deployment
- See `.agents/skills/cypherlexicon-offchain__blockchain-layer` — Arc provider setup

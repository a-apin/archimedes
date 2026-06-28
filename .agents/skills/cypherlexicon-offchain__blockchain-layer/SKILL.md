---
name: cypherlexicon-offchain__blockchain-layer
description: ethers.js blockchain interaction — contract instances, ABI loading, event listeners, and Arc testnet provider setup
triggers: [cypherlexicon blockchain, cypherlexicon ethers, cypherlexicon contract, cypherlexicon arc]
---

# CypherLexicon Blockchain Layer

**Source**: cypherlexicon-offchain
**Category**: Integration

## When to use this skill
Working on blockchain interaction: contract instantiation with ethers.js, event listening, ABI management, Arc testnet provider config.

## Key files and folders
- **Blockchain helper (root)**: `/home/ricardo/github/CypherLexicon-offchain/backend/blockchain.js`
- **Blockchain helper (core)**: `/home/ricardo/github/CypherLexicon-offchain/backend/core/blockchain.js`
- **Solidity copies (source of ABI)**: `/home/ricardo/github/CypherLexicon-offchain/.contracts/`
- **Server entry**: `/home/ricardo/github/CypherLexicon-offchain/backend/server.js`
- **Package**: `/home/ricardo/github/CypherLexicon-offchain/package.json`

> **Note**: A dedicated `contractABIs/` directory does not yet exist. Contract ABIs are
> currently available only from the Solidity sources in `.contracts/`. When adding ABI
> loading, either create a `contractABIs/` directory or call the on-chain contracts
> directly via ethers from the `.contracts/` source.

## Key concepts
- Uses `ethers` v6 for contract interactions on Arc testnet
- Provider connects to Arc testnet RPC (`https://rpc.arcchain.io` or local)
- Two `blockchain.js` files: root (main entry) and `core/` (reused by core modules)
- Event listeners for: Auction events, Market events, Token transfers
- All signer operations through Circle Developer-Controlled Wallets (no raw private keys)

## Constraints and rules
- Contract interface changes must be mirrored in both `blockchain.js` files
- Arc testnet Chain ID: 5042002

## Related skills
- See `.agents/skills/shared__prediction-market-contracts` — contract ABIs and interfaces
- See `.agents/skills/shared__arc-blockchain` — Arc testnet chain config and RPC details
- See `.agents/skills/cypherlexicon-offchain__auction-service` — auction events consumed here
- See `.agents/skills/cypherlexicon-offchain__prediction-market-backend` — market events consumed here

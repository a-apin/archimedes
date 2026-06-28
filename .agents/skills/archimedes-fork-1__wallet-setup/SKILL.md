---
name: archimedes-fork-1__wallet-setup
description: Circle wallet Node.js scripts — deploy contracts, create wallets, feed prices, seed liquidity, manage secrets
triggers: [archimedes-fork-1 wallet, Circle, wallet-setup, archimedes-fork-1 deploy, archimedes-fork-1 circle]
---

# Wallet Setup — Archimedes Fork

**Source**: `archimedes-fork-1`
**Category**: Integration

## When to use this skill
Deploying contracts on Arc testnet via Circle wallet infrastructure, creating/managing Circle wallets, feeding oracle prices, seeding liquidity, managing entity secrets, or any Circle Developer-Controlled Wallet operations.

## Key files and folders
- `/home/ricardo/github/archimedes-fork-1/wallet-setup/package.json` — Node.js project deps
- `/home/ricardo/github/archimedes-fork-1/wallet-setup/deploy.mjs` — Contract deployment script
- `/home/ricardo/github/archimedes-fork-1/wallet-setup/deploy-new.mjs` — Deploy new contracts
- `/home/ricardo/github/archimedes-fork-1/wallet-setup/deploy-oracles.mjs` — Deploy oracle contracts
- `/home/ricardo/github/archimedes-fork-1/wallet-setup/create-wallet.mjs` — Create Circle wallet
- `/home/ricardo/github/archimedes-fork-1/wallet-setup/feed-price.mjs` — Feed price to oracle
- `/home/ricardo/github/archimedes-fork-1/wallet-setup/seed-liquidity.mjs` — Seed AMM liquidity
- `/home/ricardo/github/archimedes-fork-1/wallet-setup/setvault.mjs` — Vault configuration
- `/home/ricardo/github/archimedes-fork-1/wallet-setup/gen-ciphertext.mjs` — Generate ciphertext
- `/home/ricardo/github/archimedes-fork-1/wallet-setup/register-entity-secret.mjs` — Register entity secret
- `/home/ricardo/github/archimedes-fork-1/wallet-setup/rotate-secret.mjs` — Rotate entity secret

## Key concepts
- **Circle Developer-Controlled Wallets** for on-chain operations
- **Deploy flow**: Deploy contracts via `deploy.mjs` / `deploy-new.mjs` on Arc testnet
- **Oracle**: Price feeding via `feed-price.mjs` to PriceOracle contract
- **Liquidity seeding**: `seed-liquidity.mjs` for AMM pool initialization
- **Secrets management**: Entity secret registration and rotation scripts
- All scripts use Node.js (`.mjs` — ES modules)

## Constraints and rules
- Run `npm install` in `wallet-setup/` before using scripts
- Requires Circle API credentials in environment
- Arc testnet only — never point at mainnet
- Scripts are standalone — not part of the backend Docker container

## Related skills
- See `.agents/skills/archimedes-fork-1__smart-contracts` (contracts these scripts deploy)
- See `.agents/skills/archimedes-fork-1__backend` (Circle service integration)
- See `.agents/skills/shared__arc-blockchain` (Arc testnet config)

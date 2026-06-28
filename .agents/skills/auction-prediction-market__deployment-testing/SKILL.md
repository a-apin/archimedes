---
name: auction-prediction-market__deployment-testing
description: Foundry test suite, deployment scripts, Arc testnet config, and project build tooling
triggers: [auction prediction test, auction prediction deploy, auction prediction forge, auction prediction foundry]
---

# Auction Prediction Market Deployment & Testing

**Source**: auction-prediction-market
**Category**: Infrastructure

## When to use this skill
Running Foundry tests, deploying contracts to Arc testnet, managing foundry.toml config, or working with project build files.

## Key files and folders
- **Foundry config**: `/home/ricardo/github/auction-prediction-market/foundry.toml`
- **Tests**: `/home/ricardo/github/auction-prediction-market/test/`
  - `AuctionManager.t.sol`, `PredictionMarket.t.sol`, `PublishingRightsNFT.t.sol`
- **Deploy scripts**: `/home/ricardo/github/auction-prediction-market/script/`
- **Remappings**: defined inline in `/home/ricardo/github/auction-prediction-market/foundry.toml`
- **Deployment guide**: `/home/ricardo/github/auction-prediction-market/GUIDE.md` — step-by-step deploy instructions (already verified working on Arc testnet)

## Key concepts
- Foundry with `foundry.toml` targeting Arc testnet
- Guide.md documents the full deployment sequence with exact `forge create` commands
- Test suite covers all four contracts with Foundry's native test runner
- `forge test -vv` for verbose test output

## Constraints and rules
- Run `forge build` before tests
- Arc testnet Chain ID: 5042002, RPC: configurable in `foundry.toml`
- Deploy via `forge create` with `--rpc-url arc-testnet` and `--private-key` (use Circle signer in production)

## Related skills
- See `.agents/skills/auction-prediction-market__contracts` — the contracts being tested/deployed
- See `.agents/skills/shared__arc-blockchain` — Arc testnet RPC, explorer, and chain config
- See `.agents/skills/shared__prediction-market-contracts` — the contract interfaces across the ecosystem

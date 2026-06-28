---
name: archimedes__smart-contracts
description: 10 Solidity contracts deployed on Arc testnet — vaults, AMM, oracle, synthetic assets, trace registry
triggers: [archimedes contracts, archimedes solidity, archimedes vault, archimedes amm, archimedes forge]
---

# Archimedes Smart Contracts

**Source**: archimedes
**Category**: Core

## When to use this skill
Working on Archimedes' Solidity contracts: vault logic, AMM pools, oracle, synthetic tokens, or deploying to Arc testnet via Foundry.

## Key files and folders
- **All contracts**: `/home/ricardo/github/archimedes/contracts/src/`
- **Interfaces**: `/home/ricardo/github/archimedes/contracts/src/interfaces/`
- **Chain interaction (Python)**: `/home/ricardo/github/archimedes/backend/archimedes/chain/`
  - `circle_signer.py` — all on-chain writes via Circle API (no raw private keys)
  - `contracts.py` — contract addresses + ABI loading
  - `executor.py` — transaction execution helpers
  - `oracle_runner.py` — oracle price pushes

## Key contracts
| Contract | File | Role |
|---|---|---|
| `Vault.sol` | `contracts/src/Vault.sol` | ERC-4626 user vault, multi-asset NAV via oracles |
| `VaultFactory.sol` | `contracts/src/VaultFactory.sol` | Vault deployer |
| `AMMPool.sol` | `contracts/src/AMMPool.sol` | x*y=k AMM for synthetic asset trading |
| `AMMRouter.sol` | `contracts/src/AMMRouter.sol` | Swap entry/routing |
| `PriceOracle.sol` | `contracts/src/PriceOracle.sol` | Oracle prices, Circle-Wallets-signed pushes |
| `SyntheticToken.sol` | `contracts/src/SyntheticToken.sol` | ERC-20 synths (sTSLA, sSPY, sGOLD) |
| `SyntheticFactory.sol` | `contracts/src/SyntheticFactory.sol` | Synthetic minting factory |
| `SyntheticVault.sol` | `contracts/src/SyntheticVault.sol` | Per-synth collateral vault |
| `AssetRegistry.sol` | `contracts/src/AssetRegistry.sol` | Strategy + asset registry |
| `ReasoningTraceRegistry.sol` | `contracts/src/ReasoningTraceRegistry.sol` | On-chain anchor for agent trace hashes |

## Constraints and rules
- **Solidity 0.8.19**, EVM target `paris`, optimizer 200 runs, via-IR compilation
- **Non-custodial**: agent has rebalance-only authority on vaults
- **Testing**: `forge test -vv` for contract tests
- **Deployment**: through `circle_signer.py` (Circle Developer-Controlled Wallets), not raw private keys

## Related skills
- See `.agents/skills/archimedes__backend` for the backend that calls these contracts
- See `.agents/skills/archimedes__agents` for the agent system that triggers rebalances
- See `.agents/skills/shared__arc-blockchain` for Arc testnet RPC, USDC address, explorer

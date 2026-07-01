---
name: titanoboa__deployments-verification
description: Deployments database, contract verification via Etherscan, SQLite-backed deployment registry
---

# Deployments & Verification

**Source**: titanoboa
**Category**: Domain

## When to use this skill
Tracking deployed contract addresses, verifying contracts on Etherscan, managing the local deployments SQLite database.

## Key files and folders
- `/home/ricardo/github/titanoboa/boa/deployments.py` — Deployments database (SQLite-backed registry)
- `/home/ricardo/github/titanoboa/boa/verifiers.py` — Contract verification logic
- `/home/ricardo/github/titanoboa/boa/explorer.py` — Etherscan API integration
- `/home/ricardo/github/titanoboa/boa/util/sqlitedb.py` — SQLite utilities used by the deployments DB

## Key concepts
- **Deployments DB**: local SQLite database tracking all deployed contract addresses, chain IDs, and metadata.
- **Verification**: `boa.verifiers.verify()` submits contract source to Etherscan for verification.
- **Explorer**: `from_etherscan()` loads already-deployed contracts by address from Etherscan.

## Related skills
- See `.agents/skills/titanoboa__vyper-contracts` — the contracts that get deployed and verified
- See `.agents/skills/titanoboa__forking-network` — network mode where deployments happen

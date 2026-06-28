---
name: vyper-agentic-payments__scripts-deploy
description: Deployment and interaction scripts for Vyper contracts on Arc testnet — Moccasin build tool, titanoboa deployer
triggers: [vyper-agentic-payments deploy, moccasin, vyper-agentic-payments scripts, vyper-agentic-payments config, vyper-agentic-payments deployment]
---

# Scripts & Deploy — Vyper Agentic Payments

**Source**: `vyper-agentic-payments`
**Category**: Infrastructure

## When to use this skill
Deploying Vyper contracts to Arc testnet, interacting with live contracts, configuring Moccasin, or managing the project build toolchain.

## Key files and folders
- `/home/ricardo/github/vyper-agentic-payments/scripts/deploy_boa.py` — Deploys all contracts to Arc testnet via titanoboa (reads .env for RPC, private key)
- `/home/ricardo/github/vyper-agentic-payments/scripts/interact_boa.py` — Connects to deployed contracts on Arc testnet (reads deployment artifacts, provides CLI interaction)
- `/home/ricardo/github/vyper-agentic-payments/moccasin.toml` — Moccasin project config: contract source dir (`contracts/`), external dependencies (`erc-8004-vyper`, `snekmate`)
- `/home/ricardo/github/vyper-agentic-payments/pyproject.toml` — Python project config: hatchling build, pytest, ruff, mypy
- `/home/ricardo/github/vyper-agentic-payments/.env.example` — All required env vars for deployment (CIRCLE_API_KEY, PRIVATE_KEY, ARC_TESTNET_RPC, etc.)
- `/home/ricardo/github/vyper-agentic-payments/.pre-commit-config.yaml` — Pre-commit hooks (ruff, mypy)
- `/home/ricardo/github/vyper-agentic-payments/run_tests.sh` — Test runner script (avoids titanoboa caching bug)

## Key concepts
- **Moccasin**: Vyper package manager and build tool — handles dependencies (`erc-8004-vyper`, `snekmate`)
- **Titanoboa deployer**: `deploy_boa.py` uses `boa.network` to deploy to real chains
- **Arc testnet**: Chain ID 5042002, USDC is gas token, RPC from `.env`
- **Dependencies**: `lufa23/erc-8004-vyper@0.1.0` (agent identity NFTs), `snekmate==0.1.2` (Vyper utilities)
- **Local dev**: `pip install -e .` for editable install, `pytest` for testing
- **No CI/CD**: No GitHub Actions — all testing is local via pre-commit and manual pytest

## Constraints and rules
- Set `.env` variables before any deployment (copy from `.env.example`)
- Circle API key + entity secret required for x402/Gateway operations
- All deployments target Arc testnet — never mainnet without explicit confirmation
- Moccasin dependencies install into `lib/` (gitignored)
- Pre-commit hooks enforce ruff lint/format + mypy type checking

## Related skills
- See `.agents/skills/vyper-agentic-payments__contracts` (contracts these scripts deploy)
- See `.agents/skills/vyper-agentic-payments__tests` (test commands in pyproject.toml)
- See `.agents/skills/shared__arc-blockchain` (Arc testnet config, RPC, USDC)

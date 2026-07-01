---
name: titanoboa__forking-network
description: Mainnet forking via RPC, NetworkEnv for real-chain deployment, RPC client layer
---

# Forking & Network Mode

**Source**: titanoboa
**Category**: Domain

## When to use this skill
Forking mainnet/testnet state via RPC, deploying to real chains, configuring RPC endpoints, managing the JSON-RPC client layer.

## Key files and folders
- `/home/ricardo/github/titanoboa/boa/vm/fork.py` — `AccountDBFork`, RPC-backed account DB with caching
- `/home/ricardo/github/titanoboa/boa/rpc.py` — `RPC`, `EthereumRPC` JSON-RPC client layer
- `/home/ricardo/github/titanoboa/boa/network.py` — `NetworkEnv` for real-chain mode (eth_call, eth_sendRawTransaction, EIP-1559)
- `/home/ricardo/github/titanoboa/boa/util/sqlitedb.py` — SQLite-based fork cache
- `/home/ricardo/github/titanoboa/boa/util/lrudict.py` — LRU dict for in-memory fork caching

## Key concepts
- **AccountDBFork**: extends py-evm's `AccountDB`, lazily fetches accounts/storage from RPC with caching.
- **CachingRPC**: wraps RPC responses to reduce network calls during fork sessions.
- **NetworkEnv**: subclass of `Env` that sends real transactions via `eth_sendRawTransaction`/`eth_call`, manages EIP-1559 fees, tracks deployments.
- **Fork entry points**: `boa.fork()` or `Env.fork()` from the public API.

## Decision points
- Use fork mode for testing against production state without spending gas.
- Use NetworkEnv for actual deployment and live interaction.
- Integration tests require RPC endpoints in environment variables (see `/home/ricardo/github/titanoboa/.env.unsafe.example`).

## Related skills
- See `.agents/skills/titanoboa__core-api` — the public fork/set_env API
- See `.agents/skills/titanoboa__evm-layer` — the local EVM execution used by fork mode
- See `.agents/skills/titanoboa__deployments-verification` — deployment tracking for network mode

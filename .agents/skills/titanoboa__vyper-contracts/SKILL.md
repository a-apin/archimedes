---
name: titanoboa__vyper-contracts
description: Vyper contract loading, compilation, deployment, and interaction via VyperContract and VyperDeployer
---

# Vyper Contracts

**Source**: titanoboa
**Category**: Domain

## When to use this skill
Loading, deploying, compiling, or interacting with Vyper smart contracts, including multi-version Vyper support via VVM, ABI-only contracts, and blueprint contracts.

## Key files and folders
- `/home/ricardo/github/titanoboa/boa/interpret.py` — Contract loading dispatcher (`load`, `loads`, `loads_partial`, `from_etherscan`)
- `/home/ricardo/github/titanoboa/boa/contracts/vyper/vyper_contract.py` — `VyperContract`, `VyperDeployer`, `VyperBlueprint`, `VyperFunction`
- `/home/ricardo/github/titanoboa/boa/contracts/vyper/compiler_utils.py` — Internal function bytecode, eval bytecode generation
- `/home/ricardo/github/titanoboa/boa/contracts/vyper/decoder_utils.py` — Vyper object storage decoding
- `/home/ricardo/github/titanoboa/boa/contracts/vyper/ast_utils.py` — AST helpers for source location and error messages
- `/home/ricardo/github/titanoboa/boa/contracts/vyper/ir_executor.py` — IR executor for fast-mode contract execution
- `/home/ricardo/github/titanoboa/boa/contracts/base_evm_contract.py` — `_BaseEVMContract`, `BoaError`
- `/home/ricardo/github/titanoboa/boa/contracts/abi/abi_contract.py` — `ABIContractFactory` for non-Vyper ABI-based contracts
- `/home/ricardo/github/titanoboa/boa/contracts/vvm/vvm_contract.py` — `VVMDeployer` for multi-version Vyper support
- `/home/ricardo/github/titanoboa/boa/contracts/call_trace.py` — `TraceFrame` for call trace data structures
- `/home/ricardo/github/titanoboa/boa/contracts/event_decoder.py` — Log/event decoding utilities

## Key concepts
- **VyperContract**: returned by `boa.load()`, wraps ABI + bytecode, methods are callable directly.
- **VyperDeployer**: returned by `boa.loads_partial()`, takes constructor args to deploy.
- **VyperBlueprint**: for EIP-5202 blueprint contracts; deploy via `create2` / `create` / `create_from_blueprint`.
- **VVM**: automatically downloads and uses the correct Vyper compiler version matching the source pragma.

## Decision points
- Use `boa.load()` for immediate deployment; use `boa.loads_partial()` to defer constructor args.
- Use `ABIContractFactory` for non-Vyper EVM contracts (Solidity, raw bytecode).
- Use `from_etherscan()` to fetch and recreate already-deployed contracts.

## Related skills
- See `.agents/skills/titanoboa__core-api` — the API that produces contract objects
- See `.agents/skills/titanoboa__evm-layer` — the EVM execution behind contract calls

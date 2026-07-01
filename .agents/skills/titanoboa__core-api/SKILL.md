---
name: titanoboa__core-api
description: Titanoboa public API — Env singleton, load/deploy/fork entry points, context management
---

# Core API

**Source**: titanoboa
**Category**: Core

## When to use this skill
Any task that involves the main Titanoboa Python API: creating environments, loading Vyper contracts, forking mainnet, deploying to networks, or using context managers for temporary state.

## Key files and folders
- `/home/ricardo/github/titanoboa/boa/__init__.py` — Public API surface (`load`, `loads`, `load_partial`, `fork`, `set_env`, `reverts`, `eval`, `deploy`, etc.)
- `/home/ricardo/github/titanoboa/boa/environment.py` — `Env` class with singleton pattern; all execution context
- `/home/ricardo/github/titanoboa/boa/network.py` — `NetworkEnv` subclass for real-chain deployment
- `/home/ricardo/github/titanoboa/boa/interpret.py` — Contract loading, compilation caching, Vyper import hooks
- `/home/ricardo/github/titanoboa/boa/util/open_ctx.py` — `Open` context manager for swapping global state

## Key concepts
- **Env singleton**: accessed via `Env.get_singleton()` or `boa.env`. Manages EVM state, accounts, gas policies.
- **Context managers**: `boa.reverts()`, `boa.env.anchor()`, `Open()` for scoped state changes.
- **Load flows**: `boa.load("path.vy")` → `interpret.py` → Vyper compiler → `VyperContract`.
- **eval mode**: `boa.eval()` compiles and executes Vyper expressions directly in the EVM.

## Constraints and rules
- Always use `boa.env` instead of constructing `Env` directly.
- Never import internal modules (e.g. `boa.vm.py_evm`) when the public API in `__init__.py` suffices.
- Integration tests need RPC endpoints set via environment variables; unit tests do not.

## Related skills
- See `.agents/skills/titanoboa__vyper-contracts` — contract objects produced by the API
- See `.agents/skills/titanoboa__forking-network` — fork and network modes

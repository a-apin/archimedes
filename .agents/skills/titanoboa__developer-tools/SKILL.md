---
name: titanoboa__developer-tools
description: Debugger, IPython/Jupyter magics, Etherscan explorer integration, profiling
---

# Developer Tools

**Source**: titanoboa
**Category**: Tooling

## When to use this skill
Debugging Vyper contracts, using IPython/Jupyter magics, integrating with Etherscan, profiling gas usage, or working with the browser-based signer.

## Key files and folders
- `/home/ricardo/github/titanoboa/boa/debugger.py` — `BoaDebug`, pdb-based debugger triggered by opcode `0xA6`
- `/home/ricardo/github/titanoboa/boa/ipython.py` — IPython magics: `%vyper`, `%%vyper`, `%%eval`
- `/home/ricardo/github/titanoboa/boa/explorer.py` — Etherscan integration (`from_etherscan`)
- `/home/ricardo/github/titanoboa/boa/profiling.py` — Gas profiling tables (rich-based output)
- `/home/ricardo/github/titanoboa/boa/integrations/jupyter/` — JupyterLab server extension: `BrowserSigner`, `BrowserRPC`, `BrowserEnv`
- `/home/ricardo/github/titanoboa/boa/integrations/jupyter/browser.py` — Browser-based signer and RPC
- `/home/ricardo/github/titanoboa/boa/integrations/jupyter/handlers.py` — Jupyter server extension handlers

## Key concepts
- **BoaDebug**: triggered by inserting `0xA6` (debug opcode) in Vyper source. Drops into pdb with EVM state inspection.
- **IPython magics**: `%vyper` compiles and returns ABI, `%%vyper` cell magic deploys and returns a contract object, `%%eval` evaluates Vyper expressions with access to Python variables.
- **Gas profiling**: use `boa.env.enable_gas_profiling()` and `boa.env.gas_profiler.display()` for per-opcode gas tables.

## Related skills
- See `.agents/skills/titanoboa__core-api` — the API these tools interact with
- See `.agents/skills/titanoboa__vyper-contracts` — contracts loaded and inspected via these tools

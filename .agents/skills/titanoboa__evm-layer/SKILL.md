---
name: titanoboa__evm-layer
description: PyEVM wrapper, fast-mode IR executor, gas metering, opcode instrumentation
---

# EVM Layer

**Source**: titanoboa
**Category**: Infrastructure

## When to use this skill
Understanding or modifying how Titanoboa executes EVM bytecode, uses fast mode (IR interpretation), instruments opcodes, meters gas, or patches py-evm.

## Key files and folders
- `/home/ricardo/github/titanoboa/boa/vm/py_evm.py` — `PyEVM` class wrapping py-evm; `TitanoboaComputation`, `TracingCodeStream`, opcode patching
- `/home/ricardo/github/titanoboa/boa/vm/fast_accountdb.py` — Fast-mode account DB optimizations
- `/home/ricardo/github/titanoboa/boa/vm/fast_mem.py` — Fast-mode memory optimizations
- `/home/ricardo/github/titanoboa/boa/vm/gas_meters.py` — `GasMeter`, `NoGasMeter`, `ProfilingGasMeter`
- `/home/ricardo/github/titanoboa/boa/vm/utils.py` — EVM utility conversions
- `/home/ricardo/github/titanoboa/boa/contracts/vyper/ir_executor.py` — IR executor (fast-mode contract execution)
- `/home/ricardo/github/titanoboa/boa/util/evm.py` — EVM utility helpers

## Key concepts
- **PyEVM**: wraps py-evm with opcode-level tracing (SHA3, SSTORE, JUMPI), custom `TitanoboaComputation` class.
- **Fast Mode**: interprets Vyper IR directly instead of running bytecode through py-evm. Enabled via `Env.fast_mode`.
- **Gas metering**: three strategies — `GasMeter` (default), `NoGasMeter` (disable), `ProfilingGasMeter` (collect profiles).
- **Opcode patching**: `patch_opcode` mechanism — opcode `0xA6` triggers the debugger.

## Related skills
- See `.agents/skills/titanoboa__core-api` — the Env that configures the EVM layer
- See `.agents/skills/titanoboa__forking-network` — uses the EVM layer with RPC-backed state

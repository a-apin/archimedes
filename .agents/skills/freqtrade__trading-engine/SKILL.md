---
name: freqtrade__trading-engine
description: Main bot loop, Worker lifecycle, FreqtradeBot orchestrator, state machine
triggers: [freqtradebot, worker, trading loop]
---

# Trading Engine

**Source**: freqtrade
**Category**: Core

## When to use this skill
Understanding the main bot lifecycle, the Worker throttled loop, how FreqtradeBot orchestrates trading, or the RUNNING/STOPPED/PAUSED/RELOAD_CONFIG state machine.

## Key files and folders
- `/home/ricardo/github/freqtrade/freqtrade/main.py` — CLI entry point, argument parsing, dispatches to subcommands
- `/home/ricardo/github/freqtrade/freqtrade/freqtradebot.py` — `FreqtradeBot` class (~112KB), the central orchestrator
- `/home/ricardo/github/freqtrade/freqtrade/worker.py` — `Worker` class with throttled `_process_running()` / `_process_stopped()` loop
- `/home/ricardo/github/freqtrade/freqtrade/rpc/rpc_manager.py` — `RPCManager` wired into the bot
- `/home/ricardo/github/freqtrade/freqtrade/optimize/backtesting.py` — Backtesting engine (separate CLI flow)

## Key concepts
- **Worker.run()**: throttled loop (configurable `internals.process_throttle_secs`) that calls `_process()` on each tick.
- **FreqtradeBot**: wires together Exchange, Strategy, Wallets, PairListManager, ProtectionManager, RPCManager, DataProvider, and FreqAI.
- **States**: `State.RUNNING`, `State.STOPPED`, `State.PAUSED`, `State.RELOAD_CONFIG` — transitions controlled by RPC commands.

## Related skills
- See `.agents/skills/freqtrade__exchange-integration` — the exchange layer used by the bot
- See `.agents/skills/freqtrade__strategy-engine` — the strategy layer used by the bot
- See `.agents/skills/freqtrade__rpc-ui` — RPC layer controlling bot state

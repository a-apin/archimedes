---
name: archimedes__agents
description: LLM agent subsystem — strategy architect, fusion evaluator, portfolio agent, and generation pipeline
triggers: [archimedes agent, archimedes llm, archimedes fusion, archimedes architect, archimedes portfolio agent]
---

# Archimedes Agents

**Source**: archimedes
**Category**: Core

## When to use this skill
Working on Archimedes' LLM agent system: the strategy architect (generates strategies from papers), fusion evaluator (scores/ranks), portfolio agent (manages allocation), or the generation pipeline that orchestrates them.

## Key files and folders
- **Agent definitions**: `/home/ricardo/github/archimedes/backend/archimedes/agents/`
  - `strategy_architect.py` — reads q-fin papers, proposes strategy implementations
  - `strategy_fusion.py` — evaluates and fuses multiple strategy candidates
  - `portfolio_agent.py` — manages portfolio allocation across vaults
  - `generation_pipeline.py` — orchestrates the paper-to-strategy pipeline
  - `base.py` — base agent class with LLM backend integration
- **LLM backend**: `/home/ricardo/github/archimedes/backend/archimedes/services/llm_backend.py`
- **Agent API routes**: `/home/ricardo/github/archimedes/backend/archimedes/api/agent_routes.py`
- **Fusion evaluator service**: `/home/ricardo/github/archimedes/backend/archimedes/services/fusion_evaluator.py`
- **Tool execution**: `/home/ricardo/github/archimedes/backend/archimedes/chain/agent_runner.py`

## Key concepts
- **Pipeline**: Paper → Strategy Architect → Code Generation → Backtest → Fusion Evaluation → Deployment
- **Fusion scoring**: multi-criteria evaluation combining backtest metrics, complexity, and paper fidelity
- **LLM-agnostic backend**: supports multiple LLM providers via `services/llm_backend.py`
- **Guardrails**: `services/strategy_guardrail.py` validates generated strategies before backtesting

## Related skills
- See `.agents/skills/archimedes__analytics-engine` — backtesting is the evaluation step
- See `.agents/skills/archimedes__smart-contracts` — deployed strategies live in on-chain vaults
- See `.agents/skills/archimedes__backend` — API routes expose agent state

---
name: archimedes-fork-1__backend
description: FastAPI backend for Archimedes fork — routes, services, models, chain layer, agents, DB, auth
triggers: [archimedes-fork-1 backend, FastAPI, archimedes-fork-1 api, archimedes-fork-1 services, archimedes-fork-1 models, archimedes-fork-1 chain]
---

# Backend — Archimedes Fork

**Source**: `archimedes-fork-1`
**Category**: Core

## When to use this skill
Working on the FastAPI backend — API routes, business-logic services, SQLAlchemy models, blockchain integration (web3.py), AI agent orchestration, SIWE auth, database migrations, or backend tests.

## Key files and folders
- `/home/ricardo/github/archimedes-fork-1/backend/archimedes/main.py` — FastAPI app entrypoint (473 lines)
- `/home/ricardo/github/archimedes-fork-1/backend/archimedes/db.py` — SQLAlchemy engine, session, manual migration patching
- `/home/ricardo/github/archimedes-fork-1/backend/archimedes/api/` — 34 files: route modules, schemas, auth, middleware
- `/home/ricardo/github/archimedes-fork-1/backend/archimedes/services/` — 45+ service modules (LLM, backtesting, portfolio, Circle, vault, etc.)
- `/home/ricardo/github/archimedes-fork-1/backend/archimedes/models/` — 17 SQLAlchemy ORM models
- `/home/ricardo/github/archimedes-fork-1/backend/archimedes/chain/` — 15 blockchain integration modules (web3, contracts, oracle, IPFS)
- `/home/ricardo/github/archimedes-fork-1/backend/archimedes/agents/` — 5 AI agent modules (generation pipeline, portfolio, architect, fusion)
- `/home/ricardo/github/archimedes-fork-1/backend/tests/` — 100+ test files (pytest, asyncio)
- `/home/ricardo/github/archimedes-fork-1/backend/migrations/` — 2 raw SQL migration files
- `/home/ricardo/github/archimedes-fork-1/backend/requirements.txt` — Pinned Python deps
- `/home/ricardo/github/archimedes-fork-1/backend/Dockerfile` — Multi-stage Docker build

## Key concepts
- **Framework**: FastAPI + SQLAlchemy ORM (no Alembic — manual `create_all()` + raw SQL patching)
- **LLM**: Multi-provider (AWS Bedrock / Nova Micro primary, Anthropic, Ollama fallback)
- **Chain integration**: web3.py, Arc testnet (Chain ID 5042002), USDC (6 decimals) as gas token
- **Auth**: SIWE (Sign-In With Ethereum) with signed cookies
- **Rate limiting**: slowapi on selected endpoints
- **LLM backend**: `services/llm_backend.py` — multi-provider with `response.model` provenance
- **Backtesting**: `services/portfolio_backtester.py`, `services/strategy_signal_evaluator.py`, `services/rigor_evaluator.py` (DSR, PBO)
- **Circle integration**: `services/circle_service.py` — wallet management, USDC, Gateway

## Constraints and rules
- `pytest.ini` at project root sets `asyncio_mode=auto`, `testpaths=backend/tests`
- Ruff config: line-length 120, target py312, I/UP/B/SIM/RUF/ARG/C4/PIE/RET/SLF rules selected
- Tests require Docker stack (Postgres + Redis) running — `docker compose up -d --build` first
- Hermetic test pattern: `env -i HOME=$HOME PATH=$PATH PYTHONPATH=backend python -m pytest ...`
- Secrets never committed — all use `.env.example` pattern

## Related skills
- See `.agents/skills/archimedes-fork-1__analytics-engine` (backtesting engine these services call)
- See `.agents/skills/archimedes-fork-1__smart-contracts` (contracts the chain layer talks to)
- See `.agents/skills/shared__arc-blockchain` (Arc testnet, USDC, chain config shared across the ecosystem)

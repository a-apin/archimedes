---
name: archimedes__backend
description: FastAPI backend for Archimedes — routes, services, auth (SIWE), models, DB, and chain layer interaction
triggers: [archimedes backend, archimedes api, archimedes fastapi, archimedes routes, archimedes db]
---

# Archimedes Backend

**Source**: archimedes
**Category**: Core

## When to use this skill
Working on Archimedes' Python/FastAPI backend: adding routes, modifying services, changing auth (SIWE), working with models, or interacting with the database (DynamoDB + PostgreSQL).

## Key files and folders
- **App entry**: `/home/ricardo/github/archimedes/backend/archimedes/main.py`
- **Route modules**: `/home/ricardo/github/archimedes/backend/archimedes/api/`
- **Services**: `/home/ricardo/github/archimedes/backend/archimedes/services/`
- **Models (Pydantic/SQLAlchemy)**: `/home/ricardo/github/archimedes/backend/archimedes/models/`
- **Database layer**: `/home/ricardo/github/archimedes/backend/archimedes/db.py`
- **Auth (SIWE)**: `/home/ricardo/github/archimedes/backend/archimedes/api/auth_siwe.py`, `auth_guard.py`
- **Chain layer**: `/home/ricardo/github/archimedes/backend/archimedes/chain/`
- **Tests**: `/home/ricardo/github/archimedes/backend/tests/`
- **Dockerfile**: `/home/ricardo/github/archimedes/backend/Dockerfile`

## Key concepts
- FastAPI with route modules under `api/` registered via `main.py`
- SIWE (Sign-In with Ethereum) for auth; `auth_guard.py` provides dependency injection
- Services encapsulate business logic — see `services/fusion_evaluator.py`, `services/strategy_dsl.py`
- Dual DB: DynamoDB (papers index) + PostgreSQL (user profiles, vaults, backtests)
- ALB health check at root `/`; all API routes under `/api/`

## Constraints and rules
- **Python style**: ruff linting (`ruff.toml` at repo root). Run `ruff check` before committing.
- **No secrets in code**: use `.env` with `.env.example` as template.
- **Tests**: `pytest` under `backend/tests/`. Coverage required for new routes.
- **Route naming**: Plural nouns for collections (`/api/vaults`, `/api/strategies`).

## Related skills
- See `.agents/skills/archimedes__smart-contracts` for on-chain interactions
- See `.agents/skills/archimedes__agents` for the LLM agent subsystem
- See `.agents/skills/archimedes__payments` for the WS-C wallet module
- See `.agents/skills/shared__arc-blockchain` for Arc testnet config

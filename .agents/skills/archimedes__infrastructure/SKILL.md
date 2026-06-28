---
name: archimedes__infrastructure
description: Docker, Terraform, CI/CD, nginx, ops scripts, wallet-setup, and Circle integration tooling
triggers: [archimedes docker, archimedes terraform, archimedes deploy, archimedes ci, archimedes ops, archimedes wallet-setup]
---

# Archimedes Infrastructure

**Source**: archimedes
**Category**: Infrastructure

## When to use this skill
Deploying Archimedes, managing Docker/Terraform, CI/CD pipeline, wallet setup, or operational scripts.

## Key files and folders
- **Docker compose**: `/home/ricardo/github/archimedes/docker-compose.yml`
- **Production compose**: `/home/ricardo/github/archimedes/docker-compose.production.yml`
- **Nginx configs**: `/home/ricardo/github/archimedes/nginx/`
- **Terraform**: `/home/ricardo/github/archimedes/infra/`
- **Wallet setup**: `/home/ricardo/github/archimedes/wallet-setup/`
- **Dev script**: `/home/ricardo/github/archimedes/dev.sh`
- **Env setup**: `/home/ricardo/github/archimedes/setup-env.sh`
- **Makefile**: `/home/ricardo/github/archimedes/Makefile`
- **CI/CD**: `/home/ricardo/github/archimedes/.github/`
- **Operations doc**: `/home/ricardo/github/archimedes/OPERATIONS.md`

## Key concepts
- 6-service Docker stack (backend, ui, db, redis, etc.)
- Terraform for AWS: EC2, Aurora, ALB, WAF, CloudFront, ECR
- Circle Developer-Controlled Wallets for on-chain writes
- Pre-commit hooks via `.pre-commit-config.yaml`; secrets baseline via `.secrets.baseline`

## Constraints and rules
- **`.env` is gitignored**; always use `.env.example` as template
- **No production secrets in any config file**
- Always test changes locally with `docker compose up -d --build` before deploying
- CI runs ruff, pytest, and forge test

## Related skills
- See `.agents/skills/archimedes__backend` — the primary service in the stack
- See `.agents/skills/shared__arc-blockchain` — Arc testnet RPC and network config for deployment

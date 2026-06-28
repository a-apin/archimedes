---
name: archimedes-fork-1__infrastructure
description: Docker Compose, Terraform AWS, GitHub Actions CI/CD, nginx, and operational runbooks
triggers: [archimedes-fork-1 infra, Docker, Terraform, AWS, CI/CD, archimedes-fork-1 devops, docker-compose, GitHub Actions]
---

# Infrastructure — Archimedes Fork

**Source**: `archimedes-fork-1`
**Category**: Infrastructure

## When to use this skill
Working on infrastructure — Docker Compose stack, Terraform AWS provisioning, CI/CD pipelines, nginx config, deployment, or operational runbooks.

## Key files and folders
- `/home/ricardo/github/archimedes-fork-1/docker-compose.yml` — Main stack: postgres:16-alpine, redis:7-alpine, nginx, backend, oracle, agent, kb-runner
- `/home/ricardo/github/archimedes-fork-1/docker-compose.production.yml` — Production stack (Aurora + ElastiCache backed)
- `/home/ricardo/github/archimedes-fork-1/infra/` — Terraform IaC:
  - `main.tf` — Backend (S3), providers (AWS, TLS)
  - `vpc.tf` — VPC, subnets, NAT gateway
  - `alb.tf` — ALB + target group + health check
  - `asg.tf` — Auto Scaling Group, launch template, CloudWatch alarms
  - `aurora.tf` — Aurora Serverless v2 PostgreSQL
  - `elasticache.tf` — ElastiCache Redis
  - `cloudfront.tf` — CloudFront distribution
  - `waf.tf` — WAF web ACL
  - `iam/` — IAM policy JSON files
  - `scripts/` — AMI baking, SSM secrets, HTTPS setup, GitHub OIDC, budgets
  - `runbooks/` — Aurora backup/restore, disaster recovery, WAF reference
- `/home/ricardo/github/archimedes-fork-1/nginx/Dockerfile` — nginx container
- `/home/ricardo/github/archimedes-fork-1/nginx/nginx.conf` — nginx reverse proxy config
- `/home/ricardo/github/archimedes-fork-1/Makefile` — 25+ dev targets (up, down, pytest, lint, format, compile, test, wallet, feed, deploy)
- `/home/ricardo/github/archimedes-fork-1/.github/` — GitHub Actions workflows

## Key concepts
- **Dev stack**: Docker Compose (6 services) for local development
- **Prod stack**: AWS (Aurora, ElastiCache, EC2 ASG, ALB, CloudFront, WAF) managed via Terraform
- **Deployment**: Merges to `main` trigger GitHub Actions auto-deploy to EC2
- **Secrets**: SSM Parameter Store for production secrets
- **Account**: Dan's personal AWS (037613907429 / us-east-1)
- **Domain**: `archimedes-arc.com` (CloudFront → nginx → backend)
- **Terraform**: S3 state with `use_lockfile = true`, Terraform >= 1.10

## Constraints and rules
- `make up` to start local stack, `make down` to stop
- Tests require `docker compose up -d --build` first
- Terraform changes require AWS credentials (Dan's account)
- Never commit secrets — `.env.example` is the pattern
- `.github/` workflows are the CI source of truth

## Related skills
- See `.agents/skills/archimedes-fork-1__backend` (backend Dockerfile)
- See `.agents/skills/archimedes-fork-1__docs` (deployment runbook in docs/)

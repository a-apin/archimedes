> **ARCHIVED 2026-07-28 — historical. Current: [`docs/architecture.md`](../../architecture.md)**
>
> SSH-to-a-t3.medium bootstrap against PostgreSQL 16. Production is ECS Fargate with Aurora PostgreSQL 18.3; nothing here is safe to follow.

# Infrastructure & CI/CD Setup

> **Status:** Day-10 update (2026-05-22). Originally added 2026-05-13. Lead: Chuan.
> The Day-3 3-container stack has grown to **6 containers** (backend + postgres +
> redis + nginx + oracle + agent); the architecture diagram below is current.

## Architecture

```
GitHub (main branch)
    │  push/merge
    ▼
GitHub Actions (deploy.yml)
    │  SSH
    ▼
EC2 (t3.medium, us-east-1)
    │
    ├── docker compose (6 services)
    │   ├── backend   (FastAPI :8000) — health-checked
    │   ├── postgres  (PostgreSQL 16 :5432) — health-checked
    │   ├── redis     (Redis 7 :6379) — health-checked
    │   ├── nginx     (reverse proxy :80) — no HTTP health probe
    │   ├── oracle    (price-feed loop) — no HTTP health probe
    │   └── agent     (autonomous strategy runner) — no HTTP health probe
    │
    ├── volumes:
    │   ├── pgdata                       (Postgres data, survives redeploys)
    │   └── archimedes-corpus-artifact   (heavy KB pipeline artifact, scaffolded)
    │
    └── /opt/archimedes (git repo)
```

## EC2 Instance

| Field         | Value                                                 |
| ------------- | ----------------------------------------------------- |
| Instance ID   | `i-0987f70a131ed3ab1`                                 |
| Type          | `t3.medium` (2 vCPU, 4 GB RAM)                        |
| Region        | `us-east-1` (N. Virginia)                                  |
| AMI           | Ubuntu 24.04 LTS (x86_64)                            |
| Public IP     | `13.40.112.220`                                       |
| Public DNS    | `ec2-13-40-112-220.us-east-1.compute.amazonaws.com`   |
| Volume        | 20 GB gp3                                             |
| Cost          | ~$17/month                                            |

### SSH Access

```bash
ssh -i infra/archimedes-deploy-key.pem ubuntu@13.40.112.220
```

The private key is in `infra/archimedes-deploy-key.pem` (gitignored). Ask in
#infra on Discord if you need a copy.

### Ports Open (Security Group)

| Port  | Protocol | Purpose    |
| ----- | -------- | ---------- |
| 22    | TCP      | SSH        |
| 80    | TCP      | HTTP       |
| 443   | TCP      | HTTPS      |
| 3000  | TCP      | Next.js    |
| 8000  | TCP      | FastAPI    |

## CI/CD Pipeline

### How it works

1. Code is merged to `main`
2. GitHub Actions workflow (`.github/workflows/deploy.yml`) triggers
3. Workflow SSHes into the EC2 instance
4. Runs: `git pull` → `docker compose up --build -d` → health check
5. Old Docker images are pruned automatically

### GitHub Secrets (already configured)

| Secret            | Description                                |
| ----------------- | ------------------------------------------ |
| `EC2_HOST`        | Public IP of the EC2 instance              |
| `SSH_PRIVATE_KEY` | SSH private key for the `ubuntu` user      |

### Manual deploy (if needed)

```bash
ssh -i infra/archimedes-deploy-key.pem ubuntu@13.40.112.220
cd /opt/archimedes
git fetch origin main
git reset --hard origin/main
docker compose up --build -d
```

## Services

### Backend API

- **URL:** http://13.40.112.220:8000
- **Docs:** http://13.40.112.220:8000/docs (Swagger UI)
- **Health:** http://13.40.112.220:8000/health

### Environment Variables

The `.env` file on the EC2 instance contains database credentials and service
URLs. To update:

```bash
ssh -i infra/archimedes-deploy-key.pem ubuntu@13.40.112.220
nano /opt/archimedes/.env
docker compose restart
```

## Terraform

Infrastructure is managed with Terraform in `infra/`.

```bash
cd infra
terraform init
terraform plan     # Preview changes
terraform apply    # Apply changes
terraform output   # Show current outputs
```

**State file** (`terraform.tfstate`) is local and gitignored. Don't lose it —
it's the only record of what Terraform manages. Ask in #infra on Discord if you
need a copy.

## Troubleshooting

```bash
# Check service status
ssh -i infra/archimedes-deploy-key.pem ubuntu@13.40.112.220
cd /opt/archimedes
docker compose ps
docker compose logs backend    # Backend logs
docker compose logs postgres   # DB logs
docker compose restart backend # Restart a service

# Rebuild from scratch
docker compose down
docker compose up --build -d
```

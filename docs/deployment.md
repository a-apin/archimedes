# Deployment — local vs production (one compose file, two topologies)

> Written 2026-06-28, alongside the Aurora/ElastiCache cutover. Resolves the
> drift between "simple `docker compose up` locally" and "how it runs in prod."

## The two topologies

| | Local dev | Production (single EC2) |
|---|---|---|
| Command | `docker compose up -d` | `docker compose up -d --force-recreate --remove-orphans` (via `deploy.yml`) |
| Data stores | **in-stack** `postgres` + `redis` containers | **managed** Aurora Serverless v2 + ElastiCache |
| Switch | `.env` has `COMPOSE_PROFILES=localdb` | `.env` omits `COMPOSE_PROFILES` |
| `DATABASE_URL` | `…@postgres:5432/…` | `…@archimedes-aurora…:5432/…?sslmode=require` |
| `REDIS_URL` | `redis://redis:6379/0` | `rediss://…elasticache…:6379/0` (TLS) |

**How one file serves both:** `postgres` and `redis` are gated behind
`profiles: ["localdb"]` in `docker-compose.yml`. The `localdb` profile is active
**only** when `COMPOSE_PROFILES=localdb` is in the `.env` (which `.env.example`
ships, so local dev is unchanged — `cp .env.example .env && docker compose up`
still brings up the full self-contained stack). Production's box-local `.env`
omits `COMPOSE_PROFILES`, so those two services never start and the app tier
(`backend`, `oracle`, `agent`, `kb-runner`, `nginx`) runs against managed
Aurora + ElastiCache via the URLs in `.env`. The app services declare
`depends_on … { required: false }` so they boot even when the local DB/cache
isn't present (prod). *(Needs docker compose ≥ 2.20 for `required:` on
`depends_on`.)*

## Why this shape

Before this change, prod ran the **base** stack (in-stack postgres/redis) even
though the app `.env` already pointed at managed Aurora/ElastiCache after the
2026-06-28 cutover — so the box **double-ran** idle docker databases (paying for
managed *and* burning EC2 RAM on unused containers). Profile-gating makes the
managed-stores intent explicit and stops the waste, without forking the compose
file or changing the local workflow.

## nginx runtime DNS re-resolution (the recreate-502 fix)

`nginx/nginx.conf` previously declared `upstream backend_api { server
backend:8000; }`. nginx resolves that hostname to an IP **once at config-load**
and caches it. When `--force-recreate` gives the `backend` container a **new**
IP, nginx keeps proxying the dead IP → **every request 502s** until nginx is
restarted (this is exactly what happened during the cutover when the backend was
recreated without nginx). The fix adds a `resolver 127.0.0.11` (docker's
embedded DNS) + `zone` + the `resolve` parameter so nginx re-resolves `backend`
at **runtime** (every 10s) — a backend recreate now self-heals. A full
`deploy.yml` run recreates *all* services (nginx after backend), so it was
already safe; this fix additionally makes **partial** recreates (operational
fixes) safe.

## Production deploy flow (`deploy.yml`)

Push to `main` → (gated on `DEPLOY_ENABLED=true`) → OIDC into AWS → SSM
`SendCommand` on the EC2: `git reset --hard origin/main` (the gitignored `.env`
is **untouched**, so the managed-store URLs persist), `docker compose build`,
`docker compose up -d --force-recreate --remove-orphans`, then seed/hydrate/AMM
bootstrap + a `/health` check.

## One-time step on the live box (after merging this change)

The live EC2 currently still has the idle `postgres`/`redis` containers running
(left intact as the cutover rollback). After this PR merges and you're confident
the managed stores are stable, reclaim the resources.

**These are two separate steps. The second command runs _inside_ the interactive
SSM session, on the EC2 — do NOT run `docker compose stop` on your laptop.**

1. Open an interactive shell on the live box (substitute the real instance id /
   region — the current values are in the `prod-infra-reality` notes / `CLAUDE.md`
   AWS section):

   ```bash
   aws ssm start-session --target <INSTANCE_ID> --region <REGION>
   ```

2. **Now at the EC2 shell prompt** (the prompt changes to the instance's
   hostname), stop the idle containers:

   ```bash
   cd /opt/archimedes && docker compose stop postgres redis   # or: docker rm -f
   ```

(The `pgdata` volume persists — data isn't deleted — but once the cutover is
trusted it's just dead weight. Keep it until then as the rollback path.)

## ⚠️ Merge note

`DEPLOY_ENABLED=true` — **merging this auto-deploys.** Merge in a controlled
window with someone watching the deploy, **not** right before a demo/relaunch.
The nginx-resolver part is low-risk; the profile-gating changes which services
prod starts — validate the `/health` 200 after deploy and have the rollback
(revert + redeploy) ready.

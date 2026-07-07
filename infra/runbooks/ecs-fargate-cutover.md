# ECS Fargate Cutover Runbook — Archimedes (issue #1039)

> **Status:** Authored 2026-07-06 (build-chunk 2/4 of #1039, `terraform-ecs`).
> **Not yet applied, not yet drilled.** Written against `infra/ecr.tf` +
> `infra/ecs.tf` on the `dbrowneup/1039-fargate-infra` epic branch. No
> `terraform apply`, ALB cutover, or EC2 decommission has happened — those are
> Dan's AWS operations (see `CLAUDE.md` § AWS account access), not anything an
> agent runs. Treat every command below as *review-then-run*.

Region: `us-east-1`. Account: `037613907429`, profile `ArchimedesDanAdmin`.

---

## Before you `terraform apply` this chunk — three blockers that must close first

This chunk (`ecr.tf` + `ecs.tf`) is structurally complete IaC, but three gaps
must close before the resulting ECS service will actually serve traffic
(each is called out in a `KNOWN GAP` comment at the top of `ecs.tf` too —
this is not new information, just gathered in one place):

1. **`nginx/nginx.conf`'s upstream is `server backend:8000 resolve;`** — a
   Docker-Compose bridge-network DNS name. ECS Fargate `awsvpc` tasks share
   one ENI across containers and talk via `localhost`, not container names.
   Fix: `server localhost:8000 resolve;` (small, but shared with the
   still-live EC2/compose path where `backend:8000` is still correct —
   needs either an environment-conditional template or to land at the same
   time the EC2 path is retired).
2. **Strategies/corpus/ABI paths are host bind mounts today**
   (`./analytics-engine/strategies`, `./data/corpus`, `./contracts/abis`,
   the `archimedes-corpus-artifact` named volume — see `docker-compose.yml`).
   Fargate has no host filesystem to bind-mount from, and
   `backend/Dockerfile`'s `COPY . /app` only copies the `backend/` build
   context — none of these are baked into the image. Needs either
   Dockerfile `COPY` additions or an EFS volume attached to the task.
3. **`DATABASE_URL` / `REDIS_URL` SSM parameters don't exist yet.** The task
   definition's `secrets` block resolves them from
   `/archimedes/prod/DATABASE_URL` and `/archimedes/prod/REDIS_URL`. Seed
   them the same way `AURORA_MASTER_PASSWORD` / `EMAIL_ENCRYPTION_KEY`
   already are:
   ```bash
   # secrets.env (NOT committed):
   #   DATABASE_URL=postgresql://archimedes:<password>@<aurora_endpoint>:5432/archimedes
   #   REDIS_URL=rediss://<redis_endpoint>:6379/0
   ./infra/scripts/seed-ssm-secrets.sh /path/to/secrets.env
   ```
   (`terraform output aurora_endpoint` / `redis_endpoint` give the host
   parts; `terraform output -raw database_url` gives the full string with
   password already substituted, if you'd rather copy it directly.)

A fourth, non-blocking gap: **oracle/agent/kb-runner** (the background
daemons) have no Fargate service in this chunk — they need their own
(singleton, no-ALB) ECS services before the EC2 instance can actually be
decommissioned (#1039 P6). Tracked as a residual, not silently dropped.

---

## What `terraform apply` actually does — read this before running it

**Applying `ecs.tf` starts live traffic cutover, not just "provisioning."**
The moment `aws_ecs_service.backend` is created, ECS begins registering each
healthy Fargate task's ENI IP directly into `archimedes-backend-tg` — the
*same* target group the EC2 instance is already statically attached to via
`aws_lb_target_group_attachment.backend` in `alb.tf`. A target group with
multiple healthy targets round-robins across all of them. In practice:

- The instant the first Fargate task passes its health check, **some live
  production requests start being served by Fargate**, alongside the EC2 box,
  automatically, with no further action.
- This is actually a useful, low-effort blue/green mechanism (a natural
  canary) — but only if the three blockers above are closed. If they aren't,
  Fargate tasks will register unhealthy (or never reach RUNNING) and the ALB
  simply keeps sending everything to the still-healthy EC2 target — i.e. the
  failure mode is "no-op," not "outage," as long as `deployment_circuit_breaker`
  and the ALB health check are both in place (they are, in `ecs.tf`/`alb.tf`).

### Recommended apply sequence

```bash
export AWS_PROFILE=ArchimedesDanAdmin AWS_REGION=us-east-1
export TF_VAR_aurora_master_password="$(aws ssm get-parameter \
  --name /archimedes/prod/AURORA_MASTER_PASSWORD --with-decryption \
  --query Parameter.Value --output text)"

cd infra/
terraform plan   # scrutinize: should show ONLY new resources (ecr.tf, ecs.tf,
                  # the alb.tf security-group egress addition) — zero destroys,
                  # zero changes to aws_lb.main / aws_lb_target_group.backend /
                  # aws_rds_cluster.main / aws_elasticache_replication_group.main.
terraform apply
```

1. **Seed the SSM secrets** (blocker 3 above) — the service will otherwise
   sit in a launch-failure loop.
2. **Land the nginx.conf + Dockerfile fixes** (blockers 1–2) and push a fresh
   image through CI (build-chunk 1's pipeline) before or immediately after
   apply — until then, tasks may reach RUNNING but never pass the ALB health
   check, which is a safe (if noisy) failure mode per above.
3. `terraform apply`. Expect: ECR repos created (empty — nothing to pull yet
   if step 2 hasn't landed a working image), ECS cluster/service/task
   definition created, task(s) attempt to launch.
4. **Verify before trusting:**
   ```bash
   # Service + task status
   aws ecs describe-services --cluster "$(terraform output -raw ecs_cluster_name)" \
     --services "$(terraform output -raw ecs_service_name)" \
     --query 'services[0].{status:status,running:runningCount,desired:desiredCount,events:events[0:3]}'

   # Target health — are Fargate task IPs showing healthy in the SAME target group?
   aws elbv2 describe-target-health \
     --target-group-arn "$(aws elbv2 describe-target-groups \
        --names archimedes-backend-tg --query 'TargetGroups[0].TargetGroupArn' --output text)"

   # ECS Exec — shell into a running task (acceptance criterion #4)
   TASK_ID=$(aws ecs list-tasks --cluster "$(terraform output -raw ecs_cluster_name)" \
     --service-name "$(terraform output -raw ecs_service_name)" --query 'taskArns[0]' --output text)
   aws ecs execute-command --cluster "$(terraform output -raw ecs_cluster_name)" \
     --task "$TASK_ID" --container backend --interactive --command "/bin/sh"

   # Smoke test through the ALB directly (bypasses CloudFront/DNS)
   curl -sS -H "Host: archimedes-arc.com" "https://$(terraform output -raw alb_dns_name)/health"
   ```
5. **Exercise the rollback acceptance criterion once, deliberately:** deploy a
   deliberately-broken task-definition revision (e.g. a bad image tag) and
   confirm `deployment_circuit_breaker` auto-rolls-back without human action:
   ```bash
   aws ecs describe-services --cluster <cluster> --services <service> \
     --query 'services[0].deployments'
   ```

### Completing the cutover (P6 — fully Dan's call, not automated anywhere)

Once Fargate is verified healthy and carrying real traffic for a soak period:

1. Remove (or comment out, then `terraform apply`) `alb.tf`'s
   `aws_lb_target_group_attachment.backend` — this is the ONE line that
   detaches the EC2 instance from `archimedes-backend-tg`; ECS's own
   registrations are untouched by this change (they're managed by the ECS
   service, not by that Terraform resource).
2. Confirm 100% of target-group traffic is Fargate-only
   (`describe-target-health` shows only `ip`-type Fargate targets).
3. Retire `deploy.yml`'s SSM box-pull path (the build-chunk-1 interim) —
   replace with the CI step that registers a new task-definition revision +
   calls `aws ecs update-service --force-new-deployment` (uses the
   `archimedes-ecs-deploy` IAM policy this chunk already attached to
   `archimedes-github-deploy`).
4. Terminate `aws_instance.archimedes` (remove from `main.tf`, `terraform
   apply`) — only after step 2 is confirmed for at least one full deploy
   cycle. Aurora/ElastiCache/ALB/WAF/CloudFront/Route 53 are all unaffected;
   this chunk never touched them.

---

## Rollback (bad Fargate deploy, EC2 still attached)

Because the EC2 target stays attached to `archimedes-backend-tg` throughout
steps 1–4 above, the cheapest rollback during the cutover window is simply:
set `ecs_service_desired_count` (or the running service's `DesiredCount`) to
`0` — the ALB keeps serving 100% of traffic from the still-healthy EC2
target with no DNS change, no ALB reconfiguration, nothing else to do.

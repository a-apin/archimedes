# ECS Fargate Cutover Runbook — Archimedes (issue #1039)

> **Status:** Authored 2026-07-06 (build-chunk 2/4 of #1039, `terraform-ecs`);
> updated 2026-07-07 (build-chunk 3/4, `migrate-config` — AURORA_MASTER_PASSWORD
> / EMAIL_ENCRYPTION_KEY wired as ECS secrets, PLATFORM_ADMIN_WALLETS /
> ARCHIMEDES_TREASURY_WALLET wired as ECS env, and the Alembic pre-rollout
> migrate stage added to `.github/workflows/deploy.yml`); reorganized
> 2026-07-07 (build-chunk 4/4, `docs-runbook` — the full ordered
> apply → seed → cutover → verify → decommission sequence below, with explicit
> zero-downtime and self-heal verification drills that were previously
> implied but not spelled out); updated again 2026-07-07 (build-chunk 5,
> `infra-hardening` — PR #1041 review comprehensiveness pass: B2 static
> migrate network config splits Phase 1's Stage 2 into 2a/2b with a mandatory
> migrate-then-verify gate between them; B3 mandatory baseline-stamp
> pre-flight; N1 NAT auto-recovery alarm; N2 dead-egress RPC timeout +
> chain_connected alarm; N3 NAT AMI-drift guard; a free S3 Gateway endpoint;
> C1 CI now redeploys Fargate itself; C2 corrects the rollback doc; C3 guards
> against a concurrent SSM deploy race; R1 reorders Phase 8's decommission
> gate and adds a NAT-outage scenario to the disaster-recovery runbook).
> **Not yet applied, not yet drilled.** Written against `infra/ecr.tf` +
> `infra/ecs.tf` on the `dbrowneup/1039-fargate-infra` epic branch. No
> `terraform apply`, ALB cutover, or EC2 decommission has happened — those are
> Dan's AWS operations (see `CLAUDE.md` § AWS account access), not anything an
> agent runs. **Treat every command below as review-then-run, in order** —
> each phase assumes the previous one is done and verified.

Region: `us-east-1`. Account: `037613907429`, profile `ArchimedesDanAdmin`.

**The acceptance criteria this runbook exists to satisfy (issue #1039):** no
`docker build` on a serving host; zero ALB 5xx during a rolling deploy; a
killed task self-heals in <2 min with no human action; `aws ecs
execute-command` opens a shell and logs survive in CloudWatch; `alembic
upgrade head` runs pre-rollout; rollback = redeploy the prior image tag;
total spend stays within ±10% of ~$178/mo. Phases 5–7 below are the drills
that produce the evidence for criteria 2–4; do them once, deliberately,
before trusting the cutover.

---

## Phase 0 — Before you `terraform apply` this chunk: three blockers

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
   `AURORA_MASTER_PASSWORD` / `EMAIL_ENCRYPTION_KEY` themselves need **no**
   seeding step — build-chunk 3 (`migrate-config`) wired them as ECS
   `secrets` too, and both already exist live in SSM
   (`infra/scripts/setup-ssm-secrets.sh`, predating this epic).

Two more gaps are worth knowing about but do **not** block a launch (no seeding,
no code fix required for the service to come up healthy):

- **`platform_admin_wallets` / `archimedes_treasury_wallet` Terraform
  variables default to empty.** Build-chunk 3 wired `PLATFORM_ADMIN_WALLETS`
  / `ARCHIMEDES_TREASURY_WALLET` as plain (non-secret) ECS task-definition
  env, sourced from these two new `variables.tf` entries — set them before
  apply if you want the admin-wallet publish bypass / marketplace publish
  live on Fargate from day one:
  ```bash
  export TF_VAR_platform_admin_wallets="0xabc...,0xdef..."
  export TF_VAR_archimedes_treasury_wallet="0x123..."
  ```
  Left unset, both features stay off (matching `.env.example`'s own empty
  `ARCHIMEDES_TREASURY_WALLET` default) — a safe no-op, not a launch failure.
- **oracle/agent/kb-runner** (the background daemons) have no Fargate service
  in this chunk — they need their own (singleton, no-ALB) ECS services
  before the EC2 instance can actually be decommissioned (Phase 8 / #1039
  P6). Tracked as a residual, not silently dropped.

### Alembic pre-rollout migrate stage (#1039 P4, build-chunk 3; B2/B3 hardening, build-chunk 5)

`.github/workflows/deploy.yml` gained a `migrate` job (`needs: build-and-push`;
`deploy` now `needs: [build-and-push, migrate]`) that runs a one-off `aws ecs
run-task` against the **dedicated** migrate task definition
(`infra/ecs_migrate.tf`'s `aws_ecs_task_definition.migrate`, family
`archimedes-migrate`) — a single container, no nginx sidecar, no
healthCheck/dependsOn — but **only if `backend/alembic.ini` exists on the
checked-out ref**. (Originally this targeted the SERVICE family,
`archimedes-backend`, which bundles an nginx sidecar that `dependsOn` the
backend container being `HEALTHY`; since a migrate run overrides the backend
command away from serving HTTP, `/health` never resolves and ECS would kill
the task before Alembic finished — split out into its own family per the
PR #1041 Copilot review.) As of this PR, #1028 Phase A hasn't landed Alembic
yet (`backend/migrations/` is still hand-rolled timestamped `.sql` +
`archimedes.db.init_db()`'s idempotent `create_all` / `ADD COLUMN IF NOT
EXISTS` patches), so the job detects that and no-ops loudly (a clear log
line, exit 0) rather than either failing the pipeline on a stage with
nothing to do yet, or silently pretending to run a migration that doesn't
exist. The moment `backend/alembic.ini` lands on this branch, the step
activates itself — no further pipeline change needed.

**B2 — static network configuration, not `describe-services`.** The job
previously borrowed the LIVE ECS **service's** own `networkConfiguration` via
`aws ecs describe-services archimedes-backend`. That's a chicken-and-egg bug:
`describe-services` can only succeed once `aws_ecs_service.backend` already
exists, but the whole point of the migrate task is to run **before** that
service is created (a bare `terraform apply` creating the service also
instantly launches an un-migrated task from it). Fixed: the job now uses a
STATIC `ECS_MIGRATE_NETWORK_CONFIGURATION` literal, sourced from
`terraform output -raw ecs_migrate_network_configuration` (built from
`aws_subnet.private` + `aws_security_group.ecs_backend`, both of which exist
independently of the service) — same literal-constant pattern as
`ECS_CLUSTER` / `ECS_MIGRATE_TASK_FAMILY`. **This is why Phase 1 below applies
the cluster/task-defs/security-group in their own stage (2a) BEFORE the
service (2b), and requires a green migrate run in between.**

**B3 — MANDATORY: auto-stamp the already-populated prod DB.** Prod Aurora has
**no `alembic_version` table today** — its schema was built by
`init_db()`'s `create_all()` + hand-rolled `ADD COLUMN IF NOT EXISTS`
patches, never by Alembic. A bare `alembic upgrade head` against that
database fails immediately: it tries to `CREATE TABLE backtest_results` (the
baseline revision's first statement) against a table that already exists.
The migrate task's command is therefore **not** `python -m alembic upgrade
head` — it is `python -m archimedes.scripts.alembic_migrate_preflight`
(`backend/archimedes/scripts/alembic_migrate_preflight.py`), which:
1. Checks whether `alembic_version` is absent **and** `backtest_results`
   (the oldest pre-Alembic table) already exists.
2. If so, runs `alembic stamp af9c6a9376e4` (the baseline revision — see
   `backend/migrations/versions/af9c6a9376e4_baseline_schema.py`) first.
3. Then runs `alembic upgrade head` unconditionally.

This is idempotent — after the first successful run, `alembic_version`
exists, so every later run skips straight to step 3. **Do not run a bare
`alembic upgrade head` against prod by hand outside this task** — it will
fail on the baseline the same way. `backend/tests/scripts/
test_alembic_migrate_preflight.py` covers the decision logic hermetically
(sqlite, no Postgres/alembic needed) plus a full Postgres integration test
(`@pytest.mark.integration` — create_all() → stamp → upgrade head → assert
success) that self-activates once `backend/alembic.ini` lands.

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
  canary) — but only if the Phase 0 blockers above are closed. If they
  aren't, Fargate tasks will register unhealthy (or never reach RUNNING) and
  the ALB simply keeps sending everything to the still-healthy EC2 target —
  i.e. the failure mode is "no-op," not "outage," as long as
  `deployment_circuit_breaker` and the ALB health check are both in place
  (they are, in `ecs.tf`/`alb.tf`).

---

## Phase 1 — Terraform apply order

Apply in **three stages**, not one big-bang `terraform apply` for the whole
chunk — **1, 2a, 2b, in that order, with a hard gate between 2a and 2b.**
Two independent reasons force the staging:

- `aws_ecs_service.backend` will try to launch tasks the moment it's created,
  and a task with nothing to pull from ECR fails to start (not fatal —
  `deployment_circuit_breaker` + the still-healthy EC2 target mean this is a
  no-op, not an outage — but it's noisy and wastes a launch cycle). Staging
  means the ECS service's first-ever launch attempt already has a real image
  to pull.
- **MANDATORY (issue #1039 B2/B3): the Alembic migrate task must run to
  exit 0, with `alembic current` verified `>=` the baseline revision
  (`af9c6a9376e4`), BEFORE the `terraform apply` that creates
  `aws_ecs_service.backend`.** Once the service exists, it immediately
  launches tasks against whatever schema state the database happens to be
  in — if that apply runs before a real migrate pass, the very first Fargate
  task can boot against a stale or (on prod Aurora specifically) an
  Alembic-untracked schema. `aws_ecs_service.backend` and the migrate task
  definition both live in what was previously a single "Stage 2" apply; it
  is now split into **Stage 2a** (everything the migrate task needs — cluster,
  task definitions, the `ecs_backend` security group, the CI deploy role's
  ECS permissions — but explicitly NOT the service) and **Stage 2b** (the
  service + autoscaling), with the migrate run + verification as the gate
  between them.

```bash
aws sso login --profile ArchimedesDanAdmin
export AWS_PROFILE=ArchimedesDanAdmin AWS_REGION=us-east-1
export TF_VAR_aurora_master_password="$(aws ssm get-parameter \
  --name /archimedes/prod/AURORA_MASTER_PASSWORD --with-decryption \
  --query Parameter.Value --output text)"
aws sts get-caller-identity   # smoke-test — confirms account 037613907429

cd infra/
terraform init
terraform plan   # scrutinize: should show ONLY new resources (ecr.tf, ecs.tf,
                  # ecs_migrate.tf, the alb.tf security-group egress addition)
                  # — zero destroys, zero changes to aws_lb.main /
                  # aws_lb_target_group.backend / aws_rds_cluster.main /
                  # aws_elasticache_replication_group.main.
```

**Stage 1 — ECR only** (repos + lifecycle policies; nothing that launches a task):

```bash
terraform apply -target=aws_ecr_repository.backend -target=aws_ecr_repository.nginx \
                 -target=aws_ecr_lifecycle_policy.backend -target=aws_ecr_lifecycle_policy.nginx
```

Then go to **Phase 2** and seed a real image before continuing — do not skip
ahead to Stage 2a with empty repos.

**Stage 2a — everything the migrate task needs, but NOT the service** (IAM
roles/policies, the ECS cluster, both task definitions, the `ecs_backend`
security group) — only after Phase 2 has pushed at least one image:

```bash
terraform plan    # re-diff — should show ecs.tf/ecs_migrate.tf/ec2_iam.tf-
                   # adjacent resources; ECR resources already applied read
                   # as unchanged
terraform apply \
  -target=aws_ecs_cluster.main \
  -target=aws_ecs_task_definition.backend \
  -target=aws_ecs_task_definition.migrate \
  -target=aws_security_group.ecs_backend \
  -target=aws_iam_role_policy.github_deploy_ecs
```

1. **Seed the SSM secrets** (Phase 0, blocker 3) if you haven't yet — the
   migrate task (and later, the service) will otherwise sit in a
   launch-failure loop on `DATABASE_URL`/`REDIS_URL` secret resolution.
2. **Land the nginx.conf + Dockerfile fixes** (Phase 0, blockers 1–2) before
   or immediately after this stage — they only affect the SERVICE containers
   (nginx + backend serving HTTP), not the single-container migrate task, so
   they don't block the migrate gate below, but land them before Stage 2b.
3. `terraform apply` (the `-target` list above). Expect: ECS cluster + both
   task definitions + `ecs_backend` security group created — **no service,
   no tasks launched yet.**
4. `terraform output -raw ecs_migrate_network_configuration` — copy the
   result into `.github/workflows/deploy.yml`'s `ECS_MIGRATE_NETWORK_CONFIGURATION`
   literal (replacing the `REPLACE_WITH_*` placeholders), commit, and push
   (or merge via PR — either way this needs a real commit; the migrate job
   fails closed with a clear `::error::` while the placeholders are still
   there).

**GATE — run the migrate task and verify BEFORE Stage 2b:**

```bash
# Preferred: let CI run it for real (needs backend/alembic.ini to already be
# on `main` — issue #1028 — and DEPLOY_ENABLED=true):
gh workflow run deploy.yml --ref main
gh run watch   # follow the `migrate` job specifically; it must exit 0

# Verify `alembic current` reports a revision — not just "the job exited 0"
# (a no-op skip due to a missing alembic.ini would also exit 0, silently
# proving nothing). Run a second one-off task overriding the command:
aws ecs run-task \
  --cluster archimedes-cluster \
  --task-definition archimedes-migrate \
  --launch-type FARGATE \
  --network-configuration "$(terraform output -raw ecs_migrate_network_configuration)" \
  --overrides '{"containerOverrides":[{"name":"migrate","command":["python","-m","alembic","current"]}]}' \
  --query 'tasks[0].taskArn' --output text
# then: aws ecs wait tasks-stopped --cluster archimedes-cluster --tasks <arn>
# then check CloudWatch Logs (/archimedes/app, ecs-migrate-* stream, this
# task's own stream) for the printed revision — it must be present (not
# empty) and must be the real Alembic head, not merely "some revision".
```

Do **not** proceed to Stage 2b until: the migrate job/task exited 0, AND the
`alembic current` check above shows a real, non-empty head revision. If
either check fails, stop — do not create the service against a schema you
haven't confirmed is current.

**Stage 2b — the service + autoscaling** (only after the gate above passes):

```bash
terraform plan    # re-diff — should now show ONLY aws_ecs_service.backend,
                   # aws_appautoscaling_target.backend,
                   # aws_appautoscaling_policy.backend_cpu, and the one
                   # alb.tf SG egress rule; everything from Stage 2a reads as
                   # unchanged
terraform apply
```

Expect: ECS service + autoscaling created, task(s) attempt to launch and pull
the image Phase 2 already seeded — now against a database whose schema is
already confirmed current.

---

## Phase 2 — Seed ECR with the first image

The ECS service (Stage 2b above) needs a real, boot-validated image sitting
in `archimedes-backend` / `archimedes-nginx` **before** it launches its first
task, or that first launch attempt is guaranteed to fail (empty repo → pull
error → circuit breaker trips → task never reaches steady state). Do this
right after Stage 1 (ECR-only apply) and before Stage 2a.

**Preferred path — let CI do it** (same `build-and-push` job build-chunk 1
wired; already boot-validates both images with a live `/health` curl before
pushing, so this seeds ECR with an image that's been smoke-tested, not just
built):

```bash
# Confirm the gate is on (should already be `true` — this is the live deploy
# mechanism today, see .github/workflows/deploy.yml's header comment):
gh variable list | grep DEPLOY_ENABLED

# Trigger the same pipeline that already runs on every push to main, without
# waiting for a real commit:
gh workflow run deploy.yml --ref main
gh run watch   # follow build-and-push; it fails closed with an explicit
               # ::error:: if the ECR repos it's pushing to don't exist yet
               # (i.e. if you run this before Stage 1's ECR apply)
```

This pushes both `archimedes-backend:<commit-sha>`, `archimedes-backend:latest`,
`archimedes-nginx:<commit-sha>`, and `archimedes-nginx:latest`. The `migrate`
and `deploy` jobs also run (gated on the same `DEPLOY_ENABLED` flag) after
`build-and-push` — `build-and-push` itself has already succeeded and pushed
the images by the time either of those runs, so this step's actual goal (seed
ECR) is achieved regardless of what happens next:
- If `backend/alembic.ini` isn't on `main` yet, `migrate` no-ops loudly and
  exits 0, same as always.
- If it IS on `main` but Stage 2a hasn't been applied yet (this phase, by
  definition, runs before Stage 2a), `migrate` now **fails closed** with a
  clear `::error::` (the `ECS_MIGRATE_NETWORK_CONFIGURATION` literal still
  has its `REPLACE_WITH_*` placeholders, or the migrate task definition
  doesn't exist yet) rather than silently passing — expected and fine at this
  point in the sequence, not a real incident.
- `deploy` only touches the still-untouched EC2 box regardless, so it's
  always safe to run this trigger before Stage 2a — the worst case is a red
  Actions run for a step that had nothing to verify yet, not any live-traffic
  effect.

**Fallback — push manually** (only if CI is down or you need an image before
merging to `main`; skips CI's own boot-validation, so treat this as a
break-glass path, not the normal one):

```bash
export AWS_PROFILE=ArchimedesDanAdmin AWS_REGION=us-east-1
ECR_REGISTRY=037613907429.dkr.ecr.us-east-1.amazonaws.com
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin "$ECR_REGISTRY"

docker build -t archimedes-backend:manual -f backend/Dockerfile backend
docker tag archimedes-backend:manual "$ECR_REGISTRY/archimedes-backend:latest"
docker push "$ECR_REGISTRY/archimedes-backend:latest"

docker build -t archimedes-nginx:manual -f nginx/Dockerfile .
docker tag archimedes-nginx:manual "$ECR_REGISTRY/archimedes-nginx:latest"
docker push "$ECR_REGISTRY/archimedes-nginx:latest"
```

**Verify the seed landed** before moving to Stage 2a:

```bash
aws ecr describe-images --repository-name archimedes-backend \
  --query 'imageDetails[*].{tags:imageTags,pushed:imagePushedAt}' --output table
aws ecr describe-images --repository-name archimedes-nginx \
  --query 'imageDetails[*].{tags:imageTags,pushed:imagePushedAt}' --output table
```

---

## Phase 3 — Verify the ECS service comes up healthy

After Stage 2b's apply (which only happens after the migrate gate above passes):

```bash
# Service + task status
aws ecs describe-services --cluster "$(terraform output -raw ecs_cluster_name)" \
  --services "$(terraform output -raw ecs_service_name)" \
  --query 'services[0].{status:status,running:runningCount,desired:desiredCount,events:events[0:3]}'

# Target health — are Fargate task IPs showing healthy in the SAME target group
# the EC2 instance is attached to?
aws elbv2 describe-target-health \
  --target-group-arn "$(aws elbv2 describe-target-groups \
     --names archimedes-backend-tg --query 'TargetGroups[0].TargetGroupArn' --output text)"

# Smoke test through the ALB directly (bypasses CloudFront/DNS)
curl -sS -H "Host: archimedes-arc.com" "https://$(terraform output -raw alb_dns_name)/health"
```

Do not proceed to Phase 4 until `runningCount == desiredCount` and the
`describe-target-health` output shows the Fargate (`ip`-type) targets as
`healthy` — not just the pre-existing EC2 target.

---

## Phase 4 — Swing the ALB target from EC2 to the ECS service

This is the cutover itself — fully Dan's call, not automated anywhere. Once
Fargate is verified healthy (Phase 3) and has carried real blended traffic
(alongside the EC2 target) for a soak period Dan is comfortable with:

1. Remove (or comment out, then `terraform apply`) `alb.tf`'s
   `aws_lb_target_group_attachment.backend` — this is the ONE line that
   detaches the EC2 instance from `archimedes-backend-tg`; ECS's own
   registrations are untouched by this change (they're managed by the ECS
   service, not by that Terraform resource).
   ```bash
   terraform plan   # should show exactly ONE resource destroyed:
                     # aws_lb_target_group_attachment.backend — nothing else
   terraform apply
   ```
2. Confirm 100% of target-group traffic is Fargate-only:
   ```bash
   aws elbv2 describe-target-health \
     --target-group-arn "$(aws elbv2 describe-target-groups \
        --names archimedes-backend-tg --query 'TargetGroups[0].TargetGroupArn' --output text)"
   # expect: only `ip`-type (Fargate ENI) targets, zero `instance`-type entries
   ```
3. Retire `deploy.yml`'s SSM box-pull path (the build-chunk-1 interim) —
   replace with a CI step that registers a new task-definition revision +
   calls `aws ecs update-service --force-new-deployment` (uses the
   `archimedes-ecs-deploy` IAM policy this chunk already attached to
   `archimedes-github-deploy`).
4. Only after step 2 is confirmed for at least one full deploy cycle,
   proceed to Phase 8 (EC2 decommission) — don't terminate the instance in
   the same sitting as the ALB swing; let it sit idle-but-attached-to-nothing
   for one deploy cycle as a zero-cost rollback path (see "Rollback" below).

---

## Phase 5 — Verify zero-downtime during a rolling deploy

Acceptance criterion #2: **zero ALB 5xx during a rollout.** Exercise it
deliberately, don't just assume the `deployment_minimum_healthy_percent =
100` / `deployment_maximum_percent = 200` config (in `ecs.tf`) does the right
thing — measure it.

```bash
# Terminal 1 — continuous request loop against the ALB directly (bypasses
# CloudFront caching so you're actually hitting the live target group), left
# running for the whole rollout:
while true; do
  curl -o /dev/null -s -w "%{http_code} %{time_total}s\n" \
    -H "Host: archimedes-arc.com" "https://$(terraform output -raw alb_dns_name)/health"
  sleep 1
done | tee /tmp/rollout-watch.log

# Terminal 2 — trigger a rolling deployment (a force-new-deployment against
# the current task definition is enough to exercise the rolling-replace path
# without needing a new image):
aws ecs update-service \
  --cluster "$(terraform output -raw ecs_cluster_name)" \
  --service "$(terraform output -raw ecs_service_name)" \
  --force-new-deployment

# Watch the deployment reach steady state:
aws ecs wait services-stable \
  --cluster "$(terraform output -raw ecs_cluster_name)" \
  --services "$(terraform output -raw ecs_service_name)"
```

**Verify:** `grep -v '^200 ' /tmp/rollout-watch.log` returns nothing (every
line was `200 ...`) for the whole rollout window. Any `5xx` or connection
error in that log is a real regression against acceptance criterion #2 — do
not treat it as flaky and move on.

---

## Phase 6 — Verify self-heal (kill a task, time the recovery)

Acceptance criterion #3: **killing a task → ECS replaces it healthy in <2
min, no human action.**

```bash
CLUSTER="$(terraform output -raw ecs_cluster_name)"
SERVICE="$(terraform output -raw ecs_service_name)"

TASK_ID=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" \
  --query 'taskArns[0]' --output text)
echo "Killing $TASK_ID at $(date -u +%H:%M:%S)"

aws ecs stop-task --cluster "$CLUSTER" --task "$TASK_ID" \
  --reason "manual self-heal drill (#1039 acceptance criterion 3)"

# The service's own desiredCount is the target to watch back up to — read it
# fresh rather than assuming it (autoscaling may have moved it since apply).
DESIRED=$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[0].desiredCount' --output text)

# Poll until a NEW task (different ARN) is RUNNING and healthy — this is the
# measurement, not `aws ecs wait services-stable` alone (which can report
# stable on the surviving task before the replacement is actually healthy
# in the target group).
for i in $(seq 1 24); do
  RUNNING_TASKS=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" \
    --desired-status RUNNING --query 'length(taskArns)' --output text)
  if [ "$RUNNING_TASKS" -ge "$DESIRED" ]; then
    echo "Back to desired RUNNING count ($DESIRED) at $(date -u +%H:%M:%S) (attempt $i)"
    break
  fi
  sleep 5
done

# Confirm the replacement is healthy in the target group too, not just RUNNING:
aws elbv2 describe-target-health \
  --target-group-arn "$(aws elbv2 describe-target-groups \
     --names archimedes-backend-tg --query 'TargetGroups[0].TargetGroupArn' --output text)"
```

**Verify:** the elapsed time between the `stop-task` timestamp and the
replacement task reaching `RUNNING` + `healthy` is under 2 minutes. If it
isn't, check `health_check_grace_period_seconds` (90s in `ecs.tf`) and the
ALB target group's own health-check interval/threshold in `alb.tf` before
concluding the criterion is unmet — both feed the total.

---

## Phase 7 — Verify ECS Exec (acceptance criterion #4)

```bash
CLUSTER="$(terraform output -raw ecs_cluster_name)"
SERVICE="$(terraform output -raw ecs_service_name)"
TASK_ID=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" \
  --query 'taskArns[0]' --output text)

aws ecs execute-command --cluster "$CLUSTER" --task "$TASK_ID" \
  --container backend --interactive --command "/bin/sh"
```

Once inside, confirm logs are actually landing in CloudWatch (not just the
ECS Exec session I/O):

```bash
aws logs tail /archimedes/app --since 10m --follow
aws logs tail /archimedes/nginx --since 10m --follow
```

**Verify:** the shell opens, and a redeploy (Phase 5's `force-new-deployment`,
or a normal CI deploy) does not clear the log group — old tasks' log streams
persist alongside the new task's stream under the same group, satisfying
"logs survive a redeploy."

**Exercise the rollback acceptance criterion once, deliberately** while
you're in this verification pass: deploy a deliberately-broken task
definition revision (e.g. a bad image tag) and confirm
`deployment_circuit_breaker` auto-rolls-back without human action:

```bash
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[0].deployments'
```

---

## Phase 8 — Decommission the EC2 app instance

Only after Phase 4 is confirmed for at least one full deploy cycle with
Fargate carrying 100% of traffic:

1. Terminate `aws_instance.archimedes` (remove from `main.tf`, `terraform
   apply`). Aurora/ElastiCache/ALB/WAF/CloudFront/Route 53 are all
   unaffected; this chunk never touched them.
2. Before terminating, confirm the **oracle/agent/kb-runner** background
   daemons (Phase 0's residual — no Fargate home in this chunk) have
   somewhere to run. Decommissioning the EC2 instance while those still only
   exist as `docker compose` services on that box silently kills the
   vault/regime-state source of truth — this is the one step in this runbook
   that is NOT just "detach and delete," it needs its own Fargate
   service(s) first (singleton, no ALB, `desired_count = 1`, no
   autoscaling — same anti-goal as the EC2 ASG tier: these loops must not be
   duplicated).
3. Remove the now-dead `EC2_INSTANCE_ID` / SSM-deploy path from `deploy.yml`
   entirely (Phase 4 step 3 already replaced its function; this step is
   just deleting the now-unreachable code, not changing behavior).

---

## Rollback (bad Fargate deploy, EC2 still attached — Phases 1–4 window only)

Because the EC2 target stays attached to `archimedes-backend-tg` throughout
Phases 1–4, the cheapest rollback during the cutover window is simply: set
`ecs_service_desired_count` (or the running service's `DesiredCount`) to `0`
— the ALB keeps serving 100% of traffic from the still-healthy EC2 target
with no DNS change, no ALB reconfiguration, nothing else to do.

**After Phase 4 (EC2 detached), rollback is the standard ECS rollback**: the
`deployment_circuit_breaker` (Phase 7) already does this automatically for a
bad deployment; for a bad deploy that somehow reached steady state anyway,
redeploy the prior task-definition revision:

```bash
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
  --task-definition "$(terraform output -raw ecs_task_definition_family):<prior-revision-number>"
```

# Archimedes Infrastructure

## Local CLI tooling

The infra workflow needs three command-line tools. Two come from the `archimedes`
conda env (pinned in [`../environment.yml`](../environment.yml), so `conda env create`
gives everyone the same versions); the third is a Homebrew install because it is not
packaged on conda-forge.

| Tool | Version (verified) | How to install | Why |
| --- | --- | --- | --- |
| **Terraform** | 1.15.3 | conda env (`terraform>=1.10`) | IaC for the whole AWS stack (`infra/*.tf`). 1.10+ required for S3-native state locking (`use_lockfile=true`). |
| **AWS CLI v2** | 2.34.48 | conda env (`awscli>=2.15`) | Deploys, SSM sessions, and **`aws configure sso`** for IAM Identity Center. **v2 is required** — v1's SSO support is insufficient for the Identity Center login flow. |
| **AWS SAM CLI** | 1.162.1 | **Homebrew**: `brew install aws-sam-cli` (not on conda-forge) | Serverless build/deploy + `sam local` testing. Only needed if/when we add Lambda pieces — see note below. |

Run env-scoped tools with `conda run -n archimedes <cmd>` (or `conda activate archimedes`
first) so you are always on the pinned versions, not whatever is on the system PATH.

**On SAM's role:** the production stack is **ECS Fargate + Aurora + ALB, managed by
Terraform** (cut over from EC2 2026-07-09) — Terraform is the IaC backbone and SAM does
not replace it. SAM is
purpose-built for **Lambda / API Gateway serverless** apps and local Lambda emulation.
Treat it as *additive*: reach for it only when we introduce a concrete Lambda use-case
(e.g. an event-driven nanopayment-settlement hook, a scheduled job, or lightweight glue),
not as a second way to manage the core web tier. Until then it is installed-but-unused.

## Terraform State Backend (S3)

State is stored remotely in S3 with S3-native locking (Terraform 1.10+,
`use_lockfile = true`). The S3 bucket was created out-of-band via AWS
CLI (infrastructure-of-infrastructure — never changes, don't manage
with Terraform). No DynamoDB table needed.

### Bootstrap Commands (run once, already done)

```bash
# S3 bucket — versioned, encrypted, no public access
aws s3api create-bucket \
  --bucket archimedes-tfstate-037613907429 \
  --region us-east-1

aws s3api put-bucket-versioning \
  --bucket archimedes-tfstate-037613907429 \
  --versioning-configuration Status=Enabled

aws s3api put-bucket-encryption \
  --bucket archimedes-tfstate-037613907429 \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

aws s3api put-public-access-block \
  --bucket archimedes-tfstate-037613907429 \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# Bucket policy: deny non-TLS + restrict to account only
aws s3api put-bucket-policy \
  --bucket archimedes-tfstate-037613907429 \
  --policy '{
    "Version": "2012-10-17",
    "Statement": [
      {"Sid":"DenyNonTLS","Effect":"Deny","Principal":"*","Action":"s3:*",
       "Resource":["arn:aws:s3:::archimedes-tfstate-037613907429","arn:aws:s3:::archimedes-tfstate-037613907429/*"],
       "Condition":{"Bool":{"aws:SecureTransport":"false"}}},
      {"Sid":"RestrictToAccount","Effect":"Deny","Principal":"*","Action":"s3:*",
       "Resource":["arn:aws:s3:::archimedes-tfstate-037613907429","arn:aws:s3:::archimedes-tfstate-037613907429/*"],
       "Condition":{"StringNotEquals":{"aws:PrincipalAccount":"037613907429"}}}
    ]}'
```

### Working with Terraform

**Prereqs (every session):** the S3 backend + provider use your SSO credentials,
so Terraform needs `AWS_PROFILE` exported — without it, it falls through to
EC2-instance-metadata and dies with *"No valid credential sources found / no EC2
IMDS role found."*

```bash
aws sso login --profile ArchimedesDanAdmin           # refresh the SSO session
export AWS_PROFILE=ArchimedesDanAdmin AWS_REGION=us-east-1
aws sts get-caller-identity                          # smoke-test (account 037613907429)
```

**The Aurora master password** is required by the config (`var.aurora_master_password`).
It lives in SSM as a SecureString — pipe it straight into the TF var so it never
prints to screen or shell history:

```bash
export TF_VAR_aurora_master_password="$(aws ssm get-parameter \
  --name /archimedes/prod/AURORA_MASTER_PASSWORD \
  --with-decryption --query Parameter.Value --output text)"
```

**The core loop:**

```bash
cd infra/                # the LIVE stack (NOT infra/terraform/ — that's a separate, unwired module)
terraform init           # downloads providers, connects to the S3 backend
terraform plan           # ALWAYS preview first — scrutinize for `destroy` / route deletions
terraform apply          # type `yes` to confirm
```

**If a plan/apply was interrupted (Ctrl-C), the S3 state lock can go stale** —
you'll see `Error acquiring the state lock … PreconditionFailed`. Clear it (only
when you're sure no other terraform is actually running):

```bash
terraform force-unlock <LOCK_ID>     # the ID is printed in the error
```

**Adopting a live-but-unmanaged resource (drift) — import, don't recreate.** If a
resource exists in AWS but Terraform wants to *create* it (→ would error
`…AlreadyExists`), import it into state first. Example (the private-subnet NAT routes):

```bash
terraform import 'aws_route.private_nat[0]' rtb-<id>_0.0.0.0/0
terraform import 'aws_route.private_nat[1]' rtb-<id>_0.0.0.0/0
```

> **Route-table gotcha (load-bearing).** Terraform forbids mixing an **inline
> `route {}` block** on `aws_route_table` with **standalone `aws_route` resources**
> on the same table — the inline block claims the whole route set and will silently
> DELETE routes managed by `aws_route` (this once tried to delete the VPC-peering
> return route and would have severed app↔Aurora/Redis — see PR #836). The private
> route tables therefore use **all standalone `aws_route`** (NAT + peering); do not
> add inline `route {}` blocks to them.

> **`user_data` edits reboot the box.** Changing the EC2 `user_data` (e.g. editing
> `user-data.sh`) makes Terraform **stop/start** the instance (AWS can't modify
> user_data on a running instance) → a ~1–2 min outage. The EIP keeps the IP and
> `restart: unless-stopped` brings the app back, but the *running* instance never
> needs a user_data refresh (it only runs at first boot). To apply other changes
> without the reboot, `-target` them; to stop Terraform proposing the reboot for a
> bootstrap-script edit, add `lifecycle { ignore_changes = [user_data] }`.

### Admin Access (SSM Session Manager)

Once VPC migration is complete, admin access is via AWS SSM:

```bash
# Terminal session (replaces SSH)
aws ssm start-session --target i-<instance-id> --region us-east-1

# Port forwarding to Aurora (database access from laptop)
aws ssm start-session \
  --target i-<instance-id> \
  --region us-east-1 \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters host=<aurora-endpoint>,portNumber=5432,localPortNumber=5432
```

## Branch Protection (`main`)

`main` is build-on-deploy: every push auto-deploys to the live EC2 host. The protection
ruleset is codified in [`scripts/setup-branch-protection.sh`](../scripts/setup-branch-protection.sh)
so an admin can apply or audit it declaratively (audit #10 / issues #519, #526).

```bash
./scripts/setup-branch-protection.sh            # dry-run: print the payload, apply nothing
./scripts/setup-branch-protection.sh --apply    # apply (needs repo admin)
./scripts/setup-branch-protection.sh --verify    # print the currently-applied protection
# or, raw:  gh api repos/hackagora/archimedes-arcadia/branches/main/protection
```

What it enforces: the two hard-block CI checks (`Backend — unit tests`, `Ruff — format +
critical lint rules`), 1 approving review, no force-push, no branch deletion, and
`required_linear_history: false` (we are **merge-commits-only** — linear history would
force squash/rebase). The informational checks (lint-report, complexity) stay non-required.

**Build-on-deploy tradeoff:** the script ships with `enforce_admins=false` so repo admins
(including the `t2o2` agentic user) keep their direct-push path while non-admins are gated.
**Stale as of 2026-08-03:** `t2o2` is dormant, so this exemption now only benefits human
admins. See the REVISIT note in `scripts/setup-branch-protection.sh`.
Flipping `ENFORCE_ADMINS=true` gates everyone but forces the agentic system onto PRs — that
is a team decision (Chuan, as repo admin, owns it).

## Monitoring & Disaster Recovery

- **`cloudwatch.tf`** — SNS alert topic + alarms (EC2 CPU/status, ALB 5xx /
  unhealthy hosts / p95 latency, Aurora CPU / memory / connections) + an ops
  dashboard. Additive: `terraform apply` only *creates* new CloudWatch objects,
  it does not touch the existing EC2/ALB/Aurora/WAF resources. Set
  `alarm_email` (tfvars) to get paged. **Authored 2026-06-12, not yet
  `terraform plan`-verified** — review before applying.
- **`runbooks/disaster-recovery.md`** — RTO/RPO targets, per-scenario response
  (host loss, DB corruption, WAF lockout), restore-order, and a drill checklist.
- **`runbooks/aurora-backup-restore.md`** — exact PITR / snapshot-restore CLI
  (Aurora `backup_retention_period = 7` ⇒ 7-day PITR window already on).
- **`runbooks/waf-rules-reference.md`** — what each `waf.tf` rule does and the
  count→block promotion workflow.

> These runbooks are **authored, not drilled.** Run a game-day (see the DR
> drill checklist) before trusting the measured RTO/RPO.

## ECS Fargate (issue #1039)

- **`ecr.tf`** — the two private ECR repos CI (build-chunk 1) already pushes
  to (`archimedes-backend`, `archimedes-nginx`), each with a lifecycle policy
  (expire untagged after 1 day; keep the last 15 tagged images).
- **`ecs.tf`** — ECS cluster, the `archimedes-backend` task definition
  (nginx + backend, one Fargate task, `awsvpc` mode) and service (registered
  into the **existing** `archimedes-backend-tg` target group via a data
  source — `alb.tf` itself is untouched except one additive security-group
  egress rule), CPU-based Application Auto Scaling (min/max via
  `ecs_service_min_count` / `ecs_service_max_count`), and the task
  execution/task IAM roles + an additive inline policy on the existing
  out-of-band `archimedes-github-deploy` CI role.
- **`runbooks/ecs-fargate-cutover.md`** — the full ordered operator runbook:
  the three blockers that must close before this actually serves traffic;
  what `terraform apply` does the instant it runs (starts live blue/green
  traffic against the same target group — read before applying); a staged
  apply order (ECR first, then seed a real image, then the ECS
  cluster/service); how to seed ECR with the first image (CI trigger or a
  manual break-glass push); swinging the ALB target from the EC2 instance to
  the ECS service; dedicated verification drills for zero-downtime rollouts,
  self-heal (kill a task, time the <2 min recovery), and `ecs
  execute-command`; and the EC2 decommission sequence (gated on the
  oracle/agent/kb-runner daemons getting their own Fargate home first).

> **Authored 2026-07-06, not yet `terraform plan`-verified against live AWS
> (no credentials in this environment) — `terraform validate`/`fmt` only.**
> Same review-then-apply posture as the rest of this section.

## Per-deploy artifacts

- **`deploy_output.json`** (repo root) is written by
  [`backend/archimedes/scripts/deploy_contracts.py`](../backend/archimedes/scripts/deploy_contracts.py)
  on every contract deploy and goes stale the moment any contract is
  redeployed — it is gitignored and untracked (audit 06-14 finding I3: the
  previously-tracked copy's `synthTokens`/`synthOracles`/`vaults` addresses
  no longer matched the live deploy). It is per-deploy operator output, not a
  source of truth. The authoritative current addresses live in
  [`ui/src/config.js`](../ui/src/config.js) (frontend) and
  [`backend/archimedes/chain/client.py`](../backend/archimedes/chain/client.py)
  (backend) — update both of those after any redeploy.

## Security Notes

- **No `.pem` files in git.** `infra/*.pem` is in `.gitignore`.
- **No Terraform state in git.** State is in S3; local files are gitignored.
- **SSH keys are rotated.** The key committed in early repo history was revoked
  on 2026-05-26. The current key exists only in GitHub Secrets + local machine.
- **Port 22 will be removed** once SSM Session Manager is live.

# ── ECS Fargate — the archimedes-backend service on the EXISTING ALB ─────────
#
# Issue #1039 (P2 foundation + P3 Fargate service). Runs the same two
# containers that today share one EC2 box via docker-compose.yml
# (nginx reverse-proxy + backend FastAPI), as ONE Fargate task: awsvpc network
# mode gives both containers the same ENI, so they talk over `localhost` exactly
# like two processes on one host — nginx on :8080 (the ALB target), backend on
# :8000 (nginx's upstream). The ALB, its listener, and its target group
# (`archimedes-backend-tg`, target_type=ip, health check GET /health) are
# ALL reused unchanged from alb.tf — Fargate registers each task's ENI IP
# directly on container_port=8080, which is exactly what an `ip`-type target
# group already expects. No alb.tf resource is touched.
#
# ══════════════════════════════════════════════════════════════════════════
# KNOWN GAP — MUST FIX BEFORE THIS SERVICE CAN ACTUALLY BOOT (not faked here):
# ══════════════════════════════════════════════════════════════════════════
# 1. nginx/nginx.conf's upstream is `server backend:8000 resolve;`, relying on
#    Docker Compose's per-container DNS name on its user-defined bridge
#    network. ECS Fargate awsvpc tasks have NO such inter-container DNS —
#    containers in the same task share one ENI and reach each other via
#    `localhost`/127.0.0.1, not by container name. Left as-is, nginx's
#    resolver (127.0.0.11, Docker's embedded DNS) doesn't exist in this
#    network mode and every request 502s. Fix is a one-line nginx.conf change
#    (`server localhost:8000 resolve;` or drop `resolve` entirely since
#    localhost never changes) — deliberately NOT made in this Terraform-only
#    chunk: nginx.conf is shared with docker-compose's bridge-network EC2 path
#    where `backend:8000` is still correct, so the fix needs either an
#    environment-conditional template or to land alongside the actual cutover
#    (when the EC2 path is retired and only one value is ever needed again).
# 2. `ARCHIMEDES_STRATEGIES_DIR`, `ARCHIMEDES_CORPUS_MANIFEST`, and
#    `KB_ARTIFACT_DIR` (env vars below) point at paths that today are
#    populated via docker-compose HOST BIND MOUNTS
#    (./analytics-engine/strategies, ./data/corpus, ./contracts/abis, the
#    archimedes-corpus-artifact named volume) from the EC2 box's git
#    checkout — see docker-compose.yml. Fargate has no host filesystem to
#    bind-mount from. `backend/Dockerfile`'s `COPY . /app` only copies the
#    `backend/` build context, so none of these repo-root sibling directories
#    are baked into the image today. Until backend/Dockerfile COPYs them in
#    (or an EFS volume is attached to this task), the backend container will
#    boot but fail to find strategies/corpus data at these paths.
# 3. `DATABASE_URL` / `REDIS_URL` (below, via ECS `secrets`) resolve from
#    SSM parameters `/archimedes/prod/DATABASE_URL` and
#    `/archimedes/prod/REDIS_URL`, which do not exist yet — seed them the
#    same way AURORA_MASTER_PASSWORD / EMAIL_ENCRYPTION_KEY already are
#    (extend a local secrets.env and run `infra/scripts/seed-ssm-secrets.sh`
#    — operator step, see infra/runbooks/ecs-fargate-cutover.md). Until
#    seeded, ECS will fail to launch tasks with a secret-resolution error on
#    these two specifically — AURORA_MASTER_PASSWORD / EMAIL_ENCRYPTION_KEY
#    (config consolidation, #1039 P5, this chunk) are already live in SSM and
#    resolve without any further action.
# 4. `oracle_runner` / `agent_runner` / `kb_runner` (docker-compose services
#    `oracle`, `agent`, `kb-runner`) are NOT covered by this file. They are
#    singleton background daemons, not ALB-fronted request handlers, and
#    #1039's explicit scope for this chunk is "task definition for backend"
#    (singular, the ALB-fronted service). They need their own Fargate
#    service(s) (no load balancer, desired_count=1, no autoscaling — the
#    issue's own anti-goal for the EC2 ASG tier applies equally here: these
#    loops are the source of truth for vault/regime state and must not be
#    duplicated) before the EC2 instance can actually be decommissioned
#    (P6) — tracked as a residual for a later chunk, not silently dropped.
# ══════════════════════════════════════════════════════════════════════════
#
# APPLYING THIS FILE STARTS LIVE TRAFFIC CUTOVER: as soon as `aws_ecs_service`
# is created, ECS begins registering healthy Fargate task IPs into the SAME
# target group the EC2 instance is statically attached to (alb.tf's
# `aws_lb_target_group_attachment.backend`) — the ALB round-robins across
# BOTH immediately. See infra/runbooks/ecs-fargate-cutover.md before applying.

data "aws_caller_identity" "current" {}

# The existing target group, reused by NAME (not the `aws_lb_target_group.backend`
# resource reference in alb.tf, even though it lives in this same state/root
# module) — deliberately decoupled via a data source so this file reads as
# "attach to whatever `archimedes-backend-tg` already is" rather than
# depending on alb.tf's resource graph. It already exists live; reuse is exact.
data "aws_lb_target_group" "backend" {
  name = "${var.project_name}-backend-tg"
}

# ── ECS Cluster ────────────────────────────────────────────────────────────

resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-cluster"

  # Container Insights: per-service/per-task CPU/mem/network dashboards.
  # Modest extra CloudWatch cost for a single-service, ~0.07 req/s workload —
  # worth it since infra/cloudwatch.tf has no ECS-specific dashboard today.
  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  # DEFAULT = ECS Exec session I/O is logged via each container's own awslogs
  # configuration (see the two log groups below) — no separate log group or
  # KMS key needed to satisfy "aws ecs execute-command ... logs survive."
  configuration {
    execute_command_configuration {
      logging = "DEFAULT"
    }
  }

  tags = { Project = var.project_name }
}

# ── CloudWatch Logs — reuse the two log groups already provisioned ─────────
# infra/cloudwatch.tf (AUDIT I8) already created `/archimedes/app` and
# `/archimedes/nginx` at 90-day retention, pre-provisioned but currently
# unused (nothing ships to them — today's container logs are EC2-local,
# per #1039's own problem statement). They are exactly the right shape for
# the two ECS containers; reused here rather than creating a second,
# redundant pair of log groups. See aws_cloudwatch_log_group.app /
# aws_cloudwatch_log_group.nginx in cloudwatch.tf.

# ── Security group — ECS tasks (nginx ingress from ALB only) ───────────────
# backend is reached by nginx over localhost inside the same task (awsvpc
# shares one ENI across containers) — no SG rule needed for that hop at all.

resource "aws_security_group" "ecs_backend" {
  name        = "${var.project_name}-ecs-backend-sg"
  description = "ECS Fargate backend task (nginx + backend) - HTTP from ALB only"
  vpc_id      = aws_vpc.main.id

  # CIDR-restricted to the ALB's OWN public subnets (10.0.0.0/24,
  # 10.0.1.0/24 — aws_subnet.public in vpc.tf), NOT the whole VPC CIDR
  # (10.0.0.0/16, which also covers the private subnets Aurora/ElastiCache/
  # this same task sit in and is far wider than the ALB needs) and NOT a
  # `security_groups = [aws_security_group.alb.id]` reference: the ALB SG's
  # egress rule below already references THIS security group's id, and a
  # reciprocal reference the other way round is a dependency cycle Terraform
  # can't resolve ("Cycle: aws_security_group.ecs_backend,
  # aws_security_group.alb"). Referencing `aws_subnet.public[*].cidr_block`
  # directly (PR #1041 Copilot review) tightens the rule to exactly where the
  # ALB's ENIs live without introducing that cycle — subnet resources have no
  # dependency on either security group. Same one-directional
  # avoid-the-SG-cycle rationale main.tf's `aws_security_group.archimedes`
  # documents for its own CIDR-based rule.
  ingress {
    description = "HTTP from ALB (nginx container port), restricted to the ALB's public subnet CIDRs"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = aws_subnet.public[*].cidr_block
  }

  egress {
    description = "All outbound (Aurora, ElastiCache, Arc RPC, Bedrock, ECR, CloudWatch Logs)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-ecs-backend-sg"
    Project = var.project_name
  }
}

# alb.tf's aws_security_group.alb egress only allows the ALB to reach the EC2
# SG on port 80 (the pre-Fargate world). This is the one necessary, minimal
# addition needed for the ALB to reach the new Fargate targets on their
# actual container port (8080) — added as a SECOND inline egress block on the
# SAME resource declaration (see alb.tf), not a standalone aws_security_group_rule:
# aws_security_group with inline ingress/egress blocks is authoritative for ALL
# rules on that SG, and mixing it with standalone aws_security_group_rule
# resources causes Terraform to fight itself and delete "unmanaged" rules on
# every apply (the same footgun vpc.tf already documents for route tables).

# ── IAM — ECS task EXECUTION role (used by the ECS agent, not app code) ────
# Pulls images from ECR, ships container stdout/stderr to CloudWatch Logs, and
# resolves the `secrets` block below (SSM SecureString → env var) at launch.

resource "aws_iam_role" "ecs_task_execution" {
  name = "archimedes-ecs-task-execution-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = { Project = var.project_name }
}

# AWS-managed: ecr:GetAuthorizationToken/BatchGetImage/GetDownloadUrlForLayer/
# BatchCheckLayerAvailability + logs:CreateLogStream/PutLogEvents.
resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Resolve the DATABASE_URL / REDIS_URL `secrets` (see task definition below)
# at container launch. Scoped identically to ec2_iam.tf's existing
# `ec2_ssm_params` policy for the same prefix.
resource "aws_iam_role_policy" "ecs_task_execution_ssm_secrets" {
  name = "archimedes-ecs-execution-ssm-read"
  role = aws_iam_role.ecs_task_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadAppSecretsForTaskLaunch"
        Effect   = "Allow"
        Action   = ["ssm:GetParameters", "ssm:GetParameter"]
        Resource = "arn:aws:ssm:*:*:parameter/archimedes/prod/*"
      },
      {
        Sid      = "DecryptSecureStringViaSSM"
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = "*"
        Condition = {
          StringEquals = { "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com" }
        }
      }
    ]
  })
}

# ── IAM — ECS task ROLE (used by application code inside the container) ────
# Mirrors ec2_iam.tf's EC2 instance role almost exactly — this is the same
# app code (services/secrets_service.load_ssm_secrets, the Bedrock LLM
# backends) running under ECS task credentials instead of EC2 instance
# credentials. Plus ssmmessages:* for `aws ecs execute-command` (the SSM
# Session Manager channel equivalent of the box's existing SSM access) and
# scoped CloudWatch Logs write for exec session output.

resource "aws_iam_role" "ecs_task" {
  name = "archimedes-ecs-task-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = { Project = var.project_name }
}

resource "aws_iam_role_policy" "ecs_task_ssm_params" {
  name = "archimedes-ecs-task-ssm-read"
  role = aws_iam_role.ecs_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadAppSecrets"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        Resource = "arn:aws:ssm:*:*:parameter/archimedes/prod/*"
      },
      {
        Sid      = "DecryptSecureStringViaSSM"
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = "*"
        Condition = {
          StringEquals = { "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com" }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_task_bedrock_invoke" {
  name = "archimedes-ecs-bedrock-invoke"
  role = aws_iam_role.ecs_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeBedrockFoundationModels"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*"
        ]
      }
    ]
  })
}

# ECS Exec ("aws ecs execute-command" — the SSM-session equivalent for a
# Fargate task; no inbound port, no SSH). Required permissions per AWS docs:
# https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs-exec.html
resource "aws_iam_role_policy" "ecs_task_exec_command" {
  name = "archimedes-ecs-exec-command"
  role = aws_iam_role.ecs_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EcsExecSsmChannel"
        Effect = "Allow"
        Action = [
          "ssmmessages:CreateControlChannel",
          "ssmmessages:CreateDataChannel",
          "ssmmessages:OpenControlChannel",
          "ssmmessages:OpenDataChannel"
        ]
        Resource = "*"
      },
      {
        Sid    = "EcsExecSessionLogging"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogGroups",
          "logs:CreateLogStream",
          "logs:DescribeLogStreams",
          "logs:PutLogEvents"
        ]
        Resource = [
          aws_cloudwatch_log_group.app.arn,
          "${aws_cloudwatch_log_group.app.arn}:*",
          aws_cloudwatch_log_group.nginx.arn,
          "${aws_cloudwatch_log_group.nginx.arn}:*"
        ]
      }
    ]
  })
}

# ── IAM — extend the EXISTING CI deploy role with ECS deploy permissions ───
# `archimedes-github-deploy` is deliberately NOT Terraform-managed (created
# out-of-band by infra/scripts/setup-github-oidc.sh — see that script's own
# "tighten once ECS exists" comment). This attaches a SEPARATE, additively-
# named inline policy ("archimedes-ecs-deploy", distinct from the script's own
# "archimedes-deploy" policy) so Terraform and the script each own a disjoint
# policy name on the same role — no state fights, no clobbering.
#
# NOT wired into any CI workflow step yet (deploy.yml still only builds/pushes
# to ECR — the box-pull path from build-chunk 1). This is the IAM
# groundwork a later chunk's `aws ecs register-task-definition` +
# `aws ecs update-service --force-new-deployment` deploy step needs; landing
# it now means that later chunk needs zero IAM changes of its own.

data "aws_iam_role" "github_deploy" {
  name = "archimedes-github-deploy"
}

resource "aws_iam_role_policy" "github_deploy_ecs" {
  name = "archimedes-ecs-deploy"
  role = data.aws_iam_role.github_deploy.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EcsDeploy"
        Effect = "Allow"
        Action = [
          "ecs:RegisterTaskDefinition",
          "ecs:DeregisterTaskDefinition",
          "ecs:DescribeTaskDefinition",
          "ecs:DescribeServices",
          "ecs:DescribeTasks",
          "ecs:ListTasks",
          "ecs:UpdateService",
          # RunTask/StopTask: the dedicated Alembic one-off migrate task
          # (infra/ecs_migrate.tf's `aws_ecs_task_definition.migrate`, #1039
          # P4, coupled to #1028 Phase A) runs this same way — pre-rollout,
          # before new tasks serve traffic. `Resource = "*"` already covers
          # that family (and the service family below); no separate grant
          # needed when the migrate task-def was split out from the service
          # one (PR #1041 Copilot review).
          "ecs:RunTask",
          "ecs:StopTask"
        ]
        Resource = "*"
      },
      {
        Sid    = "PassEcsRoles"
        Effect = "Allow"
        Action = "iam:PassRole"
        # Same two roles infra/ecs_migrate.tf's task definition reuses
        # (execution_role_arn / task_role_arn) — no additional PassRole grant
        # needed for the dedicated migrate task-def.
        Resource = [
          aws_iam_role.ecs_task_execution.arn,
          aws_iam_role.ecs_task.arn
        ]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      }
    ]
  })
}

# ── Task definition — nginx + backend, one Fargate task ────────────────────

resource "aws_ecs_task_definition" "backend" {
  family                   = "${var.project_name}-backend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ecs_backend_cpu
  memory                   = var.ecs_backend_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "backend"
      image     = "${aws_ecr_repository.backend.repository_url}:${var.backend_image_tag}"
      essential = true

      portMappings = [
        { containerPort = 8000, protocol = "tcp" }
      ]

      # Mirrors backend/Dockerfile's own HEALTHCHECK exactly. Declaring it
      # here (not just relying on the image's Dockerfile HEALTHCHECK) is what
      # lets ECS track this container's health for nginx's `dependsOn
      # condition = HEALTHY` below — an image-only HEALTHCHECK isn't visible
      # to the ECS agent for container-dependency purposes.
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\" || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }

      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "AWS_SSM_PATH_PREFIX", value = "/archimedes/prod/" },
        { name = "PUBLIC_DOMAIN", value = var.domain_name },
        { name = "ARCHIMEDES_FUSION_ENABLED", value = "true" },
        # KNOWN GAP #2 (see file header): these three paths have no Fargate
        # equivalent of the docker-compose host bind mount yet.
        { name = "ARCHIMEDES_STRATEGIES_DIR", value = "/app/analytics-engine/strategies" },
        { name = "ARCHIMEDES_CORPUS_MANIFEST", value = "/app/data/corpus/manifest.jsonl" },
        { name = "KB_ARTIFACT_DIR", value = "/app/data/corpus-artifact" },
        # Deployed Arc testnet contract addresses — same defaults as
        # docker-compose.yml's backend service; not secrets.
        { name = "ARC_AMM_ROUTER_ADDRESS", value = "0xd5b829f9d364a8bbe1caf6c8b19cb05371b178f4" },
        { name = "ARC_VAULT_FACTORY_ADDRESS", value = "0xca873414070844aeb98b0bf1051f81969c79cc32" },
        { name = "ARC_REASONING_TRACE_REGISTRY_ADDRESS", value = "0x42d8a23edb897cbee203e9fa197eb05ab5106ca6" },
        { name = "ARC_ASSET_REGISTRY_ADDRESS", value = "0x2d44550711137916df6175587d17886281a0fbc7" },
        # Config consolidation (#1039 P5): public wallet addresses, sourced
        # from Terraform variables (TF_VAR_*, see variables.tf) rather than
        # hardcoded here or read off a box .env file. Empty defaults disable
        # the admin-wallet publish bypass / marketplace publish respectively
        # until Dan supplies real values.
        { name = "PLATFORM_ADMIN_WALLETS", value = var.platform_admin_wallets },
        { name = "ARCHIMEDES_TREASURY_WALLET", value = var.archimedes_treasury_wallet }
      ]

      # KNOWN GAP #3 (see file header) — NARROWED by #1039 P5 (config
      # consolidation, this chunk): AURORA_MASTER_PASSWORD and
      # EMAIL_ENCRYPTION_KEY are wired below and resolve immediately — both
      # already exist live in SSM (infra/scripts/setup-ssm-secrets.sh already
      # seeds them, predating this chunk). DATABASE_URL / REDIS_URL remain the
      # open gap: those two SSM parameters don't exist yet, seeded the same
      # way via infra/scripts/seed-ssm-secrets.sh (operator step, documented
      # in infra/runbooks/ecs-fargate-cutover.md). Until seeded, ECS fails to
      # launch tasks with a secret-resolution error on those two only.
      secrets = [
        { name = "DATABASE_URL", valueFrom = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/archimedes/prod/DATABASE_URL" },
        { name = "REDIS_URL", valueFrom = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/archimedes/prod/REDIS_URL" },
        # Already live in SSM (setup-ssm-secrets.sh, predates #1039) — no
        # seeding step needed for these two, unlike DATABASE_URL/REDIS_URL
        # above. Resolved by the execution role at task launch, same as the
        # box's runtime `secrets_service.load_ssm_secrets()` fetch today, but
        # deterministic: the app process never starts without them, rather
        # than depending on a best-effort in-process SSM fetch.
        { name = "AURORA_MASTER_PASSWORD", valueFrom = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/archimedes/prod/AURORA_MASTER_PASSWORD" },
        { name = "EMAIL_ENCRYPTION_KEY", valueFrom = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/archimedes/prod/EMAIL_ENCRYPTION_KEY" }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    },
    {
      name      = "nginx"
      image     = "${aws_ecr_repository.nginx.repository_url}:${var.backend_image_tag}"
      essential = true

      portMappings = [
        { containerPort = 8080, protocol = "tcp" } # the ALB target — matches archimedes-backend-tg's traffic-port health check
      ]

      # KNOWN GAP #1 (see file header): nginx.conf's `server backend:8000` is
      # a docker-compose-bridge-network DNS name that does not resolve in
      # ECS awsvpc mode — must become `localhost:8000` before this actually
      # proxies successfully.
      dependsOn = [
        { containerName = "backend", condition = "HEALTHY" }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.nginx.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = { Project = var.project_name }
}

# ── ECS Service — behind the existing ALB target group ─────────────────────

resource "aws_ecs_service" "backend" {
  name            = "${var.project_name}-backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  desired_count   = var.ecs_service_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id # same private subnets Aurora/ElastiCache SGs already trust (10.0.10.0/24, 10.0.11.0/24) — zero SG edits needed on those
    security_groups  = [aws_security_group.ecs_backend.id]
    assign_public_ip = false # outbound (ECR, Bedrock, Arc RPC) via the existing fck-nat instances (vpc.tf)
  }

  load_balancer {
    target_group_arn = data.aws_lb_target_group.backend.arn
    container_name   = "nginx"
    container_port   = 8080
  }

  # Grace period before the ALB's own unhealthy-target verdict can cause ECS
  # to kill a freshly-started task — gives the backend container's model-load
  # + DB-connect startup room before the health check clock counts against it.
  health_check_grace_period_seconds = 90

  # Rolling, zero-downtime deploys: 100% minimum keeps the full existing
  # fleet serving while up to another 100% of new tasks come up and pass
  # health checks, only then are old tasks drained — never below desired
  # capacity, matching the "zero ALB 5xx during rollout" acceptance criterion.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # Health-check-based auto-rollback: if the new deployment's tasks don't
  # reach a steady healthy state, ECS automatically rolls back to the last
  # known-good task definition revision — no human action required.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # `aws ecs execute-command` — the ECS Exec shell-into-a-running-task
  # acceptance criterion. Needs the task role's ssmmessages:* above.
  enable_execute_command = true

  depends_on = [
    aws_iam_role_policy_attachment.ecs_task_execution_managed,
    aws_iam_role_policy.ecs_task_execution_ssm_secrets,
    aws_iam_role_policy.ecs_task_ssm_params,
    aws_iam_role_policy.ecs_task_bedrock_invoke,
    aws_iam_role_policy.ecs_task_exec_command,
  ]

  lifecycle {
    # Real deploys after bootstrap register a new task-definition revision
    # (commit-SHA image tag) and call `aws ecs update-service
    # --force-new-deployment` directly — NOT `terraform apply`. Ignoring
    # drift on these two fields means that out-of-band deploy path (and the
    # CPU-based autoscaling policy's own DesiredCount changes) is never
    # silently reverted by a later plan/apply, mirroring main.tf's existing
    # `ignore_changes = [ami, user_data]` on the EC2 instance for the same reason.
    ignore_changes = [task_definition, desired_count]
  }

  tags = { Project = var.project_name }
}

# ── Application Auto Scaling — CPU target tracking, min 1 / max N ──────────

resource "aws_appautoscaling_target" "backend" {
  max_capacity       = var.ecs_service_max_count
  min_capacity       = var.ecs_service_min_count
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.backend.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "backend_cpu" {
  name               = "${var.project_name}-backend-cpu-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.backend.resource_id
  scalable_dimension = aws_appautoscaling_target.backend.scalable_dimension
  service_namespace  = aws_appautoscaling_target.backend.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = var.ecs_autoscale_cpu_target
    scale_in_cooldown  = 300 # slow scale-in, mirrors asg.tf's own "slow, to avoid flapping" rationale
    scale_out_cooldown = 60  # fast scale-out
  }
}

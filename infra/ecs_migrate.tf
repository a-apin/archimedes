# ── ECS Task Definition — one-off Alembic migrate task (#1039 P4) ──────────
#
# Copilot review on PR #1041 (.github/workflows/deploy.yml:312): the `migrate`
# job's `aws ecs run-task` originally targeted the SERVICE task-def family
# (`archimedes-backend`, ecs.tf's `aws_ecs_task_definition.backend`) and only
# overrode the `backend` container's command. That family defines TWO
# containers — `backend` AND an `nginx` sidecar with `dependsOn { containerName
# = "backend", condition = "HEALTHY" }` — so running the migrate one-off
# against it would boot the whole app (nginx included) just to run a schema
# migration. Worse than wasteful: during a migration, the `backend` container's
# command is overridden to `python -m alembic upgrade head` instead of serving
# HTTP, so its `/health` endpoint is never reachable, its ECS `healthCheck`
# never turns HEALTHY, nginx's `dependsOn` condition is never satisfied, and
# ECS is liable to kill the task as unhealthy before Alembic finishes.
#
# This is a dedicated, single-container task definition instead: no nginx, no
# healthCheck, no dependsOn. It runs to completion (exit 0 on success, nonzero
# on failure) and stops — the correct shape for a `run-task` one-off, not a
# long-lived ALB-fronted service task. It reuses the SAME image, the SAME two
# IAM roles (execution + task — both already grant exactly what a migration
# needs: ECR pull, CloudWatch Logs, SSM secret resolution; no new IAM resource
# needed, and `aws_iam_role_policy.github_deploy_ecs` in ecs.tf already scopes
# `iam:PassRole` to these same two role ARNs) and the SAME four SSM-backed
# secrets as the service task definition below — self-sufficient to launch on
# its own via `aws ecs run-task`, given a `--network-configuration` (private
# subnets + a security group with egress to Aurora/ElastiCache/ECR/SSM/KMS —
# `aws_security_group.ecs_backend` already provides exactly that; no inbound
# access is needed for a one-off task that never accepts connections).
#
# Family name is exported via `outputs.tf` (`ecs_migrate_task_definition_family`)
# so `.github/workflows/deploy.yml`'s `migrate` job documents, rather than
# silently assumes, which Terraform resource its hardcoded `ECS_MIGRATE_TASK_FAMILY`
# literal must stay in sync with — the same literal-constant pattern already used
# for `ECS_CLUSTER` / `EC2_INSTANCE_ID` there (CI can't run `terraform output`
# without a full backend/state setup, so the value itself stays a literal; the
# Terraform output is the sync-check a reviewer / future change can grep for).

resource "aws_ecs_task_definition" "migrate" {
  family                   = "${var.project_name}-migrate"
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
      name      = "migrate"
      image     = "${aws_ecr_repository.backend.repository_url}:${var.backend_image_tag}"
      essential = true

      # Deliberately NO portMappings, NO healthCheck, NO dependsOn: this is a
      # single-container, run-to-completion task (`aws ecs run-task`, command
      # overridden by CI to `python -m alembic upgrade head`) — there is no
      # HTTP surface to health-check and nothing else in the task definition
      # to depend on it, unlike the `backend` container in the service task
      # definition below, which nginx depends on being HEALTHY.

      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "AWS_SSM_PATH_PREFIX", value = "/archimedes/prod/" },
      ]

      # Same four SSM-backed secrets as the `backend` service container
      # (below) — DATABASE_URL is the one Alembic actually needs; the other
      # three are included so this task definition is self-sufficient against
      # the SAME image without depending on which entrypoint path the image
      # happens to exercise at import time (mirrors the service container's
      # secrets set exactly, avoiding a second, drifting source of truth for
      # "what secrets does this image need to boot").
      secrets = [
        { name = "DATABASE_URL", valueFrom = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/archimedes/prod/DATABASE_URL" },
        { name = "REDIS_URL", valueFrom = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/archimedes/prod/REDIS_URL" },
        { name = "AURORA_MASTER_PASSWORD", valueFrom = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/archimedes/prod/AURORA_MASTER_PASSWORD" },
        { name = "EMAIL_ENCRYPTION_KEY", valueFrom = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/archimedes/prod/EMAIL_ENCRYPTION_KEY" }
      ]

      # Reuses the same `/archimedes/app` log group as the service's `backend`
      # container (cloudwatch.tf) — a distinct stream prefix (`ecs-migrate`
      # instead of `ecs`) keeps one-off migrate runs visually separable from
      # steady-state service logs without provisioning a third log group.
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs-migrate"
        }
      }
    }
  ])

  tags = { Project = var.project_name }
}

# ── kb-runner — scheduled (batch) Fargate task, not a loop ─────────────────
#
# Issue #1065 decision #2 (drafted default: point the schedule at the
# already-one-shot `python -m archimedes.scripts.run_kb_pipeline` — verified
# present at backend/archimedes/scripts/run_kb_pipeline.py — rather than
# adding a `run_once()` to services/kb_runner.py). Trade-off this draft
# accepts: `run_kb_pipeline.py` skips `needs_rerun()` gating (new-paper
# threshold / max-days-since-last, services/kb_runner.py's own logic) and the
# Redis lease services/runner_lease.py provides for the async singleton
# runners — every scheduled invocation just runs (or, with
# KB_PIPELINE_ENABLED unset, writes a `{"status": "skipped"}` manifest.json
# and returns — see that module's `run_pipeline()`). That's an accepted,
# documented gap, not an oversight: kb-runner is a batch job, not a
# funds-adjacent singleton like oracle/agent, so a rare overlapping run is a
# wasted-compute risk, not a double-spend risk.
#
# This is intentionally NOT an ECS *service* (no desired_count, no ALB, no
# autoscaling) — EventBridge Scheduler invokes `ecs:RunTask` directly on the
# schedule below, and the task runs to completion and stops, exactly like
# ecs_migrate.tf's one-off migrate task (closest existing template).

# ── Security group — no ingress; egress only (EFS, ECR, SSM, CloudWatch) ───

resource "aws_security_group" "kb_runner" {
  name        = "${var.project_name}-kb-runner-sg"
  description = "kb-runner scheduled Fargate task - no ingress, outbound only"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "All outbound (EFS, ECR, SSM secrets resolution, CloudWatch Logs)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-kb-runner-sg"
    Project = var.project_name
  }
}

# ── CloudWatch log group — dedicated (not the shared /archimedes/app group)
# so the failure-detection metric filter below can't cross-contaminate with
# unrelated backend/migrate log lines (a log-group-wide filter has no
# per-stream scoping in Terraform).

resource "aws_cloudwatch_log_group" "kb_runner" {
  name              = "/archimedes/kb-runner"
  retention_in_days = 90
  tags              = { Project = var.project_name }
}

# ── Task definition ─────────────────────────────────────────────────────
# Single container, run-to-completion — no healthCheck/dependsOn (same
# shape as ecs_migrate.tf's aws_ecs_task_definition.migrate, the closest
# existing template for a Fargate one-off).

resource "aws_ecs_task_definition" "kb_runner" {
  family                   = "${var.project_name}-kb-runner"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.kb_runner_cpu
  memory                   = var.kb_runner_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn # ecs.tf — reused, no new execution role needed
  task_role_arn            = aws_iam_role.ecs_task.arn           # ecs.tf — reused; carries the EFS grant added in efs.tf

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  volume {
    name = "corpus-artifact"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.corpus_artifact.id
      transit_encryption = "ENABLED" # required by AWS whenever an access point is used

      authorization_config {
        access_point_id = aws_efs_access_point.corpus_artifact.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "kb-runner"
      image     = "${aws_ecr_repository.backend.repository_url}:${var.backend_image_tag}"
      essential = true

      # run_kb_pipeline.py (verified present, see file header) — one-shot,
      # exits after writing manifest.json, unlike docker-compose's
      # `services.kb_runner` module (an infinite poll loop; NOT used here).
      command = ["python", "-m", "archimedes.scripts.run_kb_pipeline"]

      mountPoints = [
        { sourceVolume = "corpus-artifact", containerPath = "/srv/corpus-artifact", readOnly = false }
      ]

      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "KB_ARTIFACT_DIR", value = "/srv/corpus-artifact" },
        # backend/Dockerfile COPYs data/corpus → /app/data/corpus into the
        # SAME image this task runs (verified in the Dockerfile) — the
        # corpus TEXT source needs no EFS mount and no docker-compose-style
        # host bind mount; it's already baked in at build time.
        { name = "KB_CORPUS_DIR", value = "/app/data/corpus" },
        # KB_PIPELINE_ENABLED deliberately left UNSET (matches
        # docker-compose.yml's own default): the real SPECTER2/HDBSCAN/
        # BERTopic/REBEL/SciSpacy pipeline needs a dedicated ~6 GB-model
        # image this task definition does not yet build/reference (see
        # run_kb_pipeline.py's own NotImplementedError guard). Until that
        # ships, every scheduled run intentionally no-ops to a "skipped"
        # manifest.json — which is still enough to satisfy #1065 Step 3's
        # "confirm a manifest.json lands under the EFS artifact path" check
        # and to prove the schedule → EFS path works end-to-end.
      ]

      # NOTE: no `secrets` block — DATABASE_URL is deliberately NOT wired
      # here. run_kb_pipeline.py's current skeleton (verified via grep) does
      # not read DATABASE_URL at all; the docstring's "Postgres
      # papers.cluster_id / kg_entities / kg_relations" writes are the REAL
      # pipeline's future behavior, gated behind the same KB_PIPELINE_ENABLED
      # flag as above. Adding a DATABASE_URL secret now would make this
      # task's launch depend on the /archimedes/prod/DATABASE_URL SSM
      # parameter existing (per ecs.tf's own header comment, NOT seeded as of
      # that file) — an unnecessary hard-fail risk for a still-skip-mode
      # batch job. Add it back (same SSM ARN pattern as ecs.tf /
      # ecs_migrate.tf) in the same PR that flips KB_PIPELINE_ENABLED on.

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.kb_runner.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "kb"
        }
      }
    }
  ])

  tags = { Project = var.project_name }
}

# ── IAM — EventBridge Scheduler's own role (assumes into ecs:RunTask) ──────

resource "aws_iam_role" "kb_scheduler" {
  name = "archimedes-kb-scheduler-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } # ecs.tf
      }
    }]
  })
  tags = { Project = var.project_name }
}

resource "aws_iam_role_policy" "kb_scheduler_run_task" {
  name = "archimedes-kb-scheduler-run-task"
  role = aws_iam_role.kb_scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunKbTask"
        Effect = "Allow"
        Action = ["ecs:RunTask"]
        # Family-wildcard (no pinned revision) ARN — matches the schedule
        # target's own `task_definition_arn` below (also revision-less), so
        # this grant stays valid across every future CI-registered revision
        # without a Terraform change.
        Resource = "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task-definition/${aws_ecs_task_definition.kb_runner.family}:*"
        Condition = {
          ArnLike = { "ecs:cluster" = aws_ecs_cluster.main.arn } # ecs.tf
        }
      },
      {
        Sid      = "PassKbTaskRoles"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = [aws_iam_role.ecs_task_execution.arn, aws_iam_role.ecs_task.arn] # ecs.tf
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      }
    ]
  })
}

# ── Schedule ─────────────────────────────────────────────────────────────
# `task_definition_arn` is deliberately the REVISION-LESS ARN (family only,
# no trailing `:N`) — per AWS's own EventBridge Scheduler + ECS RunTask
# behavior, this resolves to the latest ACTIVE revision at invocation time,
# so `.github/workflows/deploy-runners.yml` registering a new revision after
# every image push is picked up automatically with NO schedule update. Using
# `aws_ecs_task_definition.kb_runner.arn` instead (Terraform's own
# `revision`-suffixed ARN) would pin the schedule to whatever revision this
# PR's `terraform apply` happens to create and never move again — verify
# this at `terraform plan`/apply time; if AWS rejects a revision-less ARN
# here, fall back to `.arn` and have deploy-runners.yml additionally call
# `aws scheduler update-schedule` with the new revision ARN.

resource "aws_scheduler_schedule" "kb_runner" {
  name                = "${var.project_name}-kb-runner"
  schedule_expression = var.kb_runner_schedule_expression
  state               = "ENABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.main.arn # ecs.tf
    role_arn = aws_iam_role.kb_scheduler.arn

    ecs_parameters {
      task_definition_arn = "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task-definition/${aws_ecs_task_definition.kb_runner.family}"
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = aws_subnet.private[*].id # vpc.tf
        security_groups  = [aws_security_group.kb_runner.id]
        assign_public_ip = false
      }
    }

    retry_policy {
      maximum_retry_attempts = 1
    }
  }
}

# ── CloudWatch — failure alarm ──────────────────────────────────────────
# Heuristic proxy (log-pattern metric filter), not a precise ECS exit-code
# signal: matches an ERROR/Exception/unhandled-Traceback line in the
# task's own stdout/stderr. This is simple, needs no extra Lambda/EventBridge
# plumbing, and catches both a logged `logger.error(...)` and an unhandled
# Python exception's traceback (which is what a crash from
# run_kb_pipeline.py's NotImplementedError guard, or any future real-pipeline
# failure, would emit). A more PRECISE signal is possible (an EventBridge
# rule on "ECS Task State Change" events filtered to this task family +
# non-zero container exitCode, routed straight to the SNS topic) but adds a
# second alerting path for a draft that's not yet applied — noted here as a
# documented follow-up, not implemented in this PR.

resource "aws_cloudwatch_log_metric_filter" "kb_runner_errors" {
  name           = "${var.project_name}-kb-runner-errors"
  log_group_name = aws_cloudwatch_log_group.kb_runner.name
  pattern        = "?ERROR ?Error ?Traceback"

  metric_transformation {
    name          = "KbRunnerErrorCount"
    namespace     = "Archimedes/KbRunner"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "kb_runner_failed" {
  alarm_name          = "archimedes-kb-runner-failed"
  alarm_description   = "kb-runner scheduled Fargate task logged an ERROR/Exception/Traceback in its last run — heuristic proxy for a failed pipeline invocation (see infra/kb_runner.tf for a more precise EventBridge-based alternative)."
  namespace           = "Archimedes/KbRunner"
  metric_name         = "KbRunnerErrorCount"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  # kb-runner is a batch job on the schedule below (default: daily), not a
  # continuous loop — a short evaluation window would sit permanently
  # INSUFFICIENT_DATA between runs. An hourly window with notBreaching on
  # missing data means the alarm only evaluates (and can only fire) in the
  # hour a run's error output actually lands.
  period             = 3600
  evaluation_periods = 1
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn] # cloudwatch.tf's existing SNS topic
  ok_actions         = [aws_sns_topic.alerts.arn]
  tags               = { Project = var.project_name }
}

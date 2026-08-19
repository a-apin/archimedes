# ── Runner EC2 — oracle + agent, relocated off the stranded backend box ────
#
# Issue #1065 (execution checklist) / #1043 (parent). DRAFT — Dan applies
# this himself POST-T3.2 (`terraform plan` is the real gate; see the PR body
# for the full caveat list). The `oracle_runner` / `agent_runner`
# docker-compose services (docker-compose.yml lines ~122-198) were EXCLUDED
# from the ECS Fargate cutover (ecs.tf's file header, "KNOWN GAP #4") because
# they are funds-adjacent, exactly-once singletons — services/runner_lease.py
# is the authoritative app-layer Redis lease control, and duplicating either
# process (an ASG, a Fargate service with desired_count > 1, or two live
# copies during a bad deploy) risks a double-signed on-chain tx. Decision #1
# in #1065: a SINGLE dedicated `aws_instance`, never an ASG.
#
# Placement: the PRIVATE subnets (same as the ECS backend task, Aurora,
# ElastiCache — aws_subnet.private, vpc.tf) rather than a new SG surface on
# Aurora/ElastiCache. Both aws_security_group.aurora (aurora.tf) and
# aws_security_group.redis (elasticache.tf) already have an ingress rule for
# "Postgres/Redis from private subnets" (10.0.10.0/24, 10.0.11.0/24) — so
# this instance reaches both with ZERO edits to those other-lane files,
# mirroring the same "don't touch aurora.tf/elasticache.tf, use what's
# already open to the private subnet CIDRs" reasoning asg.tf documents for
# its own (unused) backend_asg tier.
#
# No inbound SSH — admin access is SSM Session Manager only (same posture as
# main.tf's aws_security_group.archimedes). Outbound is unrestricted
# (0.0.0.0/0) via the existing fck-nat instances (vpc.tf): ECR, Arc RPC,
# Aurora, ElastiCache, SSM, and CloudWatch Logs (for the docker awslogs
# driver) all go out that path.

# ── Security group ──────────────────────────────────────────────────────

resource "aws_security_group" "runner" {
  name        = "${var.project_name}-runner-sg"
  description = "Oracle+agent runner EC2 - no inbound (SSM Session Manager only)"
  vpc_id      = aws_vpc.main.id

  # No ingress block at all — SSM Session Manager needs no inbound port
  # (outbound HTTPS to the SSM/EC2Messages/SSMMessages VPC endpoints or, as
  # here, out through the NAT instances, is sufficient).

  egress {
    description = "All outbound (ECR, Arc RPC, Aurora, ElastiCache, SSM, CloudWatch Logs)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-runner-sg"
    Project = var.project_name
  }
}

# ── IAM — instance role ─────────────────────────────────────────────────
# Deliberately a NEW, separate role from aws_iam_role.ec2 (ec2_iam.tf, the
# main backend box's role) rather than reusing it: this is a dedicated
# instance per #1065 decision #1, and least-privilege scoping is cleaner
# with its own role (e.g. it needs ECR pull + a NEW log group; it does NOT
# need Bedrock invoke — neither oracle_runner.py nor agent_runner.py imports
# any LLM/Bedrock backend, verified by grep — so that grant is deliberately
# omitted here, unlike ec2_iam.tf's ec2_bedrock_invoke / ecs.tf's
# ecs_task_bedrock_invoke).

resource "aws_iam_role" "runner" {
  name = "archimedes-runner-ec2-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = { Project = var.project_name }
}

# SSM agent registration + Session Manager (no inbound port needed).
resource "aws_iam_role_policy_attachment" "runner_ssm_core" {
  role       = aws_iam_role.runner.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Read /archimedes/prod/* secrets at every (re)start — mirrors
# ec2_iam.tf's ec2_ssm_params exactly (same prefix, same KMS-via-SSM
# condition), for the SAME reason: fetch-secrets.sh (runner-user-data.sh)
# is this box's equivalent of services/secrets_service.load_ssm_secrets(),
# just invoked from a shell script instead of at Python import time (neither
# oracle_runner.py nor agent_runner.py calls load_ssm_secrets() itself — they
# read os.environ directly, e.g. chain/executor.py's CIRCLE_API_KEY /
# ARC_AGENT_PRIVATE_KEY / WALLET_ADDRESS reads — so the env must already be
# populated before `docker run`, which is exactly what fetch-secrets.sh
# + `--env-file` do).
resource "aws_iam_role_policy" "runner_ssm_params" {
  name = "archimedes-runner-ssm-parameter-read"
  role = aws_iam_role.runner.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadAppSecrets"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        # BOTH ARNs are required. ssm:GetParametersByPath authorizes against the PATH
        # itself (".../parameter/archimedes/prod"), which the "/*" child pattern does
        # NOT match — granting only the child pattern yields:
        #   AccessDeniedException ... not authorized to perform: ssm:GetParametersByPath
        #   on resource: arn:aws:ssm:us-east-1:<acct>:parameter/archimedes/prod
        # The child ARN is still needed for GetParameter/GetParameters on individual keys.
        Resource = [
          "arn:aws:ssm:*:*:parameter/archimedes/prod",
          "arn:aws:ssm:*:*:parameter/archimedes/prod/*",
        ]
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

# ECR pull — GetAuthorizationToken is account-level (Resource "*" is the only
# valid scope per AWS's own policy grammar for that action); the actual pull
# actions are scoped to the one repository this box ever pulls from. This is
# a real gap the main backend box's role (ec2_iam.tf) also has today per
# deploy.yml's own header comment ("Box-side prerequisite ... NOT applied by
# this PR — tracked in #1039 chunk 2") — not fixed here (out of scope for
# this PR, which only owns the NEW runner role), but the pattern below is
# exactly what that box will eventually need too.
resource "aws_iam_role_policy" "runner_ecr_pull" {
  name = "archimedes-runner-ecr-pull"
  role = aws_iam_role.runner.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid    = "EcrPullBackendImage"
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability"
        ]
        Resource = aws_ecr_repository.backend.arn
      }
    ]
  })
}

# CloudWatch Logs write for the docker `awslogs` log driver (runner-user-data.sh's
# systemd units) — scoped to the new /archimedes/runners log group only.
# awslogs-create-group is NOT set (defaults false) since the log group is
# Terraform-managed below, so logs:CreateLogGroup is deliberately not granted
# (least privilege, mirrors ecs_task_exec_command's own scoped-stream-only
# pattern in ecs.tf).
resource "aws_iam_role_policy" "runner_logs" {
  name = "archimedes-runner-logs-write"
  role = aws_iam_role.runner.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunnerLogStreamWrite"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = [
          aws_cloudwatch_log_group.runners.arn,
          "${aws_cloudwatch_log_group.runners.arn}:*"
        ]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "runner" {
  name = "archimedes-runner-ec2-profile"
  role = aws_iam_role.runner.name
}

# ── CloudWatch log group ────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "runners" {
  name              = "/archimedes/runners"
  retention_in_days = 90 # matches aws_cloudwatch_log_group.app / .nginx (cloudwatch.tf)
  tags              = { Project = var.project_name }
}

# ── EC2 instance ─────────────────────────────────────────────────────────

resource "aws_instance" "runner" {
  ami                    = data.aws_ami.ubuntu.id # reuses main.tf's Ubuntu 24.04 LTS data source
  instance_type          = var.runner_instance_type
  subnet_id              = aws_subnet.private[0].id
  vpc_security_group_ids = [aws_security_group.runner.id]
  iam_instance_profile   = aws_iam_instance_profile.runner.name
  # No key_name: SSM Session Manager only, no SSH surface at all on this box
  # (main.tf's single EC2 still carries an SSH key pair for legacy/rollback
  # reasons; this new box has no such history to preserve).

  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true # same posture the decommissioned app box's root volume carried (main.tf, removed 2026-08-19)
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only, same as asg.tf's launch template
    http_put_response_hop_limit = 2
  }

  # base64gzip, NOT plain user_data. EC2 caps user-data at 16384 bytes BEFORE
  # base64 encoding, and the rendered script had grown to 16383 raw — one
  # comment away from the ceiling. It duly went over, and `terraform plan`
  # then fails REPO-WIDE with "expected length of user_data to be in the range
  # (0 - 16384)", blocking every unrelated apply. cloud-init detects the gzip
  # magic bytes and decompresses automatically, which buys ~4x headroom and
  # removes the whole class of failure. See the size guard in
  # scripts/check-user-data-size.sh, wired into CI.
  #
  # user_data_base64 is in ignore_changes alongside user_data below: this is a
  # bootstrap-only, funds-adjacent singleton that must never be replaced to
  # pick up a script edit.
  user_data_base64 = base64gzip(templatefile("${path.module}/runner-user-data.sh", {
    ecr_registry      = "${aws_ecr_repository.backend.repository_url}"
    ecr_registry_host = split("/", aws_ecr_repository.backend.repository_url)[0]
    aws_region        = var.aws_region
    log_group_name    = aws_cloudwatch_log_group.runners.name
  }))

  tags = {
    Name    = "${var.project_name}-runner"
    Project = var.project_name
  }

  lifecycle {
    # Same rationale as aws_instance.nat's own ignore_changes = [ami]
    # (vpc.tf): an unrelated `terraform apply` picking up a newer Ubuntu AMI,
    # or a user-data.sh edit that's bootstrap-only (runs once at first boot),
    # must not silently replace/reboot this live funds-adjacent singleton.
    ignore_changes = [ami, user_data, user_data_base64]
  }
}

# ── CloudWatch alarm ─────────────────────────────────────────────────────
# EC2 instance/system status check failed — mirrors cloudwatch.tf's
# ec2_status_check_failed alarm exactly (same thresholds/periods), scoped to
# this instance. Named per #1065 decision (exact literal name specified in
# the issue).

resource "aws_cloudwatch_metric_alarm" "runner_ec2_status_check_failed" {
  alarm_name          = "archimedes-runner-ec2-status-check-failed"
  alarm_description   = "Oracle+agent runner EC2 instance/system status check failed — the funds-adjacent singleton runners are down (no oracle price pushes, no agent rebalances) until this recovers."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 60
  evaluation_periods  = 3
  dimensions          = { InstanceId = aws_instance.runner.id }
  alarm_actions       = [aws_sns_topic.alerts.arn] # cloudwatch.tf's existing SNS topic
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "breaching"
  tags                = { Project = var.project_name }
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "domain_name" {
  description = "Primary domain for the stack (ACM cert + Route 53 zone + CloudFront/ALB aliases). The hosted zone must already exist — auto-created when the domain is registered via Route 53 Domains."
  type        = string
  default     = "archimedes-arc.com"
}

variable "instance_type" {
  description = "EC2 instance type. t3.medium (4 GB) fixes the t3.small docker-build OOM (#439)."
  type        = string
  default     = "t3.medium"
}

variable "key_name" {
  description = "Name for the SSH key pair"
  type        = string
  default     = "archimedes-deploy-key"
}

variable "aurora_master_password" {
  description = "Master password for Aurora PostgreSQL. Set via TF_VAR_aurora_master_password env var."
  type        = string
  sensitive   = true
}

variable "project_name" {
  description = "Project name for tagging"
  type        = string
  default     = "archimedes"
}

variable "repo_url" {
  description = "GitHub repo HTTPS URL for cloning on the instance"
  type        = string
  default     = "https://github.com/a-apin/archimedes.git"
}

# AMI for the backend auto-scaling group (issue #155, OPTIONAL virality tier).
# Bake via infra/scripts/bake-backend-ami.sh, then set this to the resulting
# AMI id (or pass TF_VAR_backend_ami_id). Empty default keeps the var present
# without forcing a value when the ASG is not being applied. The launch
# template / ASG in asg.tf only become real on `terraform apply` — a plan with
# an empty value will simply error on the launch template until an AMI is set,
# which is the intended "supply the AMI to enable the tier" gate.
variable "backend_ami_id" {
  description = "Custom backend AMI id for the auto-scaling group launch template (issue #155). Set after baking via infra/scripts/bake-backend-ami.sh."
  type        = string
  default     = ""
}

# ── ECS Fargate (issue #1039) ──────────────────────────────────────────────

variable "ecs_backend_cpu" {
  description = "Fargate task-level vCPU units for the archimedes-backend task (nginx + backend containers, one task). 1024 = 1 vCPU. Must be a valid Fargate cpu/memory pairing — see https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-cpu-memory-error.html."
  type        = string
  default     = "1024"
}

variable "ecs_backend_memory" {
  description = "Fargate task-level memory (MiB) for the archimedes-backend task. Must pair validly with ecs_backend_cpu (1024 cpu allows 2048-8192 in 1024 steps)."
  type        = string
  default     = "3072"
}

variable "ecs_service_desired_count" {
  description = "Steady-state desired task count for the archimedes-backend ECS service. Terraform sets this once at bootstrap; day-to-day changes (CPU autoscaling, CI deploys) are ignored via lifecycle.ignore_changes on the service (see ecs.tf) so they're never reverted by a later apply."
  type        = number
  default     = 1
}

variable "ecs_service_min_count" {
  description = "Application Auto Scaling floor for the archimedes-backend service."
  type        = number
  default     = 1
}

variable "ecs_service_max_count" {
  description = "Application Auto Scaling ceiling for the archimedes-backend service. 4 mirrors the cost cap already used by the optional EC2 ASG tier (asg.tf) for this single-user, ~0.07 req/s workload."
  type        = number
  default     = 4
}

variable "ecs_autoscale_cpu_target" {
  description = "Target average CPU utilization (%) for the archimedes-backend service's target-tracking autoscaling policy."
  type        = number
  default     = 60
}

variable "backend_image_tag" {
  description = "Image tag Terraform registers in the initial archimedes-backend/archimedes-nginx task-definition revision (bootstrap only). Real deploys after that are CI registering a new task-definition revision (commit-SHA tag) and calling `aws ecs update-service --force-new-deployment` directly, NOT `terraform apply` — the service ignores task_definition/desired_count drift (see ecs.tf) so those out-of-band deploys are never reverted by a later plan/apply."
  type        = string
  default     = "latest"
}

variable "google_oauth_enabled" {
  description = "Inject Google OAuth client ID/secret from SSM into Better Auth. Enable only after both /archimedes/prod/GOOGLE_CLIENT_* parameters exist."
  type        = bool
  default     = false
}

variable "github_oauth_enabled" {
  description = "Inject GitHub OAuth client ID/secret from SSM into Better Auth. Enable only after both /archimedes/prod/GITHUB_CLIENT_* parameters exist."
  type        = bool
  default     = false
}

# ── Config consolidation (issue #1039 P5) ──────────────────────────────────
# Public wallet addresses (not secrets — no private key material), but
# operator-specific and NOT baked into the image or a box `.env` file. Set at
# `terraform apply` time (TF_VAR_platform_admin_wallets /
# TF_VAR_archimedes_treasury_wallet) so they land as first-class, IaC-tracked
# ECS task-definition environment values — the same "config lives in one
# place, not a box file" goal SSM SecureStrings serve for the actual secrets
# below. Empty defaults keep both features off (no admin-wallet bypass, no
# marketplace publish) until Dan supplies real values, matching the
# ARCHIMEDES_TREASURY_WALLET default in `.env.example`.

variable "platform_admin_wallets" {
  description = "Space/comma-separated wallet addresses allowed to publish `is_example` strategies to the marketplace (backend/archimedes/models/strategy_generators.py:wallet_can_publish; issue #1037). Public addresses, not secrets."
  type        = string
  default     = ""
}

variable "archimedes_treasury_wallet" {
  description = "Platform revenue-share wallet address (marketplace publish 10% split, PR #958). Public address, not a secret. Publishing 503s (backend/archimedes/api/marketplace_routes.py) until this is set."
  type        = string
  default     = ""
}

variable "generation_payment_recipient" {
  description = "Platform wallet address that receives x402 generation payments (flip-list #834). Public address, not a secret. Defaults to the platform generation-revenue DCW (Circle Console, wallet ID af3e1cf6-76a3-55db-911a-b356860058e4) so a terraform apply without the TF_VAR cannot silently un-configure the paywall; override via TF_VAR_generation_payment_recipient only to rotate the wallet."
  type        = string
  default     = "0xffa7abba5f17cb8471ebf150bf808bd6fb8856c1"
}

# ── Runner relocation (issue #1065 / #1043) ─────────────────────────────────
# Draft IaC — Dan applies POST-T3.2. See infra/runner_ec2.tf, infra/kb_runner.tf,
# infra/efs.tf, and the PR body for the full architecture + caveats.

variable "runner_instance_type" {
  description = "EC2 instance type for the dedicated oracle+agent runner box (issue #1065 decision #1 — a single instance, never an ASG). t3.small is generous headroom for two lightweight async Python loops; bump if the agent's regime/backtest compute proves heavier in practice."
  type        = string
  default     = "t3.small"
}

variable "kb_runner_cpu" {
  description = "Fargate task-level vCPU units for the scheduled kb-runner task (infra/kb_runner.tf). 1024 = 1 vCPU. Sized for the current skip-mode default (KB_PIPELINE_ENABLED unset) — bump substantially (and add GPU-appropriate compute, out of scope for Fargate) when the real ~6 GB SPECTER2/REBEL/SciSpacy pipeline is enabled."
  type        = string
  default     = "1024"
}

variable "kb_runner_memory" {
  description = "Fargate task-level memory (MiB) for the scheduled kb-runner task. Must pair validly with kb_runner_cpu — see https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-cpu-memory-error.html."
  type        = string
  default     = "4096"
}

variable "kb_runner_schedule_expression" {
  description = "EventBridge Scheduler rate/cron expression for the kb-runner scheduled Fargate task (infra/kb_runner.tf's aws_scheduler_schedule). Daily is a conservative default for a batch job that currently no-ops to a 'skipped' manifest.json (KB_PIPELINE_ENABLED unset) — tighten or loosen once the real pipeline is live and its actual runtime/cost is known."
  type        = string
  default     = "rate(1 day)"
}

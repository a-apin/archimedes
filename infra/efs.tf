# ── EFS — shared KB corpus-artifact storage (backend ⇄ kb-runner) ──────────
#
# Issue #1065 decision #3 (drafted default: EFS, not S3 — "zero app-code
# change; needs a companion mount added to ecs.tf's backend task"). Replaces
# docker-compose.yml's single named volume (`archimedes-corpus-artifact`,
# mounted read-write at BOTH `backend`'s `/app/data/corpus-artifact` AND
# `kb-runner`'s `/srv/corpus-artifact` — see docker-compose.yml lines
# ~246-247 and ~289-291) with ONE EFS access point mounted into BOTH Fargate
# task definitions at those same two container paths. backend/Dockerfile
# already pre-creates and chowns both paths to the nonroot (uid 1001) user
# ("Paths mirror docker-compose.yml: corpus-artifact is mounted at both
# locations") — the access point's POSIX user below matches that uid/gid so
# neither container hits an EFS permission error on first write.
#
# Companion change (this PR): ecs.tf's `aws_ecs_task_definition.backend` gets
# a `volume` block + `mountPoints` entry added for the "backend" container —
# see that file's diff. That's the ONE additive, in-place update to an
# EXISTING resource this PR makes (new task-definition revision; the
# aws_ecs_service.backend service itself is untouched and keeps
# lifecycle.ignore_changes = [task_definition], so this revision does NOT
# auto-roll — a human/CI still has to point the service at it, same as any
# other deploy).

# ── Security group — NFS 2049 from the backend + kb-runner ECS tasks only ──

resource "aws_security_group" "efs" {
  name        = "${var.project_name}-efs-sg"
  description = "EFS corpus-artifact - NFS 2049 from backend + kb-runner Fargate tasks only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "NFS from the backend ECS task"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_backend.id] # ecs.tf
  }

  ingress {
    description     = "NFS from the kb-runner scheduled Fargate task"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.kb_runner.id] # kb_runner.tf
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-efs-sg"
    Project = var.project_name
  }
}

# ── File system ─────────────────────────────────────────────────────────

resource "aws_efs_file_system" "corpus_artifact" {
  creation_token = "${var.project_name}-corpus-artifact"
  encrypted      = true

  # Bursting is the standard default and fine for this workload (small
  # JSON/JSONL/npy artifacts, infrequent kb-runner writes, occasional
  # backend reads via /api/corpus/*) — no provisioned throughput needed.

  tags = {
    Name    = "${var.project_name}-corpus-artifact"
    Project = var.project_name
  }
}

resource "aws_efs_mount_target" "corpus_artifact" {
  count           = 2
  file_system_id  = aws_efs_file_system.corpus_artifact.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs.id]
}

# Access point enforces a fixed POSIX identity + a scoped root directory —
# uid/gid 1001 matches backend/Dockerfile's `useradd --uid 1001 nonroot`
# exactly, so both the backend and kb-runner containers (both run as
# `nonroot`) can read/write without a permission error, and neither task
# needs `elasticfilesystem:ClientRootAccess` (root bypass) in its IAM policy.
resource "aws_efs_access_point" "corpus_artifact" {
  file_system_id = aws_efs_file_system.corpus_artifact.id

  posix_user {
    uid = 1001
    gid = 1001
  }

  root_directory {
    path = "/corpus-artifact"
    creation_info {
      owner_uid   = 1001
      owner_gid   = 1001
      permissions = "0775"
    }
  }

  tags = {
    Name    = "${var.project_name}-corpus-artifact-ap"
    Project = var.project_name
  }
}

# ── IAM — EFS client mount/write, added to the EXISTING ECS task role ──────
# aws_iam_role.ecs_task (ecs.tf) is shared by the backend service task, the
# one-off migrate task (ecs_migrate.tf), and — as of this PR — the new
# kb-runner task (kb_runner.tf). One additive inline policy covers all three;
# only backend + kb-runner actually mount the volume, migrate is unaffected
# (an unused grant, not a functional change for it). Scoped to exactly this
# one access point via the AccessPointArn condition, per AWS's documented
# least-privilege pattern for EFS + ECS IAM authorization.
resource "aws_iam_role_policy" "ecs_task_efs_access" {
  name = "archimedes-ecs-task-efs-access"
  role = aws_iam_role.ecs_task.id # ecs.tf
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EfsClientAccessCorpusArtifact"
        Effect   = "Allow"
        Action   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
        Resource = aws_efs_file_system.corpus_artifact.arn
        Condition = {
          StringEquals = { "elasticfilesystem:AccessPointArn" = aws_efs_access_point.corpus_artifact.arn }
        }
      }
    ]
  })
}

# ── ECR — private image repos for the CI-build → Fargate-pull pipeline ────────
#
# Issue #1039 (P2 — foundation). CI (.github/workflows/deploy.yml, `build-and-push`
# job, landed in build-chunk 1) already pushes to these exact repo names —
# `archimedes-backend` (shared by backend/oracle/agent/kb-runner, which all run
# the same image with different entrypoints — see docker-compose.yml) and
# `archimedes-nginx`. Until this file is applied, that job's own
# `aws ecr describe-repositories` preflight check fails closed with an explicit
# `::error::` pointing back here — this IS that dependency landing.
#
# CI PUSH PERMISSIONS: already covered, no new IAM needed. The out-of-band
# `archimedes-github-deploy` OIDC role (infra/scripts/setup-github-oidc.sh,
# NOT Terraform-managed by design — see that script's own comments) already
# carries an `EcrPush` statement scoped to `repository/archimedes*`, which
# matches both repo names below. Verify with:
#   aws iam get-role-policy --role-name archimedes-github-deploy --policy-name archimedes-deploy

resource "aws_ecr_repository" "backend" {
  name                 = "archimedes-backend"
  image_tag_mutability = "MUTABLE" # CI re-pushes the `latest` tag on every deploy (plus an immutable commit-SHA tag)

  image_scanning_configuration {
    scan_on_push = true # security ships with the product, not after (CLAUDE.md #6)
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Project = var.project_name
    Purpose = "backend + oracle + agent + kb-runner (one image, different CMD overrides)"
  }
}

resource "aws_ecr_repository" "nginx" {
  name                 = "archimedes-nginx"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Project = var.project_name
    Purpose = "nginx reverse proxy + built React/Vite bundle"
  }
}

# ── Lifecycle policies — expire untagged, keep last N tagged ─────────────────
# Rule 1 (higher priority, evaluated first): untagged images are dangling
# manifests from interrupted/superseded pushes — never referenced by a running
# task, safe to expire fast.
# Rule 2: cap tagged-image retention so ECR storage cost doesn't grow
# unbounded across years of commit-SHA-tagged pushes. `tagStatus = "any"` +
# `imageCountMoreThan` keeps the N most-recently-pushed images regardless of
# tag — since `latest` is always the most recent push, it's always inside the
# retained window, so this can never accidentally expire the tag a fresh
# `docker compose pull` (box interim) or a fresh ECS deploy would resolve.

locals {
  ecr_keep_last_n_images = 15
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep only the last ${local.ecr_keep_last_n_images} tagged images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = local.ecr_keep_last_n_images
        }
        action = { type = "expire" }
      }
    ]
  })
}

resource "aws_ecr_lifecycle_policy" "nginx" {
  repository = aws_ecr_repository.nginx.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep only the last ${local.ecr_keep_last_n_images} tagged images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = local.ecr_keep_last_n_images
        }
        action = { type = "expire" }
      }
    ]
  })
}

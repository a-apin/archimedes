output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.archimedes.id
}

output "public_ip" {
  description = "Public IP address of the EC2 instance"
  value       = aws_instance.archimedes.public_ip
}

output "public_dns" {
  description = "Public DNS name of the EC2 instance"
  value       = aws_instance.archimedes.public_dns
}

output "ssh_command" {
  description = "SSH command to connect"
  value       = "ssh -i infra/${var.key_name}.pem ubuntu@${aws_instance.archimedes.public_ip}"
}

output "api_url" {
  description = "Backend API URL"
  value       = "http://${aws_instance.archimedes.public_ip}:8000"
}

output "private_key_path" {
  description = "Path to the SSH private key"
  value       = local_sensitive_file.private_key.filename
}

output "ssh_private_key" {
  description = "SSH private key (for GitHub Actions secret)"
  value       = tls_private_key.deploy.private_key_openssh
  sensitive   = true
}

# ── New VPC infrastructure outputs ────────────────────────────

output "vpc_id" {
  description = "New VPC ID"
  value       = aws_vpc.main.id
}

output "aurora_endpoint" {
  description = "Aurora cluster endpoint (for DATABASE_URL)"
  value       = aws_rds_cluster.main.endpoint
}

output "aurora_reader_endpoint" {
  description = "Aurora reader endpoint"
  value       = aws_rds_cluster.main.reader_endpoint
}

output "redis_endpoint" {
  description = "ElastiCache Redis endpoint (for REDIS_URL)"
  value       = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "database_url" {
  description = <<-DESC
    Full DATABASE_URL for backend .env. STATE-SENSITIVE: this output stores
    the master password in Terraform state (which lives in the S3 backend).
    The bucket policy restricts access to the AWS account principal and TLS-only,
    but the password is still in the state file. Recommended pattern going forward:
    backend fetches the password from AWS Secrets Manager / SSM Parameter Store at
    runtime and constructs the URL from `aurora_endpoint` + password — that way
    the secret never lands in Terraform state at all. Tracked as a follow-up.
  DESC
  value       = "postgresql://archimedes:${var.aurora_master_password}@${aws_rds_cluster.main.endpoint}:5432/archimedes"
  sensitive   = true
}

output "redis_url" {
  description = "Full REDIS_URL for backend .env"
  value       = "rediss://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0"
}

output "alb_dns_name" {
  description = "ALB DNS name (for Route 53 ALIAS record)"
  value       = aws_lb.main.dns_name
}

output "alb_zone_id" {
  description = "ALB hosted zone ID (for Route 53 ALIAS record)"
  value       = aws_lb.main.zone_id
}

output "acm_certificate_arn" {
  description = "ACM certificate ARN"
  value       = aws_acm_certificate.main.arn
}

output "waf_web_acl_arn" {
  description = "WAF Web ACL ARN"
  value       = aws_wafv2_web_acl.main.arn
}

# ── CloudFront + ASG (virality tier, issue #155) ──────────────

output "cloudfront_domain_name" {
  description = "CloudFront distribution domain (the *.cloudfront.net name behind archimedes-arc.app)"
  value       = aws_cloudfront_distribution.main.domain_name
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution id (for cache invalidations)"
  value       = aws_cloudfront_distribution.main.id
}

output "backend_asg_name" {
  description = "Backend auto-scaling group name (null unless the optional ASG tier is enabled via backend_ami_id)"
  value       = one(aws_autoscaling_group.backend[*].name)
}

# ── ECS Fargate outputs (issue #1039) ─────────────────────────────────────

output "ecr_backend_repository_url" {
  description = "ECR repository URL for the archimedes-backend image (backend + oracle + agent + kb-runner)"
  value       = aws_ecr_repository.backend.repository_url
}

output "ecr_nginx_repository_url" {
  description = "ECR repository URL for the archimedes-nginx image"
  value       = aws_ecr_repository.nginx.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "ecs_cluster_arn" {
  description = "ECS cluster ARN"
  value       = aws_ecs_cluster.main.arn
}

output "ecs_service_name" {
  description = "ECS service name (behind the existing archimedes-backend-tg target group)"
  value       = aws_ecs_service.backend.name
}

output "ecs_task_definition_family" {
  description = "ECS task definition family (register new revisions against this family for CI deploys)"
  value       = aws_ecs_task_definition.backend.family
}

output "ecs_task_execution_role_arn" {
  description = "ECS task execution role ARN (image pull, log shipping, secrets resolution)"
  value       = aws_iam_role.ecs_task_execution.arn
}

output "ecs_task_role_arn" {
  description = "ECS task role ARN (application runtime permissions — SSM read, Bedrock invoke, ECS Exec channel)"
  value       = aws_iam_role.ecs_task.arn
}

output "ecs_exec_shell_command" {
  description = "Template for shelling into a running backend task via ECS Exec (fill in the task id from `aws ecs list-tasks --cluster <ecs_cluster_name> --service-name <ecs_service_name>`)"
  value       = "aws ecs execute-command --cluster ${aws_ecs_cluster.main.name} --task <task-id> --container backend --interactive --command \"/bin/sh\""
}

output "ecs_migrate_task_definition_family" {
  description = "ECS task definition family for the one-off Alembic migrate task (infra/ecs_migrate.tf) — distinct from ecs_task_definition_family (the SERVICE family, backend+nginx). .github/workflows/deploy.yml's migrate job's ECS_MIGRATE_TASK_FAMILY literal must match this value."
  value       = aws_ecs_task_definition.migrate.family
}

# Static awsvpcConfiguration for `aws ecs run-task --network-configuration`
# against the migrate task family (issue #1039 B2). Sourced from aws_subnet.private
# (vpc.tf) + aws_security_group.ecs_backend (ecs.tf) — BOTH exist independently of
# aws_ecs_service.backend, unlike the `aws ecs describe-services archimedes-backend`
# call deploy.yml's migrate job previously used, which by definition can't resolve
# until the service (which the migrate task must run BEFORE) already exists. Run
# `terraform output -raw ecs_migrate_network_configuration` after applying
# aws_subnet.private + aws_security_group.ecs_backend and paste the result into
# .github/workflows/deploy.yml's ECS_MIGRATE_NETWORK_CONFIGURATION literal — same
# "CI can't run terraform output" literal-constant pattern as ECS_CLUSTER /
# ECS_MIGRATE_TASK_FAMILY there.
output "ecs_migrate_network_configuration" {
  description = "Static awsvpcConfiguration JSON for `aws ecs run-task --network-configuration` against the migrate task family. Copy into .github/workflows/deploy.yml's ECS_MIGRATE_NETWORK_CONFIGURATION literal (issue #1039 B2)."
  value = jsonencode({
    awsvpcConfiguration = {
      subnets        = aws_subnet.private[*].id
      securityGroups = [aws_security_group.ecs_backend.id]
      assignPublicIp = "DISABLED"
    }
  })
}

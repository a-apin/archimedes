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

# VPC/Aurora/ElastiCache/ALB/WAF outputs removed after Cut D teardown.
# Definitions preserved in infra/modules/security-architecture/ for re-apply.

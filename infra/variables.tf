variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-2"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.small"
}

variable "key_name" {
  description = "Name for the SSH key pair"
  type        = string
  default     = "archimedes-deploy-key"
}

# aurora_master_password moved to infra/modules/security-architecture/
# Uncomment when re-applying the full security architecture.
# variable "aurora_master_password" {
#   description = "Master password for Aurora PostgreSQL. Set via TF_VAR_aurora_master_password env var."
#   type        = string
#   sensitive   = true
# }

variable "project_name" {
  description = "Project name for tagging"
  type        = string
  default     = "archimedes"
}

variable "repo_url" {
  description = "GitHub repo HTTPS URL for cloning on the instance"
  type        = string
  default     = "https://github.com/a-apin/archimedes-arcadia.git"
}

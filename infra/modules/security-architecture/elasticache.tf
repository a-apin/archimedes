# ── ElastiCache Redis ─────────────────────────────────────────────────
#
# Replaces the Docker Compose Redis container with a managed, encrypted
# Redis instance in the private subnet.

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.project_name}-redis-subnet"
  subnet_ids = aws_subnet.private[*].id

  tags = {
    Project = var.project_name
  }
}

resource "aws_security_group" "redis" {
  name        = "${var.project_name}-redis-sg"
  description = "ElastiCache Redis — only reachable from EC2 backend"
  vpc_id      = aws_vpc.main.id

  # Inbound: Redis from private subnets
  ingress {
    description = "Redis from private subnets"
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = ["10.0.10.0/24", "10.0.11.0/24"]
  }

  # Inbound: Redis from current EC2 (default VPC, transitional)
  ingress {
    description     = "Redis from current EC2 (transitional)"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.archimedes.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-redis-sg"
    Project = var.project_name
  }
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${var.project_name}-redis"
  description          = "Archimedes Redis — regime state, traces, job queue"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = "cache.t3.micro"

  num_cache_clusters = 1 # Single node (multi-AZ is a cost upgrade)

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  # Auth token for Redis AUTH (optional — adds another layer)
  # auth_token = var.redis_auth_token

  tags = {
    Project = var.project_name
  }
}

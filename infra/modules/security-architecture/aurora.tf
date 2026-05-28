# ── Aurora Serverless v2 (PostgreSQL) ─────────────────────────────────
#
# Replaces the Docker Compose Postgres container with a managed,
# encrypted, auto-scaling database in the private subnet.
#
# Budget: min 0.5 ACU ($~27/mo baseline), max 16 ACU (burst to ~$850/mo).
# Standard pricing (not I/O-Optimized) — our workload is read-heavy.

resource "aws_db_subnet_group" "aurora" {
  name       = "${var.project_name}-aurora-subnet"
  subnet_ids = aws_subnet.private[*].id

  tags = {
    Name    = "${var.project_name}-aurora-subnet"
    Project = var.project_name
  }
}

resource "aws_security_group" "aurora" {
  name        = "${var.project_name}-aurora-sg"
  description = "Aurora — only reachable from EC2 backend"
  vpc_id      = aws_vpc.main.id

  # Inbound: Postgres from the EC2 security group (current default VPC)
  # AND from the private subnets (for when EC2 moves to private subnet)
  ingress {
    description = "Postgres from private subnets"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["10.0.10.0/24", "10.0.11.0/24"]
  }

  ingress {
    description     = "Postgres from current EC2 (default VPC, transitional)"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.archimedes.id]
  }

  # No egress needed for a database
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-aurora-sg"
    Project = var.project_name
  }
}

resource "aws_rds_cluster" "main" {
  cluster_identifier = "${var.project_name}-aurora"
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned" # Serverless v2 uses provisioned mode + serverless_v2_scaling_configuration
  engine_version     = "16.4"

  database_name   = "archimedes"
  master_username = "archimedes"
  master_password = var.aurora_master_password

  db_subnet_group_name   = aws_db_subnet_group.aurora.name
  vpc_security_group_ids = [aws_security_group.aurora.id]

  storage_encrypted = true
  # deletion_protection = true  # Enable after migration is verified — see follow-up note in PR
  iam_database_authentication_enabled = true # enables IAM-based DB auth as alternative to password (no cost)

  # Aurora automated backups — 7 days is the standard production retention.
  # Backups are continuous; point-in-time recovery available within the window.
  backup_retention_period = 7

  serverlessv2_scaling_configuration {
    min_capacity = 0.5
    max_capacity = 16
  }

  # Skip final snapshot during development (enable for production)
  # Follow-up: flip skip_final_snapshot=false + deletion_protection=true
  # BEFORE any real user data lands in the cluster.
  skip_final_snapshot = true

  tags = {
    Project = var.project_name
  }
}

resource "aws_rds_cluster_instance" "main" {
  identifier         = "${var.project_name}-aurora-1"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version

  tags = {
    Project = var.project_name
  }
}

# ── VPC Peering (default VPC ↔ new VPC) ──────────────────────
# Needed so the current EC2 in default VPC can reach Aurora in the new VPC.
# This is transitional — removed when EC2 moves to the private subnet.

resource "aws_vpc_peering_connection" "default_to_main" {
  vpc_id      = data.aws_vpc.default.id
  peer_vpc_id = aws_vpc.main.id
  auto_accept = true

  tags = {
    Name    = "${var.project_name}-vpc-peer"
    Project = var.project_name
  }
}

# Route from default VPC to new VPC's CIDR
resource "aws_route" "default_to_main" {
  route_table_id            = data.aws_vpc.default.main_route_table_id
  destination_cidr_block    = "10.0.0.0/16"
  vpc_peering_connection_id = aws_vpc_peering_connection.default_to_main.id
}

# Route from new VPC private subnets to default VPC's CIDR
resource "aws_route" "main_to_default" {
  count                     = 2
  route_table_id            = aws_route_table.private[count.index].id
  destination_cidr_block    = "172.31.0.0/16"
  vpc_peering_connection_id = aws_vpc_peering_connection.default_to_main.id
}

# ── VPC + Networking for Archimedes Production ─────────────────────────
#
# Architecture:
#   - New VPC with /16 CIDR (10.0.0.0/16)
#   - 2 public subnets (ALB, NAT instances) in eu-west-2a + eu-west-2b
#   - 2 private subnets (EC2, Aurora, ElastiCache) in same AZs
#   - fck-nat t4g.nano instances (one per AZ) for outbound internet
#   - Internet Gateway for public subnets
#
# The EC2 instance stays in the DEFAULT VPC for this PR (env-var cutover
# only). Moving EC2 to the private subnet is a separate PR after the
# deploy pipeline is converted to SSM.

locals {
  azs = ["eu-west-2a", "eu-west-2b"]
}

# ── VPC ───────────────────────────────────────────────────────

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name    = "${var.project_name}-vpc"
    Project = var.project_name
  }
}

# ── Internet Gateway ─────────────────────────────────────────

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name    = "${var.project_name}-igw"
    Project = var.project_name
  }
}

# ── Public Subnets (ALB + NAT instances) ─────────────────────

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.${count.index}.0/24"
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name    = "${var.project_name}-public-${local.azs[count.index]}"
    Project = var.project_name
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name    = "${var.project_name}-public-rt"
    Project = var.project_name
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ── Private Subnets (EC2, Aurora, ElastiCache) ───────────────

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 10}.0/24"
  availability_zone = local.azs[count.index]

  tags = {
    Name    = "${var.project_name}-private-${local.azs[count.index]}"
    Project = var.project_name
  }
}

# Each private subnet routes outbound through its AZ's NAT instance
resource "aws_route_table" "private" {
  count  = 2
  vpc_id = aws_vpc.main.id

  route {
    cidr_block           = "0.0.0.0/0"
    network_interface_id = aws_instance.nat[count.index].primary_network_interface_id
  }

  tags = {
    Name    = "${var.project_name}-private-rt-${local.azs[count.index]}"
    Project = var.project_name
  }
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ── NAT Instances (fck-nat, t4g.nano, one per AZ) ───────────

data "aws_ami" "fck_nat" {
  most_recent = true
  owners      = ["568608671756"] # fck-nat project

  filter {
    name   = "name"
    values = ["fck-nat-al2023-*-arm64-*"]
  }
}

resource "aws_security_group" "nat" {
  name        = "${var.project_name}-nat-sg"
  description = "NAT instance — allows outbound for private subnets"
  vpc_id      = aws_vpc.main.id

  # Inbound from private subnets (all traffic)
  ingress {
    description = "Private subnets"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["10.0.10.0/24", "10.0.11.0/24"]
  }

  # Outbound — all (NAT needs to reach the internet)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-nat-sg"
    Project = var.project_name
  }
}

resource "aws_instance" "nat" {
  count                = 2
  ami                  = data.aws_ami.fck_nat.id
  instance_type        = "t4g.nano"
  subnet_id            = aws_subnet.public[count.index].id
  vpc_security_group_ids = [aws_security_group.nat.id]
  source_dest_check    = false # Required for NAT

  tags = {
    Name    = "${var.project_name}-nat-${local.azs[count.index]}"
    Project = var.project_name
  }
}

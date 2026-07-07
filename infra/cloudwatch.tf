# ─────────────────────────────────────────────────────────────────────────────
# CloudWatch monitoring — SNS alert topic, alarms, and an ops dashboard.
#
# ⚠️  AUTHORED OFFLINE — NOT yet `terraform plan`/`apply`-verified. This file was
#     written without AWS credentials in the authoring environment. Before
#     applying, run `terraform plan` from infra/ and review every resource.
#     These resources are ADDITIVE (apply creates new alarms/topic/dashboard and
#     does NOT modify or replace the existing EC2/ALB/Aurora/WAF resources), so
#     the blast radius of an apply is limited to new CloudWatch objects.
#
# Thresholds below are conservative first-cut defaults. Tune them against a few
# days of real CloudWatch baseline data — see infra/runbooks/disaster-recovery.md
# for the alarm philosophy. Nothing here is load-bearing for the app to run; it
# is purely observability + paging.
# ─────────────────────────────────────────────────────────────────────────────

variable "alarm_email" {
  description = "Email address subscribed to the CloudWatch alarm SNS topic. Leave empty to skip the subscription (alarms still fire to the topic; you can add subscribers later in the console)."
  type        = string
  default     = ""
}

# ── SNS alert topic ──────────────────────────────────────────────────────────

resource "aws_sns_topic" "alerts" {
  name = "${var.project_name}-alerts"
  tags = { Project = var.project_name }
}

# Optional email subscription. Created only when var.alarm_email is non-empty.
# AWS sends a confirmation email; the subscription is pending until confirmed.
resource "aws_sns_topic_subscription" "alerts_email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ── EC2 (application host) ───────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "ec2_cpu_high" {
  alarm_name          = "${var.project_name}-ec2-cpu-high"
  alarm_description   = "EC2 host CPU > 85% for 10 min — app host saturated."
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 85
  period              = 300
  evaluation_periods  = 2
  dimensions          = { InstanceId = aws_instance.archimedes.id }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "missing"
  tags                = { Project = var.project_name }
}

resource "aws_cloudwatch_metric_alarm" "ec2_status_check_failed" {
  alarm_name          = "${var.project_name}-ec2-status-check-failed"
  alarm_description   = "EC2 instance/system status check failed — host unhealthy."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 60
  evaluation_periods  = 3
  dimensions          = { InstanceId = aws_instance.archimedes.id }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "breaching"
  tags                = { Project = var.project_name }
}

# ── ALB (edge) ───────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "alb_5xx_high" {
  alarm_name          = "${var.project_name}-alb-target-5xx-high"
  alarm_description   = "Backend returned > 10 5xx responses in 5 min."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 10
  period              = 300
  evaluation_periods  = 1
  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.backend.arn_suffix
  }
  alarm_actions      = [aws_sns_topic.alerts.arn]
  ok_actions         = [aws_sns_topic.alerts.arn]
  treat_missing_data = "notBreaching"
  tags               = { Project = var.project_name }
}

resource "aws_cloudwatch_metric_alarm" "alb_unhealthy_hosts" {
  alarm_name          = "${var.project_name}-alb-unhealthy-hosts"
  alarm_description   = "One or more backend targets are unhealthy."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 60
  evaluation_periods  = 3
  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.backend.arn_suffix
  }
  alarm_actions      = [aws_sns_topic.alerts.arn]
  ok_actions         = [aws_sns_topic.alerts.arn]
  treat_missing_data = "breaching"
  tags               = { Project = var.project_name }
}

resource "aws_cloudwatch_metric_alarm" "alb_latency_high" {
  alarm_name          = "${var.project_name}-alb-target-latency-high"
  alarm_description   = "p95 backend response time > 2s for 10 min."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  extended_statistic  = "p95"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 2
  period              = 300
  evaluation_periods  = 2
  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.backend.arn_suffix
  }
  alarm_actions      = [aws_sns_topic.alerts.arn]
  ok_actions         = [aws_sns_topic.alerts.arn]
  treat_missing_data = "notBreaching"
  tags               = { Project = var.project_name }
}

# ── Aurora PostgreSQL ────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "aurora_cpu_high" {
  alarm_name          = "${var.project_name}-aurora-cpu-high"
  alarm_description   = "Aurora CPU > 85% for 10 min."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 85
  period              = 300
  evaluation_periods  = 2
  dimensions          = { DBClusterIdentifier = aws_rds_cluster.main.cluster_identifier }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "missing"
  tags                = { Project = var.project_name }
}

# FreeableMemory is per-instance. ~256 MB floor is a conservative paging line for
# a Serverless-v2 instance; tune to your min ACU.
resource "aws_cloudwatch_metric_alarm" "aurora_low_memory" {
  alarm_name          = "${var.project_name}-aurora-low-freeable-memory"
  alarm_description   = "Aurora freeable memory < 256 MB — risk of OOM / connection churn."
  namespace           = "AWS/RDS"
  metric_name         = "FreeableMemory"
  statistic           = "Average"
  comparison_operator = "LessThanThreshold"
  threshold           = 268435456 # 256 MiB in bytes
  period              = 300
  evaluation_periods  = 2
  dimensions          = { DBInstanceIdentifier = aws_rds_cluster_instance.main.identifier }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "missing"
  tags                = { Project = var.project_name }
}

resource "aws_cloudwatch_metric_alarm" "aurora_connections_high" {
  alarm_name          = "${var.project_name}-aurora-connections-high"
  alarm_description   = "Aurora DB connections > 80 — approaching pool/limit pressure."
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  statistic           = "Average"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  period              = 300
  evaluation_periods  = 2
  dimensions          = { DBClusterIdentifier = aws_rds_cluster.main.cluster_identifier }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "missing"
  tags                = { Project = var.project_name }
}

# ── Ops dashboard ────────────────────────────────────────────────────────────

resource "aws_cloudwatch_dashboard" "ops" {
  dashboard_name = "${var.project_name}-ops"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6,
        properties = {
          title   = "EC2 CPU",
          region  = var.aws_region,
          view    = "timeSeries",
          metrics = [["AWS/EC2", "CPUUtilization", "InstanceId", aws_instance.archimedes.id]]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title  = "ALB requests / 5xx",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.main.arn_suffix],
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.main.arn_suffix]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6,
        properties = {
          title   = "ALB target latency (p95)",
          region  = var.aws_region,
          view    = "timeSeries",
          stat    = "p95",
          metrics = [["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.main.arn_suffix, "TargetGroup", aws_lb_target_group.backend.arn_suffix]]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6,
        properties = {
          title  = "Aurora CPU / connections",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier],
            ["AWS/RDS", "DatabaseConnections", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier]
          ]
        }
      }
    ]
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# Issue #418 — Layer 1 (AWS infrastructure metrics).
#
# Adds versioned per-subsystem CloudWatch dashboards (Aurora, ElastiCache,
# VPC/NAT, EC2 backend, ALB, WAF) plus the additional alarms named in the issue
# (NAT transfer anomaly, ALB 5xx > 1% over 5 min, Aurora connections > 80%,
# Aurora ACU pinned at max, ElastiCache evictions, WAF blocked-request spike).
#
# Layer 2 (Prometheus app metrics) and Layer 3 (self-hosted Grafana) are
# SEPARATE PRs — explicitly out of scope here.
#
# All resources reference the real infra resources defined in the sibling
# infra/*.tf files (aurora.tf, elasticache.tf, alb.tf, waf.tf, vpc.tf, main.tf)
# so every widget and alarm points at a live target. The alarms reuse the
# existing aws_sns_topic.alerts topic above (no new SNS topic is created — a
# second topic would collide and split alarm routing).
#
# ⚠️  Same offline-authoring caveat as the top of this file: run `terraform plan`
#     from infra/ before applying. These are additive CloudWatch objects only.
# ─────────────────────────────────────────────────────────────────────────────

# ElastiCache CloudWatch metrics are emitted per cache node, keyed by
# CacheClusterId. For a single-node replication group the node id is
# "<replication_group_id>-001". Computed once here so widgets/alarms stay in sync
# with elasticache.tf if the node count changes.
locals {
  redis_node_id = "${aws_elasticache_replication_group.main.replication_group_id}-001"

  # WAF emits metrics in AWS/WAFV2 keyed by WebACL + Region + (per-rule) Rule.
  # Region dimension is the human region label for REGIONAL scope ACLs.
  waf_metric_name = aws_wafv2_web_acl.main.name
}

# ── Additional alarms (issue #418) ───────────────────────────────────────────

# NAT data-transfer anomaly — sustained high outbound bytes from a NAT instance
# catches both surprise bills and suspicious egress/exfiltration. fck-nat
# instances are plain EC2, so NetworkOut is the AWS/EC2 metric. Threshold is a
# conservative first cut (~5 GB / 5-min datapoint ≈ 1.1 GB/min sustained); tune
# against a baseline. One alarm per NAT instance (one per AZ).
resource "aws_cloudwatch_metric_alarm" "nat_egress_anomaly" {
  count               = length(aws_instance.nat)
  alarm_name          = "${var.project_name}-nat-egress-anomaly-${count.index}"
  alarm_description   = "NAT instance ${count.index} NetworkOut > 5 GB per 5-min datapoint for 15 min — surprise-bill / exfiltration signal."
  namespace           = "AWS/EC2"
  metric_name         = "NetworkOut"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 5368709120 # 5 GiB in bytes, per 5-min period
  period              = 300
  evaluation_periods  = 3
  dimensions          = { InstanceId = aws_instance.nat[count.index].id }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"
  tags                = { Project = var.project_name }
}

# NAT health + auto-recovery (issue #1039 N1). Mirrors the app box's own
# `ec2_status_check_failed` alarm above (same alarm-name suffix pattern,
# SNS action, tags), with two deliberate deltas from an exact mirror:
#
# 1. Metric is `StatusCheckFailed_System`, NOT the combined `StatusCheckFailed`
#    the app box alarm uses. This isn't stylistic — it's an AWS hard
#    requirement: "The recover action can be used only with
#    StatusCheckFailed_System, not with StatusCheckFailed_Instance." (AWS docs,
#    Add recover actions to Amazon CloudWatch alarms). The combined
#    StatusCheckFailed metric is Max(_System, _Instance), so an alarm on it
#    would fail to accept the ec2:recover action outright — using it here would
#    be a change that looks right but silently can't do the one thing this
#    alarm exists for. t4g.nano (the NAT instance type, vpc.tf) is in AWS's
#    supported-instance-type list for CloudWatch action based recovery.
# 2. `evaluation_periods = 2` (not 3, the app box's value) — AWS's own
#    recommendation for recover alarms specifically ("we recommend that you
#    set recover alarms to two evaluation periods of one minute each"), to
#    avoid a race condition if a reboot alarm with the same period count is
#    ever added alongside it later.
#
# BOTH actions fire on the SAME alarm_actions list: the SNS topic (so a human
# is paged the moment a NAT goes unhealthy, exactly like every other alarm in
# this file) AND the `ec2:recover` automate ARN (so AWS attempts to migrate
# the instance off failed hardware without waiting on that human) — self-heal
# and visibility are not mutually exclusive.
resource "aws_cloudwatch_metric_alarm" "nat_status_check_failed" {
  count               = length(aws_instance.nat)
  alarm_name          = "${var.project_name}-nat-status-check-failed-${count.index}"
  alarm_description   = "NAT instance ${count.index} system status check failed — host unhealthy, auto-recovering onto new hardware."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 60
  evaluation_periods  = 2
  dimensions          = { InstanceId = aws_instance.nat[count.index].id }
  alarm_actions       = [aws_sns_topic.alerts.arn, "arn:aws:automate:${var.aws_region}:ec2:recover"]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "breaching"
  tags                = { Project = var.project_name }
}

# ALB 5xx error RATE > 1% sustained 5 min. Uses a metric-math expression:
# 100 * target-5xx / request-count. This is distinct from the existing
# absolute-count alarm (alb_5xx_high) — a rate alarm catches degradation that
# scales with traffic, where a fixed count would either flap or miss it.
resource "aws_cloudwatch_metric_alarm" "alb_5xx_rate_high" {
  alarm_name          = "${var.project_name}-alb-5xx-rate-high"
  alarm_description   = "Backend 5xx rate > 1% of requests for 5 min."
  comparison_operator = "GreaterThanThreshold"
  threshold           = 1
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "error_rate"
    expression  = "100 * (m5xx / IF(reqs > 0, reqs, 1))"
    label       = "5xx error rate (%)"
    return_data = true
  }

  metric_query {
    id = "m5xx"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      stat        = "Sum"
      period      = 300
      dimensions = {
        LoadBalancer = aws_lb.main.arn_suffix
        TargetGroup  = aws_lb_target_group.backend.arn_suffix
      }
    }
  }

  metric_query {
    id = "reqs"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "RequestCount"
      stat        = "Sum"
      period      = 300
      dimensions = {
        LoadBalancer = aws_lb.main.arn_suffix
        TargetGroup  = aws_lb_target_group.backend.arn_suffix
      }
    }
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
  tags          = { Project = var.project_name }
}

# Aurora connections > 80% of a ~100-connection working ceiling for a
# Serverless-v2 instance at our ACU range. Distinct from the existing
# aurora_connections_high (absolute > 80) — kept as the issue names an
# 80%-utilization line; at our ceiling the two coincide today but this one
# documents the percentage intent for when the ceiling is tuned.
resource "aws_cloudwatch_metric_alarm" "aurora_connections_pct_high" {
  alarm_name          = "${var.project_name}-aurora-connections-pct-high"
  alarm_description   = "Aurora DB connections > 80% of working ceiling (~80 of ~100) for 10 min."
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  period              = 300
  evaluation_periods  = 2
  dimensions          = { DBClusterIdentifier = aws_rds_cluster.main.cluster_identifier }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "missing"
  tags                = { Project = var.project_name }
}

# Aurora Serverless v2 capacity pinned at the configured max (16 ACU) for
# > 10 min — the cluster cannot scale further and is a paging-grade saturation
# signal (and a cost signal). ServerlessDatabaseCapacity reports current ACUs.
resource "aws_cloudwatch_metric_alarm" "aurora_acu_max" {
  alarm_name          = "${var.project_name}-aurora-acu-at-max"
  alarm_description   = "Aurora Serverless v2 capacity pinned at max (>= 15.5 of 16 ACU) for 10 min — out of headroom."
  namespace           = "AWS/RDS"
  metric_name         = "ServerlessDatabaseCapacity"
  statistic           = "Average"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 15.5
  period              = 300
  evaluation_periods  = 2
  dimensions          = { DBClusterIdentifier = aws_rds_cluster.main.cluster_identifier }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "missing"
  tags                = { Project = var.project_name }
}

# ElastiCache evictions sustained — keys evicted under memory pressure means the
# cache is too small for the working set (regime state / job queue churn).
resource "aws_cloudwatch_metric_alarm" "redis_evictions" {
  alarm_name          = "${var.project_name}-redis-evictions"
  alarm_description   = "ElastiCache Redis evicting keys (> 100 / 5 min) for 10 min — under memory pressure."
  namespace           = "AWS/ElastiCache"
  metric_name         = "Evictions"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 100
  period              = 300
  evaluation_periods  = 2
  dimensions          = { CacheClusterId = local.redis_node_id }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"
  tags                = { Project = var.project_name }
}

# WAF blocked-request spike > 100/min (> 6000 per 5-min datapoint) — a burst of
# blocks signals an active attack/abuse wave worth eyes-on, even though the WAF
# is already mitigating it.
resource "aws_cloudwatch_metric_alarm" "waf_blocked_spike" {
  alarm_name          = "${var.project_name}-waf-blocked-spike"
  alarm_description   = "WAF blocked > 6000 requests in 5 min (> 100/min) — active attack/abuse wave."
  namespace           = "AWS/WAFV2"
  metric_name         = "BlockedRequests"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 6000
  period              = 300
  evaluation_periods  = 1
  dimensions = {
    WebACL = local.waf_metric_name
    Region = var.aws_region
    Rule   = "ALL"
  }
  alarm_actions      = [aws_sns_topic.alerts.arn]
  ok_actions         = [aws_sns_topic.alerts.arn]
  treat_missing_data = "notBreaching"
  tags               = { Project = var.project_name }
}

# ── Aurora dashboard ─────────────────────────────────────────────────────────

resource "aws_cloudwatch_dashboard" "aurora" {
  dashboard_name = "${var.project_name}-aurora"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6,
        properties = {
          title  = "Serverless capacity (ACU)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/RDS", "ServerlessDatabaseCapacity", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title  = "Connections",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/RDS", "DatabaseConnections", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6,
        properties = {
          title  = "CPU utilization (%)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6,
        properties = {
          title  = "Read/write latency p95 / p99",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/RDS", "ReadLatency", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier, { stat = "p95", label = "ReadLatency p95" }],
            ["AWS/RDS", "ReadLatency", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier, { stat = "p99", label = "ReadLatency p99" }],
            ["AWS/RDS", "WriteLatency", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier, { stat = "p95", label = "WriteLatency p95" }],
            ["AWS/RDS", "WriteLatency", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier, { stat = "p99", label = "WriteLatency p99" }]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6,
        properties = {
          title  = "IOPS (read / write)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/RDS", "ReadIOPS", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier],
            ["AWS/RDS", "WriteIOPS", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 12, width = 12, height = 6,
        properties = {
          title  = "Storage / freeable memory",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/RDS", "VolumeBytesUsed", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier, { label = "VolumeBytesUsed" }],
            ["AWS/RDS", "FreeableMemory", "DBInstanceIdentifier", aws_rds_cluster_instance.main.identifier, { label = "FreeableMemory" }]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 18, width = 12, height = 6,
        properties = {
          title  = "Deadlocks / slow-query proxy (login failures, deadlocks)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/RDS", "Deadlocks", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier, { label = "Deadlocks" }],
            ["AWS/RDS", "LoginFailures", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier, { label = "LoginFailures" }]
          ]
        }
      }
    ]
  })
}

# ── ElastiCache dashboard ────────────────────────────────────────────────────

resource "aws_cloudwatch_dashboard" "elasticache" {
  dashboard_name = "${var.project_name}-elasticache"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6,
        properties = {
          title  = "Cache hits / misses",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/ElastiCache", "CacheHits", "CacheClusterId", local.redis_node_id],
            ["AWS/ElastiCache", "CacheMisses", "CacheClusterId", local.redis_node_id]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title  = "Hit-rate (%)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            [{ expression = "100 * hits / IF((hits + misses) > 0, (hits + misses), 1)", label = "Hit-rate %", id = "hr" }],
            ["AWS/ElastiCache", "CacheHits", "CacheClusterId", local.redis_node_id, { id = "hits", visible = false }],
            ["AWS/ElastiCache", "CacheMisses", "CacheClusterId", local.redis_node_id, { id = "misses", visible = false }]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6,
        properties = {
          title  = "Memory usage (%) / bytes used",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/ElastiCache", "DatabaseMemoryUsagePercentage", "CacheClusterId", local.redis_node_id, { label = "Memory usage %" }],
            ["AWS/ElastiCache", "BytesUsedForCache", "CacheClusterId", local.redis_node_id, { label = "BytesUsedForCache" }]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6,
        properties = {
          title  = "Evictions",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/ElastiCache", "Evictions", "CacheClusterId", local.redis_node_id]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6,
        properties = {
          title  = "Current connections",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/ElastiCache", "CurrConnections", "CacheClusterId", local.redis_node_id]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 12, width = 12, height = 6,
        properties = {
          title  = "Slow-log entries / engine CPU (%)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/ElastiCache", "SlowlogLength", "CacheClusterId", local.redis_node_id, { label = "Slowlog length" }],
            ["AWS/ElastiCache", "EngineCPUUtilization", "CacheClusterId", local.redis_node_id, { label = "Engine CPU %" }]
          ]
        }
      }
    ]
  })
}

# ── VPC / NAT dashboard ──────────────────────────────────────────────────────
# NAT instances are plain EC2, so their network + CPU metrics live in AWS/EC2.
# "GB/day" is read off the NetworkOut/NetworkIn time series (Sum stat); the
# alarm above pages on the per-datapoint anomaly.

resource "aws_cloudwatch_dashboard" "vpc_nat" {
  dashboard_name = "${var.project_name}-vpc-nat"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6,
        properties = {
          title  = "NAT data transfer out (bytes / 5 min — multiply for GB/day)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            for i, nat in aws_instance.nat :
            ["AWS/EC2", "NetworkOut", "InstanceId", nat.id, { label = "NAT-${i} NetworkOut" }]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title  = "NAT data transfer in (bytes / 5 min)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            for i, nat in aws_instance.nat :
            ["AWS/EC2", "NetworkIn", "InstanceId", nat.id, { label = "NAT-${i} NetworkIn" }]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6,
        properties = {
          title  = "NAT CPU utilization (%)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            for i, nat in aws_instance.nat :
            ["AWS/EC2", "CPUUtilization", "InstanceId", nat.id, { label = "NAT-${i} CPU" }]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6,
        properties = {
          title  = "NAT network packets (out / in)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = concat(
            [for i, nat in aws_instance.nat : ["AWS/EC2", "NetworkPacketsOut", "InstanceId", nat.id, { label = "NAT-${i} pkts out" }]],
            [for i, nat in aws_instance.nat : ["AWS/EC2", "NetworkPacketsIn", "InstanceId", nat.id, { label = "NAT-${i} pkts in" }]]
          )
        }
      }
    ]
  })
}

# ── EC2 backend dashboard ────────────────────────────────────────────────────
# Memory/disk are NOT default EC2 metrics — they require the CloudWatch agent on
# the host emitting to the CWAgent namespace. The widgets reference CWAgent so
# they light up once the agent is installed (separate infra task); until then
# they render empty, which is the honest state.

resource "aws_cloudwatch_dashboard" "ec2_backend" {
  dashboard_name = "${var.project_name}-ec2-backend"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6,
        properties = {
          title  = "CPU utilization (%)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/EC2", "CPUUtilization", "InstanceId", aws_instance.archimedes.id]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title  = "Network in / out (bytes / 5 min)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            ["AWS/EC2", "NetworkIn", "InstanceId", aws_instance.archimedes.id],
            ["AWS/EC2", "NetworkOut", "InstanceId", aws_instance.archimedes.id]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6,
        properties = {
          title  = "Memory used (%) — requires CloudWatch agent (CWAgent)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["CWAgent", "mem_used_percent", "InstanceId", aws_instance.archimedes.id]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6,
        properties = {
          title  = "Disk used (%) root volume — requires CloudWatch agent (CWAgent)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["CWAgent", "disk_used_percent", "InstanceId", aws_instance.archimedes.id, "path", "/"]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6,
        properties = {
          title  = "EBS read/write bytes (container/Docker IO proxy)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            ["AWS/EC2", "EBSReadBytes", "InstanceId", aws_instance.archimedes.id],
            ["AWS/EC2", "EBSWriteBytes", "InstanceId", aws_instance.archimedes.id]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 12, width = 12, height = 6,
        properties = {
          title  = "Status check failed (host health — container-restart proxy)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Maximum",
          metrics = [
            ["AWS/EC2", "StatusCheckFailed", "InstanceId", aws_instance.archimedes.id],
            ["AWS/EC2", "StatusCheckFailed_Instance", "InstanceId", aws_instance.archimedes.id],
            ["AWS/EC2", "StatusCheckFailed_System", "InstanceId", aws_instance.archimedes.id]
          ]
        }
      }
    ]
  })
}

# ── ALB dashboard ────────────────────────────────────────────────────────────

resource "aws_cloudwatch_dashboard" "alb" {
  dashboard_name = "${var.project_name}-alb"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6,
        properties = {
          title  = "Request count",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.main.arn_suffix]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title  = "HTTP status breakdown (2xx / 4xx / 5xx)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_Target_2XX_Count", "LoadBalancer", aws_lb.main.arn_suffix, { label = "2xx" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_4XX_Count", "LoadBalancer", aws_lb.main.arn_suffix, { label = "4xx" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.main.arn_suffix, { label = "5xx" }]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6,
        properties = {
          title  = "Target response time (p50 / p95 / p99)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.main.arn_suffix, "TargetGroup", aws_lb_target_group.backend.arn_suffix, { stat = "p50", label = "p50" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.main.arn_suffix, "TargetGroup", aws_lb_target_group.backend.arn_suffix, { stat = "p95", label = "p95" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.main.arn_suffix, "TargetGroup", aws_lb_target_group.backend.arn_suffix, { stat = "p99", label = "p99" }]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6,
        properties = {
          title  = "Healthy / unhealthy target count",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Maximum",
          metrics = [
            ["AWS/ApplicationELB", "HealthyHostCount", "LoadBalancer", aws_lb.main.arn_suffix, "TargetGroup", aws_lb_target_group.backend.arn_suffix, { label = "Healthy" }],
            ["AWS/ApplicationELB", "UnHealthyHostCount", "LoadBalancer", aws_lb.main.arn_suffix, "TargetGroup", aws_lb_target_group.backend.arn_suffix, { label = "Unhealthy" }]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6,
        properties = {
          title  = "Active connection count",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            ["AWS/ApplicationELB", "ActiveConnectionCount", "LoadBalancer", aws_lb.main.arn_suffix]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 12, width = 12, height = 6,
        properties = {
          title  = "ELB-side 5xx (load balancer errors)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_ELB_5XX_Count", "LoadBalancer", aws_lb.main.arn_suffix]
          ]
        }
      }
    ]
  })
}

# ── WAF dashboard ────────────────────────────────────────────────────────────
# Per-rule blocked counts use the metric_name set on each rule's
# visibility_config in waf.tf. Top blocked source IPs / geo distribution are not
# CloudWatch metrics — they live in the WAF sampled-requests / logs surface and
# are linked from the dashboard via a text widget rather than synthesized here.

resource "aws_cloudwatch_dashboard" "waf" {
  dashboard_name = "${var.project_name}-waf"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6,
        properties = {
          title  = "Allowed vs blocked (Web ACL total)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            ["AWS/WAFV2", "AllowedRequests", "WebACL", local.waf_metric_name, "Region", var.aws_region, "Rule", "ALL", { label = "Allowed" }],
            ["AWS/WAFV2", "BlockedRequests", "WebACL", local.waf_metric_name, "Region", var.aws_region, "Rule", "ALL", { label = "Blocked" }]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title  = "Blocked requests per rule",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            ["AWS/WAFV2", "BlockedRequests", "WebACL", local.waf_metric_name, "Region", var.aws_region, "Rule", "rate-limit", { label = "rate-limit" }],
            ["AWS/WAFV2", "BlockedRequests", "WebACL", local.waf_metric_name, "Region", var.aws_region, "Rule", "aws-core-rules", { label = "core-rules" }],
            ["AWS/WAFV2", "BlockedRequests", "WebACL", local.waf_metric_name, "Region", var.aws_region, "Rule", "aws-known-bad-inputs", { label = "known-bad-inputs" }],
            ["AWS/WAFV2", "BlockedRequests", "WebACL", local.waf_metric_name, "Region", var.aws_region, "Rule", "aws-ip-reputation", { label = "ip-reputation" }]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6,
        properties = {
          title  = "Counted requests per rule (COUNT-mode SQLi)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            ["AWS/WAFV2", "CountedRequests", "WebACL", local.waf_metric_name, "Region", var.aws_region, "Rule", "aws-sqli", { label = "sqli (count mode)" }]
          ]
        }
      },
      {
        type = "text", x = 12, y = 6, width = 12, height = 6,
        properties = {
          markdown = "### Top blocked source IPs & geo distribution\n\nThese are **not** CloudWatch metrics. Open the WAF console for the `${aws_wafv2_web_acl.main.name}` Web ACL → **Sampled requests** for top source IPs and country breakdown, or query the WAF logs.\n\nThe `${var.project_name}-waf-blocked-spike` alarm pages when blocked requests exceed 100/min."
        }
      }
    ]
  })
}

# ── Log group retention (AUDIT I8) ──────────────────────────────────────────
# Without explicit log group resources, CloudWatch retains logs indefinitely
# (never expires) — unbounded cost and unnecessary data retention. 90 days is
# sufficient for post-incident forensics and covers any regulatory baseline.

resource "aws_cloudwatch_log_group" "app" {
  name              = "/archimedes/app"
  retention_in_days = 90
  tags              = { Project = var.project_name }
}

resource "aws_cloudwatch_log_group" "nginx" {
  name              = "/archimedes/nginx"
  retention_in_days = 90
  tags              = { Project = var.project_name }
}

# ── Outputs ──────────────────────────────────────────────────────────────────

output "alerts_topic_arn" {
  description = "SNS topic ARN for CloudWatch alarms. Subscribe additional endpoints (Slack via chatbot, PagerDuty, etc.) here."
  value       = aws_sns_topic.alerts.arn
}

output "ops_dashboard_name" {
  description = "CloudWatch dashboard name."
  value       = aws_cloudwatch_dashboard.ops.dashboard_name
}

# All CloudWatch dashboard names (issue #418 acceptance — `terraform output
# cloudwatch_dashboard_names`). Includes the pre-existing ops dashboard plus the
# six per-subsystem dashboards added for Layer 1.
output "cloudwatch_dashboard_names" {
  description = "Names of every CloudWatch dashboard managed by Terraform."
  value = [
    aws_cloudwatch_dashboard.ops.dashboard_name,
    aws_cloudwatch_dashboard.aurora.dashboard_name,
    aws_cloudwatch_dashboard.elasticache.dashboard_name,
    aws_cloudwatch_dashboard.vpc_nat.dashboard_name,
    aws_cloudwatch_dashboard.ec2_backend.dashboard_name,
    aws_cloudwatch_dashboard.alb.dashboard_name,
    aws_cloudwatch_dashboard.waf.dashboard_name,
  ]
}

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
# ec2_cpu_high / ec2_status_check_failed (both dimensioned on
# aws_instance.archimedes) were removed 2026-08-19 with the EC2 decommission
# (main.tf). The runner box has its own equivalent status-check alarm
# (runner_ec2.tf's runner_ec2_status_check_failed).

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
  # RE-TUNED 2026-08-21 (10 transitions/night): a rolling deploy's draining
  # task can read unhealthy for >3 minutes — 5 sustained minutes separates
  # real outages from every deploy. 5xx-count (zero false fires) remains the
  # fast tripwire.
  alarm_name          = "${var.project_name}-alb-unhealthy-hosts"
  alarm_description   = "One or more backend targets are unhealthy."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 5
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
  # RE-TUNED 2026-08-21 (36 state transitions in one night): p95 of
  # TargetResponseTime is structurally contaminated here — SSE generation
  # streams legitimately run 300s+, so at low traffic p95 EQUALS the stream
  # duration whenever anyone generates, and a 2s threshold cycles forever.
  # p50 (median) tracks systemic slowness of ordinary requests instead, and
  # three sustained periods ride out deploy cold-starts.
  alarm_name          = "${var.project_name}-alb-target-latency-high"
  alarm_description   = "Median (p50) backend response time > 1.5s for 15 min."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  extended_statistic  = "p50"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 1.5
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 3
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

# ─────────────────────────────────────────────────────────────────────────────
# Issue #418 — Layer 1 (AWS infrastructure metrics) alarms.
#
# Adds the additional per-subsystem CloudWatch alarms named in the issue (NAT
# transfer anomaly, ALB 5xx > 1% over 5 min, Aurora connections > 80%, Aurora
# ACU pinned at max, ElastiCache evictions, WAF blocked-request spike).
#
# The per-subsystem DASHBOARDS this section originally shipped alongside
# (ops, aurora, elasticache, vpc_nat, ec2_backend, alb, waf — seven, then six
# after ec2_backend was removed 2026-08-19 with the EC2 decommission) were
# consolidated 2026-08-20 into the THREE founder-readable dashboards below
# (product-health / data-stores / machines-and-network) — see that section's
# header comment for why.
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

# NAT health + auto-recovery (issue #1039 N1). Mirrors the former app box's
# `ec2_status_check_failed` alarm (removed 2026-08-19 with the EC2
# decommission, main.tf) — same alarm-name suffix pattern, SNS action,
# tags — with two deliberate deltas from an exact mirror:
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
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "error_rate"
    expression  = "IF(reqs >= 50, 100 * m5xx / reqs, 0)" # min-traffic guard: rate is meaningless under 50 req/5min (re-tuned 2026-08-21, 8 transitions/night)
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

# ── Runner EC2 liveness (issue #1402) ───────────────────────────────────────
# The runner box (aws_instance.runner, runner_ec2.tf) has wedged 3x with an OS
# memory-exhaustion signature: instance status check impaired + SSM dead. The
# existing runner_ec2_status_check_failed alarm (runner_ec2.tf) watches the
# COMBINED `StatusCheckFailed` metric (Max(_System, _Instance)) at 1-min
# granularity; this alarm is a second, narrower signal scoped specifically to
# `StatusCheckFailed_Instance` (OS/instance-level failures — exactly what an
# OOM-wedged-but-hypervisor-healthy box produces), so a founder glancing at
# the machines-and-network dashboard's alarm widget sees the OS-level failure
# mode named explicitly rather than folded into a combined metric.
#
# Deliberately does NOT attach the `ec2:recover` automate action the way
# nat_status_check_failed does: AWS documents that "The recover action can be
# used only with StatusCheckFailed_System, not with StatusCheckFailed_Instance"
# (Add recover actions to Amazon CloudWatch alarms) — attaching it here would
# target a metric AWS's own API rejects it for. SNS wiring (topic, ok action,
# tags, treat_missing_data="breaching" reasoning) otherwise mirrors
# nat_status_check_failed exactly.
resource "aws_cloudwatch_metric_alarm" "runner_instance_impaired" {
  alarm_name          = "${var.project_name}-runner-instance-impaired"
  alarm_description   = "Oracle+agent runner EC2 instance status check failed (StatusCheckFailed_Instance) for 2 consecutive 5-min periods — OS-level impairment (e.g. memory exhaustion, issue #1402) on the funds-adjacent singleton runners."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_Instance"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  period              = 300
  evaluation_periods  = 2
  dimensions          = { InstanceId = aws_instance.runner.id }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "breaching"
  tags                = { Project = var.project_name }
}

# ── Runner EC2 automatic recovery (issue #1402) ────────────────────────────
# Everything above this line PAGES. Nothing above it ACTS. That is the gap
# #1402 is still open on: all four wedges (13-day SSM ConnectionLost found
# 2026-08-19; two on 2026-08-20; the 4-hour 06:12→10:23 CDT window recorded
# in the issue) ended with a HUMAN rebooting the box, and the recorded
# outage lengths are hours because a human had to notice first. #1413's swap
# + per-container memory/CPU caps make the wedge less likely; they do not
# shorten the outage if it happens anyway. These two alarms do — they carry
# EC2 automatic actions in `alarm_actions` alongside the SNS topic, so AWS
# remediates while the page is still in flight.
#
# The two actions are NOT interchangeable, and picking the wrong one is the
# failure mode this block exists to prevent:
#
#   * `ec2:reboot` — the ONLY automatic action valid for
#     `StatusCheckFailed_Instance`. That is #1402's actual signature
#     (instance check impaired, system check ok, SSM agent stops pinging),
#     and a reboot is empirically what recovered the box every time.
#   * `ec2:recover` — AWS: "The recover action can be used only with
#     StatusCheckFailed_System, not with StatusCheckFailed_Instance."
#     (Add recover actions to Amazon CloudWatch alarms.) Attaching it to the
#     `_Instance` alarm is the change that looks right and silently cannot
#     do the one thing it was added for; `runner_instance_impaired` above
#     already carries a comment saying so, and
#     backend/tests/test_runner_recovery_alarms.py now enforces it.
#
# Evaluation periods follow AWS's own anti-race guidance for running a
# reboot alarm and a recover alarm on the same instance: reboot at three
# 1-minute periods, recover at two. Recover therefore fires first on a
# genuine hardware failure (where both checks fail together) and the reboot
# alarm never gets to third strike; on an OS-only wedge the system check
# stays healthy, the recover alarm never leaves OK, and reboot is the only
# action that fires. `var.runner_instance_type` (t3.small) is in AWS's
# supported-instance-type list for CloudWatch action based recovery.
#
# `treat_missing_data = "missing"` on BOTH — deliberately different from the
# paging alarms' "breaching". A missing datapoint here means "EC2 stopped
# publishing status checks", which is exactly what a stopped instance and an
# in-progress reboot both look like. Under "breaching", the reboot this
# block just triggered would blank the metric and drive the recover alarm to
# ALARM, stop/starting the box on top of its own reboot — automation
# reacting to its own side effects. Absence-of-metric detection is not lost:
# `runner_instance_impaired` and `runner_ec2_status_check_failed`
# (runner_ec2.tf) both keep "breaching" and both page a human on it. The
# split is intentional — humans should be paged on ambiguity, robots should
# not act on it.
#
# NOT AUTOMATED HERE, on purpose: nothing stops, terminates, or replaces the
# instance. This is a funds-adjacent exactly-once singleton (runner_ec2.tf
# header, #1065 decision #1); reboot and recover both preserve instance id,
# EBS root volume, and private IP, so the SSM host-prep state #1413 installs
# survives. A `terminate` action would silently discard it.

resource "aws_cloudwatch_metric_alarm" "runner_instance_reboot" {
  alarm_name          = "${var.project_name}-runner-instance-reboot"
  alarm_description   = "Oracle+agent runner EC2 OS-level status check (StatusCheckFailed_Instance) failed 3x 1-min — issue #1402's wedge signature. Automatically reboots the instance (ec2:reboot) AND pages, turning a multi-hour manual-reboot outage into a ~2-minute blip."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_Instance"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 60
  evaluation_periods  = 3
  dimensions          = { InstanceId = aws_instance.runner.id }
  alarm_actions       = [aws_sns_topic.alerts.arn, "arn:aws:automate:${var.aws_region}:ec2:reboot"]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "missing"
  tags                = { Project = var.project_name }
}

resource "aws_cloudwatch_metric_alarm" "runner_system_recover" {
  alarm_name          = "${var.project_name}-runner-system-recover"
  alarm_description   = "Oracle+agent runner EC2 system status check (StatusCheckFailed_System) failed 2x 1-min — underlying hardware/hypervisor is unhealthy. Automatically migrates the instance onto new hardware (ec2:recover) AND pages. Mirrors nat_status_check_failed."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 60
  evaluation_periods  = 2
  dimensions          = { InstanceId = aws_instance.runner.id }
  alarm_actions       = [aws_sns_topic.alerts.arn, "arn:aws:automate:${var.aws_region}:ec2:recover"]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "missing"
  tags                = { Project = var.project_name }
}

# ── SSM-agent liveness, by proxy (issue #1402) ─────────────────────────────
# HONEST LIMITATION FIRST: there is no CloudWatch metric for "is this box's
# SSM agent pinging". SSM's own liveness signal is `LastPingDateTime` on
# `ssm:DescribeInstanceInformation`, an API field — not a metric, so it
# cannot be alarmed on without new infrastructure (a scheduled poller
# publishing a custom metric, or an EventBridge rule on inventory events).
# The direct check is therefore a COMMAND, documented step-by-step in
# docs/runbooks/runner-ec2-wedge.md § Diagnosis, not an alarm.
#
# What CAN be alarmed for free is the proxy, and #1402's own forensics show
# it is a tight one: the agent log stream went silent at 2026-08-20T10:56Z
# and SSM's LastPingDateTime for the same incident is 05:55:09-05:00 —
# 10:55Z. The same host-level starvation kills the SSM agent and the docker
# logging driver within the same minute, because it starves everything. So
# "the runner has stopped writing logs" is the machine-detectable shadow of
# "the SSM agent has stopped pinging".
#
# `IncomingLogEvents` on the runner log group (AWS/Logs, free, no agent
# install, no new IAM) is that signal. The oracle loop writes on a verified
# 60-second cadence (#1402: "clean 60-second cadence ... cadence gaps are
# exactly 60s"), so a healthy 5-minute window carries >= 5 events from the
# oracle stream alone; three consecutive windows below 1 means ~15 minutes
# of total silence from BOTH runners. CloudWatch publishes no zero for an
# idle log group — it publishes nothing — so `treat_missing_data` must be
# "breaching" or total silence, the exact condition being detected, would
# leave the alarm sitting in OK forever.
#
# WHAT THIS ALARM DOES NOT MEAN: it is not proof the SSM agent is dead. A
# deliberately stopped container, a paused deploy, or an oracle disabled by
# flag all produce the same silence. It says "the runner has stopped
# talking" — which is worth a page on a box whose whole job is to talk — and
# the runbook's first diagnosis step is the `describe-instance-information`
# call that distinguishes the cases. No automatic action is attached for
# exactly that reason: rebooting a box because someone stopped a container
# on purpose would be automation acting on an inference.

resource "aws_cloudwatch_metric_alarm" "runner_log_silence" {
  alarm_name          = "${var.project_name}-runner-log-silence"
  alarm_description   = "No log events reached ${aws_cloudwatch_log_group.runners.name} for 3 consecutive 5-min periods. The oracle loop writes every 60s, so ~15 min of silence means the runner box has gone quiet — the machine-visible proxy for the dead-SSM-agent wedge in issue #1402. Not proof the SSM agent is dead: see docs/runbooks/runner-ec2-wedge.md."
  namespace           = "AWS/Logs"
  metric_name         = "IncomingLogEvents"
  statistic           = "Sum"
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  period              = 300
  evaluation_periods  = 3
  dimensions          = { LogGroupName = aws_cloudwatch_log_group.runners.name }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "breaching"
  tags                = { Project = var.project_name }
}

# ── Dashboards — consolidated 6→3, founder-readable (2026-08-20) ───────────
# Replaces the per-subsystem dashboards this file used to define (ops,
# aurora, elasticache, vpc_nat, alb, waf — six by the time of this change,
# ec2_backend having been removed 2026-08-19 with the EC2 decommission) with
# three dashboards organized around the three questions Dan (founder,
# non-SRE — the actual reader) asks: is the site up and fast, are the
# databases OK, are the background machines alive and is network traffic
# normal. Cost driver, not just readability: CloudWatch bills the 4th+
# dashboard at $3/mo each on top of the ~$19/mo CloudWatch base — 6
# dashboards cost $9/mo more than 3 for zero benefit to the one person who
# reads them.
#
# Each dashboard opens with an `alarm` widget (live state of every alarm the
# dashboard's panels cover) and pairs every metric widget with a `text`
# (markdown) widget explaining, in plain English, what normal looks like and
# when to worry — using the SAME numbers as the matching alarm's threshold,
# so the prose and the chart never disagree. `annotations.horizontal` draws
# that threshold as a line on every latency/CPU/5xx-rate widget so the gap
# (or breach) between "where we are" and "the alarm line" is visible without
# reading the alarm widget at all.

resource "aws_cloudwatch_dashboard" "product_health" {
  dashboard_name = "${var.project_name}-product-health"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "alarm", x = 0, y = 0, width = 24, height = 3,
        properties = {
          title = "Alarms — product health",
          alarms = [
            aws_cloudwatch_metric_alarm.alb_5xx_high.arn,
            aws_cloudwatch_metric_alarm.alb_5xx_rate_high.arn,
            aws_cloudwatch_metric_alarm.alb_unhealthy_hosts.arn,
            aws_cloudwatch_metric_alarm.alb_latency_high.arn,
            aws_cloudwatch_metric_alarm.waf_blocked_spike.arn,
            aws_cloudwatch_metric_alarm.chain_disconnected_alarm.arn,
          ]
        }
      },
      # Row 1 — request volume
      {
        type = "metric", x = 0, y = 3, width = 12, height = 6,
        properties = {
          title  = "Requests served",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.main.arn_suffix]
          ]
        }
      },
      {
        type = "text", x = 12, y = 3, width = 12, height = 6,
        properties = {
          markdown = "### Requests served\n\nRequests the site handled. Normal: varies with traffic — watch the shape, not a fixed number. **Worry if** it drops to ~0 during hours it's normally non-zero (site may be unreachable), or if 5xx count (next row) climbs alongside it."
        }
      },
      # Row 2 — 5xx count
      {
        type = "metric", x = 0, y = 9, width = 12, height = 6,
        properties = {
          title  = "Backend errors (5xx count)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.main.arn_suffix]
          ]
          annotations = {
            horizontal = [
              { label = "Alarm threshold (10 / 5 min)", value = 10 }
            ]
          }
        }
      },
      {
        type = "text", x = 12, y = 9, width = 12, height = 6,
        properties = {
          markdown = "### Backend errors (5xx count)\n\nRequests the backend failed to serve. Normal: 0, or a handful during a deploy. **Worry if** this crosses **10 in a 5-min window** — that's the alarm line (dashed, on the chart)."
        }
      },
      # Row 3 — 5xx rate
      {
        type = "metric", x = 0, y = 15, width = 12, height = 6,
        properties = {
          title  = "Backend error rate (%)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            [{ expression = "100 * (m5xx / IF(reqs > 0, reqs, 1))", label = "5xx error rate (%)", id = "error_rate" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.main.arn_suffix, { id = "m5xx", visible = false, stat = "Sum" }],
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.main.arn_suffix, { id = "reqs", visible = false, stat = "Sum" }]
          ]
          annotations = {
            horizontal = [
              { label = "Alarm threshold (1%)", value = 1 }
            ]
          }
        }
      },
      {
        type = "text", x = 12, y = 15, width = 12, height = 6,
        properties = {
          markdown = "### Backend error rate (%)\n\n5xx errors as a share of all requests — catches problems that scale with traffic, not just raw count. Normal: well under 1%. **Worry if** it crosses **1% for 5 minutes** — that's the alarm line."
        }
      },
      # Row 4 — latency
      {
        type = "metric", x = 0, y = 21, width = 12, height = 6,
        properties = {
          title  = "Response time (p50 / p99, seconds)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.main.arn_suffix, "TargetGroup", aws_lb_target_group.backend.arn_suffix, { stat = "p50", label = "p50" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.main.arn_suffix, "TargetGroup", aws_lb_target_group.backend.arn_suffix, { stat = "p99", label = "p99" }]
          ]
          annotations = {
            horizontal = [
              { label = "Alarm threshold (p95 > 2s)", value = 2 }
            ]
          }
        }
      },
      {
        type = "text", x = 12, y = 21, width = 12, height = 6,
        properties = {
          markdown = "### Response time\n\nHow long the backend takes to answer. p50 = typical request, p99 = the slowest 1%. Normal: p50 well under a second. **Worry if** either line approaches **2 seconds** — the alarm actually fires on p95 (not plotted directly), but p99 crossing this line is the earlier warning."
        }
      },
      # Row 5 — healthy hosts
      {
        type = "metric", x = 0, y = 27, width = 12, height = 6,
        properties = {
          title  = "Healthy / unhealthy backend targets",
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
        type = "text", x = 12, y = 27, width = 12, height = 6,
        properties = {
          markdown = "### Backend targets\n\nHow many backend tasks are passing health checks (autoscaling floor ${var.ecs_service_min_count}, ceiling ${var.ecs_service_max_count}) vs. failing them. Normal: Healthy >= 1, Unhealthy = 0. **Worry if** Unhealthy is ever above 0 — that's the alarm line."
        }
      },
      # Row 6 — WAF blocked
      {
        type = "metric", x = 0, y = 33, width = 12, height = 6,
        properties = {
          title  = "WAF blocked requests",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            ["AWS/WAFV2", "BlockedRequests", "WebACL", local.waf_metric_name, "Region", var.aws_region, "Rule", "ALL", { label = "Blocked" }]
          ]
          annotations = {
            horizontal = [
              { label = "Alarm threshold (6000 / 5 min, ~100/min)", value = 6000 }
            ]
          }
        }
      },
      {
        type = "text", x = 12, y = 33, width = 12, height = 6,
        properties = {
          markdown = "### WAF blocked requests\n\nRequests the firewall rejected (bad bots, known exploit patterns, rate-limit). Normal: a steady trickle — background noise on any public site. **Worry if** it spikes past **100/min (6000 per 5-min datapoint)** — that's the alarm line, signaling an active attack/abuse wave worth eyes-on even though WAF is already blocking it."
        }
      },
      # Row 7 — ECS CPU/Memory
      {
        type = "metric", x = 0, y = 39, width = 12, height = 6,
        properties = {
          title  = "Backend service CPU / memory (%)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.backend.name, { label = "CPU %" }],
            ["AWS/ECS", "MemoryUtilization", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.backend.name, { label = "Memory %" }]
          ]
          annotations = {
            horizontal = [
              { label = "Autoscale adds capacity (~${var.ecs_autoscale_cpu_target}% CPU)", value = var.ecs_autoscale_cpu_target }
            ]
          }
        }
      },
      {
        type = "text", x = 12, y = 39, width = 12, height = 6,
        properties = {
          markdown = "### Backend service CPU / memory\n\nHow hard the running backend containers are working. Normal: comfortably under the autoscale line — above it, AWS adds another task automatically (floor ${var.ecs_service_min_count}, ceiling ${var.ecs_service_max_count}). **Worry if** it's pinned near 100% even after scaling out, or if Memory climbs steadily with no plateau (possible leak). No CloudWatch alarm is wired to this panel yet — it's watch-only."
        }
      }
    ]
  })
}

resource "aws_cloudwatch_dashboard" "data_stores" {
  dashboard_name = "${var.project_name}-data-stores"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "alarm", x = 0, y = 0, width = 24, height = 3,
        properties = {
          title = "Alarms — data stores",
          alarms = [
            aws_cloudwatch_metric_alarm.aurora_cpu_high.arn,
            aws_cloudwatch_metric_alarm.aurora_low_memory.arn,
            aws_cloudwatch_metric_alarm.aurora_connections_high.arn,
            aws_cloudwatch_metric_alarm.aurora_connections_pct_high.arn,
            aws_cloudwatch_metric_alarm.aurora_acu_max.arn,
            aws_cloudwatch_metric_alarm.redis_evictions.arn,
          ]
        }
      },
      # Row 1 — Aurora CPU
      {
        type = "metric", x = 0, y = 3, width = 12, height = 6,
        properties = {
          title  = "Aurora CPU (%)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier]
          ]
          annotations = {
            horizontal = [
              { label = "Alarm threshold (85%)", value = 85 }
            ]
          }
        }
      },
      {
        type = "text", x = 12, y = 3, width = 12, height = 6,
        properties = {
          markdown = "### Aurora CPU\n\nHow hard the database is working. Normal: comfortably under 85%. **Worry if** it's pinned near **85%** for 10+ minutes — that's the alarm line."
        }
      },
      # Row 2 — ACU
      {
        type = "metric", x = 0, y = 9, width = 12, height = 6,
        properties = {
          title  = "Aurora Serverless capacity (ACU)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/RDS", "ServerlessDatabaseCapacity", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier]
          ]
          annotations = {
            horizontal = [
              { label = "Alarm threshold (15.5 of 16 max)", value = 15.5 }
            ]
          }
        }
      },
      {
        type = "text", x = 12, y = 9, width = 12, height = 6,
        properties = {
          markdown = "### Database capacity (ACU)\n\nAurora Serverless auto-scales compute between 0.5 and 16 ACU as load changes. Normal: floats well below 16. **Worry if** it's pinned near **16 (the max)** — the database is out of headroom to scale further, which is also a cost signal."
        }
      },
      # Row 3 — connections
      {
        type = "metric", x = 0, y = 15, width = 12, height = 6,
        properties = {
          title  = "Aurora connections",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/RDS", "DatabaseConnections", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier]
          ]
          annotations = {
            horizontal = [
              { label = "Alarm threshold (80, ~80% of ~100 working ceiling)", value = 80 }
            ]
          }
        }
      },
      {
        type = "text", x = 12, y = 15, width = 12, height = 6,
        properties = {
          markdown = "### Database connections\n\nOpen connections to Aurora. Our working ceiling is ~100 connections at this ACU range, so the absolute alarm (80) and ~80% of that ceiling land on the same line. Normal: well under 80. **Worry if** this line crosses **80**."
        }
      },
      # Row 4 — storage / freeable memory
      {
        type = "metric", x = 0, y = 21, width = 12, height = 6,
        properties = {
          title  = "Aurora storage / freeable memory (bytes)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/RDS", "VolumeBytesUsed", "DBClusterIdentifier", aws_rds_cluster.main.cluster_identifier, { label = "VolumeBytesUsed" }],
            ["AWS/RDS", "FreeableMemory", "DBInstanceIdentifier", aws_rds_cluster_instance.main.identifier, { label = "FreeableMemory" }]
          ]
          annotations = {
            horizontal = [
              { label = "Freeable-memory alarm threshold (256 MiB)", value = 268435456 }
            ]
          }
        }
      },
      {
        type = "text", x = 12, y = 21, width = 12, height = 6,
        properties = {
          markdown = "### Storage / freeable memory\n\nVolumeBytesUsed = data on disk (grows slowly, not urgent). FreeableMemory = RAM headroom on the instance — much smaller in bytes, so it reads near-flat on this shared axis. Normal: freeable memory well above 256 MiB. **Worry if** FreeableMemory drops toward the **256 MiB** line — that's the alarm, and it risks OOM / connection churn."
        }
      },
      # Row 5 — Redis CPU/memory
      {
        type = "metric", x = 0, y = 27, width = 12, height = 6,
        properties = {
          title  = "Redis CPU / memory (%)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/ElastiCache", "EngineCPUUtilization", "CacheClusterId", local.redis_node_id, { label = "Engine CPU %" }],
            ["AWS/ElastiCache", "DatabaseMemoryUsagePercentage", "CacheClusterId", local.redis_node_id, { label = "Memory %" }]
          ]
        }
      },
      {
        type = "text", x = 12, y = 27, width = 12, height = 6,
        properties = {
          markdown = "### Redis CPU / memory\n\nHow hard the cache is working and how full it is. Normal: both comfortably under 100%. **Worry if** either climbs steadily with no plateau. No CloudWatch alarm is wired to this panel yet — Evictions (next row) is the earlier, more reliable memory-pressure signal."
        }
      },
      # Row 6 — evictions
      {
        type = "metric", x = 0, y = 33, width = 12, height = 6,
        properties = {
          title  = "Redis evictions",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/ElastiCache", "Evictions", "CacheClusterId", local.redis_node_id]
          ]
          annotations = {
            horizontal = [
              { label = "Alarm threshold (100 / 5 min)", value = 100 }
            ]
          }
        }
      },
      {
        type = "text", x = 12, y = 33, width = 12, height = 6,
        properties = {
          markdown = "### Redis evictions\n\nKeys Redis discarded to free memory under pressure. Normal: 0. **Worry if** this crosses **100 in a 5-min window** — that's the alarm, meaning the cache is too small for what's being asked of it (regime state / job queue churn)."
        }
      },
      # Row 7 — connections
      {
        type = "metric", x = 0, y = 39, width = 12, height = 6,
        properties = {
          title  = "Redis connections",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/ElastiCache", "CurrConnections", "CacheClusterId", local.redis_node_id]
          ]
        }
      },
      {
        type = "text", x = 12, y = 39, width = 12, height = 6,
        properties = {
          markdown = "### Redis connections\n\nOpen client connections to the cache. Normal: a small, roughly steady number tracking backend task count. **Worry if** it climbs unbounded (a connection leak) — no alarm is wired here; watch alongside CPU/memory above."
        }
      }
    ]
  })
}

resource "aws_cloudwatch_dashboard" "machines_and_network" {
  dashboard_name = "${var.project_name}-machines-and-network"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "alarm", x = 0, y = 0, width = 24, height = 3,
        properties = {
          title = "Alarms — machines & network",
          alarms = concat(
            [
              aws_cloudwatch_metric_alarm.runner_ec2_status_check_failed.arn,
              aws_cloudwatch_metric_alarm.runner_instance_impaired.arn,
              aws_cloudwatch_metric_alarm.runner_instance_reboot.arn,
              aws_cloudwatch_metric_alarm.runner_system_recover.arn,
              aws_cloudwatch_metric_alarm.runner_log_silence.arn,
            ],
            aws_cloudwatch_metric_alarm.nat_status_check_failed[*].arn,
            aws_cloudwatch_metric_alarm.nat_egress_anomaly[*].arn,
          )
        }
      },
      # Row 1 — runner status check
      {
        type = "metric", x = 0, y = 3, width = 12, height = 6,
        properties = {
          title  = "Runner instance status check (StatusCheckFailed_Instance)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Maximum",
          metrics = [
            ["AWS/EC2", "StatusCheckFailed_Instance", "InstanceId", aws_instance.runner.id]
          ]
          annotations = {
            horizontal = [
              { label = "Alarm line (>= 1): pages at 2x 5-min, auto-reboots at 3x 1-min", value = 1 }
            ]
          }
        }
      },
      {
        type = "text", x = 12, y = 3, width = 12, height = 6,
        properties = {
          markdown = "### Runner instance status check\n\nThe oracle+agent runner's OS-level health check — catches memory exhaustion / OS wedges even when the underlying hardware is fine (issue #1402). Normal: flat at 0. **Worry if** this hits **1** — it means no oracle price pushes and no agent rebalances until it recovers.\n\nTwo alarms watch this one line. `runner-instance-reboot` fires after **3 minutes** and **automatically reboots the box** (AWS `ec2:reboot`), which is what recovered it manually all four times in #1402. `runner-instance-impaired` pages after **10 minutes** and does nothing else. So the expected shape of an incident now is a ~2-minute blip that self-heals, not an outage waiting on a human. **If the reboot alarm goes to ALARM twice in a day, stop trusting the automation** and open the runbook: `docs/runbooks/runner-ec2-wedge.md`."
        }
      },
      # Row 2 — runner CPU
      {
        type = "metric", x = 0, y = 9, width = 12, height = 6,
        properties = {
          title  = "Runner CPU utilization (%)",
          region = var.aws_region,
          view   = "timeSeries",
          metrics = [
            ["AWS/EC2", "CPUUtilization", "InstanceId", aws_instance.runner.id]
          ]
        }
      },
      {
        type = "text", x = 12, y = 9, width = 12, height = 6,
        properties = {
          markdown = "### Runner CPU\n\nHow hard the runner box is working (two lightweight async Python loops — oracle + agent). Normal: low and steady. **Worry if** it climbs steadily or spikes with no plateau. No CloudWatch alarm is wired to this panel — memory is the box's known failure mode (#1402), and stock EC2 metrics don't expose memory without a CloudWatch agent install (deliberately not added here); the status-check panel above is the proxy signal."
        }
      },
      # Row 3 — NAT status
      {
        type = "metric", x = 0, y = 15, width = 12, height = 6,
        properties = {
          title  = "NAT instance status check (StatusCheckFailed_System)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Maximum",
          metrics = [
            for i, nat in aws_instance.nat :
            ["AWS/EC2", "StatusCheckFailed_System", "InstanceId", nat.id, { label = "NAT-${i}" }]
          ]
          annotations = {
            horizontal = [
              { label = "Alarm threshold (> 0, 2x 1-min periods)", value = 0 }
            ]
          }
        }
      },
      {
        type = "text", x = 12, y = 15, width = 12, height = 6,
        properties = {
          markdown = "### NAT instance status\n\nHardware/hypervisor health of the two fck-nat instances that give the private subnets (backend, Aurora, ElastiCache, runner) their internet egress. Normal: flat at 0 for both. **Worry if** either goes above **0** — that's the alarm, and it auto-triggers AWS's ec2:recover action as well as paging."
        }
      },
      # Row 4 — NAT egress bytes
      {
        type = "metric", x = 0, y = 21, width = 12, height = 6,
        properties = {
          title  = "NAT egress bytes (5-min Sum)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = [
            for i, nat in aws_instance.nat :
            ["AWS/EC2", "NetworkOut", "InstanceId", nat.id, { label = "NAT-${i} NetworkOut" }]
          ]
          annotations = {
            horizontal = [
              { label = "Alarm threshold (5 GiB / 5-min datapoint)", value = 5368709120 }
            ]
          }
        }
      },
      {
        type = "text", x = 12, y = 21, width = 12, height = 6,
        properties = {
          markdown = "### NAT egress bytes\n\nOutbound data leaving each NAT instance. Normal: modest and steady (ECR pulls, Arc RPC, Aurora/ElastiCache traffic, CloudWatch Logs). **Worry if** a NAT instance crosses **5 GiB in a single 5-min datapoint, sustained 15 min** — that's the alarm, and it doubles as a surprise-bill / exfiltration signal."
        }
      },
      # Row 5 — total NAT-processed bytes trend
      {
        type = "metric", x = 0, y = 27, width = 12, height = 6,
        properties = {
          title  = "VPC NAT-processed bytes (in + out, 5-min Sum)",
          region = var.aws_region,
          view   = "timeSeries",
          stat   = "Sum",
          metrics = concat(
            [for i, nat in aws_instance.nat : ["AWS/EC2", "NetworkOut", "InstanceId", nat.id, { label = "NAT-${i} out" }]],
            [for i, nat in aws_instance.nat : ["AWS/EC2", "NetworkIn", "InstanceId", nat.id, { label = "NAT-${i} in" }]]
          )
        }
      },
      {
        type = "text", x = 12, y = 27, width = 12, height = 6,
        properties = {
          markdown = "### Total NAT traffic trend\n\nBoth directions of traffic through the NAT instances, together — the overall shape of how much the private subnet is talking to the internet. Normal: tracks app activity, gently varying, no big step changes. **Worry if** you see a sudden sustained step up or down with no matching deploy/traffic change — could be a stuck retry loop, a runaway job, or a network problem."
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

# ── Dead-egress detection — chain_connected health signal (issue #1039 N2) ──
#
# Deliberately does NOT flip /health's HTTP status code on chain-down (that
# risks cascading the whole ECS service on a transient Arc RPC blip — the ALB
# target-group health check and ECS's own container healthCheck both key off
# /health's status code, so a 5xx there would drain/kill tasks over an RPC-only
# problem). Instead: backend/archimedes/main.py's `/health` handler logs a
# loud, greppable WARNING (`HEALTH_CHAIN_DISCONNECTED`) whenever
# `chain_connected` is false while still returning HTTP 200 — a "degraded but
# healthy" task keeps serving traffic (correct) while this filter+alarm pair
# makes that degraded state page a human instead of silently sitting in the
# JSON body of a response nobody is reading.
resource "aws_cloudwatch_log_metric_filter" "chain_disconnected" {
  name           = "${var.project_name}-chain-disconnected"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "\"HEALTH_CHAIN_DISCONNECTED\""

  metric_transformation {
    name          = "ChainDisconnectedCount"
    namespace     = "Archimedes/Health"
    value         = "1"
    default_value = 0
  }
}

resource "aws_cloudwatch_metric_alarm" "chain_disconnected_alarm" {
  alarm_name          = "${var.project_name}-chain-disconnected"
  alarm_description   = "/health reported chain_connected=false 3+ times in 5 min — Arc RPC unreachable (dead NAT egress or upstream RPC outage) on a task that is still serving HTTP 200."
  namespace           = aws_cloudwatch_log_metric_filter.chain_disconnected.metric_transformation[0].namespace
  metric_name         = aws_cloudwatch_log_metric_filter.chain_disconnected.metric_transformation[0].name
  statistic           = "Sum"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 3
  period              = 300
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = { Project = var.project_name }
}

# ── Oracle-freshness detection — HEALTH_ORACLE_STALE (issue #1371) ──
#
# Every deployed PriceOracle on Arc testnet has been stale since the T3.2
# redeploy with nothing reporting it — isFresh()/lastUpdated() had zero
# backend callers. backend/archimedes/services/oracle_health.py probes the
# on-chain push set directly (never runner/process liveness — a stalled push
# can loop happily every 60s and log success while the on-chain write itself
# never lands, which is exactly what went undetected for 42+ days). The
# `/health` handler (main.py) logs a loud WARNING (`HEALTH_ORACLE_STALE`)
# whenever `oracle_fresh` is false while still returning HTTP 200 — same
# "degraded but healthy task keeps serving traffic" reasoning as the
# chain_disconnected pair above (see the boxed comment there); this
# filter+alarm pair turns repeated occurrences into a paging alarm instead of
# sitting silently in a JSON body nobody is reading.
resource "aws_cloudwatch_log_metric_filter" "oracle_stale" {
  name           = "${var.project_name}-oracle-stale"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "\"HEALTH_ORACLE_STALE\""

  metric_transformation {
    name          = "OracleStaleCount"
    namespace     = "Archimedes/Health"
    value         = "1"
    default_value = 0
  }
}

resource "aws_cloudwatch_metric_alarm" "oracle_stale_alarm" {
  alarm_name          = "${var.project_name}-oracle-stale"
  alarm_description   = "/health reported oracle_fresh=false 3+ times per 5-min window in 2 of the last 3 windows — the probed on-chain PriceOracle push set is not current (see oracle_oldest_age_s / oracle_reason in the /health response)."
  namespace           = aws_cloudwatch_log_metric_filter.oracle_stale.metric_transformation[0].namespace
  metric_name         = aws_cloudwatch_log_metric_filter.oracle_stale.metric_transformation[0].name
  statistic           = "Sum"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 3
  period              = 300
  # 2-of-3 windows, not 1-of-1 (2026-08-31): during the Arc RPC 429 incident
  # the single-window form flapped ALARM↔OK for hours and, with ok_actions
  # also subscribed, emailed the owner on every transition — dozens of
  # messages for one underlying condition. Requiring two breaching windows
  # out of three fires within ~10–15 min of REAL sustained staleness (the
  # 42-day silent-stale defect this alarm exists for would still page) while
  # a single flappy window no longer does. The signal is not muted — the
  # threshold and metric are untouched; only sustained-ness is required.
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = { Project = var.project_name }
}

# ── Reveal-reconciliation alarms — dangling commit-reveal pairs (issue #1596) ──
#
# #1403 made the dangling-reveal state COUNTABLE (`/health` publishes
# `reveal_reconcile_pending` / `reveal_reconcile_terminal` off an O(1) SCARD)
# and said so honestly in its own body: countable, not alertable — no metric
# filter, no alarm. Countable-but-silent is the fail-soft shape
# `docs/architectural-principles.md` forbids for anything a claim depends on,
# and the claim here is provenance: a terminal record is a trade whose
# reasoning can never be proven to have preceded it.
#
# WHERE THE SIGNAL COMES FROM. Not from `/health`. That handler logs no
# greppable literal for these two fields (#1403 review recorded exactly that),
# and #1596's anti-goals fence off editing it. The reconciliation loop itself
# already logs both transitions, loudly and with stable literals
# (`backend/archimedes/chain/agent_runner.py`, `_reconcile_terminal` and
# `_reconcile_failure`) — so these filters key on the loop's own output, in the
# `/archimedes/runners` log group the agent container ships to
# (`runner_ec2.tf`'s awslogs driver, stream `agent`), NOT `/archimedes/app`
# where the chain/oracle pairs above live. Wrong log group = a filter that
# matches nothing, forever, while looking correct.
#
# THE COUNTER-VS-LEVEL TRAP, AND WHY A LOG FILTER SIDESTEPS IT. `/health`'s
# `reveal_reconcile_terminal` is a CUMULATIVE lifetime count — members are
# never removed from the set — so, as main.py's own comment warns, a static
# threshold on that VALUE fires once at the first historical give-up and stays
# fired forever. A log metric filter does not have this problem: it counts
# EVENTS, so `Sum` over a period is the rate of NEW terminal transitions, which
# is the quantity worth paging on. This is the reason these alarms are wired to
# the loop's log rather than to the gauge the issue names.
#
# The `terraform plan`-before-apply caveat at the top of this file applies to
# both resources below.

resource "aws_cloudwatch_log_metric_filter" "reveal_reconcile_terminal" {
  name           = "${var.project_name}-reveal-reconcile-terminal"
  log_group_name = aws_cloudwatch_log_group.runners.name # runner_ec2.tf — the agent loop, not the web tier
  pattern        = "\"REVEAL RECONCILIATION TERMINAL\""

  metric_transformation {
    name          = "RevealReconcileTerminalCount"
    namespace     = "Archimedes/Reveal"
    value         = "1"
    default_value = 0
  }
}

# Terminal is a permanent give-up on making one executed trade's reasoning
# on-chain-verifiable. It is supposed to be a never event, so a single
# occurrence pages — no flap damping, deliberately: `evaluation_periods = 1`
# here is not a copy-paste of the oracle/chain pairs' shape but the same
# judgement they made, that one datapoint of a should-never-happen event is
# signal rather than noise. (Contrast the pending alarm below, where recurrence
# IS the signal and damping is what separates it from ordinary retry churn.)
resource "aws_cloudwatch_metric_alarm" "reveal_reconcile_terminal_alarm" {
  alarm_name          = "${var.project_name}-reveal-reconcile-terminal"
  alarm_description   = "The agent gave up reconciling a dangling commit-reveal pair (REVEAL RECONCILIATION TERMINAL). That trade's reasoning trace can never be proven to have preceded it; the record stays honestly unverified. Counts NEW terminal transitions per 5 min, not /health's cumulative reveal_reconcile_terminal total."
  namespace           = aws_cloudwatch_log_metric_filter.reveal_reconcile_terminal.metric_transformation[0].namespace
  metric_name         = aws_cloudwatch_log_metric_filter.reveal_reconcile_terminal.metric_transformation[0].name
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 300
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = { Project = var.project_name }
}

resource "aws_cloudwatch_log_metric_filter" "reveal_reconcile_pending" {
  name           = "${var.project_name}-reveal-reconcile-pending"
  log_group_name = aws_cloudwatch_log_group.runners.name
  # `_reconcile_failure`'s per-attempt WARNING. Deliberately NOT matching on
  # "Reveal reconciliation" alone: the same module logs "Reveal reconciliation
  # first-seen SEED FAILED" and "Reveal reconciliation persist FAILED", which
  # are different failures with different responses. Narrowing to "attempt"
  # keeps this metric one thing.
  pattern = "\"Reveal reconciliation attempt\""

  metric_transformation {
    name          = "RevealReconcilePendingRetryCount"
    namespace     = "Archimedes/Reveal"
    value         = "1"
    default_value = 0
  }
}

# "Pending stuck beyond a threshold age", expressed as an alarm window rather
# than as an age gauge — because the loop publishes no per-record age, and
# #1596 forbids adding one.
#
# The arithmetic that makes this a real bound rather than a guessed one:
# AGENT_INTERVAL_SECONDS defaults to 300 (one tick per 5-min period) and
# REVEAL_RECONCILE_MAX_ATTEMPTS defaults to 3, so a record that is merely
# failing its way to a normal give-up can emit this literal in at most ~3
# consecutive periods before `_reconcile_terminal` closes it out and the
# alarm above takes over. Requiring 10 breaching datapoints inside a 60-minute
# window therefore cannot be produced by the ordinary attempt-cap path at all —
# it takes either a record whose attempt counter is not persisting (the exact
# compound failure #1353's max-age breaker exists for, where retries recur
# unbounded for up to REVEAL_RECONCILE_MAX_AGE_SECONDS = 24h) or a sustained
# arrival of new dangling commitments. Both warrant a human.
#
# 10-of-12 rather than 12-of-12 is the file's flap-damping idiom (alb_5xx_rate_high's
# 2-of-3, alb_unhealthy_hosts' 5-of-5): a runner restart, a lease handover, or a
# tick that scans nothing must not reset the clock on a genuinely stuck record.
resource "aws_cloudwatch_metric_alarm" "reveal_reconcile_pending_stuck" {
  alarm_name          = "${var.project_name}-reveal-reconcile-pending-stuck"
  alarm_description   = "Reveal reconciliation has been retrying and failing in 10 of the last 12 five-minute periods (~50 of 60 min) — longer than the 3-attempt cap can produce. A commitment is stuck dangling: either its attempt counter is not persisting (#1353 compound failure) or new dangling commitments keep arriving."
  namespace           = aws_cloudwatch_log_metric_filter.reveal_reconcile_pending.metric_transformation[0].namespace
  metric_name         = aws_cloudwatch_log_metric_filter.reveal_reconcile_pending.metric_transformation[0].name
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 300
  evaluation_periods  = 12
  datapoints_to_alarm = 10
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = { Project = var.project_name }
}

# ── Deploy drift — is prod running origin/main's tip? (issue #1596 / #1346 AC2) ──
#
# ⚠️  This block introduces TWO AWS services the stack has not used before —
#     Lambda and EventBridge Rules. Per CLAUDE.md § "When to ask before
#     acting", that is Dan's call (infra/AWS-account owner) and this PR does
#     not apply it. Cost at the schedule below: 8,640 invocations/month at
#     128 MB and ~1s each — inside the perpetual Lambda free tier, and the
#     custom metric is 1 metric at $0.30/mo.
#
# WHY NOT A LOG METRIC FILTER, like every other alarm in this file. Because the
# question is not answerable from AWS data. "Is the running image tag the tip of
# origin/main" depends on a fact held outside the account, and the two in-repo
# places that already know it (the deploy workflow; the /health handler, which
# reports the running SHA as `version`) are both explicit #1596 anti-goals. The
# issue anticipated this and names the alternative: "a CloudWatch alarm **or
# scheduled job emitting a metric**". This is that job, and the alarm at the
# bottom of the block is an ordinary metric alarm again.
#
# The probe's own reasoning — including why it reads git's ref advertisement
# instead of the GitHub REST API, and why every "cannot tell" state publishes 1
# rather than nothing — is documented in lambda/deploy_drift/index.py. Its pure
# verdict logic is unit-tested in backend/tests/test_cloudwatch_alarms.py; the
# wiring below is text-pinned by the same file.

variable "deploy_drift_repo_url" {
  description = "Git remote whose branch tip the deploy-drift probe compares the running image tag against. Public HTTPS clone URL; read anonymously via git's ref advertisement (no token)."
  type        = string
  default     = "https://github.com/aprin-labs/archimedes"
}

variable "deploy_drift_git_ref" {
  description = "Fully-qualified ref the deploy-drift probe treats as the deploy source of truth. Must be fully qualified ('refs/heads/main', not 'main') — it is matched against the ref advertisement verbatim."
  type        = string
  default     = "refs/heads/main"
}

data "archive_file" "deploy_drift" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/deploy_drift"
  output_path = "${path.module}/lambda/deploy_drift.zip"
}

resource "aws_iam_role" "deploy_drift" {
  name = "${var.project_name}-deploy-drift"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = { Project = var.project_name }
}

# Read-only on ECS, write-only on one metric namespace and one log group. No
# managed policy (AWSLambdaBasicExecutionRole grants logs:* on every group).
resource "aws_iam_role_policy" "deploy_drift" {
  name = "${var.project_name}-deploy-drift"
  role = aws_iam_role.deploy_drift.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecs:DescribeServices"]
        Resource = aws_ecs_service.backend.id # ecs.tf — the service ARN
      },
      {
        # ecs:DescribeTaskDefinition does not support resource-level
        # permissions (AWS service-authorization reference), so "*" is the
        # only grantable form. It is a read of a task definition's own JSON —
        # no secret VALUES, which resolve from SSM at task start.
        Effect   = "Allow"
        Action   = ["ecs:DescribeTaskDefinition"]
        Resource = "*"
      },
      {
        # PutMetricData has no resource ARN either; the namespace condition is
        # the only way to scope it, and it does scope it — this role cannot
        # write into AWS/* or any other namespace.
        Effect    = "Allow"
        Action    = ["cloudwatch:PutMetricData"]
        Resource  = "*"
        Condition = { StringEquals = { "cloudwatch:namespace" = "Archimedes/Deploy" } }
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.deploy_drift.arn}:*"
      }
    ]
  })
}

# Declared explicitly (rather than left to Lambda's implicit creation) so the
# 90-day retention the AUDIT I8 section above establishes applies here too —
# an implicitly-created Lambda log group retains forever.
resource "aws_cloudwatch_log_group" "deploy_drift" {
  name              = "/aws/lambda/${var.project_name}-deploy-drift"
  retention_in_days = 90
  tags              = { Project = var.project_name }
}

resource "aws_lambda_function" "deploy_drift" {
  function_name    = "${var.project_name}-deploy-drift"
  description      = "Publishes Archimedes/Deploy DeployDrift: 1 when the running ECS image tag is not ${var.deploy_drift_git_ref}'s tip (or cannot be compared to it), 0 when it is."
  role             = aws_iam_role.deploy_drift.arn
  handler          = "index.handler"
  runtime          = "python3.12" # matches environment.yml's interpreter
  filename         = data.archive_file.deploy_drift.output_path
  source_code_hash = data.archive_file.deploy_drift.output_base64sha256
  timeout          = 30 # the probe's own HTTP timeout is 10s; this is headroom, not a second budget
  memory_size      = 128

  # NOT in the VPC, deliberately: the probe's only egress is a public HTTPS
  # GET, and putting it in the private subnets would route that through the
  # fck-nat instances (vpc.tf) for no benefit while adding a NAT dependency to
  # the thing that watches deploys.

  # ECS_CONTAINER names the container whose image tag carries the commit SHA
  # (ecs.tf's "backend"); nginx and auth are tagged from the same var but the
  # backend is the one /health's `version` comes from.
  environment {
    variables = {
      ECS_CLUSTER   = aws_ecs_cluster.main.name
      ECS_SERVICE   = aws_ecs_service.backend.name
      ECS_CONTAINER = "backend"
      REPO_URL      = var.deploy_drift_repo_url
      GIT_REF       = var.deploy_drift_git_ref
    }
  }

  depends_on = [aws_cloudwatch_log_group.deploy_drift]
  tags       = { Project = var.project_name }
}

# 5 minutes: fast enough that the alarm window below is made of real
# datapoints, slow enough to stay free.
resource "aws_cloudwatch_event_rule" "deploy_drift" {
  name                = "${var.project_name}-deploy-drift"
  description         = "Runs the deploy-drift probe every 5 minutes."
  schedule_expression = "rate(5 minutes)"
  tags                = { Project = var.project_name }
}

resource "aws_cloudwatch_event_target" "deploy_drift" {
  rule      = aws_cloudwatch_event_rule.deploy_drift.name
  target_id = "deploy-drift-lambda"
  arn       = aws_lambda_function.deploy_drift.arn
}

resource "aws_lambda_permission" "deploy_drift_events" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.deploy_drift.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.deploy_drift.arn
}

# "Longer than one deploy cycle", sized against the deploy workflow's own
# numbers rather than guessed: build-and-push has `timeout-minutes: 30` and
# deploy-ecs budgets DEPLOY_ROLLOUT_BUDGET_SECONDS = 1200 (20 min) for the
# rollout, so one full cycle is ~50 min worst case. The concurrency group
# QUEUES rather than cancels (#1346), so a merge landing behind an in-flight
# deploy can legitimately leave prod behind main for a second cycle. Requiring
# 10 breaching datapoints in a 2-hour window clears both, while still catching
# the failure #1346 named: prod stale for hours or days with nothing saying so.
#
# treat_missing_data = "breaching" is the load-bearing choice here. If the
# probe stops running — IAM revoked, function deleted, schedule disabled — the
# metric goes empty, and "empty" must not read as "aligned". A dead watchman
# pages, ~100 minutes later, instead of going quiet.
resource "aws_cloudwatch_metric_alarm" "deploy_drift" {
  alarm_name          = "${var.project_name}-deploy-drift"
  alarm_description   = "The running ECS backend image tag has not matched ${var.deploy_drift_git_ref}'s tip in 10 of the last 12 ten-minute periods (~100 of 120 min) — longer than one full deploy cycle. Either a deploy is failing silently or the probe itself stopped reporting; the probe's CloudWatch log line names which (reason=drifted | head-unreadable | image-untagged | image-tag-not-a-commit)."
  namespace           = "Archimedes/Deploy"
  metric_name         = "DeployDrift"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 600
  evaluation_periods  = 12
  datapoints_to_alarm = 10
  dimensions          = { Service = aws_ecs_service.backend.name }
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = { Project = var.project_name }
}

# ── Outputs ──────────────────────────────────────────────────────────────────

output "alerts_topic_arn" {
  description = "SNS topic ARN for CloudWatch alarms. Subscribe additional endpoints (Slack via chatbot, PagerDuty, etc.) here."
  value       = aws_sns_topic.alerts.arn
}

output "product_health_dashboard_name" {
  description = "CloudWatch dashboard name — 'is the site up and fast' (product-health)."
  value       = aws_cloudwatch_dashboard.product_health.dashboard_name
}

# All CloudWatch dashboard names (issue #418 acceptance — `terraform output
# cloudwatch_dashboard_names`). Consolidated 2026-08-20 from six per-subsystem
# dashboards (ops, aurora, elasticache, vpc_nat, alb, waf — ec2_backend having
# already been removed 2026-08-19 with the EC2 decommission) down to the three
# founder-readable dashboards below — CloudWatch bills the 4th+ dashboard at
# $3/mo each, and six SRE-shaped dashboards were not what the (non-SRE)
# founder needed to answer "is it up, are the DBs ok, are the background
# machines alive."
output "cloudwatch_dashboard_names" {
  description = "Names of every CloudWatch dashboard managed by Terraform."
  value = [
    aws_cloudwatch_dashboard.product_health.dashboard_name,
    aws_cloudwatch_dashboard.data_stores.dashboard_name,
    aws_cloudwatch_dashboard.machines_and_network.dashboard_name,
  ]
}

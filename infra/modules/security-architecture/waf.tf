# ── AWS WAF v2 ────────────────────────────────────────────────────────
#
# Attached to the ALB. Per the locked spec:
#   - Core Rule Set (AWSManagedRulesCommonRuleSet)
#   - Known Bad Inputs (AWSManagedRulesKnownBadInputsRuleSet)
#   - IP Reputation (AWSManagedRulesAmazonIpReputationList)
#   - SQL Database (AWSManagedRulesSQLiRuleSet)
#   - Rate-based rule: 1000 requests per 5 minutes per IP
#   - NO Bot Control (cost optimization)
#   - NO geo-blocking

resource "aws_wafv2_web_acl" "main" {
  name  = "${var.project_name}-waf"
  scope = "REGIONAL"

  default_action {
    allow {}
  }

  # ── Rate-based rule: 1000 req / 5 min / IP ────────────────

  rule {
    name     = "rate-limit"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 1000
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-rate-limit"
    }
  }

  # ── AWS Managed Rules ──────────────────────────────────────

  rule {
    name     = "aws-core-rules"
    priority = 10

    override_action {
      count {} # COUNT mode — observe before blocking
    }

    # COUNT mode for initial rollout (24-48h observation).
    # Core+SQLi will likely false-positive on LLM endpoints — users
    # pasting prompts with SQL-like words ("select top strategies by sharpe")
    # trip these rules. Flip to none {} (BLOCK) after observation period.
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-core-rules"
    }
  }

  rule {
    name     = "aws-known-bad-inputs"
    priority = 20

    override_action {
      count {} # COUNT mode — observe before blocking
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-known-bad-inputs"
    }
  }

  rule {
    name     = "aws-ip-reputation"
    priority = 30

    override_action {
      none {} # IP reputation can block immediately — known-bad IPs
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesAmazonIpReputationList"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-ip-reputation"
    }
  }

  rule {
    name     = "aws-sqli"
    priority = 40

    override_action {
      count {} # COUNT mode — LLM prompts with SQL-like words will false-positive
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesSQLiRuleSet"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-sqli"
    }
  }

  visibility_config {
    sampled_requests_enabled   = true
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project_name}-waf"
  }

  tags = {
    Project = var.project_name
  }
}

# ── Associate WAF with ALB ───────────────────────────────────

resource "aws_wafv2_web_acl_association" "main" {
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.main.arn
}

# ── Inbound mail for privacy@${var.domain_name} (#1460) ──────────────────────
#
# These resources were stood up BY CLI on 2026-08-21 to unblock PR #1432, which
# publishes privacy@archimedes-arc.com on /privacy and /terms as the contact for
# data-protection requests. The agreement at the time was that Terraform would
# follow; until it does, the resources exist only in the live account and
# nothing in the repo records that they exist.
#
# So this file is written to be IMPORTED onto what is already there, not
# applied fresh. The names below match the live resources exactly and must not
# be changed: the address is published on a live public page, and recreating
# the MX record drops inbound mail. Import commands are in the PR body.
#
# Deliberately NOT here: the apex TXT record. #1460 lists it, but the SPF work
# in #1462 has to manage that same record set (Route 53 keeps one record set
# per name+type, so SPF and the Google Search Console string must share a
# single resource). Declaring it in both files would give Terraform two
# resources for one record and a permanent diff. It lives in dns_email.tf as
# aws_route53_record.apex_txt.
#
# data.aws_caller_identity.current is declared in ecs.tf and reused below.

# ── SNS topic that receives the mail ─────────────────────────────────────────

resource "aws_sns_topic" "privacy_inbox" {
  name = "${var.project_name}-privacy-inbox"
  tags = { Project = var.project_name }
}

resource "aws_sns_topic_policy" "privacy_inbox" {
  arn = aws_sns_topic.privacy_inbox.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSESPublish"
        Effect = "Allow"
        Principal = {
          Service = "ses.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.privacy_inbox.arn
        # Scoped to this account so another SES tenant cannot publish into our
        # privacy inbox — the condition the live policy already carries.
        Condition = {
          StringEquals = {
            "AWS:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      },
    ]
  })
}

# Optional email subscription, mirroring cloudwatch.tf's alerts_email pattern.
# Created only when var.privacy_inbox_email is non-empty, and left empty in the
# repo on purpose: the live endpoint is a personal inbox, and a personal
# address does not belong in a public repository. Set it in
# infra/terraform.tfvars (gitignored) to bring the existing subscription under
# management; leave it unset and the live subscription simply stays unmanaged,
# which is the status quo rather than a regression.
resource "aws_sns_topic_subscription" "privacy_inbox_email" {
  count     = var.privacy_inbox_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.privacy_inbox.arn
  protocol  = "email"
  endpoint  = var.privacy_inbox_email
}

# ── SES receipt rules ────────────────────────────────────────────────────────

resource "aws_ses_receipt_rule_set" "inbound" {
  rule_set_name = "${var.project_name}-inbound"
}

resource "aws_ses_active_receipt_rule_set" "inbound" {
  rule_set_name = aws_ses_receipt_rule_set.inbound.rule_set_name
}

resource "aws_ses_receipt_rule" "privacy_inbox" {
  name          = "privacy-inbox"
  rule_set_name = aws_ses_receipt_rule_set.inbound.rule_set_name
  recipients    = ["privacy@${var.domain_name}"]
  enabled       = true
  scan_enabled  = true

  # Optional, NOT Require, and left that way deliberately (#1460 anti-goal):
  # Require bounces senders on older TLS, and this is the address people are
  # told to use for data-protection requests — the one inbox that must not
  # silently reject a legitimate sender.
  tls_policy = "Optional"

  sns_action {
    topic_arn = aws_sns_topic.privacy_inbox.arn
    encoding  = "UTF-8"
    position  = 1
  }
}

# ── MX ───────────────────────────────────────────────────────────────────────
#
# Points the domain's inbound mail at SES. Recreating this record drops inbound
# mail for the published privacy address, so the plan for it must show no
# changes — see the acceptance notes in #1460.
resource "aws_route53_record" "mx" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.domain_name
  type    = "MX"
  ttl     = 300
  records = ["10 inbound-smtp.${var.aws_region}.amazonaws.com"]
}

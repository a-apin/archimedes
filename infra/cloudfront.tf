# ── CloudFront distribution in front of the ALB ───────────────────────
#
# OPTIONAL / virality-prep (issue #155). INERT until `terraform apply` runs
# with AWS credentials — merging the PR changes nothing on the live stack.
#
# Edge layer that sits in front of the existing ALB (aws_lb.main in alb.tf):
#   - Static assets (/assets/*, /static/*, *.js, *.css) cached 1h at the edge.
#   - /api/* and /events/* NEVER cached (dynamic + SSE pass-through).
#   - HTML ("/") cached 60s, respecting origin Cache-Control.
#   - Origin Shield in us-east-1 (cheapest ACM region; collapses origin fetches).
#   - Edge rate-limit 2000 req/min/IP via a CLOUDFRONT-scope WAF (us-east-1),
#     defense-in-depth alongside the REGIONAL WAF on the ALB (waf.tf) and
#     slowapi in the backend.
#
# CloudFront REQUIRES the viewer ACM cert AND any attached WAF to live in
# us-east-1 — hence the aws.us_east_1 provider alias below.

# ── us-east-1 provider (CloudFront ACM + WAF must be us-east-1) ──
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

# ── ACM certificate for CloudFront (us-east-1) ────────────────
# Separate from the REGIONAL ALB cert (aws_acm_certificate.main in alb.tf,
# us-east-1). CloudFront can only attach a us-east-1 cert.
resource "aws_acm_certificate" "cloudfront" {
  provider                  = aws.us_east_1
  domain_name               = var.domain_name
  subject_alternative_names = ["www.${var.domain_name}"]
  validation_method         = "DNS"

  tags = {
    Project = var.project_name
  }

  lifecycle {
    create_before_destroy = true
  }
}

# DNS validation records for the CloudFront cert (reuses the existing zone
# data source defined in alb.tf as data.aws_route53_zone.main).
resource "aws_route53_record" "cloudfront_acm_validation" {
  for_each = {
    for dvo in aws_acm_certificate.cloudfront.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id         = data.aws_route53_zone.main.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "cloudfront" {
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.cloudfront.arn
  validation_record_fqdns = [for r in aws_route53_record.cloudfront_acm_validation : r.fqdn]
}

# ── Edge WAF (CLOUDFRONT scope, us-east-1) ────────────────────
# Rate-limit 2000 req / 5 min / IP. CloudFront/WAFv2 rate statements use a
# 5-minute window; 2000/min ≈ 10000/5min, but the issue specifies a 2000/min
# guard, so we set the 5-min limit to 2000 to enforce the stricter 2000-per-
# rolling-window bound the spec asks for at the edge.
resource "aws_wafv2_web_acl" "cloudfront" {
  provider = aws.us_east_1
  name     = "${var.project_name}-cloudfront-waf"
  scope    = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "edge-rate-limit"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 2000
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-edge-rate-limit"
    }
  }

  visibility_config {
    sampled_requests_enabled   = true
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project_name}-cloudfront-waf"
  }

  tags = {
    Project = var.project_name
  }
}

# ── Cache policies ────────────────────────────────────────────

# Long-lived caching for static assets (1h default, respects max-age up to 1d).
resource "aws_cloudfront_cache_policy" "static_assets" {
  name        = "${var.project_name}-static-assets"
  default_ttl = 3600
  min_ttl     = 0
  max_ttl     = 86400

  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    query_strings_config {
      query_string_behavior = "none"
    }
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true
  }
}

# Short-lived caching for HTML (60s), respecting origin Cache-Control.
resource "aws_cloudfront_cache_policy" "html" {
  name        = "${var.project_name}-html"
  default_ttl = 60
  min_ttl     = 0
  max_ttl     = 60

  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "whitelist"
      headers {
        items = ["Host"]
      }
    }
    query_strings_config {
      query_string_behavior = "all"
    }
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true
  }
}

# Origin request policy: forward everything to the ALB for dynamic paths so
# the backend sees the real Host, headers, cookies, and query string.
resource "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "${var.project_name}-all-viewer"

  cookies_config {
    cookie_behavior = "all"
  }
  headers_config {
    header_behavior = "allViewerAndWhitelistCloudFront"
    headers {
      # CloudFront-Viewer-Country + device headers feed the visitor-insights
      # instrument (#787): the backend can't see the real client IP (CloudFront
      # masks it), so these CloudFront-derived headers are the only clean source
      # for visitor geography + device class. Forwarded to origin here.
      items = [
        "CloudFront-Forwarded-Proto",
        "CloudFront-Viewer-Country",
        "CloudFront-Is-Mobile-Viewer",
        "CloudFront-Is-Tablet-Viewer",
        "CloudFront-Is-Desktop-Viewer",
        "CloudFront-Is-SmartTV-Viewer",
      ]
    }
  }
  query_strings_config {
    query_string_behavior = "all"
  }
}

# Response headers policy: HSTS + standard security headers at the edge.
resource "aws_cloudfront_response_headers_policy" "security" {
  name = "${var.project_name}-security-headers"

  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
  }
}

# ── Distribution ──────────────────────────────────────────────

locals {
  alb_origin_id = "${var.project_name}-alb-origin"
}

resource "aws_cloudfront_distribution" "main" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "${var.project_name} edge CDN (virality tier, issue #155)"
  price_class     = "PriceClass_100" # NA + EU edges only (cost containment)
  aliases         = ["${var.domain_name}"]

  origin {
    domain_name = aws_lb.main.dns_name
    origin_id   = local.alb_origin_id

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only" # CloudFront → ALB always over TLS
      origin_ssl_protocols   = ["TLSv1.2"]
    }

    # Origin Shield in us-east-1 — collapses concurrent origin fetches and
    # cuts cost (cheapest region; per the issue spec).
    origin_shield {
      enabled              = true
      origin_shield_region = "us-east-1"
    }
  }

  # Default behavior: HTML — cached 60s, respects origin Cache-Control.
  default_cache_behavior {
    target_origin_id           = local.alb_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.html.id
    origin_request_policy_id   = aws_cloudfront_origin_request_policy.all_viewer.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
    compress                   = true
  }

  # /api/* — NEVER cached (dynamic responses: live prices, traces). Uses the
  # AWS-managed CachingDisabled policy so nothing is cached at the edge.
  ordered_cache_behavior {
    path_pattern               = "/api/*"
    target_origin_id           = local.alb_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id   = aws_cloudfront_origin_request_policy.all_viewer.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
    compress                   = false
  }

  # /events/* — SSE stream. NEVER cached + no compression (compression buffers
  # and breaks streaming). Pass straight through to the origin.
  ordered_cache_behavior {
    path_pattern             = "/events/*"
    target_origin_id         = local.alb_origin_id
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = aws_cloudfront_origin_request_policy.all_viewer.id
    compress                 = false
  }

  # /assets/* — built JS/CSS bundles. Cached 1h at the edge.
  ordered_cache_behavior {
    path_pattern               = "/assets/*"
    target_origin_id           = local.alb_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.static_assets.id
    origin_request_policy_id   = aws_cloudfront_origin_request_policy.all_viewer.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
    compress                   = true
  }

  # /static/* — static files. Cached 1h at the edge.
  ordered_cache_behavior {
    path_pattern               = "/static/*"
    target_origin_id           = local.alb_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.static_assets.id
    origin_request_policy_id   = aws_cloudfront_origin_request_policy.all_viewer.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
    compress                   = true
  }

  # *.js — top-level JS files. Cached 1h at the edge.
  ordered_cache_behavior {
    path_pattern               = "*.js"
    target_origin_id           = local.alb_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.static_assets.id
    origin_request_policy_id   = aws_cloudfront_origin_request_policy.all_viewer.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
    compress                   = true
  }

  # *.css — top-level CSS files. Cached 1h at the edge.
  ordered_cache_behavior {
    path_pattern               = "*.css"
    target_origin_id           = local.alb_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.static_assets.id
    origin_request_policy_id   = aws_cloudfront_origin_request_policy.all_viewer.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
    compress                   = true
  }

  web_acl_id = aws_wafv2_web_acl.cloudfront.arn

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.cloudfront.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  tags = {
    Project = var.project_name
  }
}

# AWS-managed CachingDisabled policy (well-known id) for the dynamic paths.
data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

# ── Route 53 alias: ${var.domain_name} → CloudFront ───────────
# Replaces the direct A record to the EC2 EIP. Uses the existing zone data
# source (data.aws_route53_zone.main, defined in alb.tf).
resource "aws_route53_record" "apex_cloudfront" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.main.domain_name
    zone_id                = aws_cloudfront_distribution.main.hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "apex_cloudfront_ipv6" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.domain_name
  type    = "AAAA"

  alias {
    name                   = aws_cloudfront_distribution.main.domain_name
    zone_id                = aws_cloudfront_distribution.main.hosted_zone_id
    evaluate_target_health = false
  }
}

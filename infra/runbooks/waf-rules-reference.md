# WAF Rule Reference — Archimedes

> Reference for `infra/waf.tf` (`aws_wafv2_web_acl.main`), as of 2026-06-12.
> This documents the **already-deployed** WAF; it is not a change. Tuning
> suggestions at the end are proposals, not applied.

**Scope:** regional WAF associated with `aws_lb.main` (the ALB).
**Default action:** `allow` — the ACL is allow-by-default; only the rules below
block. **No geo-blocking** is configured (intentional).

Every rule has `cloudwatch_metrics_enabled = true` and `sampled_requests_enabled
= true`, so each emits a CloudWatch metric under namespace `AWS/WAFV2` and you
can inspect sampled requests in the console.

## Rules (in priority order)

| Priority | Name | Action | What it does |
|---|---|---|---|
| 1 | `rate-limit` | **BLOCK** | Rate-based: blocks an IP exceeding **1000 requests / 5 min** (rolling). `aggregate_key_type = IP`. First line of defense against bursty abuse / scraping. |
| 10 | `aws-core-rules` | **COUNT** | `AWSManagedRulesCommonRuleSet` in **count mode** — observes (does not block) the OWASP-ish common ruleset. Counting first avoids false-positive blocks on the app's own traffic before you've seen the sampled hits. |
| 20 | `aws-known-bad-inputs` | **COUNT** | `AWSManagedRulesKnownBadInputsRuleSet` in **count mode** — known exploit patterns, observe-only for now. |
| 30 | `aws-ip-reputation` | **BLOCK** | `AWSManagedRulesAmazonIpReputationList` — blocks immediately (no override). Amazon-maintained known-bad source IPs; low false-positive risk, so blocking from day one is safe. |
| 40 | `aws-sqli` | **COUNT** | `AWSManagedRulesSQLiRuleSet` — SQL-injection signatures, **observe-only on purpose**: LLM prompts and generated-strategy text routinely contain SQL-like tokens (`SELECT`, `DROP`, `--`) that would false-positive against a real backend whose DB access is parameterized. Counting here is the correct call, not a gap. |

### Action posture: two block, three count
IP-reputation and the rate limiter have very low false-positive risk, so they
**block** immediately. The Common, Known-Bad-Inputs, and SQLi managed groups can
match legitimate app payloads — rich JSON bodies, base64, and (for SQLi) the
LLM/strategy text that is the whole point of this product — so they run in
**count** mode. The tuning workflow is: watch their CloudWatch counts + sampled
requests, confirm no legitimate traffic matches, then flip `count {}` →
`none {}` per rule. **SQLi is the one to be most cautious about promoting** given
the LLM payloads; prefer a scoped-down exclusion over blocking the whole group.

## Tuning / hardening proposals (NOT applied)

1. **Promote Common + Known-Bad-Inputs to block** once their count metrics show
   no legitimate matches. One-line change each: `count {}` → `none {}` in the
   rule's `override_action`.
2. **Scope the rate limit per route.** 1000/5min is global per IP. The
   `/api/generate` LLM path is expensive; consider a stricter rate-based rule
   scoped with a `scope_down_statement` (byte-match on URI prefix `/api/generate`)
   at a lower limit (e.g. 30/5min).
3. **Alarm on WAF blocks.** Add a CloudWatch alarm on the `BlockedRequests`
   metric (namespace `AWS/WAFV2`, dimension the web ACL) to the SNS topic in
   `cloudwatch.tf` — a sudden block spike is either an attack or a bad tuning
   change. (Left out of the first `cloudwatch.tf` cut to avoid guessing the
   exact metric dimensions without console access.)
4. **Excluded-rule granularity.** If a single sub-rule in a managed group causes
   false positives, exclude just that `rule_action_override` rather than counting
   the whole group.

## Inspecting WAF activity

```bash
# List the metrics the WAF is emitting:
aws cloudwatch list-metrics --namespace AWS/WAFV2 --region us-east-1

# Blocked vs allowed counts for the ACL over the last hour (fill in the ACL
# dimensions from the console — Region/Rule/WebACL):
aws cloudwatch get-metric-statistics --namespace AWS/WAFV2 \
  --metric-name BlockedRequests --start-time "$(date -u -v-1H +%FT%TZ)" \
  --end-time "$(date -u +%FT%TZ)" --period 300 --statistics Sum --region us-east-1
```

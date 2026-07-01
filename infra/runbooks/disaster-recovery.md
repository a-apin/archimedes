# Disaster Recovery Runbook — Archimedes

> **Status:** Authored 2026-06-12. **Not yet drilled.** Commands below were
> written against the Terraform in `infra/` (`aws_instance.archimedes`,
> `aws_lb.main`, `aws_rds_cluster.main`) and the AWS-access protocol in the root
> `CLAUDE.md`, but have **not** been executed end-to-end in this environment (no
> AWS credentials here). Treat every command as *review-then-run*, and schedule a
> real game-day drill (see § Drills) before relying on this.

Region: `us-east-1`. Account / profile: `archimedes` (see `CLAUDE.md` § AWS
account access). All admin access is via **SSM Session Manager**, not SSH.

---

## Objectives (proposed — confirm with stakeholders)

| Metric | Target | Rationale |
|---|---|---|
| **RTO** (time to restore service) | ≤ 1 hour | Single-region, single-EC2 app host; Aurora restore dominates. |
| **RPO** (max data loss) | ≤ 5 minutes | Aurora continuous backup → PITR to ~any second within the retention window. |

Aurora `backup_retention_period = 7` (days) is set in `infra/aurora.tf`, so
point-in-time recovery is available across a rolling 7-day window with no extra
configuration.

---

## Failure scenarios & responses

### 1. Application host (EC2) down / unhealthy
**Detect:** `archimedes-ec2-status-check-failed` or `archimedes-alb-unhealthy-hosts`
alarm (see `infra/cloudwatch.tf`), or `https://archimedes-arc.com/` 502/503.

**Respond:**
1. Confirm the target health:
   ```bash
   aws elbv2 describe-target-health \
     --target-group-arn "$(aws elbv2 describe-target-groups \
        --names archimedes-backend-tg --query 'TargetGroups[0].TargetGroupArn' --output text)" \
     --region us-east-1
   ```
2. Try an in-place recovery first (fastest). Open a session and restart the stack:
   ```bash
   aws ssm start-session --target <instance-id> --region us-east-1
   # on host:
   cd /opt/archimedes && docker compose ps && docker compose up -d
   ```
3. If the host itself is gone, recreate it from Terraform (the app is
   stateless — all state is in Aurora/ElastiCache):
   ```bash
   cd infra && terraform plan -target=aws_instance.archimedes
   terraform apply -target=aws_instance.archimedes
   ```
   `user-data.sh` re-bootstraps Docker + pulls the stack on first boot. The ALB
   target group re-attaches via `aws_lb_target_group_attachment.backend`.

### 2. Database (Aurora) corruption or bad migration
**Detect:** app 5xx spike, `archimedes-aurora-*` alarms, or a known-bad deploy.
**Respond:** point-in-time restore — see
[`aurora-backup-restore.md`](aurora-backup-restore.md). RPO ≤ 5 min, RTO bounded
by clone+failover (typically 10–30 min for a small cluster).

### 3. Accidental WAF/SG lockout
The WAF (`infra/waf.tf`) and security groups can lock out legitimate traffic if
mis-tuned. Recover by reverting the offending Terraform change and re-applying;
if the console is reachable, temporarily set the WAF default action to `allow`
on `aws_wafv2_web_acl.main` while you diagnose. Never leave it on `allow`.

### 4. Region outage
Out of scope for the current single-region design. Documented gap: there is no
cross-region replica today. If this becomes a requirement, the cheapest first
step is an Aurora cross-region automated-backup replication
(`aws rds start-db-instance-automated-backups-replication`) into a DR region,
plus Terraform parameterized on region.

---

## Restore-order dependency

When rebuilding from scratch, apply in this order (the Terraform graph mostly
enforces it, but for `-target` restores follow it manually):

1. `vpc.tf` (network) → 2. `aurora.tf` + `elasticache.tf` (data) →
3. `alb.tf` + `waf.tf` (edge) → 4. `main.tf` (`aws_instance.archimedes`, app) →
5. `cloudwatch.tf` (observability).

---

## Drills (do this before trusting the runbook)

- [ ] **PITR drill:** restore Aurora to a *new* cluster at a timestamp 1 h ago,
      point a throwaway app instance at it, confirm data, then destroy. Time it
      — record the actual RTO.
- [ ] **Host-loss drill:** terminate the EC2 in a maintenance window, run the
      §1.3 Terraform recreate, confirm the ALB target goes healthy. Time it.
- [ ] **Alarm drill:** stop the backend container; confirm
      `archimedes-alb-unhealthy-hosts` fires to the SNS topic and the subscribed
      email/Slack receives it.

Record actual measured RTO/RPO here after the first drill and revise the targets
above to reality.

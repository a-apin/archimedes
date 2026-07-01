# Aurora Backup & Restore Runbook — Archimedes

> **Status:** Authored 2026-06-12, **not yet drilled.** Commands target the
> cluster defined in `infra/aurora.tf` (`aws_rds_cluster.main`, identifier
> `archimedes-aurora`, instance `archimedes-aurora-1`, engine `aurora-postgresql`
> 16.4). Run in `us-east-1` with `AWS_PROFILE=ArchimedesDanAdmin`. **Review each command
> before running** — they were not executed in the authoring environment.

---

## What backups exist today

`infra/aurora.tf` sets:

```hcl
backup_retention_period = 7   # days of continuous backup → PITR window
```

This gives **two** recovery mechanisms automatically, no extra config:

1. **Continuous backup / PITR** — restore to *any second* within the last 7
   days (RPO ≈ 5 min, often tighter). This is the primary mechanism.
2. **Automated daily snapshots** — retained for the same 7 days.

There is currently **no** long-term/manual snapshot schedule beyond the 7-day
window. If you need monthly/quarterly retention for compliance, see § Long-term
snapshots below.

> ⚠️ `aurora.tf` may set `skip_final_snapshot = true` for hackathon convenience.
> **Before any destructive action on the cluster, confirm** and override:
> `terraform apply -var ...` is not enough — take a manual snapshot first (§ Manual
> snapshot). Losing the cluster with `skip_final_snapshot = true` and no manual
> snapshot is unrecoverable.

---

## Take a manual snapshot (do this before risky migrations / destroys)

```bash
aws rds create-db-cluster-snapshot \
  --db-cluster-identifier archimedes-aurora \
  --db-cluster-snapshot-identifier archimedes-aurora-manual-$(date +%Y%m%d-%H%M) \
  --region us-east-1
# wait until available:
aws rds wait db-cluster-snapshot-available \
  --db-cluster-snapshot-identifier <the-id-you-just-used> --region us-east-1
```

---

## Point-in-time restore (primary recovery path)

PITR **always restores into a NEW cluster** — it never overwrites the live one.
That is a feature: you validate the restored data before cutting over.

```bash
# 1. Restore to a new cluster at a chosen timestamp (UTC).
aws rds restore-db-cluster-to-point-in-time \
  --source-db-cluster-identifier archimedes-aurora \
  --db-cluster-identifier archimedes-aurora-restore \
  --restore-to-time 2026-06-12T13:45:00Z \
  --region us-east-1
#   …or for the latest possible point: add  --use-latest-restorable-time
#   (and drop --restore-to-time).

# 2. A restored CLUSTER has no instances — add one (match the live class).
aws rds create-db-instance \
  --db-instance-identifier archimedes-aurora-restore-1 \
  --db-cluster-identifier archimedes-aurora-restore \
  --engine aurora-postgresql \
  --db-instance-class db.serverless \
  --region us-east-1
aws rds wait db-instance-available \
  --db-instance-identifier archimedes-aurora-restore-1 --region us-east-1

# 3. Get the new endpoint and validate before cutover.
aws rds describe-db-clusters \
  --db-cluster-identifier archimedes-aurora-restore \
  --query 'DBClusters[0].Endpoint' --output text --region us-east-1
```

**Validate** against the restore endpoint (read-only first): row counts on
`strategies`, `backtests`, `reasoning_traces`, `strategy_proposals`; spot-check
the most recent rows; confirm the bad migration/corruption is absent.

### Cutover options

- **Preferred (no app reconfig):** rename clusters so the app's `DATABASE_URL`
  keeps working. Rename live out of the way, then restore into its name:
  ```bash
  aws rds modify-db-cluster --db-cluster-identifier archimedes-aurora \
    --new-db-cluster-identifier archimedes-aurora-broken --apply-immediately --region us-east-1
  aws rds modify-db-cluster --db-cluster-identifier archimedes-aurora-restore \
    --new-db-cluster-identifier archimedes-aurora --apply-immediately --region us-east-1
  ```
  ⚠️ Renaming changes the endpoint host; if `DATABASE_URL` pins the endpoint DNS,
  update SSM and restart the app (`docker compose up -d`). Confirm which form the
  app uses before relying on "no reconfig".
- **Alternative:** point the app at the restore endpoint by updating the
  `DATABASE_URL` secret in SSM and restarting the stack. Faster to reason about,
  but leaves cluster names mismatched vs Terraform state — reconcile after.

### Reconcile Terraform after a manual restore

Manual console/CLI restores drift from Terraform state. After the incident,
either `terraform import` the surviving cluster back under `aws_rds_cluster.main`
or plan a maintenance window to recreate cleanly. Do **not** `terraform apply`
blind post-restore — it may try to destroy the manually-restored cluster.

---

## Restore from a specific snapshot

```bash
aws rds restore-db-cluster-from-snapshot \
  --db-cluster-identifier archimedes-aurora-fromsnap \
  --snapshot-identifier <snapshot-id> \
  --engine aurora-postgresql \
  --region us-east-1
# then create-db-instance as in PITR step 2.
```

---

## Long-term snapshots (optional hardening, not yet implemented)

The 7-day window does not satisfy monthly/quarterly retention. Options, cheapest
first:

1. **AWS Backup plan** — a managed monthly/quarterly schedule with its own vault
   and lifecycle. Add as `aws_backup_plan` + `aws_backup_selection` in a new
   `infra/backup.tf`. Recommended if compliance retention is ever required.
2. **EventBridge + Lambda** to call `create-db-cluster-snapshot` on a cron and
   prune by age. More moving parts; prefer AWS Backup.

Neither is wired today — flagged here so the gap is explicit rather than silent.

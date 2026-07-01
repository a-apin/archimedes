#!/usr/bin/env bash
# Bake a backend AMI for the virality-tier auto-scaling group (issue #155).
#
# Snapshots the running backend EC2 instance into an AMI that the ASG launch
# template (infra/asg.tf, var.backend_ami_id) consumes. Each ASG instance
# boots from this AMI, clones the repo, and starts the production stack
# (backend + nginx; Postgres/Redis live in Aurora + ElastiCache, so the AMI
# carries NO database state).
#
# This is an OPTIONAL, operator-run step — NOT wired into CI. Run it from a
# workstation with AWS creds (SecurityAudit + EC2 image perms), or extend it
# into a scheduled bake later.
#
# Precedent: infra/user-data.sh is the bootstrap baked into the AMI; this
# script is the snapshot-and-register half. Style follows
# infra/scripts/setup-https.sh.
#
# Usage:
#   AWS_PROFILE=ArchimedesDanAdmin ./infra/scripts/bake-backend-ami.sh
#   AWS_PROFILE=ArchimedesDanAdmin SOURCE_INSTANCE_ID=i-0abc... ./infra/scripts/bake-backend-ami.sh
#
# On success it prints the new AMI id. Feed it back to Terraform via:
#   terraform apply -var "backend_ami_id=ami-0newbakedami..."
# (or export TF_VAR_backend_ami_id=ami-...).
#
# SECURITY: do NOT bake secrets into the AMI. The instance pulls DATABASE_URL
# / REDIS_URL / API keys from SSM Parameter Store at boot (see deploy
# convention). Verify /opt/archimedes/.env is absent or scrubbed before
# baking if you snapshot a live box.

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PROJECT="${PROJECT_NAME:-archimedes}"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
AMI_NAME="${PROJECT}-backend-${TIMESTAMP}"

echo "=== [1/4] Resolve source backend instance ==="
if [ -n "${SOURCE_INSTANCE_ID:-}" ]; then
  INSTANCE_ID="${SOURCE_INSTANCE_ID}"
  echo "Using SOURCE_INSTANCE_ID override: ${INSTANCE_ID}"
else
  # Discover the tagged backend EC2 (Name=archimedes-server, from main.tf).
  INSTANCE_ID="$(aws ec2 describe-instances \
    --region "${REGION}" \
    --filters "Name=tag:Name,Values=${PROJECT}-server" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].InstanceId' \
    --output text)"
  if [ -z "${INSTANCE_ID}" ] || [ "${INSTANCE_ID}" = "None" ]; then
    echo "ERROR: could not find a running instance tagged Name=${PROJECT}-server." >&2
    echo "       Pass SOURCE_INSTANCE_ID=i-... explicitly." >&2
    exit 1
  fi
  echo "Discovered source instance: ${INSTANCE_ID}"
fi

echo "=== [2/4] Pre-bake secret check (advisory) ==="
echo "Reminder: the AMI must NOT contain secrets. The instance pulls"
echo "DATABASE_URL / REDIS_URL / API keys from SSM at boot. If you are"
echo "snapshotting a live box, ensure /opt/archimedes/.env is scrubbed first:"
echo "  aws ssm send-command --instance-ids ${INSTANCE_ID} \\"
echo "    --document-name AWS-RunShellScript \\"
echo "    --parameters 'commands=[\"sudo shred -u /opt/archimedes/.env || true\"]' \\"
echo "    --region ${REGION}"

echo "=== [3/4] Create AMI (no-reboot to avoid downtime) ==="
AMI_ID="$(aws ec2 create-image \
  --region "${REGION}" \
  --instance-id "${INSTANCE_ID}" \
  --name "${AMI_NAME}" \
  --description "Archimedes backend AMI baked ${TIMESTAMP} from ${INSTANCE_ID} (issue #155)" \
  --no-reboot \
  --tag-specifications \
    "ResourceType=image,Tags=[{Key=Project,Value=${PROJECT}},{Key=Name,Value=${AMI_NAME}}]" \
    "ResourceType=snapshot,Tags=[{Key=Project,Value=${PROJECT}},{Key=Name,Value=${AMI_NAME}}]" \
  --query 'ImageId' \
  --output text)"
echo "Registering AMI: ${AMI_ID}"

echo "=== [4/4] Wait for AMI to become available ==="
aws ec2 wait image-available --region "${REGION}" --image-ids "${AMI_ID}"

echo ""
echo "AMI ready: ${AMI_ID}"
echo ""
echo "Next step — point the ASG launch template at it:"
echo "  cd infra && terraform apply -var \"backend_ami_id=${AMI_ID}\""
echo "or export TF_VAR_backend_ami_id=${AMI_ID} before your apply."

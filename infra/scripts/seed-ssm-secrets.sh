#!/usr/bin/env bash
# Seed SSM secrets from a local env file.
# Usage: ./infra/scripts/seed-ssm-secrets.sh /path/to/secrets.env
#
# The secrets file is NOT committed — it's a one-time operator artifact.
# Format: KEY=VALUE (one per line, same as .env)
set -euo pipefail

SECRETS_FILE="${1:?Usage: $0 /path/to/secrets.env}"
REGION="${AWS_REGION:-us-east-1}"
PREFIX="${AWS_SSM_PATH_PREFIX:-/archimedes/prod}"

if [ ! -f "$SECRETS_FILE" ]; then
  echo "Error: $SECRETS_FILE not found" >&2
  exit 1
fi

echo "Seeding SSM parameters from $SECRETS_FILE → ${PREFIX}/* (region: $REGION)"

count=0
while IFS='=' read -r key value; do
  # Skip empty lines and comments
  [[ -z "$key" || "$key" =~ ^# ]] && continue
  # Strip quotes from value
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"

  if [ -z "$value" ]; then
    echo "  SKIP: $key (empty value)"
    continue
  fi

  aws ssm put-parameter \
    --name "${PREFIX}/${key}" \
    --value "$value" \
    --type SecureString \
    --overwrite \
    --region "$REGION" \
    --query "Version" --output text 2>&1 | xargs -I{} echo "  OK: $key (v{})"
  count=$((count + 1))
done < "$SECRETS_FILE"

echo "Done. Seeded $count parameters under ${PREFIX}/"
echo "Verify: aws ssm get-parameters-by-path --path ${PREFIX}/ --recursive --query 'Parameters[*].Name' --region $REGION"

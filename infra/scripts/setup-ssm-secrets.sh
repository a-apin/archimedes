#!/usr/bin/env bash
# Archimedes — push app secrets to SSM Parameter Store (SecureString).
#
# Secrets NEVER live in the repo, in Terraform state, or in GitHub. They live in
# SSM Parameter Store under /archimedes/prod/* as SecureString, and the EC2/ECS
# instance role reads them at deploy/runtime. This script pushes them.
#
# Values are read from your SHELL ENVIRONMENT (never hardcoded here). Export the
# ones you have, then run. Missing ones are skipped, so partial runs are fine:
#
#   export PINATA_JWT='...'; export CIRCLE_API_KEY='...'
#   AWS_PROFILE=ArchimedesDanAdmin conda run -n archimedes ./setup-ssm-secrets.sh          # dry run
#   AWS_PROFILE=ArchimedesDanAdmin conda run -n archimedes ./setup-ssm-secrets.sh --apply  # write them
#
# Tip: keep values in a gitignored file and `set -a; source secrets.env; set +a` first.
# The script prints parameter NAMES only — never the secret values.
set -euo pipefail

PREFIX="/archimedes/prod"
# NAMES match what services/secrets_service.load_ssm_secrets() reads under
# /archimedes/prod/*. Missing env vars are skipped, so partial runs are fine.
PARAMS=(
  # --- Current runtime secrets (loaded into os.environ at backend startup) ---
  LLM_PROVIDER             # LLM backend selector (GLM today; revisited when Bedrock lands, T3.1)
  LLM_AUTH_TOKEN           # LLM API auth token (BYOK / current provider)
  LLM_BASE_URL             # LLM endpoint base URL
  EMAIL_ENCRYPTION_KEY     # at-rest encryption key for stored user emails
  AURORA_MASTER_PASSWORD   # DB master password (mirror TF_VAR_aurora_master_password)
  # --- Forthcoming, as features land (roadmap T1.x) ---
  PINATA_JWT               # IPFS pinning for reasoning-trace provenance (T1.4)
  CIRCLE_API_KEY           # Circle wallets / Gateway nanopayments (T1.2)
  CIRCLE_ENTITY_SECRET     # Circle dev-controlled wallet entity secret
  WALLET_ID                # Circle DCW wallet UUID (oracle/agent Circle signer) — T3.2
  WALLET_ADDRESS           # Circle DCW EVM address (public; agent signer) — T3.2
  INTERNAL_AGENT_API_KEY   # X-Internal-Agent-Key runner->backend auth — T3.2
)
# NOTE: VITE_CIRCLE_CLIENT_KEY is a BUILD-TIME secret baked into the UI bundle at
# `docker compose build` — it lives in the box-local .env (seeded by user-data.sh),
# NOT read from SSM at runtime. Do not add build-time secrets here.

APPLY=false; for a in "$@"; do case "$a" in
  --apply) APPLY=true;; -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
  *) echo "unknown arg: $a" >&2; exit 2;; esac; done
$APPLY && echo ">>> APPLY MODE — writing SecureString params under ${PREFIX}/" \
        || echo ">>> DRY RUN — re-run with --apply to write. Values are read from env; names only are shown."

put=0; skip=0
for name in "${PARAMS[@]}"; do
  val="${!name:-}"
  path="${PREFIX}/${name}"
  if [ -z "$val" ]; then
    printf '  skip  %s   (env var %s not set)\n' "$path" "$name"; skip=$((skip+1)); continue
  fi
  printf '  put   %s   (SecureString, %d chars)\n' "$path" "${#val}"
  if $APPLY; then
    aws ssm put-parameter --name "$path" --type SecureString --value "$val" --overwrite >/dev/null
  fi
  put=$((put+1))
done

echo
echo "summary: ${put} to write, ${skip} skipped (env not set)."
$APPLY || echo "(dry run — re-run with --apply to write the ${put} parameter(s))"
echo "Verify (names + metadata only, never values):"
echo "  aws ssm get-parameters-by-path --path ${PREFIX} --query 'Parameters[].Name'"

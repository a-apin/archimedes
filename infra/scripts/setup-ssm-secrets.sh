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
  DATABASE_URL             # Aurora connection URL — consumed by the backend Fargate task (ecs.tf secrets) AND the relocated oracle/agent runners (fetch-secrets.sh); ecs.tf's header flags this as not-yet-seeded
  REDIS_URL                # ElastiCache connection URL — same two consumers; same not-yet-seeded gap
  # --- Forthcoming, as features land (roadmap T1.x) ---
  PINATA_JWT               # IPFS pinning for reasoning-trace provenance (T1.4)
  CIRCLE_API_KEY           # Circle wallets / Gateway nanopayments (T1.2) — also the oracle+agent Circle DCW signer (#1065)
  CIRCLE_ENTITY_SECRET     # Circle dev-controlled wallet entity secret (oracle+agent, #1065)
  # --- Runner relocation (issue #1065 / #1043) — oracle+agent EC2 + kb-runner ---
  # Names only, matching issue #1065's execution checklist Step 1 (the
  # Agora-workspace-level coordination doc T32-COORDINATION-DELTA-2026-07-08.md
  # §2 has the same list). Values set by Dan post-T3.2, once decision #1
  # (agent signer: Circle DCW vs raw key) is resolved.
  WALLET_ID                # Circle DCW wallet UUID (oracle/agent Circle signer)
  WALLET_ADDRESS           # that wallet's EVM address (public, informational)
  INTERNAL_AGENT_API_KEY   # X-Internal-Agent-Key shared secret, agent runner -> backend internal API
  ARC_AGENT_PRIVATE_KEY    # raw-key agent signer FALLBACK (chain/executor.py) — only if not using Circle DCW for the agent (decision #1)
  # ALL mutable ARC_*_ADDRESS values are SSM-sourced (never hardcoded): they
  # change at every contract redeploy (T3.2), so the runner reads them from
  # here via fetch-secrets.sh → --env-file (single source of truth; the
  # systemd units pass NO `-e ARC_*_ADDRESS` flag that could override them,
  # and a missing address fails the container closed rather than signing a
  # dead contract). Seed every new address in ONE `--apply` at T3.2.
  ARC_VAULT_FACTORY_ADDRESS           # VaultFactory — oracle + agent runner (fetch-secrets.sh)
  ARC_AMM_ROUTER_ADDRESS              # AMMRouter — agent runner rebalance path (fetch-secrets.sh)
  ARC_REASONING_TRACE_REGISTRY_ADDRESS # ReasoningTraceRegistry — oracle + agent runner (fetch-secrets.sh)
  ARC_STRATEGY_REGISTRY_ADDRESS       # StrategyRegistry — changes at every contract redeploy (T3.2), so SSM-sourced, not hardcoded
  ARC_PAYMENT_SPLITTER_ADDRESS        # PaymentSplitter (marketplace payouts) — same rationale
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

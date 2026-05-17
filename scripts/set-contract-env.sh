#!/bin/bash
# Add deployed contract addresses to .env on EC2
# Usage: SSH into EC2 and run: bash scripts/set-contract-env.sh
set -euo pipefail

ENV_FILE="${1:-.env}"
if [ ! -f "$ENV_FILE" ]; then echo "Error: $ENV_FILE not found"; exit 1; fi

add_if_missing() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE" && ! grep -q "^${key}=[a-fA-F0-9]" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    echo "Set $key"
  elif ! grep -q "^${key}=" "$ENV_FILE"; then
    echo "${key}=${val}" >> "$ENV_FILE"
    echo "Added $key"
  else
    echo "$key already set, skipping"
  fi
}

add_if_missing "ARC_AMM_ROUTER_ADDRESS" "0xd5b829f9d364a8bbe1caf6c8b19cb05371b178f4"
add_if_missing "ARC_VAULT_FACTORY_ADDRESS" "0xca873414070844aeb98b0bf1051f81969c79cc32"
add_if_missing "ARC_REASONING_TRACE_REGISTRY_ADDRESS" "0x42d8a23edb897cbee203e9fa197eb05ab5106ca6"
add_if_missing "ARC_ASSET_REGISTRY_ADDRESS" "0x2d44550711137916df6175587d17886281a0fbc7"

echo ""
echo "Done. Restart backend: docker compose restart backend"

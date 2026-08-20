#!/usr/bin/env bash
# infra/apply.sh — guarded terraform wrapper for the archimedes stack.
#
# Wraps `terraform plan` / `terraform apply` with the checks that used to be
# tribal knowledge (or just forgotten): a maintained terraform.tfvars per
# infra/README.md's "Operational variables" section (PR #1417), no stray
# TF_VAR_* shell exports shadowing it (the landmine PR #1417 documented), and
# a sanity check that you are actually authenticated into the right AWS
# account before anything touches prod.
#
# Usage:
#   infra/apply.sh                              # terraform plan
#   infra/apply.sh --allow-empty alarm_email     # plan, tolerating one empty var
#   infra/apply.sh --apply                       # terraform apply (interactive confirm)
#   infra/apply.sh --apply --yes                 # terraform apply -auto-approve
#   infra/apply.sh -- -target=aws_instance.foo   # extra args pass through to terraform
#
# Can be run from anywhere — it always operates on its own directory
# (infra/), never the caller's cwd.

# AWS-RunShellScript / some CI invokers run scripts with /bin/sh (dash),
# which rejects `set -o pipefail`. Re-exec under bash first. Precedent:
# .github/workflows/deploy-runners.yml (PR #1335).
[ -z "${BASH_VERSION:-}" ] && exec /bin/bash "$0" "$@"
set -euo pipefail

# ─── 1. cd safety: always operate on infra/, never the caller's cwd ────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$SCRIPT_DIR"

# Sanity check that SCRIPT_DIR really is the infra/ terraform root and not a
# stray copy of this script dropped somewhere else — "operate on infra/,
# refuse to run from elsewhere."
if [[ ! -f "variables.tf" || ! -f "terraform.tfvars.example" ]]; then
  echo "ERROR: apply.sh must live in infra/ next to variables.tf and" >&2
  echo "terraform.tfvars.example. Resolved script directory:" >&2
  echo "  $SCRIPT_DIR" >&2
  echo "does not look like the infra/ terraform root — refusing to run." >&2
  exit 2
fi

TFVARS="terraform.tfvars"
TFVARS_EXAMPLE="terraform.tfvars.example"

# ─── Parse CLI args ─────────────────────────────────────────────────────────
ALLOW_EMPTY=()
DO_APPLY=0
AUTO_YES=0
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-empty)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --allow-empty requires a variable name argument." >&2
        exit 2
      fi
      ALLOW_EMPTY+=("$2")
      shift 2
      ;;
    --apply)
      DO_APPLY=1
      shift
      ;;
    --yes)
      AUTO_YES=1
      shift
      ;;
    --)
      shift
      EXTRA_ARGS+=("$@")
      break
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ "$AUTO_YES" -eq 1 && "$DO_APPLY" -ne 1 ]]; then
  echo "ERROR: --yes only makes sense together with --apply (plan never" >&2
  echo "prompts for confirmation, so there is nothing for --yes to approve)." >&2
  exit 2
fi

# ─── 2. Preflight: terraform.tfvars must exist ─────────────────────────────
if [[ ! -f "$TFVARS" ]]; then
  cat >&2 <<EOF
ERROR: $TFVARS not found in $SCRIPT_DIR.

Copy the example and fill in real values before running apply.sh:
  cp $TFVARS_EXAMPLE $TFVARS

See README.md's "Operational variables" section for what each variable
gates and the silent-revert landmine this file exists to close.
EOF
  exit 2
fi

# Derive the authoritative operational-variable list from the example file
# itself (grep-level, no deps) rather than hardcoding it here, so a new
# variable added to terraform.tfvars.example can't silently drift out of
# this check. Matches top-level `name = value` assignments only — comment
# lines start with '#' and are excluded by the anchor.
OP_VARS=()
while IFS= read -r v; do
  [[ -n "$v" ]] && OP_VARS+=("$v")
done < <(grep -oE '^[a-zA-Z_][a-zA-Z0-9_]*[[:space:]]*=' "$TFVARS_EXAMPLE" | sed -E 's/[[:space:]]*=$//')

if [[ ${#OP_VARS[@]} -eq 0 ]]; then
  echo "ERROR: could not derive any operational variables from $TFVARS_EXAMPLE — check its format." >&2
  exit 2
fi

# Grep-level tfvars value lookup: returns "missing", "empty", or "ok" for a
# given variable name. Strips a trailing inline comment (# ...) and
# surrounding whitespace; treats a bare "" as empty. Explicit bool values
# (true/false) are never "empty" — they are a real, intentional setting.
tfvars_status() {
  local name="$1" line raw
  line="$(grep -E "^${name}[[:space:]]*=" "$TFVARS" | tail -n1 || true)"
  if [[ -z "$line" ]]; then
    echo "missing"
    return
  fi
  raw="${line#*=}"
  raw="${raw%%#*}"
  raw="$(printf '%s' "$raw" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  if [[ "$raw" == '""' || -z "$raw" ]]; then
    echo "empty"
  else
    echo "ok"
  fi
}

is_allowed_empty() {
  local name="$1" a
  if [[ ${#ALLOW_EMPTY[@]} -gt 0 ]]; then
    for a in "${ALLOW_EMPTY[@]}"; do
      [[ "$a" == "$name" ]] && return 0
    done
  fi
  return 1
}

PROBLEM_NAMES=()
PROBLEM_REASONS=()
for v in "${OP_VARS[@]}"; do
  status="$(tfvars_status "$v")"
  if [[ "$status" == "missing" ]]; then
    PROBLEM_NAMES+=("$v")
    PROBLEM_REASONS+=("missing from $TFVARS")
  elif [[ "$status" == "empty" ]]; then
    PROBLEM_NAMES+=("$v")
    PROBLEM_REASONS+=("empty string in $TFVARS")
  fi
done

if [[ ${#PROBLEM_NAMES[@]} -gt 0 ]]; then
  echo "WARNING: the following operational variables are missing or empty:" >&2
  BLOCKING=()
  for i in "${!PROBLEM_NAMES[@]}"; do
    name="${PROBLEM_NAMES[$i]}"
    reason="${PROBLEM_REASONS[$i]}"
    if is_allowed_empty "$name"; then
      echo "  - $name ($reason) — allowed via --allow-empty" >&2
    else
      echo "  - $name ($reason)" >&2
      BLOCKING+=("$name")
    fi
  done
  if [[ ${#BLOCKING[@]} -gt 0 ]]; then
    echo "" >&2
    echo "ERROR: refusing to proceed. Fill in a real value in $TFVARS, or pass" >&2
    echo "--allow-empty <var> explicitly (repeatable) for each variable you" >&2
    echo "intend to leave unset. Blocking: ${BLOCKING[*]}" >&2
    exit 2
  fi
  echo "" >&2
fi

# ─── 3. Refuse if a TF_VAR_* env var shadows a maintained tfvars entry ─────
# This is the exact landmine infra/README.md's "Operational variables"
# section documents: a one-off TF_VAR_* export silently overriding the
# maintained file on the next apply. Checks ALL tfvars entries, not just the
# operational ones above, so it also catches aurora_master_password-style
# vars if they ever land in tfvars.
SHADOWED=()
while IFS= read -r envname; do
  [[ -z "$envname" ]] && continue
  varname="${envname#TF_VAR_}"
  if grep -qE "^${varname}[[:space:]]*=" "$TFVARS"; then
    SHADOWED+=("$envname")
  fi
done < <(env | grep -oE '^TF_VAR_[A-Za-z0-9_]+' || true)

if [[ ${#SHADOWED[@]} -gt 0 ]]; then
  echo "ERROR: the following TF_VAR_* environment variables shadow entries" >&2
  echo "already maintained in $TFVARS — this is the old landmine path" >&2
  echo "(infra/README.md \"Operational variables\"): a bare terraform apply" >&2
  echo "from a shell with these set would silently override the file." >&2
  for v in "${SHADOWED[@]}"; do
    echo "  - $v" >&2
  done
  echo "" >&2
  echo "Move the value into $TFVARS and unset the env var, then re-run." >&2
  exit 2
fi

# ─── 4. AWS sanity: right identity, right account ──────────────────────────
EXPECTED_ACCOUNT="037613907429"
if ! ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>&1)"; then
  echo "ERROR: 'aws sts get-caller-identity' failed:" >&2
  echo "$ACCOUNT_ID" >&2
  echo "Hint: aws sso login --profile ArchimedesDanAdmin" >&2
  exit 2
fi

if [[ "$ACCOUNT_ID" != "$EXPECTED_ACCOUNT" ]]; then
  echo "ERROR: aws sts get-caller-identity resolved to account $ACCOUNT_ID," >&2
  echo "expected $EXPECTED_ACCOUNT. This guards against applying with the" >&2
  echo "wrong AWS_PROFILE. Check \$AWS_PROFILE and re-run 'aws sso login'." >&2
  exit 2
fi
echo "AWS sanity OK — account $ACCOUNT_ID."

# ─── 5. terraform plan (default) or apply (--apply) ────────────────────────
if [[ "$DO_APPLY" -eq 1 ]]; then
  CMD=(terraform apply)
  if [[ "$AUTO_YES" -eq 1 ]]; then
    CMD+=(-auto-approve)
  fi
else
  CMD=(terraform plan)
fi
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

echo "+ ${CMD[*]}"
"${CMD[@]}"

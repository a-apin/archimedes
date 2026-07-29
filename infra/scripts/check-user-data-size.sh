#!/usr/bin/env bash
# Guard: EC2 caps instance user-data at 16384 bytes of DECODED payload.
#
# Why this exists: runner-user-data.sh grew to 16383 raw bytes — one comment
# short of the ceiling — and the next edit pushed it over. The failure is not
# local to that file: `terraform plan` then errors REPO-WIDE with
#   Error: expected length of user_data to be in the range (0 - 16384)
# which blocks every unrelated apply until someone deletes prose. Nothing
# warned before the ceiling was hit; the first signal was a hard stop.
#
# CRITICAL: the number that counts against AWS depends on HOW each script is
# shipped, and this repo does both:
#   * base64gzip(templatefile(...))  -> AWS decodes to the GZIPPED bytes
#                                       (runner_ec2.tf / runner-user-data.sh)
#   * templatefile(...) / base64encode(templatefile(...))
#                                    -> AWS decodes to the RAW bytes
#                                       (main.tf, asg.tf / user-data.sh)
# Gating every script on its gzipped size would let a plainly-shipped script
# sail past 16 KB while the guard reported ~4x headroom it does not have. So
# the shipping mode is detected per file from the .tf that references it.
#
# Known limitation, stated rather than hidden: this measures the template ON
# DISK, before templatefile() interpolation. Interpolation only ever grows the
# payload (substituted ARNs/URLs are longer than "${ecr_registry}"), so the
# measurement is a LOWER BOUND, not the exact shipped size. That is why the
# gate fires at 80% of the limit rather than at 100% — the margin absorbs
# interpolation growth. Rendering exactly would require terraform + real
# variable values in CI, which is not worth the dependency for a size check.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"

AWS_LIMIT=16384
# Fail at 80% so a breach arrives as a warning with room to act, not as a wall.
THRESHOLD=$((AWS_LIMIT * 80 / 100))

status=0
found=0
for template in "$INFRA_DIR"/*user-data*.sh; do
  [ -e "$template" ] || continue
  found=1
  name="$(basename "$template")"

  # Shipping mode: gzipped only if some .tf wraps THIS file in base64gzip().
  if grep -rqF "base64gzip(templatefile(\"\${path.module}/$name\"" "$INFRA_DIR"/*.tf 2>/dev/null; then
    mode="gzipped"
    measured=$(gzip -9 -c "$template" | wc -c | tr -d ' ')
  else
    mode="plain"
    measured=$(wc -c < "$template" | tr -d ' ')
  fi
  raw=$(wc -c < "$template" | tr -d ' ')

  printf '%s: shipped %s -> %s bytes count against AWS (raw on disk %s; limit %s, gate %s)\n' \
    "$name" "$mode" "$measured" "$raw" "$AWS_LIMIT" "$THRESHOLD"

  if [ "$measured" -gt "$THRESHOLD" ]; then
    printf '  FAIL: %s is over %d%% of the EC2 user-data limit.\n' "$name" 80 >&2
    printf '  Trim the script, or move bulk setup into the container image.\n' >&2
    if [ "$mode" = plain ]; then
      printf '  This file ships PLAIN — wrapping it in base64gzip() in its .tf\n' >&2
      printf '  would buy ~4x headroom (cloud-init decompresses automatically).\n' >&2
      printf '  If you do, add user_data_base64 to that resource ignore_changes.\n' >&2
    fi
    printf '  Do NOT just raise this threshold — the limit is AWS-side and hard.\n' >&2
    status=1
  fi
done

if [ "$found" -eq 0 ]; then
  # A rename that silently stops matching the glob would make this guard pass
  # vacuously forever — exactly the failure mode it exists to prevent.
  echo "FAIL: no *user-data*.sh found under $INFRA_DIR — glob is stale." >&2
  exit 2
fi

exit "$status"

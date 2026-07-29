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
# runner_ec2.tf now ships the script via base64gzip(), so the number that
# actually counts against AWS is the GZIPPED size. That buys ~4x headroom, and
# this check keeps it honest by failing while there is still room to act.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"

AWS_LIMIT=16384
# Fail at 80% so the breach is a warning with headroom, not a wall.
THRESHOLD=$((AWS_LIMIT * 80 / 100))

status=0
for template in "$INFRA_DIR"/*user-data*.sh; do
  [ -e "$template" ] || continue
  name="$(basename "$template")"
  raw=$(wc -c < "$template" | tr -d ' ')
  gz=$(gzip -9 -c "$template" | wc -c | tr -d ' ')

  # Terraform interpolation only grows the payload, so the raw file is a lower
  # bound, not the final size. Report both; gate on the gzipped number, which
  # is what base64gzip() actually hands to AWS.
  printf '%s: raw=%s bytes, gzipped=%s bytes (AWS limit %s, gate %s)\n' \
    "$name" "$raw" "$gz" "$AWS_LIMIT" "$THRESHOLD"

  if [ "$gz" -gt "$THRESHOLD" ]; then
    printf '  FAIL: gzipped user-data is over %s%% of the EC2 limit.\n' 80 >&2
    printf '  Trim the script or move bulk setup into the container image.\n' >&2
    printf '  Do NOT just raise this threshold — the limit is AWS-side and hard.\n' >&2
    status=1
  fi

  if [ "$raw" -gt "$AWS_LIMIT" ]; then
    printf '  NOTE: raw size alone exceeds the limit — this file MUST be shipped\n' >&2
    printf '  via base64gzip() (it is), and can never revert to plain user_data.\n' >&2
  fi
done

exit "$status"

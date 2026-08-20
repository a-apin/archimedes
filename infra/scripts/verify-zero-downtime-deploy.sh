#!/usr/bin/env bash
# Issue #1309 acceptance criterion: "Verify with a timed probe loop across a
# real deploy: 0 non-200s end to end." Previously this lived only as
# copy/paste bash in infra/runbooks/ecs-fargate-cutover.md ("Phase 5 — Verify
# zero-downtime during a rolling deploy") — the runbook itself said "exercise
# it deliberately, don't just assume the config does the right thing", but
# nothing enforced that anyone actually ran it. This script IS that exercise,
# committed and reusable so the check survives copy/paste drift.
#
# What it does:
#   1. Starts a 1-request/second GET /health loop against the ALB directly
#      (bypasses CloudFront so it measures the target group, not the CDN
#      cache) and logs every response code + latency.
#   2. Force-triggers a new ECS rolling deployment (same task definition —
#      no new image needed to exercise the rolling-replace path).
#   3. Waits for the service to reach steady state.
#   4. Fails loudly (non-zero exit, and prints every offending line) if ANY
#      request during the window was not a 200 — a 502/503/000/timeout is a
#      real regression against issue #1309, not something to wave off as
#      flaky.
#
# Requires: awscli (authenticated — same OIDC-assumed role or an operator's
# IAM session; this repo has NO AWS credentials baked in, see CLAUDE.md
# "AWS: ... ask Dan for a scoped IAM user"), curl, terraform (to read
# outputs — run from infra/ with the S3 backend already initialized, see
# infra/README.md).
#
# Usage:
#   cd infra && ./scripts/verify-zero-downtime-deploy.sh
#   # or, without terraform state access, supply everything explicitly:
#   ./scripts/verify-zero-downtime-deploy.sh \
#     --alb-dns archimedes-alb-xxxx.us-east-1.elb.amazonaws.com \
#     --host archimedes-arc.com \
#     --cluster archimedes-cluster --service archimedes-backend
#
# This script does NOT prove what the ROOT CAUSE of a violation is (task
# healthStatus vs ALB target-group health vs config drift between the live
# service and infra/ecs.tf's deployment_minimum_healthy_percent/
# deployment_maximum_percent — see infra/ecs.tf's nginx healthCheck comment
# and the #1309 issue thread) — it only proves whether the acceptance
# criterion holds for the specific deploy it observed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"

ALB_DNS=""
HOST_HEADER="archimedes-arc.com"
CLUSTER=""
SERVICE=""
PROBE_INTERVAL=1
LOG_FILE="$(mktemp -t rollout-watch.XXXXXX.log)"

usage() {
  cat <<EOF
Usage: $0 [--alb-dns HOST] [--host HOST_HEADER] [--cluster NAME] [--service NAME] [--interval SECONDS]

Any flag omitted is read from 'terraform output' in $INFRA_DIR (requires the
S3 backend to already be initialized — infra/README.md).
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --alb-dns) ALB_DNS="$2"; shift 2 ;;
    --host) HOST_HEADER="$2"; shift 2 ;;
    --cluster) CLUSTER="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --interval) PROBE_INTERVAL="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "::error::unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

command -v curl >/dev/null 2>&1 || { echo "::error::curl is required" >&2; exit 1; }
command -v aws >/dev/null 2>&1 || { echo "::error::awscli is required (this repo ships no AWS credentials — see CLAUDE.md 'AWS' section for how to get a scoped IAM user from Dan)" >&2; exit 1; }

if [ -z "$ALB_DNS" ] || [ -z "$CLUSTER" ] || [ -z "$SERVICE" ]; then
  command -v terraform >/dev/null 2>&1 || { echo "::error::terraform is required to read outputs when --alb-dns/--cluster/--service are not all supplied" >&2; exit 1; }
  pushd "$INFRA_DIR" >/dev/null
  [ -n "$ALB_DNS" ] || ALB_DNS="$(terraform output -raw alb_dns_name)"
  [ -n "$CLUSTER" ] || CLUSTER="$(terraform output -raw ecs_cluster_name)"
  [ -n "$SERVICE" ] || SERVICE="$(terraform output -raw ecs_service_name)"
  popd >/dev/null
fi

echo "ALB:     $ALB_DNS (Host: $HOST_HEADER)"
echo "Cluster: $CLUSTER"
echo "Service: $SERVICE"
echo "Log:     $LOG_FILE"
echo

# ── Background probe loop ───────────────────────────────────────────────
probe_pid=""
cleanup() {
  if [ -n "$probe_pid" ] && kill -0 "$probe_pid" 2>/dev/null; then
    kill "$probe_pid" 2>/dev/null || true
    wait "$probe_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

(
  while true; do
    code_and_time=$(curl -o /dev/null -s -w "%{http_code} %{time_total}s" \
      --max-time 5 -H "Host: $HOST_HEADER" "https://$ALB_DNS/health" 2>/dev/null || echo "000 0s")
    # Plain %H:%M:%S, not %3N — GNU date supports sub-second precision but
    # BSD/macOS date (Dan's local shell, darwin) does not, and silently
    # prints the literal "3N" instead of erroring, which would corrupt every
    # logged line. 1-second resolution is enough at this probe interval.
    echo "$(date -u +%H:%M:%S) $code_and_time" >> "$LOG_FILE"
    sleep "$PROBE_INTERVAL"
  done
) &
probe_pid=$!
echo "Probe loop running (pid $probe_pid, 1 req/${PROBE_INTERVAL}s)..."

# Let the probe establish a healthy baseline before triggering the deploy —
# a cold-start 000/5xx before the loop is warmed up would be a false positive
# unrelated to the rollout itself.
sleep 5

# ── Trigger + wait for the rolling deployment ───────────────────────────
echo "Triggering force-new-deployment on $SERVICE..."
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --force-new-deployment >/dev/null

deploy_start="$(date -u +%H:%M:%S)"
echo "Deploy started at $deploy_start (UTC) — waiting for steady state..."
aws ecs wait services-stable --cluster "$CLUSTER" --services "$SERVICE"
deploy_end="$(date -u +%H:%M:%S)"
echo "Steady state reached at $deploy_end (UTC)."

# Give the probe a few more seconds past steady-state before stopping, to
# catch any post-"stable" wobble (e.g. a CloudFront/DNS propagation tail).
sleep 5
cleanup
trap - EXIT

# ── Verdict ──────────────────────────────────────────────────────────────
total=$(wc -l < "$LOG_FILE" | tr -d ' ')
bad_lines="$(grep -vE ' 200 ' "$LOG_FILE" || true)"

echo
echo "=== $(basename "$LOG_FILE") — $total probes, window ${deploy_start}Z .. ${deploy_end}Z ==="

if [ -z "$bad_lines" ]; then
  echo "PASS — 0 non-200 responses across $total probes during the rollout window."
  echo "Full log kept at $LOG_FILE"
  exit 0
else
  echo "FAIL — non-200 response(s) observed during the rollout window (issue #1309 acceptance criterion violated):"
  echo "$bad_lines"
  echo
  echo "Full log kept at $LOG_FILE"
  exit 1
fi

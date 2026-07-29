#!/bin/bash
set -euxo pipefail

# ---------------------------------------------------------------------------
# Archimedes runner EC2 bootstrap — runs once on first boot via cloud-init.
#
# Issue #1065 / #1043 — relocates the `oracle` + `agent` docker-compose
# services (docker-compose.yml, formerly stranded on the detached backend
# box) onto their OWN dedicated EC2 instance, as two systemd-managed docker
# containers pulling the SAME `archimedes-backend` ECR image the backend/kb
# tasks use (different `command:` override each — exactly the docker-compose
# pattern, just relocated). This box:
#   - NEVER builds an image on-box — ECR pull only (anti-goal, #1065).
#   - Has NO inbound SSH — admin access is SSM Session Manager only, same
#     posture as the main EC2 (see main.tf's aws_security_group.archimedes).
#   - Does NOT run oracle/agent in an ASG — these are funds-adjacent,
#     exactly-once singletons (services/runner_lease.py is the app-layer
#     lease control); duplicating the box would risk a double-signed tx.
#
# Unlike the main box's user-data.sh, this script does NOT `git clone` the
# repo or write a docker-compose .env — there is no local build context and
# no docker-compose stack here, just two `docker run` invocations managed by
# systemd, each pulling its own secrets from SSM at every (re)start via
# fetch-secrets.sh below.
#
# NOTE: like the main user-data.sh, this file is rendered by Terraform's
# templatefile() (see infra/runner_ec2.tf), which parses the WHOLE file
# (comments included, even THIS sentence) and treats a dollar-sign followed
# by a brace as the start of a template directive. Any literal shell
# variable-in-braces reference in this script is therefore written with a
# DOUBLED leading dollar sign so the rendered script keeps real shell
# expansion at runtime; a plain dollar-variable with no braces needs no
# escaping. The only REAL Terraform
# template variables in this file are `ecr_registry` (full repository URL,
# e.g. "<acct>.dkr.ecr.<region>.amazonaws.com/archimedes-backend" — used for
# `docker pull`/`docker run`), `ecr_registry_host` (bare registry host only,
# no repo path — the correct `docker login` target), `aws_region`, and
# `log_group_name` (all substituted below, undoubled).
# ---------------------------------------------------------------------------

export DEBIAN_FRONTEND=noninteractive

# ── System + Docker (same as the main box's user-data.sh) ──────────────────
apt-get update -y
apt-get upgrade -y

apt-get install -y ca-certificates curl gnupg unzip
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

systemctl enable docker
systemctl start docker

# ── AWS CLI v2 — needed for `aws ecr get-login-password` (image pulls) and
# `aws ssm get-parameters-by-path` (fetch-secrets.sh below). Ubuntu's apt
# `awscli` package is v1 and too old for some SSO/SSM flags; install v2
# directly from AWS, same as infra/scripts/bake-backend-ami.sh does.
# Arch-aware: hardcoding x86_64 would break bootstrap (and with it ECR login
# + secret fetch) the day runner_instance_type moves to Graviton (review).
# NOTE: Terraform renders this file through `templatefile()`, so a bare
# dollar-brace sequence is TERRAFORM interpolation, not shell — including inside
# comments, which the template engine does not treat as comments. Shell variables
# must be doubled (`$${ARCH}`) so Terraform emits a single dollar-brace for bash
# to expand at boot; same convention as the REGION line further down. Leaving it
# undoubled fails `terraform plan` with: vars map does not contain key "ARCH".
ARCH="$(uname -m)"   # x86_64 | aarch64 — matches AWS's installer naming
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$${ARCH}.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install
rm -rf /tmp/awscliv2.zip /tmp/aws

mkdir -p /opt/archimedes-runners
chmod 700 /opt/archimedes-runners

# ── fetch-secrets.sh — pulls the FLAT /archimedes/prod/* namespace into a
# docker --env-file at every (re)start (systemd ExecStartPre, below), so a
# secret ROTATION in SSM takes effect on the next `systemctl restart` with no
# redeploy. Same SSM prefix + same IAM scope
# (services/secrets_service.load_ssm_secrets()'s /archimedes/prod/* contract)
# the backend already reads — this mirrors that access pattern for a
# standalone (non-FastAPI) process where there is no in-process SSM fetch to
# reuse. Pulls the WHOLE prefix (DATABASE_URL, REDIS_URL,
# AURORA_MASTER_PASSWORD, EMAIL_ENCRYPTION_KEY, CIRCLE_*, WALLET_ID,
# WALLET_ADDRESS, INTERNAL_AGENT_API_KEY, ARC_AGENT_PRIVATE_KEY, and ALL of
# the ARC_*_ADDRESS contract addresses — ARC_VAULT_FACTORY_ADDRESS,
# ARC_AMM_ROUTER_ADDRESS, ARC_REASONING_TRACE_REGISTRY_ADDRESS,
# ARC_STRATEGY_REGISTRY_ADDRESS, ARC_PAYMENT_SPLITTER_ADDRESS, ...) into ONE
# env file consumed by BOTH containers — harmless unused vars in either
# container's env, and avoids maintaining two divergent per-service param
# lists here. NEVER logs a value, only parameter NAMES (matches
# setup-ssm-secrets.sh's own norm).
#
# SINGLE SOURCE OF TRUTH FOR CONTRACT ADDRESSES (correctness, coordinator
# review): the ARC_*_ADDRESS values are pulled from SSM here and are the ONLY
# place they come from — the systemd units below pass NO `-e ARC_*_ADDRESS`
# flag, deliberately. A `docker run -e KEY=VAL` flag OVERRIDES the
# `--env-file`, so a hardcoded literal `-e ARC_VAULT_FACTORY_ADDRESS=0x...`
# would pin the runner to whatever address was baked into this bootstrap
# script, silently defeating a T3.2 re-seed in SSM and making the runner sign
# against a DEAD contract. Sourcing them here instead means Dan re-seeds every
# new address in ONE step at T3.2 (`setup-ssm-secrets.sh --apply`) and both
# runners pick them up on their next `systemctl restart`, no bootstrap edit.
# This is also fail-CLOSED: a missing/empty address in SSM → the container
# raises loudly at startup rather than silently transacting on a stale/wrong
# contract, the correct posture for a funds-adjacent runner.
#
# KNOWN LIMITATION: docker's --env-file format is `KEY=VALUE` per line, no
# shell expansion, and treats a line starting with `#` as a comment — a
# secret VALUE containing an embedded newline or a leading `#` would break
# this. None of the current /archimedes/prod/* secrets are multi-line
# (API keys, hex secrets, DB URLs, addresses), so this is a documented,
# not-yet-hit edge case, not a live bug.
cat > /opt/archimedes-runners/fetch-secrets.sh <<'FETCHEOF'
#!/bin/bash
set -euo pipefail

# NOTE: the region is a Terraform-templated literal (substituted at render
# time, below), not a bash-runtime default-value fallback — nesting a bash
# "variable colon-dash default" expansion inside a templatefile() directive
# would be parsed as an (invalid) HCL expression and fail the render. If
# this ever needs to vary at runtime instead, read it as a plain doubled-
# dollar env var with NO nested directive inside it, e.g. (doubled so
# Terraform emits it literally): REGION="$${AWS_REGION:-us-east-1}"
REGION="${aws_region}"
PREFIX="/archimedes/prod"
OUT="/opt/archimedes-runners/runner.env"

# Build into a temp file and rename into place ATOMICALLY. Writing $OUT
# directly (truncate-then-append) is a real race: BOTH units run this as
# ExecStartPre and each restarts independently every 10s, so one unit's
# `docker run --env-file` can read $OUT while the other unit's fetch has
# truncated it and is still appending — the container then boots with a
# PARTIAL environment and no error anywhere. (Observed live: a read of
# runner.env during a restart loop returned 10 of 15 parameters.) rename(2)
# is atomic, so a reader sees either the whole old file or the whole new one.
# It also means a mid-flight aws failure leaves the last-good file intact
# instead of a truncated one.
# mktemp, not "$OUT.tmp.$$": this file is rendered through Terraform's
# templatefile(), where `$$` sits next to HCL's `$${` escape — mktemp sidesteps
# the ambiguity entirely and also guarantees a unique name when both units
# fetch concurrently.
umask 077
TMP="$(mktemp "$OUT.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

aws ssm get-parameters-by-path \
  --path "$PREFIX" \
  --recursive \
  --with-decryption \
  --region "$REGION" \
  --query 'Parameters[*].[Name,Value]' \
  --output text |
while IFS=$'\t' read -r name value; do
  key="$(basename "$name")"
  printf '%s=%s\n' "$key" "$value" >> "$TMP"
done

# LOAD-BEARING INPUT CHECK — be loud about exactly the inputs the runners
# cannot function without, and silent about the rest. An AccessDenied already
# fails hard (set -euo pipefail), but a SUCCESSFUL call that returns nothing
# (wrong prefix, wiped namespace, wrong account) would otherwise write an
# empty env file and boot a funds-adjacent container with no credentials and
# no error — fail-soft on state the product needs is how an outage becomes a
# silence. Names only; never a value.
MISSING=""
for required in DATABASE_URL REDIS_URL CIRCLE_API_KEY CIRCLE_ENTITY_SECRET WALLET_ID AGENT_DRY_RUN; do
  grep -q "^$required=" "$TMP" || MISSING="$MISSING $required"
done
if [ -n "$MISSING" ]; then
  echo "fetch-secrets: FATAL — required parameter(s) absent from $PREFIX:$MISSING" >&2
  echo "fetch-secrets: refusing to write $OUT; seed them with infra/scripts/setup-ssm-secrets.sh --apply" >&2
  exit 4
fi

chmod 600 "$TMP"
mv -f "$TMP" "$OUT"
trap - EXIT
echo "fetch-secrets: wrote $(wc -l < "$OUT") parameter(s) to $OUT (names only, never values, in this log line)"
FETCHEOF
chmod 700 /opt/archimedes-runners/fetch-secrets.sh

# ── ECR login helper — re-run before every `docker pull` since ECR tokens
# expire after 12h and both units restart independently of each other.
cat > /opt/archimedes-runners/ecr-login.sh <<'LOGINEOF'
#!/bin/bash
set -euo pipefail
aws ecr get-login-password --region "${aws_region}" | \
  docker login --username AWS --password-stdin "${ecr_registry_host}"
LOGINEOF
chmod 700 /opt/archimedes-runners/ecr-login.sh

# ── systemd units — one per singleton runner. Both:
#   - pull the SAME archimedes-backend image (never build on-box)
#   - refresh secrets from SSM on every (re)start (ExecStartPre)
#   - pass ONLY static, non-redeploy-variable config as `-e` flags
#     (AWS_REGION, ORACLE_INTERVAL_SECONDS/AGENT_INTERVAL_SECONDS).
#     AGENT_DRY_RUN is deliberately NOT an `-e` flag: it is a funds-BEHAVIOUR
#     switch, and `docker run -e` overrides `--env-file`, so hardcoding it here
#     would make it unchangeable in practice — `aws_instance.runner` carries
#     `lifecycle.ignore_changes = [ami, user_data]` (runner_ec2.tf), so whatever
#     this file says at FIRST BOOT is baked in for the life of the instance.
#     Sourcing it from SSM instead means Dan flips dry-run -> live with
#     `setup-ssm-secrets.sh --apply` + `systemctl restart archimedes-agent`,
#     with no instance replacement. Seed it as "true" and only flip to "false"
#     after a dry-run smoke pass. If the SSM param is absent the app default
#     applies (agent_runner.py: AGENT_DRY_RUN defaults to "false"), so ALWAYS
#     seed it explicitly before the agent has working credentials.
#     Every mutable ARC_*_ADDRESS contract address comes from
#     the SSM-sourced --env-file instead — NEVER as an `-e` flag, which would
#     override the env-file and re-hardcode a soon-to-be-stale address (see
#     the fetch-secrets.sh preamble above for the full rationale).
#   - ship stdout/stderr to the /archimedes/runners CloudWatch log group via
#     docker's native `awslogs` log driver (uses the instance role's
#     credentials automatically — no CloudWatch agent needed)
#     ⚠️ The option is `awslogs-stream` (an EXACT stream name). It is NOT
#     `awslogs-stream-prefix` — that one is an *ECS task definition*
#     logConfiguration option and is rejected by the docker daemon with
#     `unknown log opt 'awslogs-stream-prefix' for awslogs log driver`,
#     which fails `docker run` with exit 125 BEFORE the container starts.
#     Because this file is `ignore_changes = [user_data]` (bootstrap-only),
#     that mistake is not self-healing: it bricks the unit into a permanent
#     `activating (auto-restart)` loop that no amount of restarting fixes.
#   - Restart=always — systemd itself is the box-local restart policy; the
#     Redis lease in services/runner_lease.py is the SEPARATE, authoritative
#     exactly-once control if this box is ever accidentally duplicated.
#
# Image tag: both units run the mutable `:latest` tag (same convention the
# retired EC2 backend docker-compose path used — see the old `deploy` job in
# .github/workflows/deploy.yml, git history). `.github/workflows/deploy-runners.yml`'s
# SSM RunCommand step re-`docker pull`s `:latest` after every CI image push,
# then restarts these two units so the freshly-pulled layer becomes the
# running container — no unit-file edit needed per deploy.
cat > /etc/systemd/system/archimedes-oracle.service <<'ORACLEEOF'
[Unit]
Description=Archimedes oracle runner (price feeder, funds-adjacent singleton)
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=simple
TimeoutStartSec=120
ExecStartPre=-/usr/bin/docker stop archimedes-oracle
ExecStartPre=-/usr/bin/docker rm archimedes-oracle
ExecStartPre=/opt/archimedes-runners/fetch-secrets.sh
ExecStartPre=/opt/archimedes-runners/ecr-login.sh
ExecStart=/usr/bin/docker run --rm --name archimedes-oracle \
  --env-file /opt/archimedes-runners/runner.env \
  -e AWS_REGION=${aws_region} \
  -e ORACLE_INTERVAL_SECONDS=60 \
  --log-driver=awslogs \
  --log-opt awslogs-region=${aws_region} \
  --log-opt awslogs-group=${log_group_name} \
  --log-opt awslogs-stream=oracle \
  ${ecr_registry}:latest \
  python -m archimedes.chain.oracle_runner
ExecStop=/usr/bin/docker stop archimedes-oracle
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
ORACLEEOF

cat > /etc/systemd/system/archimedes-agent.service <<'AGENTEOF'
[Unit]
Description=Archimedes strategy agent runner (autonomous rebalancer, funds-adjacent singleton)
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=simple
TimeoutStartSec=120
ExecStartPre=-/usr/bin/docker stop archimedes-agent
ExecStartPre=-/usr/bin/docker rm archimedes-agent
ExecStartPre=/opt/archimedes-runners/fetch-secrets.sh
ExecStartPre=/opt/archimedes-runners/ecr-login.sh
ExecStart=/usr/bin/docker run --rm --name archimedes-agent \
  --env-file /opt/archimedes-runners/runner.env \
  -e AWS_REGION=${aws_region} \
  -e AGENT_INTERVAL_SECONDS=300 \
  --log-driver=awslogs \
  --log-opt awslogs-region=${aws_region} \
  --log-opt awslogs-group=${log_group_name} \
  --log-opt awslogs-stream=agent \
  ${ecr_registry}:latest \
  python -m archimedes.chain.agent_runner
ExecStop=/usr/bin/docker stop archimedes-agent
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
AGENTEOF

systemctl daemon-reload

# First-boot image pull + login (subsequent pulls happen via CI's SSM
# RunCommand step, .github/workflows/deploy-runners.yml — inert until Dan
# flips vars.RUNNER_DEPLOY_ENABLED post-apply, see that workflow's header).
/opt/archimedes-runners/ecr-login.sh || echo "WARN: initial ECR login failed — image pull below may fail closed; the unit will retry on next restart once SSM/ECR access is confirmed."
docker pull "${ecr_registry}:latest" || echo "WARN: initial image pull failed — archimedes-backend:latest may not be pushed to ECR yet. Units will retry per Restart=always once an image exists."

systemctl enable archimedes-oracle.service archimedes-agent.service
systemctl start archimedes-oracle.service archimedes-agent.service

echo "archimedes-runner-bootstrap-complete" > /tmp/bootstrap-done

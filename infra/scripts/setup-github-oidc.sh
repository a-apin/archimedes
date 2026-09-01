#!/usr/bin/env bash
# Archimedes — GitHub Actions -> AWS auth via OIDC (no long-lived keys).
#
# Why: CI/CD should assume a short-lived role via OpenID Connect, NOT store an
# AWS_ACCESS_KEY_ID/SECRET in GitHub secrets. The only thing GitHub holds is the
# role ARN (printed at the end) — not a secret. App runtime secrets live in SSM
# Parameter Store (see setup-ssm-secrets.sh), never in GitHub.
#
# What this creates:
#   1. An IAM OIDC identity provider for token.actions.githubusercontent.com
#   2. A deploy role whose trust policy ONLY accepts a-apin/archimedes on
#      refs/heads/main (build-on-deploy), assumed via sts:AssumeRoleWithWebIdentity.
#   3. A STARTER permissions policy (ECR push + SSM SendCommand + EC2 describe),
#      plus the docs-site publish grants (#1634): sync into the
#      docs-site/infra bucket and invalidate its CloudFront distribution.
#      >>> Tighten the Resource ARNs once the ECR repo + instance/ECS service exist. <<<
#
#   NOTE: the docs-site grants must be applied (re-run this script with --apply)
#   before .github/workflows/docs-site.yml can publish. Until then its publish
#   steps fail on AccessDenied — the site keeps building, nothing goes live.
#   `cloudfront:ListDistributions` cannot be resource-scoped (it is a list call,
#   Resource "*" is the only valid form); `CreateInvalidation` is scoped to this
#   account's distributions.
#
# DRY-RUN BY DEFAULT.  ./setup-github-oidc.sh   |   ./setup-github-oidc.sh --apply
# Requires: AWS_PROFILE exported, aws CLI v2.
set -euo pipefail

# ─── Config ──────────────────────────────────────────────────────────────────
# ACCOUNT_ID/REGION are not hardcoded — auto-detected from the active profile (override via env).
ACCOUNT_ID="${ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)}"
REGION="${AWS_REGION:-us-east-1}"
GITHUB_ORG="a-apin"
GITHUB_REPO="archimedes"
DEPLOY_BRANCH_SUB="repo:${GITHUB_ORG}/${GITHUB_REPO}:ref:refs/heads/main"
ROLE_NAME="archimedes-github-deploy"
OIDC_URL="token.actions.githubusercontent.com"
OIDC_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_URL}"
# GitHub's OIDC thumbprints (AWS no longer validates these for this provider, but the
# API still wants at least one). Both current values included for safety.
THUMBPRINTS="6938fd4d98bab03faadb97b34396831e3780aea1 1c58a3a8518e8759bf075b76b750d4f2df264fcd"
# ─────────────────────────────────────────────────────────────────────────────

APPLY=false; for a in "$@"; do case "$a" in
  --apply) APPLY=true;; -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
  *) echo "unknown arg: $a" >&2; exit 2;; esac; done
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
do_() { printf '  + %s\n' "$*"; if $APPLY; then eval "$*"; fi; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
[ -n "$ACCOUNT_ID" ] || { echo "ERROR: could not resolve ACCOUNT_ID (set AWS_PROFILE / export ACCOUNT_ID)"; exit 1; }
$APPLY && echo ">>> APPLY MODE — account ${ACCOUNT_ID}" || echo ">>> DRY RUN — re-run with --apply to execute."

# ─── 1. OIDC provider ─────────────────────────────────────────────────────────
say "GitHub OIDC identity provider"
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" >/dev/null 2>&1; then
  echo "  exists: $OIDC_ARN"
else
  do_ "aws iam create-open-id-connect-provider --url https://${OIDC_URL} --client-id-list sts.amazonaws.com --thumbprint-list ${THUMBPRINTS}"
fi

# ─── 2. Deploy role (trust scoped to main branch only) ────────────────────────
say "Deploy role: ${ROLE_NAME} (trusts ${DEPLOY_BRANCH_SUB})"
cat > "$TMP/trust.json" <<JSON
{ "Version":"2012-10-17",
  "Statement":[{
    "Effect":"Allow",
    "Principal":{"Federated":"${OIDC_ARN}"},
    "Action":"sts:AssumeRoleWithWebIdentity",
    "Condition":{
      "StringEquals":{"${OIDC_URL}:aud":"sts.amazonaws.com"},
      "StringLike":{"${OIDC_URL}:sub":"${DEPLOY_BRANCH_SUB}"}
    } }] }
JSON
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "  role exists — updating trust policy"
  do_ "aws iam update-assume-role-policy --role-name $ROLE_NAME --policy-document file://$TMP/trust.json"
else
  do_ "aws iam create-role --role-name $ROLE_NAME --assume-role-policy-document file://$TMP/trust.json --description 'GitHub Actions deploy (OIDC, main only)'"
fi

# ─── 3. STARTER permissions (TIGHTEN once ECR repo + deploy target exist) ─────
say "Deploy permissions (STARTER — scope Resources after the stack is up)"
cat > "$TMP/perms.json" <<JSON
{ "Version":"2012-10-17",
  "Statement":[
    {"Sid":"EcrAuth","Effect":"Allow","Action":["ecr:GetAuthorizationToken"],"Resource":"*"},
    {"Sid":"EcrPush","Effect":"Allow",
     "Action":["ecr:BatchCheckLayerAvailability","ecr:InitiateLayerUpload","ecr:UploadLayerPart",
               "ecr:CompleteLayerUpload","ecr:PutImage","ecr:BatchGetImage"],
     "Resource":"arn:aws:ecr:${REGION}:${ACCOUNT_ID}:repository/archimedes*"},
    {"Sid":"SsmDeploy","Effect":"Allow",
     "Action":["ssm:SendCommand","ssm:GetCommandInvocation","ssm:ListCommands","ssm:ListCommandInvocations"],
     "Resource":"*"},
    {"Sid":"Ec2Discover","Effect":"Allow","Action":["ec2:DescribeInstances"],"Resource":"*"},
    {"Sid":"DocsSiteBucket","Effect":"Allow",
     "Action":["s3:ListBucket"],
     "Resource":"arn:aws:s3:::archimedes-docs-site-${ACCOUNT_ID}"},
    {"Sid":"DocsSiteObjects","Effect":"Allow",
     "Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],
     "Resource":"arn:aws:s3:::archimedes-docs-site-${ACCOUNT_ID}/*"},
    {"Sid":"DocsSiteInvalidate","Effect":"Allow",
     "Action":["cloudfront:CreateInvalidation"],
     "Resource":"arn:aws:cloudfront::${ACCOUNT_ID}:distribution/*"},
    {"Sid":"DocsSiteFindDistribution","Effect":"Allow",
     "Action":["cloudfront:ListDistributions"],"Resource":"*"}
  ] }
JSON
do_ "aws iam put-role-policy --role-name $ROLE_NAME --policy-name archimedes-deploy --policy-document file://$TMP/perms.json"

say "Role ARN to put in GitHub (NOT a secret — a repo variable is fine):"
echo "  arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
cat <<YAML

  # In .github/workflows/deploy.yml, replace stored AWS keys with:
  permissions:
    id-token: write   # required for OIDC
    contents: read
  steps:
    - uses: aws-actions/configure-aws-credentials@v4
      with:
        role-to-assume: arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}
        aws-region: ${REGION}
  # No AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY secrets needed.
YAML
$APPLY || echo "(dry run — re-run with --apply to create the above)"

# IAM — `archimedes-backend-role`

The IAM policy + role that the backend EC2 instance (and the future ECS task / Lambda
that runs the KB pipeline on GPU) uses to talk to AWS managed services. **Least-privilege
by design** — every action is scoped to a specific bucket / table / parameter prefix.

## What this policy grants

| AWS service | Scope | Why |
| --- | --- | --- |
| `s3:GetObject`, `PutObject`, `DeleteObject` (+ versioning reads) | `archimedes-corpus-artifacts-prod/*` | KB pipeline writes `embeddings.npy`, `clusters.json`, `topics.json`, `kg_triples.jsonl`, `kg_graph.json`, `manifest.json`; backend reads them via `/api/corpus/*` |
| `s3:GetObject`, `PutObject`, `DeleteObject` | `archimedes-paper-pdfs-prod/*` | Paper PDF storage for the corpus (input to the KB pipeline) |
| `s3:ListBucket`, `GetBucketLocation`, `GetBucketVersioning` | Both buckets | List + introspection only on the bucket itself (no other buckets) |
| `dynamodb:GetItem`, `PutItem`, `UpdateItem`, `DeleteItem`, `Query`, `Scan`, `BatchGetItem`, `BatchWriteItem`, `DescribeTable` | `archimedes-papers-index` (+ all GSIs) | Paper metadata index — DynamoDB is the additive read index per [T3.1 spec](https://github.com/a-apin/archimedes-arcadia/issues/147); Postgres remains the source of truth |
| `ssm:GetParameter`, `GetParameters`, `GetParametersByPath` | `/archimedes/prod/*` | Secrets surface for [TS.2 #176](https://github.com/a-apin/archimedes-arcadia/issues/176) — backend reads at startup |
| `kms:Decrypt` (conditional) | KMS via `ssm.<region>.amazonaws.com` only | Decrypts SecureString SSM parameters; condition prevents using this for arbitrary KMS keys |
| `logs:CreateLogStream`, `PutLogEvents`, `DescribeLogStreams` | `/archimedes/*` log groups | Backend writes structured logs to CloudWatch (operator observability) |

## What this policy does NOT grant

Anti-goals from the original [T3.1 spec](https://github.com/a-apin/archimedes-arcadia/issues/147):

- **No bucket-policy edits** — separating data-plane (this role) from control-plane (Chuan's deployer credentials)
- **No public-read** of either bucket (no `s3:PutBucketAcl`, no `s3:PutObjectAcl`)
- **No cross-region replication** for v1 (no `s3:ReplicateObject`)
- **No access to other AWS accounts** — every resource ARN scoped to `*` for account but the bucket/table names are unique to this project
- **No IAM operations** (the role can't create more roles, attach policies, or assume other roles)
- **No Bedrock** — [T3.5 #154](https://github.com/a-apin/archimedes-arcadia/issues/154) is OPTIONAL and held; if/when it fires, append a separate Bedrock statement
- **No ECS / Lambda / EC2 management** — provisioning happens with deployer credentials, not the backend runtime role

## How pi (or anyone) applies this

```bash
# 1. Create the policy in AWS (one-time)
aws iam create-policy \
  --policy-name archimedes-backend-policy \
  --policy-document file://infra/iam/archimedes-backend-policy.json \
  --description "Backend runtime access for Archimedes — S3 artifacts + DynamoDB index + SSM secrets"
# Note the returned policy ARN; you'll need it for step 2.

# 2. Create the role with EC2 trust + attach the policy
aws iam create-role \
  --role-name archimedes-backend-role \
  --assume-role-policy-document file://infra/iam/trust-policy-ec2.json
aws iam attach-role-policy \
  --role-name archimedes-backend-role \
  --policy-arn <POLICY_ARN_FROM_STEP_1>

# 3. Create the instance profile + add the role
aws iam create-instance-profile \
  --instance-profile-name archimedes-backend-profile
aws iam add-role-to-instance-profile \
  --instance-profile-name archimedes-backend-profile \
  --role-name archimedes-backend-role

# 4. Associate with the running EC2 (or set on launch template)
aws ec2 associate-iam-instance-profile \
  --instance-id <BACKEND_INSTANCE_ID> \
  --iam-instance-profile Name=archimedes-backend-profile
```

After step 4, the backend can `boto3.client("s3").get_object(...)` etc. without any
long-lived credentials in `.env` — the EC2 metadata service vends temporary STS
tokens automatically.

## The trust policy (companion file — pi creates this)

The companion `trust-policy-ec2.json` (not committed; pi creates ad-hoc) should contain:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ec2.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

If the KB pipeline runs on ECS Fargate or Lambda instead of EC2, add the relevant
service principal (`ecs-tasks.amazonaws.com` or `lambda.amazonaws.com`) to the
`Principal.Service` array.

## Cross-account / multi-environment notes

- **Single-account model for v1.** Everything in Chuan's hackathon AWS account.
- **`*` in resource ARNs** is the account-ID wildcard. AWS resolves to the account
  the API call is made from, so this is effectively "this account only."
- **Region `*` in DynamoDB / SSM / KMS / Logs** is deliberate — we may run the
  backend in any AWS region without policy changes.
- **For staging / dev environments**, create separate policies with `-staging` /
  `-dev` suffixes on the resource names; do not share this role across env tiers.

## Validation checklist

After provisioning the role + attaching to backend EC2:

- [ ] `aws sts get-caller-identity` from the EC2 returns the role's ARN (not a user ARN)
- [ ] `aws s3 ls s3://archimedes-corpus-artifacts-prod/` succeeds (empty list OK)
- [ ] `aws dynamodb describe-table --table-name archimedes-papers-index` returns `TableStatus: ACTIVE`
- [ ] `aws ssm get-parameter --name /archimedes/prod/test --with-decryption` returns either the value (if seeded) or `ParameterNotFound` (NOT `AccessDenied`)
- [ ] Backend can `python -c "from archimedes.services.s3_artifact_store import S3ArtifactStore; print(S3ArtifactStore().list_keys()[:5])"` (returns `[]` if bucket is empty — that's fine)

## Open question / future

- **CloudWatch Logs ingestion volume** could spike if we log per-request at INFO. If
  AWS billing surfaces this as a concern, downgrade to WARN/ERROR-only in production
  via `LOG_LEVEL=WARNING` and use a sampling strategy for important traces.
- **DynamoDB read/write capacity** — left at on-demand (per the T3.1 anti-goals).
  Migrate to provisioned only after we have a real read pattern signal.

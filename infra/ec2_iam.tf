# ── EC2 instance role (SSM agent + Parameter Store secrets) ───────────
#
# Wires the single backend EC2 to AWS Systems Manager so:
#   - SSM SendCommand can target it (deploy.yml) — via AmazonSSMManagedInstanceCore
#   - the backend can read /archimedes/prod/* SecureString secrets at startup
#     (services/secrets_service.load_ssm_secrets) — scoped GetParameter* + kms:Decrypt
#
# Named `archimedes-ec2-role` to match the Bedrock-deny budget action target
# (infra/scripts/setup-budgets.sh DENY_TARGET_ROLE). Broader runtime perms (S3
# corpus artifacts, DynamoDB index, Bedrock invoke, CloudWatch Logs) are added
# as those features come online — see infra/iam/archimedes-backend-policy.json.
#
# NOTE: SSM SendCommand also needs the amazon-ssm-agent running on the box.
# Ubuntu 24.04 ships it via snap; ensure user-data.sh installs/enables it
# (`snap install amazon-ssm-agent --classic` or apt) before the first deploy.

resource "aws_iam_role" "ec2" {
  name = "archimedes-ec2-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = { Project = var.project_name }
}

# SSM agent registration + SendCommand targeting (AWS-managed policy).
resource "aws_iam_role_policy_attachment" "ec2_ssm_core" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Read app secrets from SSM Parameter Store + decrypt SecureString (scoped to
# the /archimedes/prod/* prefix and to KMS-via-SSM only).
resource "aws_iam_role_policy" "ec2_ssm_params" {
  name = "archimedes-ssm-parameter-read"
  role = aws_iam_role.ec2.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadAppSecrets"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        # BOTH ARNs are required. ssm:GetParametersByPath authorizes against the PATH
        # itself (".../parameter/archimedes/prod"), which the "/*" child pattern does
        # NOT match — granting only the child pattern yields:
        #   AccessDeniedException ... not authorized to perform: ssm:GetParametersByPath
        #   on resource: arn:aws:ssm:us-east-1:<acct>:parameter/archimedes/prod
        # The child ARN is still needed for GetParameter/GetParameters on individual keys.
        Resource = [
          "arn:aws:ssm:*:*:parameter/archimedes/prod",
          "arn:aws:ssm:*:*:parameter/archimedes/prod/*",
        ]
      },
      {
        Sid      = "DecryptSecureStringViaSSM"
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = "*"
        Condition = {
          StringEquals = { "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com" }
        }
      }
    ]
  })
}

# Invoke Bedrock foundation models (IAM auth — no API key). The LLM backends call
# this via the instance role: services/llm_backend.BedrockBackend (Anthropic SDK)
# and BedrockConverseBackend (the Converse API, for ANY provider — Amazon Nova,
# Meta Llama, Mistral, DeepSeek, Qwen, Z.AI GLM, Moonshot Kimi, Anthropic, ...).
# Scoped to foundation models in ALL regions (cross-region inference profiles like
# `us.*` route the call to us-east-1/2 + us-west-2, so InvokeModel is checked
# against both the inference-profile ARN and the destination foundation-model
# ARNs) plus this account's inference profiles. Broad across providers because the
# multi-model cost picker can target any of them; spend is bounded by the backstop
# below, not by this resource scope.
#
# COST BACKSTOP: the budget guardrail (infra/scripts/setup-budgets.sh) attaches a
# Bedrock-DENY policy to THIS role when the cost budget trips. An explicit Deny
# overrides this Allow, so a runaway LLM spend self-throttles — intended.
resource "aws_iam_role_policy" "ec2_bedrock_invoke" {
  name = "archimedes-bedrock-invoke"
  role = aws_iam_role.ec2.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeBedrockFoundationModels"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*:037613907429:inference-profile/*"
        ]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "ec2" {
  name = "archimedes-ec2-profile"
  role = aws_iam_role.ec2.name
}

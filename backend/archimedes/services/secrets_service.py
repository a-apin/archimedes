"""AWS SSM Parameter Store secrets loader.

Production: reads all parameters under ``/archimedes/prod/*`` via boto3 SSM
and injects them into ``os.environ`` so downstream services (LLM, Circle,
chain client) work without any code changes.

Local development: no-op when ``AWS_SSM_PATH_PREFIX`` is unset or when
boto3 cannot reach SSM (no instance profile, no credentials). Falls back
silently to .env-based values already loaded by python-dotenv.

Usage (in main.py, BEFORE init_db / service imports):
    from archimedes.services.secrets_service import load_ssm_secrets
    load_ssm_secrets()

Security notes:
    - Never logs secret VALUES — only names + count.
    - Rotation: operator re-seeds SSM params, restarts the container.
    - IAM: scoped to ssm:GetParametersByPath on /archimedes/prod/*
      (see infra/iam/archimedes-backend-policy.json).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Default prefix — matches infra/iam policy + .env.example documentation.
_DEFAULT_PREFIX = "/archimedes/prod/"

# Map SSM param names (last segment after prefix) → env var names.
# e.g. /archimedes/prod/LLM_AUTH_TOKEN → LLM_AUTH_TOKEN
# Identity mapping by default (uppercase last segment = env var name).
# Override with explicit entries if SSM naming diverges from env var naming.
_PARAM_TO_ENV: dict[str, str] = {
    # Add explicit mappings here if needed:
    # "anthropic-auth-token": "ANTHROPIC_AUTH_TOKEN",
}


def _extract_env_name(param_name: str, prefix: str) -> str:
    """Convert SSM parameter name to environment variable name.

    /archimedes/prod/LLM_AUTH_TOKEN → LLM_AUTH_TOKEN
    /archimedes/prod/circle/api-key → CIRCLE_API_KEY (nested path → underscore + upper)
    """
    # Strip the prefix to get the relative key
    relative = param_name.removeprefix(prefix).strip("/")

    # Check explicit mapping first
    if relative in _PARAM_TO_ENV:
        return _PARAM_TO_ENV[relative]

    # Default: replace slashes and hyphens with underscores, uppercase
    return relative.replace("/", "_").replace("-", "_").upper()


def load_ssm_secrets(
    prefix: str | None = None,
    region: str | None = None,
    override_existing: bool = False,
) -> int:
    """Load secrets from AWS SSM Parameter Store into os.environ.

    Args:
        prefix: SSM path prefix (default: AWS_SSM_PATH_PREFIX env var or /archimedes/prod/)
        region: AWS region (default: AWS_REGION env var or eu-west-2)
        override_existing: If True, overwrite env vars that already have values.
            Default False — .env values take precedence (useful for local dev override).

    Returns:
        Number of parameters loaded.

    Raises:
        Nothing — all errors are caught and logged as warnings.
        The app boots degraded rather than crashing on SSM failure.
    """
    prefix = prefix or os.environ.get("AWS_SSM_PATH_PREFIX", "").strip()
    if not prefix:
        logger.debug("secrets_service: AWS_SSM_PATH_PREFIX not set — skipping SSM load")
        return 0

    region = region or os.environ.get("AWS_REGION", "eu-west-2")

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError:
        logger.warning("secrets_service: boto3 not installed — cannot load SSM secrets")
        return 0

    try:
        client = boto3.client("ssm", region_name=region)
        parameters = _fetch_all_parameters(client, prefix)
    except NoCredentialsError:
        logger.info("secrets_service: no AWS credentials available — skipping SSM (expected in local dev)")
        return 0
    except (BotoCoreError, ClientError) as exc:
        logger.warning("secrets_service: SSM fetch failed: %s — falling back to .env", exc)
        return 0

    loaded = 0
    for param in parameters:
        env_name = _extract_env_name(param["Name"], prefix)
        if not override_existing and os.environ.get(env_name):
            logger.debug("secrets_service: %s already set — skipping (override_existing=False)", env_name)
            continue
        os.environ[env_name] = param["Value"]
        loaded += 1
        logger.debug("secrets_service: loaded %s from SSM", env_name)

    logger.info("Loaded %d secrets from SSM (prefix=%s, region=%s)", loaded, prefix, region)
    return loaded


def _fetch_all_parameters(client: Any, prefix: str) -> list[dict[str, Any]]:
    """Paginate through all SSM parameters under the given prefix."""
    parameters: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "Path": prefix,
        "Recursive": True,
        "WithDecryption": True,
        "MaxResults": 10,
    }

    while True:
        response = client.get_parameters_by_path(**kwargs)
        parameters.extend(response.get("Parameters", []))
        next_token = response.get("NextToken")
        if not next_token:
            break
        kwargs["NextToken"] = next_token

    return parameters


def list_ssm_parameters(prefix: str | None = None, region: str | None = None) -> list[str]:
    """List parameter names (not values) under the prefix. Useful for diagnostics."""
    prefix = prefix or os.environ.get("AWS_SSM_PATH_PREFIX", _DEFAULT_PREFIX)
    region = region or os.environ.get("AWS_REGION", "eu-west-2")

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError

        client = boto3.client("ssm", region_name=region)
        params = _fetch_all_parameters(client, prefix)
        return [p["Name"] for p in params]
    except (ImportError, BotoCoreError, ClientError) as exc:
        logger.warning("secrets_service: list failed: %s", exc)
        return []

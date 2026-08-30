"""The backend Fargate container must receive its Circle credentials (#1463).

``chain/circle_signer.py``, ``chain/oracle_updater.py``,
``services/circle_service.py`` and ``marketplace/wallet_provisioner.py`` all
read ``CIRCLE_API_KEY`` / ``CIRCLE_ENTITY_SECRET`` straight off the process
environment and raise "Circle credentials not configured" when they are blank.
Nothing reads them at *boot*, so a task definition that omits them starts
healthy and passes ``/health`` — every Circle-signed path (agent trade
execution, oracle updates, wallet provisioning, the revenue sweep) then fails
at call time, in production, with no alarm. That is exactly the fail-soft shape
CLAUDE.md § fail-soft names as the defect: an outage converted into a silence.

These tests read ``infra/ecs.tf`` as text and assert the wiring. They are
hermetic by construction — no AWS, no terraform binary, no network, no env
vars, no ``.env``: the only input is a file in the repo.

Terraform ``validate`` cannot catch any of this. A missing secret, a secret
pointed at the wrong SSM parameter, and a secret placed outside the prefix the
execution role can read are all syntactically valid HCL.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ECS_TF = REPO_ROOT / "infra" / "ecs.tf"

# The SSM prefix the ECS *execution* role is scoped to. Every `secrets` entry
# must live under it or the task cannot start (AccessDenied at pull time).
SSM_PREFIX = "parameter/archimedes/prod/"

# Added by #1463. Named explicitly so deleting them fails here, loudly.
REQUIRED_CIRCLE_SECRETS = ("CIRCLE_API_KEY", "CIRCLE_ENTITY_SECRET")

# Pre-existing before #1463. Listed so this guard also catches a future edit
# that adds something and drops one of these on the way past.
REQUIRED_EXISTING_SECRETS = (
    "DATABASE_URL",
    "REDIS_URL",
    "AURORA_MASTER_PASSWORD",
    "EMAIL_ENCRYPTION_KEY",
)

# #1463's anti-goal: arming the revenue sweep is a separate, owner-gated call.
# Seeding the credentials must not smuggle in the flag that spends with them.
FORBIDDEN_NAMES = ("REVENUE_SWEEP_ENABLED",)

_CONTAINER_NAME_RE = re.compile(r'^      name\s*=\s*"([^"]+)"', re.MULTILINE)
_ENTRY_RE = re.compile(r'\{\s*name\s*=\s*"([^"]+)"\s*,\s*(valueFrom|value)\s*=\s*"([^"]*)"')


def _tf_source() -> str:
    assert ECS_TF.is_file(), f"missing {ECS_TF}"
    return ECS_TF.read_text(encoding="utf-8")


def _container_block(name: str) -> str:
    """Slice one container out of the `container_definitions` list.

    Containers are delimited by their own 6-space-indented `name = "..."`
    line, so the block runs from this container's name to the next one (or to
    end-of-file for the last container).
    """
    src = _tf_source()
    matches = list(_CONTAINER_NAME_RE.finditer(src))
    assert matches, "no container blocks found in infra/ecs.tf — did the file shape change?"
    names = [m.group(1) for m in matches]
    assert name in names, f"no {name!r} container in infra/ecs.tf; found {names}"
    idx = names.index(name)
    start = matches[idx].start()
    end = matches[idx + 1].start() if idx + 1 < len(matches) else len(src)
    return src[start:end]


def _block(container: str, keyword: str) -> str:
    """Return the text of `<keyword> = [ ... ]` inside a container block.

    Bracket-matched rather than regex-terminated: the backend container's
    `environment` list contains nested `[...]` and a naive `\\]` would cut it
    short, silently shrinking the set under test.
    """
    match = re.search(rf"^      {keyword}\s*=\s*(?:concat\()?\[", container, re.MULTILINE)
    assert match, f"no `{keyword}` block found in the container"
    depth, i = 0, match.end() - 1
    while i < len(container):
        if container[i] == "[":
            depth += 1
        elif container[i] == "]":
            depth -= 1
            if depth == 0:
                return container[match.start() : i + 1]
        i += 1
    raise AssertionError(f"unbalanced brackets in the `{keyword}` block")


def _entries(text: str) -> dict[str, str]:
    """{name: value/valueFrom} for every `{ name = ..., value... = ... }` entry."""
    return {m.group(1): m.group(3) for m in _ENTRY_RE.finditer(text)}


@pytest.fixture(scope="module")
def backend_secrets() -> dict[str, str]:
    return _entries(_block(_container_block("backend"), "secrets"))


@pytest.fixture(scope="module")
def backend_environment() -> dict[str, str]:
    return _entries(_block(_container_block("backend"), "environment"))


class TestCircleCredentialsReachTheContainer:
    @pytest.mark.parametrize("secret", REQUIRED_CIRCLE_SECRETS)
    def test_secret_is_declared(self, secret: str, backend_secrets: dict[str, str]) -> None:
        """Fails against `main` before #1463 — neither key was in the block."""
        assert secret in backend_secrets, (
            f"{secret} is missing from the backend container's `secrets` block. "
            "The task will start healthy and every Circle-signed call will fail "
            "at runtime with 'Circle credentials not configured'."
        )

    @pytest.mark.parametrize("secret", REQUIRED_CIRCLE_SECRETS)
    def test_secret_resolves_from_ssm_not_a_literal(self, secret: str, backend_secrets: dict[str, str]) -> None:
        """A credential pasted as a plaintext `value` would be a committed secret."""
        value_from = backend_secrets[secret]
        assert value_from.startswith("arn:aws:ssm:"), (
            f"{secret} must resolve from an SSM parameter ARN, got {value_from!r}"
        )

    @pytest.mark.parametrize("secret", REQUIRED_CIRCLE_SECRETS)
    def test_secret_points_at_its_own_parameter(self, secret: str, backend_secrets: dict[str, str]) -> None:
        """Catches the copy-paste that maps both names to the same parameter.

        Duplicating the ARN when adding the second entry is the likeliest way
        to get this wrong, and it is invisible to `terraform validate`: the
        container boots with ENTITY_SECRET holding the API key and Circle
        rejects every signature.
        """
        assert backend_secrets[secret].endswith(SSM_PREFIX + secret), (
            f"{secret} resolves from {backend_secrets[secret]!r}, which is not /{SSM_PREFIX}{secret}"
        )


class TestTheExistingWiringSurvives:
    @pytest.mark.parametrize("secret", REQUIRED_EXISTING_SECRETS)
    def test_preexisting_secret_still_present(self, secret: str, backend_secrets: dict[str, str]) -> None:
        assert secret in backend_secrets

    def test_every_secret_sits_under_the_execution_roles_prefix(self, backend_secrets: dict[str, str]) -> None:
        """The execution role reads `parameter/archimedes/prod/*` and nothing else.

        A secret seeded at any other path makes the task fail to *start*
        (ResourceInitializationError), taking the whole service down — a worse
        outcome than the gap #1463 closes, so it gets its own guard.
        """
        stray = {n: v for n, v in backend_secrets.items() if SSM_PREFIX not in v}
        assert not stray, f"secrets outside the execution role's readable prefix: {stray}"

    def test_execution_role_policy_is_a_prefix_wildcard(self) -> None:
        """Why #1463 carries no IAM diff — and a tripwire if that stops holding.

        The `archimedes-ecs-execution-ssm-read` policy grants a prefix
        wildcard, so new parameters under it need no policy change. If someone
        narrows it to an enumeration, this fails and points at the secrets
        that would silently lose read access.
        """
        src = _tf_source()
        policy = src[src.index('name = "archimedes-ecs-execution-ssm-read"') :]
        policy = policy[: policy.index("\n}\n")]
        assert '"arn:aws:ssm:*:*:parameter/archimedes/prod/*"' in policy, (
            "the execution-role SSM policy is no longer a prefix wildcard — every "
            f"secret, including {list(REQUIRED_CIRCLE_SECRETS)}, must now be "
            "enumerated in its Resource list"
        )


class TestAntiGoals:
    """#1463 seeds credentials. It must not also arm what spends with them."""

    @pytest.mark.parametrize("name", FORBIDDEN_NAMES)
    def test_flag_is_not_set_on_the_backend_container(
        self,
        name: str,
        backend_secrets: dict[str, str],
        backend_environment: dict[str, str],
    ) -> None:
        assert name not in backend_environment, (
            f"{name} was added to the backend container's `environment`. Arming "
            "the revenue sweep is a separate, owner-gated decision (#1463 anti-goal)."
        )
        assert name not in backend_secrets, f"{name} was added to the backend `secrets`"

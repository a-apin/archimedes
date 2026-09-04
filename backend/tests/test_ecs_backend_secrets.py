"""The backend Fargate container must receive its Circle credentials (#1463).

``chain/circle_signer.py``, ``chain/oracle_updater.py``,
``services/circle_service.py`` and ``marketplace/wallet_provisioner.py`` read
Circle credentials straight off the process environment and raise "Circle
credentials not configured" when they are blank. The credential set is a TRIO,
not a pair — ``CircleSigner.is_configured`` is
``api_key and entity_secret and wallet_id`` — so ``WALLET_ID`` is as
load-bearing as the two ``CIRCLE_*`` names, and shipping only the two leaves
the signer and the oracle updater raising the exact error this wiring exists to
remove. Nothing reads any of them at *boot*, so a task definition that omits
one starts healthy and passes ``/health`` — every Circle-signed path (agent
trade execution, oracle updates, wallet provisioning, the revenue sweep) then
fails at call time, in production, with no alarm. That is exactly the fail-soft
shape CLAUDE.md § fail-soft names as the defect: an outage converted into a
silence.

These tests read ``infra/ecs.tf`` as text and assert the wiring. They are
hermetic by construction — no AWS, no terraform binary, no network, no env
vars, no ``.env``: the only input is a file in the repo.

Terraform ``validate`` cannot catch any of this. A missing secret, a secret
pointed at the wrong SSM parameter, and a secret placed outside the prefix the
execution role can read are all syntactically valid HCL.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ECS_TF = REPO_ROOT / "infra" / "ecs.tf"
CIRCLE_SIGNER_PY = REPO_ROOT / "backend" / "archimedes" / "chain" / "circle_signer.py"

# The SSM prefix the ECS *execution* role is scoped to. Every `secrets` entry
# must live under it or the task cannot start (AccessDenied at pull time).
SSM_PREFIX = "parameter/archimedes/prod/"

# Added by #1463 — the full credential trio, not just the two CIRCLE_*-prefixed
# names. `CircleSigner.is_configured` (backend/archimedes/chain/circle_signer.py)
# ANDs all three, so omitting WALLET_ID keeps the signer and the oracle updater
# raising "Circle credentials not configured" even with both CIRCLE_* wired.
# Named explicitly so deleting any one of them fails here, loudly.
REQUIRED_CIRCLE_SECRETS = ("CIRCLE_API_KEY", "CIRCLE_ENTITY_SECRET", "WALLET_ID")

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

# For the comment-truthfulness checks below: a counted claim in prose is only
# checkable if the checker can spell the count the same way a human would.
COUNT_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}

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


def _comment_region(start_anchor: str, end_anchor: str) -> str:
    """The slice of ecs.tf between two anchors, for prose-truthfulness checks."""
    src = _tf_source()
    assert start_anchor in src, f"anchor moved or was reworded: {start_anchor!r}"
    start = src.index(start_anchor)
    tail = src[start:]
    assert end_anchor in tail, f"anchor moved or was reworded: {end_anchor!r}"
    return tail[: tail.index(end_anchor)]


def _signer_required_env_names() -> set[str]:
    """Env var names ``CircleSigner.is_configured`` actually ANDs, read from source.

    Parsed with ``ast`` rather than imported: keeps the test hermetic (no
    ``archimedes`` import, no env, no aiohttp) and keeps this list derived from
    the code instead of hand-copied, so a fourth credential added to the gate
    cannot silently go unwired in ecs.tf.
    """
    tree = ast.parse(CIRCLE_SIGNER_PY.read_text(encoding="utf-8"))
    cls = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "CircleSigner"),
        None,
    )
    assert cls is not None, f"no CircleSigner class in {CIRCLE_SIGNER_PY}"

    # `self._attr = os.getenv("NAME", ...)` → {_attr: NAME}
    attr_to_env: dict[str, str] = {}
    for node in ast.walk(cls):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "getenv"):
            continue
        if not call.args or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                attr_to_env[target.attr] = call.args[0].value

    gate = next(
        (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "is_configured"),
        None,
    )
    assert gate is not None, "CircleSigner.is_configured not found — did the gate move?"

    names = {
        attr_to_env[n.attr]
        for n in ast.walk(gate)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
        and n.attr in attr_to_env
    }
    assert names, "no os.getenv-backed attributes read by CircleSigner.is_configured"
    return names


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

    def test_the_signers_whole_gate_is_wired(self, backend_secrets: dict[str, str]) -> None:
        """Every env var `CircleSigner.is_configured` ANDs must reach the container.

        Derived from circle_signer.py itself, so it stays true if the gate
        grows a fourth credential. This is the check that catches the shipped
        defect the parametrized cases above could not: with only the two
        CIRCLE_* names declared, the task boots and `is_configured` is still
        False, because WALLET_ID is the third conjunct.
        """
        required = _signer_required_env_names()
        missing = sorted(required - backend_secrets.keys())
        assert not missing, (
            f"CircleSigner.is_configured requires {sorted(required)} but the backend "
            f"container's `secrets` block omits {missing} — the signer stays "
            "unconfigured and every signed call raises 'Circle credentials not "
            "configured' at runtime."
        )
        unlisted = sorted(required - set(REQUIRED_CIRCLE_SECRETS))
        assert not unlisted, (
            f"circle_signer.py now gates on {unlisted}, which this file's "
            "REQUIRED_CIRCLE_SECRETS does not name — add them there too so the "
            "per-secret ARN checks cover them."
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


class TestTheCommentsTellTheTruth:
    """The two comments that describe the `secrets` block must describe *this* block.

    Both said "all four secrets" while the block held four; both were then
    edited into an unfalsifiable "every secret" hand-wave while the block held
    six — and the review caught that the block was *itself* incomplete. A
    counted, enumerated claim is checkable, so these tests check it: prose that
    asserts a property is the same defect surface as code that does
    (CLAUDE.md § "A guard must be shown to reject something").
    """

    # The two comment regions, by stable anchors either side of each.
    HEADER = ("# 3. RESOLVED (2026-07-08)", "# 4. `oracle_runner`")
    INLINE = ("# KNOWN GAP #3 (see file header)", "secrets = [")

    @pytest.mark.parametrize("region", ["HEADER", "INLINE"])
    def test_comment_enumerates_every_secret_in_the_block(self, region: str, backend_secrets: dict[str, str]) -> None:
        text = _comment_region(*getattr(self, region))
        unnamed = sorted(name for name in backend_secrets if name not in text)
        assert not unnamed, (
            f"the {region.lower()} comment describes the `secrets` block but does not "
            f"name {unnamed}. Enumerate every entry, or the next reader trusts a list "
            "that has silently gone stale — which is how WALLET_ID was missed."
        )

    @pytest.mark.parametrize("region", ["HEADER", "INLINE"])
    def test_comment_states_the_real_count(self, region: str, backend_secrets: dict[str, str]) -> None:
        text = _comment_region(*getattr(self, region))
        actual = len(backend_secrets)
        assert actual in COUNT_WORDS, f"{actual} secrets — extend COUNT_WORDS"
        expected = COUNT_WORDS[actual]
        stated = [w for w in COUNT_WORDS.values() if re.search(rf"\b{w}\b", text, re.IGNORECASE)]
        assert stated, (
            f"the {region.lower()} comment no longer states how many secrets the block "
            f"has. Say '{expected}' — a counted claim is checkable, 'every secret' is not."
        )
        wrong = [w for w in stated if w != expected]
        assert not wrong, (
            f"the {region.lower()} comment says {wrong} but the block has {actual} "
            f"entries ({sorted(backend_secrets)}). It should say '{expected}'."
        )


class TestTheFreeAllowanceIsPinnedNotInherited:
    """`FREE_GENERATIONS_PER_ACCOUNT` must be a decision, not a code default.

    Flip-list finding A5: prod served three free generations per account
    because `free_generations.allowance()` falls back to `DEFAULT_ALLOWANCE`,
    not because anybody put the number in a task definition. That is the same
    config-drift shape `GENERATION_DAILY_CAP_*` and `GENERATION_TIMEOUT_SECONDS`
    are plumbed here to avoid, and this is the one knob on that page that gives
    away paid product.

    Fails against `main` before this change: the name was nowhere in ecs.tf.
    """

    FREE_GENERATIONS_NAME = "FREE_GENERATIONS_PER_ACCOUNT"
    FREE_GENERATIONS_VALUE = "3"

    def test_the_allowance_is_declared_on_the_backend_container(self, backend_environment: dict[str, str]) -> None:
        assert self.FREE_GENERATIONS_NAME in backend_environment, (
            f"{self.FREE_GENERATIONS_NAME} is missing from the backend container's "
            "`environment` block, so prod's free allowance is whatever "
            "services/free_generations.py's code default happens to be (A5)."
        )

    def test_the_declared_value_is_the_owners_number(self, backend_environment: dict[str, str]) -> None:
        assert backend_environment[self.FREE_GENERATIONS_NAME] == self.FREE_GENERATIONS_VALUE, (
            f"{self.FREE_GENERATIONS_NAME} is "
            f"{backend_environment[self.FREE_GENERATIONS_NAME]!r} in infra/ecs.tf; the "
            f"owner's 2026-09-02 call is {self.FREE_GENERATIONS_VALUE!r}. Changing the "
            "number someone gets for free is a policy change and wants its own review."
        )

    def test_it_is_environment_not_a_secret(self, backend_secrets: dict[str, str]) -> None:
        """An allowance is not a credential; SSM would hide it from this guard."""
        assert self.FREE_GENERATIONS_NAME not in backend_secrets


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

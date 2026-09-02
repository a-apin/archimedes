#!/usr/bin/env python3
"""Rewrite the live ECS task definition for a CI deploy.

``deploy.yml`` registers a new revision by cloning the *currently registered*
task definition and retagging images. It does **not** apply terraform, so
pins that exist only in ``infra/ecs.tf`` are not live until someone applies.
Proven on task-def :211 (``fc884113``, #1725 image): the clone carried no
``PAPER_ADVANCE_ENABLED``, the code default was still ON, and ``/health``
502'd at ``PAPER_ADVANCE_STARTUP_DELAY_S`` (240s) after deploy-ecs success.

So this script pins the name EXPLICITLY on every deploy, in whichever
direction :data:`PAPER_ADVANCE_VALUE` points. The property that matters is not
which value ships but that the value ships *because this file says so*, rather
than because a months-old last-good revision happened to carry the name.

This script is the path that actually ships. It:

1. Retags the backend / nginx / auth images to this commit.
2. Pins ``PAPER_ADVANCE_ENABLED`` to :data:`PAPER_ADVANCE_VALUE` on the
   backend container, whether the cloned definition had the name unset,
   ``"true"``, or ``"false"``.
3. Ensures the backend container carries the :data:`TIINGO_SECRET_NAME`
   secret, resolving from ``/archimedes/prod/TIINGO_API_TOKEN``.
4. Drops the describe-only fields ``register-task-definition`` rejects.

Step 3 exists for the same reason step 2 does, one issue later (#1798, with
#1799 as the reason it cannot be an ``ecs.tf``-only change). The SSM parameter
has been seeded since 2026-08-31 and no container has ever been handed it,
because the live revisions are clones of clones and ``infra/ecs.tf`` is only
live after somebody applies. ``market_data_provider._tiingo_api_key()`` reads
the name off the process environment and raises rather than falling back to
yfinance, so a missing token is not a degraded path — it is a hard failure on
the first fetch after ``MARKET_DATA_PROVIDER=tiingo``. The twin entry lives in
``infra/ecs.tf``'s backend ``secrets`` block; both paths are pinned together by
``backend/tests/test_ecs_backend_secrets.py``.

The pinned value is ``"true"`` as of 2026-09-01 (#1778, the #1632 lift): the
paper-advance tick is ARMED. It never runs in the web interpreter —
``arm_paper_advance_for_web_tier`` spawns a child (#1728) — so a C-level abort
on the tick kills that child while ``/health``, in the parent, keeps
answering. That process boundary is what makes arming defensible; it is not a
claim that the tick's own frame is proven clean (#1632's found-and-fixed
mechanism, #1740, was elsewhere and was caught with this flag off).

To pull it back: set :data:`PAPER_ADVANCE_VALUE` to ``"false"`` here — this is
the pin that ships — and set the twin line in ``infra/ecs.tf`` to match, then
deploy. The code default in ``services/paper_trading.py`` stays ``"false"`` on
purpose: unset must still mean OFF, which is the :211 hole.

terraform apply is still required for other ``ecs.tf`` drift. This flag must
not depend on it.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

PAPER_ADVANCE_NAME = "PAPER_ADVANCE_ENABLED"
PAPER_ADVANCE_VALUE = "true"
BACKEND_CONTAINER = "backend"

# #1798. The env var name is the canonical one the provider reads first
# (``market_data_provider._TIINGO_TOKEN_ENV_VARS[0]``); the SSM path is the
# owner-seeded SecureString. Both halves are pinned against their real
# sources by backend/tests/test_ecs_backend_secrets.py.
TIINGO_SECRET_NAME = "TIINGO_API_TOKEN"
TIINGO_SSM_PATH = "parameter/archimedes/prod/TIINGO_API_TOKEN"

# Same drop-list the previous inline jq used. These fields come back from
# ``describe-task-definition`` and ``register-task-definition`` will not
# accept them.
_DROP_FIELDS = (
    "taskDefinitionArn",
    "revision",
    "status",
    "requiresAttributes",
    "compatibilities",
    "registeredAt",
    "registeredBy",
)


class RewriteError(ValueError):
    """The cloned task definition cannot be turned into a deployable revision."""


def pin_backend_paper_advance(environment: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return a copy of ``environment`` with the tick flag pinned explicitly.

    Entries other than ``PAPER_ADVANCE_ENABLED`` are preserved in order. The
    pin replaces any previous value and is otherwise appended, so a cloned
    last-good revision that never heard of the name still ships
    :data:`PAPER_ADVANCE_VALUE` — exactly once, whichever way it points.
    """
    pinned = [entry for entry in (environment or []) if entry.get("name") != PAPER_ADVANCE_NAME]
    pinned.append({"name": PAPER_ADVANCE_NAME, "value": PAPER_ADVANCE_VALUE})
    return pinned


def tiingo_secret_arn(task_def: dict[str, Any]) -> str:
    """Build the Tiingo SSM ARN from the cloned definition's OWN ARN.

    Region and account are read out of ``taskDefinitionArn``
    (``arn:aws:ecs:<region>:<account>:task-definition/<family>:<rev>``), which
    ``aws ecs describe-task-definition`` always returns, rather than taken from
    a workflow env var or a CLI flag. Same reasoning as
    :data:`PAPER_ADVANCE_VALUE`'s: a parameter someone can forget to pass has a
    default, and that default silently decides production. Deriving it means
    the ARN is correct in whatever account and region this deploy is actually
    talking to, and wrong nowhere.

    Raises :class:`RewriteError` rather than guessing. A guessed account id
    produces a syntactically perfect ARN that fails at task-launch time with
    ``ResourceInitializationError`` — the whole service down, not one feature
    degraded.
    """
    arn = task_def.get("taskDefinitionArn")
    if not isinstance(arn, str):
        raise RewriteError(
            "cloned task definition has no taskDefinitionArn — cannot derive the "
            f"account/region for the {TIINGO_SECRET_NAME} secret ARN"
        )
    parts = arn.split(":")
    # arn : partition : service : region : account : resource[ : qualifier ]
    if len(parts) < 6 or parts[0] != "arn" or not parts[1] or not parts[3] or not parts[4]:
        raise RewriteError(
            f"taskDefinitionArn {arn!r} is not a parseable ARN — cannot derive the "
            f"account/region for the {TIINGO_SECRET_NAME} secret ARN"
        )
    partition, region, account = parts[1], parts[3], parts[4]
    return f"arn:{partition}:ssm:{region}:{account}:{TIINGO_SSM_PATH}"


def ensure_backend_tiingo_secret(secrets: list[dict[str, Any]] | None, arn: str) -> list[dict[str, Any]]:
    """Return a copy of ``secrets`` carrying exactly one Tiingo entry.

    Idempotent in the same shape as :func:`pin_backend_paper_advance`: any
    existing entry under the name is dropped and the canonical one appended, so
    a clone that already has it (every deploy after the first, and every clone
    of a terraform-registered revision) comes out unchanged in content and
    carrying it exactly once, while a clone that predates the wiring gains it.
    Other secrets are preserved in order.
    """
    kept = [entry for entry in (secrets or []) if entry.get("name") != TIINGO_SECRET_NAME]
    kept.append({"name": TIINGO_SECRET_NAME, "valueFrom": arn})
    return kept


def rewrite_registered_task_definition(
    task_def: dict[str, Any],
    *,
    backend_image: str,
    nginx_image: str,
    auth_image: str,
) -> dict[str, Any]:
    """Clone ``task_def``, retag application images, pin the tick flag and the
    Tiingo secret.

    Raises :class:`RewriteError` if there is no backend container — a silent
    skip would register a revision whose ``PAPER_ADVANCE_ENABLED`` is whatever
    the clone happened to carry, decided by nobody. That is the :211 shape.
    """
    containers = task_def.get("containerDefinitions")
    if not isinstance(containers, list) or not containers:
        raise RewriteError("task definition has no containerDefinitions")

    secret_arn = tiingo_secret_arn(task_def)

    rewritten: list[dict[str, Any]] = []
    saw_backend = False
    for container in containers:
        name = container.get("name")
        next_container = dict(container)
        if name == BACKEND_CONTAINER:
            saw_backend = True
            next_container["image"] = backend_image
            next_container["environment"] = pin_backend_paper_advance(container.get("environment"))
            next_container["secrets"] = ensure_backend_tiingo_secret(container.get("secrets"), secret_arn)
        elif name == "nginx":
            next_container["image"] = nginx_image
        elif name == "auth":
            next_container["image"] = auth_image
        rewritten.append(next_container)

    if not saw_backend:
        raise RewriteError(
            f"no {BACKEND_CONTAINER!r} container in the cloned task definition — "
            "refusing to register a revision that cannot pin PAPER_ADVANCE_ENABLED "
            f"or {TIINGO_SECRET_NAME}"
        )

    out = {key: value for key, value in task_def.items() if key not in _DROP_FIELDS}
    out["containerDefinitions"] = rewritten
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_def_json", help="describe-task-definition taskDefinition blob")
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--nginx-image", required=True)
    parser.add_argument("--auth-image", required=True)
    args = parser.parse_args(argv)

    with open(args.task_def_json, encoding="utf-8") as fh:
        task_def = json.load(fh)

    try:
        rewritten = rewrite_registered_task_definition(
            task_def,
            backend_image=args.backend_image,
            nginx_image=args.nginx_image,
            auth_image=args.auth_image,
        )
    except RewriteError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    json.dump(rewritten, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

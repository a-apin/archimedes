#!/usr/bin/env python3
"""Rewrite the live ECS task definition for a CI deploy.

``deploy.yml`` registers a new revision by cloning the *currently registered*
task definition and retagging images. It does **not** apply terraform, so
pins that exist only in ``infra/ecs.tf`` are not live until someone applies.
Proven on task-def :211 (``fc884113``, #1725 image): the clone carried no
``PAPER_ADVANCE_ENABLED``, the code default was still ON, and ``/health``
502'd at ``PAPER_ADVANCE_STARTUP_DELAY_S`` (240s) after deploy-ecs success.
The code default is now OFF as well; this pin remains so a future default
flip cannot tick through a cloned task-def.

This script is the path that actually ships. It:

1. Retags the backend / nginx / auth images to this commit.
2. Pins ``PAPER_ADVANCE_ENABLED=false`` on the backend container, whether
   the cloned definition had the name unset, ``true``, or already ``false``.
3. Strips the retired env names in ``RETIRED_BACKEND_ENV`` from the backend
   container. #1766 deleted ``services/backtest_scheduler.py``; nothing on
   main reads ``BACKTEST_REFRESH_*``/``BACKTEST_MAX_AGE_HOURS`` any more, but
   the owner's hand-pinned ``BACKTEST_REFRESH_ENABLED=false`` (task-def 216,
   the #1760 mitigation) rides forward on every clone. Cleanups ship with the
   deploy rather than as an operator ritual, so the clone drops them here.
4. Drops the describe-only fields ``register-task-definition`` rejects.

terraform apply is still required for other ``ecs.tf`` drift. This flag
must not depend on it. Flip-back of the tick is this pin *and* the ecs.tf
line, after #1632 has a proven cause and a fix — do not flip it here.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

PAPER_ADVANCE_NAME = "PAPER_ADVANCE_ENABLED"
PAPER_ADVANCE_VALUE = "false"
BACKEND_CONTAINER = "backend"

# Env names the backend container must no longer carry. Every name here has
# zero readers under ``backend/archimedes`` — asserted by
# ``backend/tests/test_ecs_paper_advance_deploy_pin.py``, so re-adding a
# reader without taking the name off this tuple goes red rather than shipping
# a flag the deploy silently deletes.
#
# ``BACKTEST_REFRESH_ENABLED`` is the live pin: the owner set it by hand as
# task-def revision 216 during the 2026-09-01 #1760 storm, #1766 deleted the
# loop it switched, and the deploy has cloned it forward ever since.
RETIRED_BACKEND_ENV = (
    "BACKTEST_REFRESH_ENABLED",
    "BACKTEST_REFRESH_INTERVAL_HOURS",
    "BACKTEST_MAX_AGE_HOURS",
    "BACKTEST_REFRESH_STARTUP_DELAY_S",
)

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


def pin_backend_paper_advance_off(environment: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return a copy of ``environment`` with the kill switch pinned false.

    Existing entries other than ``PAPER_ADVANCE_ENABLED`` are preserved in
    order. The pin is appended (or replaces a previous value) so a cloned
    last-good revision that never heard of the name still ships ``false``.
    """
    pinned = [entry for entry in (environment or []) if entry.get("name") != PAPER_ADVANCE_NAME]
    pinned.append({"name": PAPER_ADVANCE_NAME, "value": PAPER_ADVANCE_VALUE})
    return pinned


def strip_retired_backend_env(
    environment: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return ``environment`` without any :data:`RETIRED_BACKEND_ENV` name.

    Idempotent: a clone that already lost them comes back unchanged with an
    empty removal list, so the deploy after the first one is a no-op. Entries
    that survive keep their order and their objects untouched.

    Returns the filtered list and the names that were actually removed, so
    the caller can say what it deleted instead of deleting silently.
    """
    retired = set(RETIRED_BACKEND_ENV)
    kept: list[dict[str, Any]] = []
    removed: list[str] = []
    for entry in environment or []:
        name = entry.get("name")
        if name in retired:
            removed.append(name)
        else:
            kept.append(entry)
    return kept, removed


def rewrite_registered_task_definition(
    task_def: dict[str, Any],
    *,
    backend_image: str,
    nginx_image: str,
    auth_image: str,
) -> dict[str, Any]:
    """Clone ``task_def``, retag application images, pin the kill switch.

    Raises :class:`RewriteError` if there is no backend container — a silent
    skip would ship the code default ON, which is the outage.
    """
    containers = task_def.get("containerDefinitions")
    if not isinstance(containers, list) or not containers:
        raise RewriteError("task definition has no containerDefinitions")

    rewritten: list[dict[str, Any]] = []
    saw_backend = False
    for container in containers:
        name = container.get("name")
        next_container = dict(container)
        if name == BACKEND_CONTAINER:
            saw_backend = True
            next_container["image"] = backend_image
            environment, removed = strip_retired_backend_env(container.get("environment"))
            if removed:
                print(
                    f"::notice::dropping retired backend env from the cloned task definition: {', '.join(removed)}",
                    file=sys.stderr,
                )
            next_container["environment"] = pin_backend_paper_advance_off(environment)
        elif name == "nginx":
            next_container["image"] = nginx_image
        elif name == "auth":
            next_container["image"] = auth_image
        rewritten.append(next_container)

    if not saw_backend:
        raise RewriteError(
            f"no {BACKEND_CONTAINER!r} container in the cloned task definition — "
            "refusing to register a revision that cannot pin PAPER_ADVANCE_ENABLED"
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

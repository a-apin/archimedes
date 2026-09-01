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
3. Pins the backend container ``healthCheck.startPeriod`` to 90s so a
   cloned live revision (startPeriod 30) cannot leave the #1713 warmup
   budget (60s) outside ECS's ignored-failure window. Command / interval
   / timeout / retries on an existing healthCheck are preserved; a clone
   with no healthCheck gets the same command ecs.tf declares.
4. Drops the describe-only fields ``register-task-definition`` rejects.

terraform apply is still required for other ``ecs.tf`` drift. These pins
must not depend on it. Flip-back of the tick is the PAPER_ADVANCE pin
*and* the ecs.tf line, after #1632 has a proven cause and a fix — do
not flip it here. Do not terraform-noop ``deployment_minimum_healthy_percent``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

PAPER_ADVANCE_NAME = "PAPER_ADVANCE_ENABLED"
PAPER_ADVANCE_VALUE = "false"
BACKEND_CONTAINER = "backend"

# Must be >= archimedes.services.request_path_warmup.WARMUP_BUDGET_SECONDS
# (60). 90 matches ecs.tf health_check_grace_period_seconds / alb.tf grace.
# Live cloned revisions still carry startPeriod=30; without this pin the
# 60s warmup budget sits outside the ignored-failure window and a slow
# prime is killed (or, worse, the task is marked healthy while still
# warming — the #1713 bug if warmup fail-softs). Do not lower this below
# the warmup budget. Do not raise desiredCount. Do not flip PAPER_ADVANCE.
BACKEND_HEALTHCHECK_START_PERIOD = 90
BACKEND_HEALTHCHECK_COMMAND = [
    "CMD-SHELL",
    'python -c "import urllib.request; urllib.request.urlopen(\'http://localhost:8000/health\')" || exit 1',
]
BACKEND_HEALTHCHECK_INTERVAL = 30
BACKEND_HEALTHCHECK_TIMEOUT = 5
BACKEND_HEALTHCHECK_RETRIES = 3

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


def pin_backend_healthcheck_start_period(health_check: dict[str, Any] | None) -> dict[str, Any]:
    """Return a healthCheck with startPeriod pinned, existing command kept.

    A cloned last-good revision may have no healthCheck at all, or one
    with startPeriod 30. Overwriting startPeriod only would drop a
    missing command (register-task-definition rejects that) or leave 30
    in place if we skipped a missing block. Existing command / interval
    / timeout / retries are preserved so this pin cannot silently replace
    the /health probe.
    """
    pinned = dict(health_check or {})
    if not pinned.get("command"):
        pinned["command"] = list(BACKEND_HEALTHCHECK_COMMAND)
        pinned.setdefault("interval", BACKEND_HEALTHCHECK_INTERVAL)
        pinned.setdefault("timeout", BACKEND_HEALTHCHECK_TIMEOUT)
        pinned.setdefault("retries", BACKEND_HEALTHCHECK_RETRIES)
    pinned["startPeriod"] = BACKEND_HEALTHCHECK_START_PERIOD
    return pinned


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
            next_container["environment"] = pin_backend_paper_advance_off(container.get("environment"))
            next_container["healthCheck"] = pin_backend_healthcheck_start_period(container.get("healthCheck"))
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

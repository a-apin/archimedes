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
3. Drops the describe-only fields ``register-task-definition`` rejects.

The pinned value is ``"true"`` as of 2026-09-01 (#1741, the #1632 lift): the
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


def rewrite_registered_task_definition(
    task_def: dict[str, Any],
    *,
    backend_image: str,
    nginx_image: str,
    auth_image: str,
) -> dict[str, Any]:
    """Clone ``task_def``, retag application images, pin the tick flag.

    Raises :class:`RewriteError` if there is no backend container — a silent
    skip would register a revision whose ``PAPER_ADVANCE_ENABLED`` is whatever
    the clone happened to carry, decided by nobody. That is the :211 shape.
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
            next_container["environment"] = pin_backend_paper_advance(container.get("environment"))
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

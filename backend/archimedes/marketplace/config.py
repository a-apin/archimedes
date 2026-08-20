"""Shared configuration defaults for the marketplace money seam.

Single source of truth for chain-name defaults so that payment charging,
Gateway withdrawal, and settlement all read from the same constant.
"""

from __future__ import annotations

import os

DEFAULT_GATEWAY_CHAIN = "arcTestnet"


def payments_halted() -> bool:
    """Live, per-call kill switch for real-money charging (issue #1240).

    ``PAYMENTS_DRY_RUN`` is the primary safety switch, but ``MarketService``
    reads it once at construction (``main.py`` boot) and caches it on
    ``self.payments_dry_run`` — flipping it off requires a container
    restart. ``PAYMENTS_HALT`` is read fresh from ``os.environ`` on every
    call (never cached), so an operator can stop real charges by setting
    the env var — sourced from an SSM parameter under the same
    ``/archimedes/prod/*`` prefix ``secrets_service.load_ssm_secrets``
    already sweeps into ``os.environ`` at boot — and cycling the ECS
    service (``aws ecs update-service --force-new-deployment``), which is
    a same-image task restart, not the full build/push/roll deploy
    pipeline.

    Only the literal truthy strings enable it; unset/anything else is
    "not halted" (fails toward the status quo — PAYMENTS_DRY_RUN remains
    the primary, default-on safety net).
    """
    return os.getenv("PAYMENTS_HALT", "false").strip().lower() in ("1", "true", "yes")

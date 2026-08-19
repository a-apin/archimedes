"""Repair: create the Better Auth tables where 9ad1c4e2b7f0's DDL never ran.

Prod incident 2026-08-19: Aurora's ``alembic_version`` was stamped
``b7e3f1a2c9d4`` by an Aug-3 vintage of that revision id (the migration chain
was re-serialized twice afterward and the id was reused with different
parentage), so the deploy's pre-rollout ``alembic upgrade head`` no-op'd while
``9ad1c4e2b7f0``'s DDL — the auth_* tables and linked_wallets — never
executed. The child migration's own DDL (owner_user_id) IS present.

This repair re-invokes the ORIGINAL migration's upgrade() verbatim, guarded by
an inspector check, so environments that already ran 9ad1c4e2b7f0 (local
compose, CI, any correctly-migrated DB) no-op cleanly and prod converges onto
the identical schema with zero hand-translated DDL.

Hazard note for the migration-chain guard (#1270): id reuse across
re-serializations — same revision id, different content/parentage — is
invisible to both alembic and the guard. Never reuse a revision id.

Revision ID: e4d9a7f21c58
Revises: c9f2e8d4a1b7
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op
from sqlalchemy import inspect

revision = "e4d9a7f21c58"
down_revision = "c9f2e8d4a1b7"
branch_labels = None
depends_on = None

_ORIGINAL = Path(__file__).with_name("9ad1c4e2b7f0_add_better_auth_and_linked_wallets.py")


def _load_original():
    spec = importlib.util.spec_from_file_location("_repair_target_9ad1c4e2b7f0", _ORIGINAL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def upgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("auth_users"):
        return  # 9ad1c4e2b7f0 really ran here — nothing to repair.
    _load_original().upgrade()


def downgrade() -> None:
    # A repair migration never un-repairs: downgrading the schema it fixed is
    # 9ad1c4e2b7f0's own downgrade path, reachable only via a correct chain.
    pass

"""assoc/v1: passport projection columns (schema only, no data rewrite)

Issue #1637, PR-1 of the #1688 split. **A paper association is a record, not a
string** — and this revision adds the four columns the passport projection was
silently dropping, and nothing else.

``passport_paper_refs`` gains ``role`` / ``selection_rank`` /
``semantic_score`` / ``content_hash``. These are the ``assoc/v1`` fields
(``archimedes/models/paper_assoc.py``) the projection could not carry, so the
renderer had no way to show them. All nullable except ``role``, which defaults
to ``"cited"`` — every association that existed before this column WAS a
citation, so that default is a recoverable fact rather than a guess. The other
three have **no** server_default on purpose: ``NULL`` must stay
distinguishable from a real rank / score / hash, and #1091 means
``content_hash`` is genuinely NULL for every paper in production.

────────────────────────────────────────────────────────────────────────────
WHAT THIS REVISION DELIBERATELY DOES NOT DO
────────────────────────────────────────────────────────────────────────────

#1688 also re-stamped ``strategy_store.content_hash`` under the new
identity-only hash definition, and normalized ``strategy_store.source_papers``
to ``assoc/v1`` on every row. Owner decision on #1688 (2026-09-03): *"Yes to
the redefinition, not yet to the re-stamp… a dedup-hygiene step does not get
to be irreversible on an unmeasured table. Restamp lands only after the
dry-run."* So both halves of that data rewrite are held for PR-2, and this
revision is a pure ``ADD COLUMN`` / ``DROP COLUMN`` pair — reversible, byte
for byte.

**The ``source_papers`` normalization is held back with the re-stamp, not
separately from it, and that is load-bearing.** PR-2's gate is a read-only
dry-run that recomputes each row's *historical* hash with the frozen
pre-#1637 function over that row's **stored ``source_papers`` JSON**, and
re-stamps only the rows where the recomputation reproduces what is actually
stored. Normalizing the column first destroys that input: the legacy hash
would no longer reproduce for any row, the dry-run would report a ~0 reproduce
rate, and the re-stamp it gates would correctly refuse to do anything —
permanently. (#1688's own ``test_assoc_migration_upgrade_is_idempotent``
relies on exactly this property to prove a second pass is a no-op.) The
normalization therefore belongs in the same pass that reads the raw shape, and
it costs nothing to wait: every reader in ``strategy_store`` runs its input
through ``paper_assoc.normalize_assocs``, so a legacy-shaped row and an
``assoc/v1`` row decode identically today.

The one interim cost, stated rather than hidden: ``_compute_content_hash`` now
produces a value no pre-existing row carries, so re-upserting the *same*
content inserts a second row instead of matching the first. Nothing downstream
joins on ``content_hash`` (``id`` is the FK every other table uses, and it
never moves), and #1792's stored rigor verdict keys off ``id`` too — so this
is the pre-existing per-writer split-brain moved, not widened. PR-2 closes it.

Revision ID: c8a4d1f70b93
Revises: d3a71f5c9e28
Create Date: 2026-09-03 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8a4d1f70b93"
down_revision: str | Sequence[str] | None = "d3a71f5c9e28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

#: The four columns, newest-first for the drop. Written once so upgrade and
#: downgrade cannot disagree about which columns this revision owns.
_COLUMNS = (
    ("role", sa.String(length=16), {"nullable": False, "server_default": "cited"}),
    ("selection_rank", sa.Integer(), {"nullable": True}),
    ("semantic_score", sa.Float(), {"nullable": True}),
    ("content_hash", sa.String(length=64), {"nullable": True}),
)


def upgrade() -> None:
    with op.batch_alter_table("passport_paper_refs") as batch_op:
        for name, type_, kwargs in _COLUMNS:
            batch_op.add_column(sa.Column(name, type_, **kwargs))


def downgrade() -> None:
    with op.batch_alter_table("passport_paper_refs") as batch_op:
        for name, _type, _kwargs in reversed(_COLUMNS):
            batch_op.drop_column(name)

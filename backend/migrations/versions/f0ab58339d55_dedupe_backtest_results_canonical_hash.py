"""collapse backtest_results duplicates onto the fixed canonical hash

Issue #1347. Root cause: ``canonical_artifact_hash``
(``backend/archimedes/services/backtest_mapper.py``) used to hash the WHOLE
artifact payload, including ``run_id`` and ``timestamp_utc`` — both minted
fresh on every run regardless of content — so every run's ``content_hash`` was
unique by construction and ``insert_backtest_if_missing``'s content-hash
dedupe could never fire. #1263 fixed an unrelated ``PermissionError`` that
had, as a side effect, always killed the refresh *before* the DB insert, which
masked this defect (zero rows ever persisted). Once #1263 landed, every
scheduled refresh started re-inserting every strategy's backtest on every
container restart. Measured against production 2026-08-20 (read-only query):
**646 rows over 51 distinct strategies**, top offenders 31 / 25 / 24 rows,
long tail at ~22 — consistent with the restart cadence around the #1309 /
#1328 / #1329 / #1337 crash-loop window.

The code fix (same PR, ``backtest_mapper.py``) makes ``canonical_artifact_hash``
exclude ``run_id``/``timestamp_utc`` before hashing, going forward. This
migration is the one-time cleanup for rows already written under the old,
volatile-inclusive hash: it does NOT change what future rows hash to (that is
entirely the code fix); it only recomputes each EXISTING row's canonical hash
from the row's own stored ``artifact_json`` payload — using the SAME
``canonical_artifact_hash`` function the app now uses, imported directly
rather than reimplemented, so migration and app can never define "canonical
hash" two different ways — and collapses rows that recompute to the same
(strategy_id, hash) pair.

Per (strategy_id, recomputed_hash) group with more than one row, the EARLIEST
row survives (ordered by ``created_at`` ascending, tiebreak ``id`` ascending —
the same "when did the row land" ordering ``backtest_repository.py`` already
uses for "latest", just reversed), and every other row in the group is
deleted. The survivor's ``content_hash`` column is then normalized to the
recomputed value (even for rows that were never part of a duplicate group) so
that after this migration, EVERY row's ``content_hash`` agrees with what the
fixed ``canonical_artifact_hash`` would produce for its own stored payload —
which is what makes a future re-run's dedupe check
(``WHERE strategy_id = ? AND content_hash = ?``) actually match a normalized
historical row instead of just failing to find a match forever.

Rows whose ``artifact_json`` is NULL or fails to parse are left untouched
(their existing ``content_hash`` stands in as their own "recomputed" value, so
they never merge with anything) — there is no payload to recompute a
canonical hash FROM, and refusing to guess is the honest choice (see
CLAUDE.md's fail-soft principle: an unresolvable row stays exactly as it is,
never silently coerced into a group it cannot be proven to belong to).

FK safety: grepped every model under ``backend/archimedes/models/*.py`` for
``ForeignKey`` — nothing references ``backtest_results.id`` (or any other
``backtest_results`` column). Every consumer of ``BacktestResultRecord``
(``backtest_repository.py``, ``strategies_routes.py``,
``audit_backtest_universe.py``) looks rows up by ``strategy_id`` /
``content_hash``, never by a stored foreign row id. Deleting superseded rows
here is therefore FK-safe with no re-pointing needed.

Idempotent by construction: a second run recomputes the same hash from the
same stored ``artifact_json`` for every surviving row, finds each
(strategy_id, hash) group already collapsed to one row, and updates nothing
(the survivor's ``content_hash`` already equals its recomputed value from the
first run) — see ``test_dedupe_backtest_results_migration_upgrade_is_idempotent``
in ``backend/tests/test_alembic_migrations.py``.

Downgrade is a documented no-op: this migration makes no schema change (no
column added/removed), so there is nothing to structurally revert. The row
deletions themselves are NOT reversible — the deleted rows' data is gone —
and that is honest, not a gap: they were byte-for-byte redundant duplicates
of a surviving row's content, so there is nothing a downgrade could restore
that the survivor doesn't already carry.

Revision ID: f0ab58339d55
Revises: c9396e0d95d4
Create Date: 2026-08-20 00:00:00.000000

"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0ab58339d55"
down_revision: str | Sequence[str] | None = "c9396e0d95d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Recompute canonical hashes from stored payloads; collapse duplicates."""
    # Deferred, function-local import: alembic revision modules are not on
    # the app's normal import path (see migrations/env.py's own comment on
    # why it inserts backend/ onto sys.path), but backend/ IS on sys.path by
    # the time `upgrade()` runs inside the alembic subprocess, so importing
    # the app's OWN hashing function here — rather than re-deriving the same
    # logic a second time — is safe and is what keeps this migration from
    # ever drifting out of sync with what canonical_artifact_hash means.
    from archimedes.services.backtest_mapper import canonical_artifact_hash

    bind = op.get_bind()

    rows = (
        bind.execute(
            sa.text(
                "SELECT id, strategy_id, content_hash, artifact_json, created_at "
                "FROM backtest_results "
                "ORDER BY strategy_id, created_at ASC, id ASC"
            )
        )
        .mappings()
        .all()
    )

    # (strategy_id, recomputed_hash) -> [(id, existing_content_hash), ...] in
    # created_at/id ascending order (guaranteed by the ORDER BY above), so
    # group[0] is always the earliest row for that group.
    groups: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)

    for row in rows:
        payload = None
        if row["artifact_json"]:
            try:
                payload = json.loads(row["artifact_json"])
            except (TypeError, ValueError):
                payload = None

        # No resolvable payload: fall back to the row's own existing hash,
        # which — by the pre-existing uq_backtest_strategy_content constraint
        # — is already unique per strategy_id, so this row is its own
        # singleton group and is never merged with anything.
        recomputed_hash = canonical_artifact_hash(payload) if isinstance(payload, dict) else row["content_hash"]

        groups[(row["strategy_id"], recomputed_hash)].append((row["id"], row["content_hash"]))

    to_delete: list[int] = []
    to_update: list[dict[str, object]] = []

    for (_strategy_id, recomputed_hash), members in groups.items():
        survivor_id, survivor_old_hash = members[0]
        to_delete.extend(member_id for member_id, _old_hash in members[1:])
        if survivor_old_hash != recomputed_hash:
            to_update.append({"id": survivor_id, "content_hash": recomputed_hash})

    # Delete duplicates FIRST: only after a group has collapsed to one row is
    # it safe to update that survivor's content_hash to the shared recomputed
    # value without transiently colliding with a sibling row that is about to
    # be deleted anyway (uq_backtest_strategy_content is (strategy_id,
    # content_hash) and would otherwise reject the UPDATE mid-migration).
    if to_delete:
        bind.execute(
            sa.text("DELETE FROM backtest_results WHERE id = :id"),
            [{"id": row_id} for row_id in to_delete],
        )

    if to_update:
        bind.execute(
            sa.text("UPDATE backtest_results SET content_hash = :content_hash WHERE id = :id"),
            to_update,
        )


def downgrade() -> None:
    """Documented no-op — see the module docstring's "Downgrade" section.

    No schema change was made (no column added/removed), so there is nothing
    structural to revert. The row deletions performed by `upgrade()` are not
    reversible: the deleted rows were byte-for-byte content duplicates of a
    surviving row, so nothing is lost that the survivor doesn't already
    carry, and there is no prior state to restore them from.
    """

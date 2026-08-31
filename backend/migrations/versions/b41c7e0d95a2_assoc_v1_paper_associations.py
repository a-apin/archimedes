"""assoc/v1 paper associations: passport projection columns + one-time re-stamp

Issue #1637. Two things, one decision: **a paper association is a record, not a
string, and its identity is the paper — not the shape the writer happened to
use.**

1. ``passport_paper_refs`` gains ``role`` / ``selection_rank`` /
   ``semantic_score`` / ``content_hash``. These are the ``assoc/v1`` fields the
   passport projection was dropping, so the renderer had no way to show them.
   All nullable except ``role``, which defaults to ``"cited"`` — every
   association that existed before this column WAS a citation, so that default
   is a recoverable fact rather than a guess. The other three have **no**
   server_default on purpose: ``NULL`` must stay distinguishable from a real
   rank / score / hash, and #1091 means ``content_hash`` is genuinely NULL for
   every paper in production.

2. ``strategy_store.source_papers`` is normalized to ``assoc/v1``, and
   ``strategy_store.content_hash`` is re-stamped under the new hash definition
   (identity only — ``sorted((arxiv_id, role))`` — instead of the whole
   association dicts).

**Why step 2 must happen here rather than being left to fix forward.** The hash
definition changed. Without a re-stamp, the next regeneration of an existing
strategy would compute a value no stored row carries, miss the dedup lookup,
and insert a DUPLICATE row — the exact split-brain #1637 exists to remove,
reintroduced by the fix for it.

**The re-stamp is exact, not a guess, and it verifies itself.** For each row it
recomputes the *historical* hash from that row's own stored columns
(``generation_method``, ``strategy_name``, ``thesis``, the raw ``source_papers``
JSON, ``asset_universe``) using the frozen pre-#1637 function, and rewrites
``content_hash`` **only when the recomputation reproduces what is actually
stored**. A row that does not reproduce is left alone. That is what keeps the
provider-example rows safe: ``main.py`` seeds them with a domain-separated
SHA-256 (``"example:" + id``) that this function never produced and can never
reproduce, so they are skipped by construction rather than by a special case.

``source_papers`` is normalized on **every** row, including skipped ones — one
shape in the column is the point, and normalization does not touch identity.

**Collisions are disclosed, not resolved.** ``strategy_store`` carries
``UNIQUE(content_hash)``. On a database that already holds the split-brain — the
same strategy written twice through two writers — both rows compute the same new
hash, and a naive UPDATE would abort the whole migration on a constraint
violation. The oldest row is re-stamped; any row that would collide keeps its
legacy hash and is named in a WARNING. Merging them is deliberately NOT done
here: ``strategy_store.id`` is a foreign key from the generator, marketplace,
billing and deployment tables, so collapsing two rows is an ownership decision,
not a schema step. This migration guarantees no NEW duplicate is created and
hands the pre-existing ones to a human with their ids.

``id`` is deliberately NOT recomputed. It is a foreign key from the
marketplace, billing, deployment and generator tables; re-deriving it from the
new hash would orphan all of them. ``id`` has always meant "the id this row was
created with"; only ``content_hash`` means "the current identity of this
content".

**Downgrade re-stamps nothing.** The per-writer shapes the old hash was
computed over are not recoverable from the normalized column, so a downgrade
cannot reproduce the historical values — and inventing them is exactly what
this issue is about. Rolling the code back without the data leaves existing
rows readable and correctly ided; the cost is that dedup misses once per
strategy again, which is disclosed rather than hidden.

Revision ID: b41c7e0d95a2
Revises: a7f2c93b1d64
Create Date: 2026-08-31 12:00:00.000000
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b41c7e0d95a2"
down_revision: str | Sequence[str] | None = "a7f2c93b1d64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def _restamp_strategy_store() -> None:
    """Normalize ``source_papers`` and re-stamp ``content_hash`` where it verifies."""
    # Deferred, function-local imports: alembic revision modules are not on the
    # app's normal import path, but backend/ IS on sys.path by the time
    # upgrade() runs inside the alembic subprocess (see migrations/env.py).
    # Importing the app's OWN hash functions rather than re-deriving them a
    # second time is what keeps this migration from ever drifting out of sync
    # with what "content hash" means — same argument, and same precedent, as
    # f0ab58339d55.
    from archimedes.models.paper_assoc import normalize_assocs
    from archimedes.models.strategy_store import _compute_content_hash, _compute_content_hash_v0

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, content_hash, generation_method, strategy_name, thesis, "
            "source_papers, asset_universe FROM strategy_store ORDER BY created_at, id"
        )
    ).fetchall()

    # `strategy_store` carries UNIQUE(content_hash) (`uq_strategy_content_hash`).
    # The whole point of the new hash is that rows which differ only by writer
    # shape now agree — so on a database that actually contains the split-brain
    # this issue describes, two rows WILL compute the same new value and a naive
    # UPDATE would abort the migration on a constraint violation. That is not a
    # theoretical edge: it is the exact state the fix is for.
    #
    # So: first re-stamp wins (rows are ordered by created_at, so it is the
    # oldest), and a row whose new hash is already taken keeps its legacy hash
    # and is NAMED in the log. It is deliberately not deleted or merged —
    # `strategy_store.id` is a foreign key from the generator, marketplace,
    # billing and deployment tables, and collapsing two rows into one is an
    # ownership decision with consequences a schema migration must not take on
    # its own. What this migration guarantees is that no NEW duplicate is
    # created; reconciling duplicates that already exist stays a human call,
    # with the ids printed here to make it actionable.
    taken = {r.content_hash for r in rows if r.content_hash}

    normalized_count = 0
    restamped_count = 0
    collisions: list[str] = []

    for row in rows:
        try:
            raw_papers = json.loads(row.source_papers) if row.source_papers else []
            universe = json.loads(row.asset_universe) if row.asset_universe else []
        except (TypeError, ValueError):
            # A corrupt JSON column is not something to repair by guessing.
            # Leave the row exactly as found; it is already unreadable by the
            # application's own decoders and this migration must not invent a
            # value that makes it look healthy.
            continue

        if not isinstance(raw_papers, list) or not isinstance(universe, list):
            continue

        assocs = normalize_assocs(raw_papers)
        updates: dict[str, object] = {"source_papers": json.dumps(assocs)}
        normalized_count += 1

        legacy = _compute_content_hash_v0(
            row.generation_method or "",
            row.strategy_name or "",
            row.thesis or "",
            raw_papers,
            universe,
        )
        if legacy == row.content_hash:
            new_hash = _compute_content_hash(
                row.generation_method or "",
                row.strategy_name or "",
                row.thesis or "",
                assocs,
                universe,
            )
            # This row's own current value is not an obstacle to itself.
            taken.discard(row.content_hash)
            if new_hash in taken:
                collisions.append(row.id)
                taken.add(row.content_hash)  # it keeps what it had
            else:
                updates["content_hash"] = new_hash
                taken.add(new_hash)
                restamped_count += 1

        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        bind.execute(
            sa.text(f"UPDATE strategy_store SET {set_clause} WHERE id = :row_id"),
            {**updates, "row_id": row.id},
        )

    if collisions:
        logging.getLogger("alembic.runtime.migration").warning(
            "assoc/v1: %d row(s) already duplicate an existing strategy under the new "
            "identity and kept their legacy content_hash — reconcile by hand: %s",
            len(collisions),
            ", ".join(sorted(collisions)),
        )

    logging.getLogger("alembic.runtime.migration").info(
        "assoc/v1: normalized source_papers on %d rows; re-stamped content_hash on %d "
        "(the rest did not reproduce their stored hash and were left untouched)",
        normalized_count,
        restamped_count,
    )


def upgrade() -> None:
    with op.batch_alter_table("passport_paper_refs") as batch_op:
        batch_op.add_column(
            sa.Column("role", sa.String(length=16), nullable=False, server_default="cited"),
        )
        batch_op.add_column(sa.Column("selection_rank", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("semantic_score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("content_hash", sa.String(length=64), nullable=True))

    _restamp_strategy_store()


def downgrade() -> None:
    # See the module docstring: the content-hash re-stamp is not reversed,
    # because the per-writer shapes it was computed over are gone.
    with op.batch_alter_table("passport_paper_refs") as batch_op:
        batch_op.drop_column("content_hash")
        batch_op.drop_column("semantic_score")
        batch_op.drop_column("selection_rank")
        batch_op.drop_column("role")

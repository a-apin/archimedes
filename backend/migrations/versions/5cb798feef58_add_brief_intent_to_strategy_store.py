"""add brief_intent to strategy_store

The strategy passport shows the methodology derived FROM the user's ask, but
never the ask itself — the free-text brief the user typed lives only in
``strategy_proposals.payload["intent"]`` (an episodic, non-authoritative log:
see ``strategy_memory.persist_proposal``), not on the persisted
``StrategyRecord`` the passport route (``strategies_routes._passport_to_
strategy_response`` / ``get_strategy``) actually reads. This revision adds a
nullable ``brief_intent`` (free text, mirrors ``strategy_spec``'s Text-not-
JSON-typed column for the same SQLite-portability reason) so a strategy can
carry its own originating brief going forward — ``generation_pipeline.
_persist_candidate`` writes it at generation time from the ``GenerateBrief``
it already has in hand (no proposal lookup needed for NEW rows).

BACKFILL (migrations-ship-with-their-data rule): attempted, partial, and
honestly bounded — this is the "genuinely ambiguous" case the rule
anticipates rather than a full backfill.

There is no FK or shared id between ``strategy_store`` and
``strategy_proposals``: a proposal is keyed by ``generation_id`` (== the
generation job's ``job_id``), a strategy by its own content hash, and nothing
on either row names the other directly. ``generation_costs`` links
``job_id -> strategy_id``, but that table is brand new (``e2b7f4c81d93``,
2026-08-20) and covers only post-instrumentation runs — using it alone would
backfill a handful of the newest rows and call the rest unresolvable, which
is not true.

The reliable link is content: ``generation_pipeline.run_generation`` (and the
older ``_run_fusion_job``) persist the SAME ``strategy_name``/``thesis`` pair
into both tables in the same request — ``upsert_strategy(strategy_name=...,
thesis=...)`` and, in the very same code path,
``persist_proposal(strategy_spec={"strategy_name": ..., "thesis": ..., ...},
intent=brief.intent)``. So this migration joins on that pair: for each
``strategy_store`` row, find every ``strategy_proposals`` row whose
``payload->strategy_spec`` carries the identical ``(strategy_name, thesis)``.
Every proposal from the SAME generation shares one ``intent`` (it is the same
brief for every candidate in that job), so the join does not even need to
resolve to a single proposal row — only to a single DISTINCT intent value.

A ``(strategy_name, thesis)`` pair is backfilled only when:
  1. at least one ``strategy_proposals`` row matches it, AND
  2. every matching row agrees on the SAME ``intent`` string.

Rows failing either condition are left ``brief_intent IS NULL`` — no match
(most likely: legacy strategies persisted before ``persist_proposal`` existed,
or curated/example rows, which have no brief at all), or a genuine collision
where two different generations coincidentally produced identical
strategy_name/thesis text under two different briefs (most plausible for the
deterministic fixture path, which can emit the same name/thesis regardless of
input). Guessing which of two intents is right would put a fabricated brief
on a passport — the thing this column exists to show honestly — so an
ambiguous match is refused, not resolved by taking the first hit.

Revision ID: 5cb798feef58
Revises: a3f19c7d2e84
Create Date: 2026-08-30 00:00:00.000000

SEQUENCING: chained onto ``a3f19c7d2e84``, the chain head at authoring time.
If another migration lands on main first, re-point ``down_revision`` at the
new head — ``.github/scripts/migration_chain_guard.py`` catches a fork.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5cb798feef58"
down_revision: str | Sequence[str] | None = "a3f19c7d2e84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Add the column, then backfill unambiguous rows from strategy_proposals."""
    with op.batch_alter_table("strategy_store", schema=None) as batch_op:
        # Text, not a typed JSON column — matches strategy_spec/source_papers
        # on this same table, hand-encoded Text for SQLite portability.
        # Nullable: curated/example rows and any row this backfill cannot
        # resolve have no brief to carry.
        batch_op.add_column(sa.Column("brief_intent", sa.Text(), nullable=True))

    bind = op.get_bind()

    # ── Build (strategy_name, thesis) -> {distinct intents} from every
    # proposal's payload. Bounded, one-time read: strategy_proposals is an
    # episodic log, not a live-traffic table.
    proposal_rows = bind.execute(sa.text("SELECT payload FROM strategy_proposals")).mappings().all()

    intents_by_key: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in proposal_rows:
        raw = row["payload"]
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        intent = payload.get("intent")
        spec = payload.get("strategy_spec")
        if not intent or not isinstance(spec, dict):
            continue
        name = spec.get("strategy_name")
        thesis = spec.get("thesis")
        if not name or not thesis:
            continue
        intents_by_key[(name, thesis)].add(intent)

    # ── Resolve each strategy_store row against that map. Only a key whose
    # matching proposals agree on ONE intent is trustworthy; a name/thesis
    # pair that maps to more than one distinct intent is a genuine collision
    # (most plausible cause: the deterministic fixture path can emit the same
    # strategy_name/thesis regardless of the brief) and is left NULL rather
    # than guessed.
    strategy_rows = (
        bind.execute(
            sa.text(
                "SELECT id, strategy_name, thesis FROM strategy_store "
                "WHERE brief_intent IS NULL AND generation_method != 'curated'"
            )
        )
        .mappings()
        .all()
    )

    to_update: list[dict[str, str]] = []
    for row in strategy_rows:
        name, thesis = row["strategy_name"], row["thesis"]
        if not name or not thesis:
            continue
        intents = intents_by_key.get((name, thesis))
        if intents and len(intents) == 1:
            to_update.append({"id": row["id"], "brief_intent": next(iter(intents))})

    if to_update:
        bind.execute(
            sa.text("UPDATE strategy_store SET brief_intent = :brief_intent WHERE id = :id"),
            to_update,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("strategy_store", schema=None) as batch_op:
        batch_op.drop_column("brief_intent")

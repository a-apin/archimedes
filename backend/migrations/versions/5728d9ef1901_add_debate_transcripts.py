"""add debate_transcripts — persist the bull/bear debate transcript (v8 Lane 2.3)

``_run_debate_leaderboard`` has always run a real 4-turn LLM debate
(``_debate_round``) — one bull + one bear verdict per round, with a visible
round-2 rebuttal — before C-null culls the pool down to a winner. The caller
discarded the return value outright: ``debate_engine.py`` used to read
``await _debate_round(pool, model, emit, candidate_id)`` with no assignment,
so a real, paid LLM transcript vanished the instant the coroutine returned.
This table is where it survives.

Every leaderboard entry from ONE debate run shares the SAME transcript (the
debate runs once, over the whole pool, before C-null picks a winner), so a
row is keyed to ``(generation_id, candidate_id)``, not to a strategy:

  - The K=1 winner's row also carries a ``strategy_id`` (its
    ``strategy_store`` row).
  - Final-round losers (Considered Alternatives) are never threaded into
    ``strategy_store`` — K=1 persistence, see ``_persist_candidate`` — so
    their row carries ``strategy_id=NULL``. NULL means "considered
    alternative, not the winner", never "write failed".

``strategy_id`` carries no foreign key, mirroring ``generation_costs`` /
``paper_deployments`` — the sibling tables that also reference
``strategy_store.id`` without a constraint: a purge of the strategy row must
not delete the record that a debate produced it.

Sanitization (internal-jargon stripping) happens at WRITE time in
``archimedes.models.debate_transcript.record_debate_transcript``, not as a
migration concern — this file only creates the shape the sanitized JSON lands
in.

BACKFILL (migrations-ship-with-their-data rule): **none, and none is
possible.** Every debate that ran before this revision had its transcript
discarded at the call site the moment it returned — there is no recoverable
copy anywhere to backfill from. Strategies generated before this lands render
as "no debate transcript" (404 on the read route), which is the honest state.

Revision ID: 5728d9ef1901
Revises: a3f19c7d2e84
Create Date: 2026-08-30 00:00:00.000000

SEQUENCING: chained onto ``a3f19c7d2e84`` (add_generation_credits), the chain
head at authoring time. If another migration lands on main first, re-point
``down_revision`` at the new head — ``.github/scripts/migration_chain_guard.py``
catches a fork.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5728d9ef1901"
down_revision: str | Sequence[str] | None = "a3f19c7d2e84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "debate_transcripts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=True),
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("candidate_id", sa.String(length=64), nullable=False),
        sa.Column("transcript_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_debate_transcripts_strategy", "debate_transcripts", ["strategy_id"])
    op.create_index(
        "ix_debate_transcripts_generation_candidate",
        "debate_transcripts",
        ["generation_id", "candidate_id"],
    )


def downgrade() -> None:
    """Downgrade schema.

    Dropping the table discards every debate transcript recorded while this
    revision was live. There is nowhere else they were kept — the whole point
    of this revision is that the pipeline discarded them before this table
    existed — so a downgrade is a deliberate loss, not a reversible move.
    """
    op.drop_index("ix_debate_transcripts_generation_candidate", table_name="debate_transcripts")
    op.drop_index("ix_debate_transcripts_strategy", table_name="debate_transcripts")
    op.drop_table("debate_transcripts")

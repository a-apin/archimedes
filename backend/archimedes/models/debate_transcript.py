"""Persisted debate transcripts — the bull/bear adversarial round the society
runs before C-null selects a winner (v8 Lane 2.3).

``_run_debate_leaderboard`` has always run a real 4-turn LLM debate
(``_debate_round``) — paid backend output, one bull + one bear verdict per
round, with a visible round-2 rebuttal — but the caller discarded the return
value outright: ``debate_engine.py`` used to read
``await _debate_round(pool, model, emit, candidate_id)`` with no assignment,
so the transcript vanished the instant the coroutine returned. This module is
where it survives.

Every leaderboard entry produced by ONE debate run shares the SAME
transcript: the debate happens once, over the whole proposed pool, BEFORE
C-null culls it down to a winner (see ``_run_debate_leaderboard``'s step
ordering, where step 2 — the debate — runs ahead of steps 3-5). A row here is
therefore keyed to ``(generation_id, candidate_id)``, not to a strategy:

* The K=1 winner's row also carries a ``strategy_id`` (the ``strategy_store``
  row ``_persist_candidate`` created for it).
* Final-round losers — the Considered Alternatives — are never threaded into
  ``strategy_store`` (K=1 persistence; see ``_persist_candidate``'s own
  docstring), so their row carries ``strategy_id=None``. NULL means "this
  candidate was a considered alternative, not the winner", never "unknown" or
  "write failed".

Sanitization happens at WRITE time, not read time (see ``sanitize_transcript``
below): every string field of every turn is stripped of internal-jargon
patterns before the JSON is serialized, so the stored column is never the
place a leak has to be caught — a raw read of ``transcript_json`` is already
safe.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from archimedes.models.chat import Base

logger = logging.getLogger(__name__)

# Defensive, not exhaustive: internal roadmap vocabulary an LLM debate turn
# could echo back from system-prompt bleed-through or corpus-context leakage.
# Each match is replaced, never silently dropped — see sanitize_debate_text.
_JARGON_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"T\d\.\d"),  # tier/milestone codes, e.g. "T3.5", "T1.1"
    re.compile(r"cutover", re.IGNORECASE),
    re.compile(r"Phase-\w*"),  # "Phase-3", "Phase-4a", or a bare "Phase-"
)

_REDACTED = "[redacted]"


def sanitize_debate_text(text: str) -> str:
    """Strip internal-jargon patterns from one string. Idempotent; never raises.

    Non-string / empty input passes through unchanged — this is a text
    scrubber, not a validator; callers decide what counts as absent.
    """
    if not text:
        return text
    out = text
    for pattern in _JARGON_PATTERNS:
        out = pattern.sub(_REDACTED, out)
    return out


def sanitize_transcript(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply :func:`sanitize_debate_text` to every string field of every turn.

    Only the two fields ``_debate_round`` actually emits per turn — ``verdict``
    (a string) and ``claims`` (a list of strings) — are scrubbed; everything
    else on a turn dict (``role``, ``round``) passes through untouched. A
    non-dict entry is dropped rather than raising, matching the "best-effort,
    never gates" posture of the debate round itself.
    """
    sanitized: list[dict[str, Any]] = []
    for turn in transcript:
        if not isinstance(turn, dict):
            continue
        new_turn = dict(turn)
        verdict = new_turn.get("verdict")
        if isinstance(verdict, str):
            new_turn["verdict"] = sanitize_debate_text(verdict)
        claims = new_turn.get("claims")
        if isinstance(claims, list):
            new_turn["claims"] = [sanitize_debate_text(c) if isinstance(c, str) else c for c in claims]
        sanitized.append(new_turn)
    return sanitized


class DebateTranscriptRecord(Base):
    """One debate run's bull/bear transcript, keyed to (generation, candidate).

    ``strategy_id`` carries no foreign key — mirrors ``generation_costs`` /
    ``paper_deployments``, the sibling tables that also reference
    ``strategy_store.id`` without a constraint — and is nullable for the
    reason in the module docstring: only the K=1 winner is ever threaded into
    ``strategy_store``.
    """

    __tablename__ = "debate_transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    transcript_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_debate_transcripts_strategy", "strategy_id"),
        Index("ix_debate_transcripts_generation_candidate", "generation_id", "candidate_id"),
    )

    # ── Readout ──────────────────────────────────────────────────────────

    def to_payload(self) -> dict[str, Any] | None:
        """The API shape, or ``None`` when the stored JSON cannot be decoded.

        A corrupt ``transcript_json`` yields ``None`` rather than an empty
        transcript — the row exists to carry a transcript, so one we cannot
        decode is an absence (``CLAUDE.md`` § fail-soft), not a fabricated
        empty list.
        """
        try:
            transcript = json.loads(self.transcript_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning("debate_transcripts: corrupt transcript_json for id=%s — treating as absent", self.id)
            return None
        if not isinstance(transcript, list):
            return None
        return {
            "strategy_id": self.strategy_id,
            "generation_id": self.generation_id,
            "candidate_id": self.candidate_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "transcript": transcript,
        }


def record_debate_transcript(
    session: Session,
    *,
    strategy_id: str | None,
    generation_id: str,
    candidate_id: str,
    transcript: list[dict[str, Any]],
) -> DebateTranscriptRecord:
    """Insert one sanitized transcript row.

    Not an upsert: a job never re-runs the same ``(generation_id,
    candidate_id)`` pair, so unlike ``generation_costs`` (whose job can
    legitimately retry) there is nothing to dedup against.

    Raises ``ValueError`` on a missing ``generation_id``/``candidate_id`` or a
    non-list ``transcript`` — writing a row nothing could ever look back up
    would be strictly worse than not writing it.
    """
    if not generation_id:
        raise ValueError("record_debate_transcript requires a generation_id")
    if not candidate_id:
        raise ValueError("record_debate_transcript requires a candidate_id")
    if not isinstance(transcript, list):
        raise ValueError(f"record_debate_transcript requires a list transcript, got {type(transcript).__name__}")

    sanitized = sanitize_transcript(transcript)
    transcript_json = json.dumps(sanitized, sort_keys=True, ensure_ascii=False, default=str)

    record = DebateTranscriptRecord(
        strategy_id=strategy_id,
        generation_id=generation_id,
        candidate_id=candidate_id,
        transcript_json=transcript_json,
        created_at=datetime.now(UTC),
    )
    session.add(record)
    session.flush()
    return record


def debate_transcript_for_strategy(session: Session, strategy_id: str) -> dict[str, Any] | None:
    """The most recently recorded transcript for a strategy's row, or ``None``.

    Only the K=1 winner ever carries a non-NULL ``strategy_id`` on this table
    (see the module docstring), so this is the only lookup the read API
    needs — a considered-alternative has no ``strategy_id`` to look up by in
    the first place, and ``None`` here is the honest answer for every
    strategy generated before this table existed, and for every curated one.
    """
    if not strategy_id:
        return None
    record = (
        session.query(DebateTranscriptRecord)
        .filter_by(strategy_id=strategy_id)
        .order_by(DebateTranscriptRecord.created_at.desc(), DebateTranscriptRecord.id.desc())
        .first()
    )
    return record.to_payload() if record is not None else None

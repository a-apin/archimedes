"""Session-owning wrapper around the free-generation ledger (#1643).

``models/free_generation_grant.py`` is pure persistence and takes a ``Session``.
This module owns the sessions and decides what a failure means — the same split
``services/generation_credits.py`` makes, and for the same reason: the answer
changes with what is at stake.

**Every failure here resolves to "no free slot".** That is fail-closed on
*generosity*, and it is deliberate:

- The fallback is not an outage. A caller who cannot be granted a free slot
  falls through to the wallet gate + paywall — the behaviour production served
  for the twelve days before this shipped. Degrading to the previous policy is
  a strictly smaller harm than degrading to unbounded free LLM spend.
- The opposite choice is unrecoverable. Granting on a read error means a
  database blip hands out uncapped free generations, and nothing records that
  it happened, so nothing can be reconciled afterwards.

The one place this module must NOT resolve a failure to a number is
``remaining()``, which feeds ``GET /api/account/usage``. There, "we could not
read the ledger" is reported as ``None`` and rendered as an honest blank —
never as ``0`` (which would tell a fresh account it has nothing left) and never
as the full allowance (which would promise free runs the gate may refuse).

Allowance (``FREE_GENERATIONS_PER_ACCOUNT``, default 3) is read per call, not
cached, so an operator can change it without a deploy. ``<= 0`` disables the
free path entirely and restores the pre-#1643 wallet-gate-on-first-call
behaviour exactly — the kill switch for this policy.
"""

from __future__ import annotations

import logging
import os

from archimedes.models.free_generation_grant import (
    claim_free_grant,
    release_grant,
    stamp_grant_job,
    used_count,
)

logger = logging.getLogger(__name__)

#: The owner's 2026-08-31 call: "3 generations on a mere account, then wallet-gate."
DEFAULT_ALLOWANCE = 3


def allowance() -> int:
    """Free generations granted per account, lifetime. ``<= 0`` disables the path."""
    raw = os.getenv("FREE_GENERATIONS_PER_ACCOUNT", str(DEFAULT_ALLOWANCE))
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "invalid FREE_GENERATIONS_PER_ACCOUNT=%r; using default %d",
            raw,
            DEFAULT_ALLOWANCE,
        )
        return DEFAULT_ALLOWANCE


def _session():
    from archimedes.db import get_session

    return get_session()


def claim(user_id: str) -> int | None:
    """Take one free generation for *user_id*; ``None`` when there is none to take.

    ``None`` covers all four honest answers — allowance exhausted, allowance
    disabled, a concurrent request won the slot, or the ledger could not be
    read — because the caller does the same thing in every one of them: fall
    through to the wallet gate. The log line distinguishes them for an operator.
    """
    if not user_id:
        return None
    limit = allowance()
    if limit <= 0:
        return None
    try:
        with _session() as session:
            grant = claim_free_grant(session, user_id=user_id, allowance=limit)
            if grant is None:
                return None
            grant_id = grant.id
            session.commit()
            return grant_id
    except Exception:
        # Includes the IntegrityError a concurrent claim raises on
        # uq_free_generation_grants_user_seq — that is the constraint doing its
        # job, so it is logged at info, not as a defect. Anything else here is
        # a real ledger failure and the caller still falls back to the gate.
        logger.info(
            "free-generation claim did not succeed for user %s — falling through to the wallet gate",
            user_id,
            exc_info=True,
        )
        return None


def stamp_job(grant_id: int, *, job_id: str) -> None:
    """Record which job the slot funded. Quiet: the generation is already queued."""
    try:
        with _session() as session:
            stamp_grant_job(session, grant_id, job_id=job_id)
            session.commit()
    except Exception:
        logger.error(
            "free-generation grant %s could not be stamped with job %s — the slot is still "
            "correctly counted as used, only its provenance is missing.",
            grant_id,
            job_id,
            exc_info=True,
        )


def release(grant_id: int) -> None:
    """Hand a claimed slot back because nothing was delivered against it.

    Quiet on failure, but logged at ``error``: a slot that fails to be released
    silently costs the account one of its free generations for a run it never
    got. That errs against the user, which is why it is not logged as noise.
    """
    try:
        with _session() as session:
            release_grant(session, grant_id)
            session.commit()
    except Exception:
        logger.error(
            "free-generation grant %s could not be released — the account was charged one "
            "free generation for a run that never queued.",
            grant_id,
            exc_info=True,
        )


def remaining(user_id: str) -> int | None:
    """Free generations left for *user_id*, or ``None`` if the ledger is unreadable.

    ``None`` is the honest absence ``GET /api/account/usage`` renders as a
    blank. A fabricated number in either direction is a claim the gate does not
    keep, which is exactly the defect the repo's first rule names.
    """
    limit = allowance()
    if limit <= 0:
        return 0
    try:
        with _session() as session:
            return max(0, limit - used_count(session, user_id))
    except Exception:
        logger.exception("free-generation remaining lookup failed for user %s — reporting unknown", user_id)
        return None

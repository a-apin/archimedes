"""Strategy memory — write path for the episodic proposals table.

Non-blocking write: every fusion / architect / agent output gets persisted
as a content-hashed ``StrategyProposal`` row.  If the write fails, we log
and continue — the generation pipeline is never blocked by a memory write.

The keccak256 content hash matches the on-chain convention so future
Layer-C builds can cross-reference off-chain proposals with on-chain anchors.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def _compute_keccak256(canonical: str) -> str:
    """Keccak256 of a string, 0x-prefixed."""
    from web3 import Web3

    return Web3.keccak(text=canonical).hex()


def _canonicalize_payload(
    intent: str,
    strategy_spec: dict | None,
    papers: list[str],
    rigor_verdict: dict | None,
    agent: str,
    extra: dict | None = None,
) -> str:
    """Deterministic canonical JSON for content hashing."""
    return json.dumps(
        {
            "intent": intent,
            "strategy_spec": strategy_spec or {},
            "papers": sorted(papers),
            "rigor_verdict": rigor_verdict or {},
            "agent": agent,
            "extra": extra or {},
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def persist_proposal(
    *,
    generation_id: str,
    agent: str,
    intent: str,
    strategy_spec: dict | None = None,
    papers: list[str] | None = None,
    rigor_verdict: dict | None = None,
    verdict: str = "pending",
    regime_tag: str | None = None,
    parent_proposal_id: str | None = None,
    extra: dict | None = None,
    owner_wallet: str | None = None,
    owner_user_id: str | None = None,
) -> str | None:
    """Persist a proposal row. Returns the proposal ID or None on failure.

    ``owner_user_id`` is the canonical Better Auth owner. ``owner_wallet`` is
    optional verified-wallet provenance retained for compatibility. Neither
    value is client supplied or allowed to overwrite an existing owner.

    Non-blocking: catches all DB errors, logs them, returns None.
    """
    try:
        from archimedes.db import get_session
        from archimedes.models.strategy_proposal import StrategyProposal

        papers = papers or []
        proposal_id = uuid.uuid4().hex[:16]
        owner_wallet = owner_wallet.lower() if owner_wallet else None

        canonical = _canonicalize_payload(intent, strategy_spec, papers, rigor_verdict, agent, extra)
        content_hash = _compute_keccak256(canonical)

        # Derive verdict from rigor_verdict if not explicitly set
        if verdict == "pending" and rigor_verdict is not None:
            verdict = "rigor_pass" if rigor_verdict.get("passing") else "rigor_fail"

        # Trust level follows verdict
        trust_level = "VALIDATED" if verdict == "rigor_pass" else "CANDIDATE"

        payload = {
            "intent": intent,
            "strategy_spec": strategy_spec,
            "papers": papers,
            "rigor_verdict": rigor_verdict,
            "agent": agent,
            **(extra or {}),
        }

        with get_session() as session:
            # Dedup by content_hash
            existing = (
                session.query(StrategyProposal)
                .filter_by(
                    content_hash=content_hash,
                )
                .first()
            )
            if existing:
                changed = False
                # Update verdict if changed
                if verdict != existing.verdict:
                    existing.verdict = verdict
                    existing.trust_level = trust_level
                    changed = True
                # Backfill ownership on a dedup hit (mirrors
                # strategy_store.upsert_strategy): never overwrite an existing
                # owner, only attach one to a previously-anonymous row.
                if owner_user_id and not existing.owner_user_id:
                    existing.owner_user_id = owner_user_id
                    changed = True
                if owner_wallet and not existing.owner_wallet:
                    existing.owner_wallet = owner_wallet
                    changed = True
                if changed:
                    existing.updated_at = datetime.now(UTC)
                    session.commit()
                return existing.proposal_id

            row = StrategyProposal(
                id=content_hash[:16],
                generation_id=generation_id,
                proposal_id=proposal_id,
                parent_proposal_id=parent_proposal_id,
                verdict=verdict,
                trust_level=trust_level,
                content_hash=content_hash,
                agent=agent,
                regime_tag=regime_tag,
                owner_wallet=owner_wallet,
                owner_user_id=owner_user_id,
                payload=json.dumps(payload, ensure_ascii=False),
            )
            session.add(row)
            session.commit()

        logger.info(
            "memory: persisted proposal %s (%s, verdict=%s)",
            proposal_id,
            agent,
            verdict,
        )
        return proposal_id

    except Exception as exc:
        logger.warning("memory: persist_proposal failed (non-fatal): %s", exc)
        return None


def query_proposals(
    *,
    verdict: str | None = None,
    agent: str | None = None,
    since: str | None = None,
    limit: int = 50,
    offset: int = 0,
    owner_wallet: str | None = None,
    owner_user_id: str | None = None,
) -> tuple[list[dict], int]:
    """Query proposals with filtering and pagination.

    ``owner_wallet``, when given, scopes the query to rows whose stored
    ``owner_wallet`` exactly (case-insensitively) matches — pushed into the
    SQL ``WHERE`` clause, not filtered in Python after a fetch-all. A NULL
    ``owner_wallet`` column (legacy, pre-ownership rows) never satisfies this
    equality filter, so those rows are returned to no caller. Callers that
    omit both ownership keys get unscoped query — internal/trusted use only;
    HTTP callers pass canonical user ID and optional linked-wallet fallback.

    Returns (proposals, total_count).
    """
    try:
        from sqlalchemy import and_, or_

        from archimedes.db import get_session
        from archimedes.models.strategy_proposal import StrategyProposal

        with get_session() as session:
            q = session.query(StrategyProposal)

            if owner_user_id is not None:
                owner_filters = [StrategyProposal.owner_user_id == owner_user_id]
                if owner_wallet is not None:
                    owner_filters.append(
                        and_(
                            StrategyProposal.owner_user_id.is_(None),
                            StrategyProposal.owner_wallet == owner_wallet.lower(),
                        )
                    )
                q = q.filter(or_(*owner_filters))
            elif owner_wallet is not None:
                q = q.filter(StrategyProposal.owner_wallet == owner_wallet.lower())
            if verdict:
                q = q.filter(StrategyProposal.verdict == verdict)
            if agent:
                q = q.filter(StrategyProposal.agent == agent)
            if since:
                try:
                    since_dt = datetime.fromisoformat(since)
                    q = q.filter(StrategyProposal.created_at >= since_dt)
                except (ValueError, TypeError):
                    logger.debug("invalid 'since' filter %r — ignoring date bound", since, exc_info=True)

            total = q.count()
            rows = q.order_by(StrategyProposal.created_at.desc()).offset(offset).limit(limit).all()
            return [r.to_dict() for r in rows], total

    except Exception as exc:
        logger.warning("memory: query_proposals failed: %s", exc)
        return [], 0


def get_siblings(
    generation_id: str, *, owner_wallet: str | None = None, owner_user_id: str | None = None
) -> list[dict]:
    """Get all proposals from the same generation (for 'considered alternatives').

    ``owner_wallet``, when given, scopes to rows owned by that wallet
    (case-insensitive, pushed into the SQL filter — see ``query_proposals``
    docstring for the same NULL-never-matches guarantee). Omitting it returns
    unscoped sibling set — internal/trusted use only; HTTP callers pass canonical
    user ID and optional linked-wallet fallback.
    """
    try:
        from sqlalchemy import and_, or_

        from archimedes.db import get_session
        from archimedes.models.strategy_proposal import StrategyProposal

        with get_session() as session:
            q = session.query(StrategyProposal).filter(StrategyProposal.generation_id == generation_id)
            if owner_user_id is not None:
                owner_filters = [StrategyProposal.owner_user_id == owner_user_id]
                if owner_wallet is not None:
                    owner_filters.append(
                        and_(
                            StrategyProposal.owner_user_id.is_(None),
                            StrategyProposal.owner_wallet == owner_wallet.lower(),
                        )
                    )
                q = q.filter(or_(*owner_filters))
            elif owner_wallet is not None:
                q = q.filter(StrategyProposal.owner_wallet == owner_wallet.lower())
            rows = q.order_by(StrategyProposal.created_at.asc()).all()
            return [r.to_dict() for r in rows]

    except Exception as exc:
        logger.warning("memory: get_siblings failed: %s", exc)
        return []

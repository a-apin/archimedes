"""Passport loader — unified write path for strategy passports.

Every strategy (curated file, fusion output, architect output) is
ingested here and written to the ``strategy_passports`` Postgres table.
Content-hash dedup prevents duplicates.

Usage:
    from archimedes.services.passport_loader import ingest_passport
    record = ingest_passport(session, strategy_passport)
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from archimedes.models.paper_ref import PaperRef
from archimedes.models.strategy import StrategyPassport
from archimedes.models.strategy_passport_record import (
    PassportPaperRef,
    StrategyPassportRecord,
)

logger = logging.getLogger(__name__)


def _compute_content_hash(passport: StrategyPassport) -> str:
    """Deterministic SHA-256 content hash for dedup.

    Based on methodology + asset universe + paper IDs — the semantic
    identity of a strategy. Two passports with the same methodology
    applied to the same assets from the same papers are the same strategy.
    """
    canonical = json.dumps(
        {
            "methodology_summary": (passport.methodology_summary or "").strip(),
            "asset_universe": sorted(passport.asset_universe),
            "paper_ids": sorted(p.arxiv_id or p.doi or p.title for p in passport.papers),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_paper_refs(passport_id: str, papers: list[PaperRef]) -> list[PassportPaperRef]:
    """Build ORM paper ref objects from dataclass PaperRefs."""
    refs = []
    for p in papers:
        refs.append(
            PassportPaperRef(
                passport_id=passport_id,
                arxiv_id=p.arxiv_id,
                title=p.title or "",
                authors=json.dumps(p.authors) if p.authors else "[]",
                doi=p.doi,
                venue=p.venue,
                year=p.year,
                citation_count=p.citation_count,
                contribution=p.contribution,
                role=getattr(p, "role", None) or "cited",
                selection_rank=getattr(p, "selection_rank", None),
                semantic_score=getattr(p, "semantic_score", None),
                content_hash=getattr(p, "content_hash", None),
            )
        )
    return refs


#: Columns merged additively on re-ingest. ``arxiv_id`` and ``role`` are the
#: identity of the association and are deliberately absent: identity is matched
#: on, never overwritten.
_MERGEABLE_REF_COLUMNS = (
    "title",
    "authors",
    "doi",
    "venue",
    "year",
    "citation_count",
    "contribution",
    "selection_rank",
    "semantic_score",
    "content_hash",
)


def _ref_key(arxiv_id: str | None, doi: str | None, title: str | None) -> str:
    """Stable identity for one paper reference within a passport.

    Delegates to :func:`archimedes.models.paper_assoc.assoc_handle` so the
    store's idea of "the same paper" and the passport's are one definition, not
    two that drift. ``arxiv_id`` is the id space (#1637); ``doi`` and the
    case-folded ``title`` are the fallbacks that keep the 34 curated references
    — every one of which declares ``PAPER_ARXIV_ID = None`` — mergeable instead
    of duplicated on every re-ingest.
    """
    from archimedes.models.paper_assoc import assoc_handle

    return assoc_handle({"arxiv_id": arxiv_id, "doi": doi, "title": title}) or ""


def _is_empty(value: object) -> bool:
    """Is this incoming value "nothing to say"? ``None``, ``""`` or ``"[]"``.

    A JSON ``"[]"`` authors column is the shape an id+title-only rebuild emits;
    treating it as a value is how a backfilled author list got wiped.
    """
    return value is None or value == "" or value == "[]"


def _merge_paper_refs(session: Session, passport_id: str, papers: list[PaperRef]) -> None:
    """Merge incoming refs onto the stored ones — never a blind DELETE (#1637).

    ``ingest_passport(force_update=True)`` used to ``DELETE FROM
    passport_paper_refs`` and rebuild from whatever the caller had in hand.
    The caller on the real-returns refresh path
    (``generation_pipeline._persist_real_returns``) has **id + title only**, so
    every backfilled author list, year, venue, DOI and contribution was
    guaranteed to be wiped on the next metrics refresh. Enrichment could not
    survive by construction.

    Three rules, in order:

    1. **Match, don't replace.** An incoming ref merges onto the stored row
       with the same :func:`_ref_key`.
    2. **Never overwrite a value with an absence.** A populated column is left
       alone when the incoming ref has nothing for it — that is the rule that
       makes the id+title-only refresh non-destructive.
    3. **Never blind-delete.** A stored ref the caller did not mention is
       *dropped only when the caller demonstrably knows the full cited set* —
       i.e. it passed a non-empty list. An empty incoming list means "I don't
       know the papers", not "this strategy has none", and must leave the
       stored set intact.
    """
    stored = session.query(PassportPaperRef).filter_by(passport_id=passport_id).all()
    by_key = {_ref_key(r.arxiv_id, r.doi, r.title): r for r in stored}
    seen: set[str] = set()

    for incoming in _build_paper_refs(passport_id, papers):
        key = _ref_key(incoming.arxiv_id, incoming.doi, incoming.title)
        seen.add(key)
        current = by_key.get(key)
        if current is None:
            session.add(incoming)
            by_key[key] = incoming
            continue
        for column in _MERGEABLE_REF_COLUMNS:
            value = getattr(incoming, column)
            if _is_empty(value):
                continue  # rule 2
            setattr(current, column, value)
        # ``role`` is identity-adjacent: a paper may be promoted from
        # "considered" to "cited" by a later run, but never silently demoted
        # back by a caller that simply defaulted the field.
        if incoming.role == "cited":
            current.role = "cited"

    if papers:  # rule 3 — an empty list is ignorance, not a deletion instruction
        for key, row in by_key.items():
            if key not in seen and row in stored:
                session.delete(row)


def ingest_passport(
    session: Session,
    passport: StrategyPassport,
    *,
    generation_method: str = "curated",
    force_update: bool = False,
    owner_wallet: str | None = None,
    owner_user_id: str | None = None,
) -> StrategyPassportRecord:
    """Ingest a StrategyPassport dataclass into the unified Postgres table.

    Idempotent: if a record with the same content hash exists, returns it
    (optionally updating fields if ``force_update=True``).

    Args:
        session: SQLAlchemy session (caller manages commit/rollback).
        passport: The StrategyPassport dataclass to persist.
        generation_method: "curated", "fusion", or "architect".
        force_update: If True, overwrite existing record fields on hash match.
        owner_wallet: Optional verified-wallet provenance, lowercase.
        owner_user_id: Canonical Better Auth user. Both owner fields only
            backfill missing values and are never reassigned.

    Returns:
        The persisted StrategyPassportRecord.
    """
    owner_wallet = owner_wallet.lower() if owner_wallet else None
    content_hash = _compute_content_hash(passport)

    existing = session.query(StrategyPassportRecord).filter_by(id=passport.id).first()

    if existing and not force_update:
        logger.debug("passport_loader: %s already exists — skipping", passport.id)
        return existing

    if existing and force_update:
        # Update in place
        _update_record(existing, passport, generation_method, content_hash)
        if owner_user_id and not existing.owner_user_id:
            existing.owner_user_id = owner_user_id
        if owner_wallet and not existing.owner_wallet:
            # Backfill only — an existing owner is never overwritten.
            existing.owner_wallet = owner_wallet
        # Merge paper refs additively — see _merge_paper_refs. This was a
        # DELETE-then-rebuild, which made enrichment impossible to keep (#1637).
        _merge_paper_refs(session, passport.id, passport.papers)
        existing.updated_at = datetime.now(UTC)
        session.flush()
        # The merge writes through the session, not through the relationship, so
        # the already-loaded (lazy="joined") collection on `existing` would hand
        # the caller a pre-merge view. Expire it so the next read reloads.
        session.expire(existing, ["paper_refs"])
        logger.info("passport_loader: updated %s (%s)", passport.id, generation_method)
        return existing

    # New record
    record = StrategyPassportRecord(
        id=passport.id,
        methodology_hash=passport.methodology_hash or passport.compute_methodology_hash(),
        content_hash=content_hash,
        generation_method=generation_method,
        methodology_summary=passport.methodology_summary or "",
        methodology_text=passport.methodology_text,
        asset_universe=json.dumps(passport.asset_universe),
        universe_source=passport.universe_source,
        position_sizing=passport.position_sizing.value
        if hasattr(passport.position_sizing, "value")
        else str(passport.position_sizing),
        rebalance_frequency=passport.rebalance_frequency.value
        if hasattr(passport.rebalance_frequency, "value")
        else str(passport.rebalance_frequency),
        risk_constraints=json.dumps(passport.risk_constraints) if passport.risk_constraints else "{}",
        risk_profiles=json.dumps(passport.risk_profiles) if passport.risk_profiles else "[]",
        status=passport.status.value if hasattr(passport.status, "value") else str(passport.status),
        regime_tag=passport.regime_tag or "regime_neutral",
        extraction_llm=passport.extraction_llm,
        extraction_prompt_hash=passport.extraction_prompt_hash,
        # Lowercased (issue #1028): curator_wallet now FKs to wallet_identities,
        # whose own primary key is enforced lowercase — a curated YAML's
        # CURATOR_WALLET metadata is human-typed and not guaranteed to match.
        curator_wallet=passport.curator_wallet.lower() if passport.curator_wallet else None,
        curator_note=passport.curator_note,
        owner_wallet=owner_wallet,
        owner_user_id=owner_user_id,
        strategy_code_path=passport.strategy_code_path,
        strategy_code_hash=passport.strategy_code_hash,
        on_chain_registration_tx=passport.on_chain_registration_tx,
        paper_claimed_sharpe=passport.paper_claimed_sharpe,
        paper_claimed_cagr=passport.paper_claimed_cagr,
        paper_claimed_max_dd=passport.paper_claimed_max_dd,
        paper_claim_blended_sharpe=passport.paper_claim_blended_sharpe,
        # Backtest results
        sharpe_ratio=passport.real_sharpe,
        sortino_ratio=passport.real_sortino,
        max_drawdown=passport.real_max_dd,
        cagr=passport.real_cagr,
        win_rate=passport.real_win_rate,
        total_trades=passport.real_total_trades,
        calmar_ratio=passport.real_calmar,
        correlation_to_spy=passport.real_corr_spy,
        backtest_start=passport.real_backtest_start,
        backtest_end=passport.real_backtest_end,
        # Rigor gate
        deflated_sharpe_ratio=passport.deflated_sharpe_ratio,
        dsr_p_value=passport.dsr_p_value,
        pbo_score=passport.pbo_score,
        out_of_sample_sharpe=passport.out_of_sample_sharpe,
        passes_rigor_gate=passport.passes_rigor_gate,
        kelly_fraction=passport.kelly_fraction,
        sharpe_ci_lower=passport.sharpe_ci_lower,
        sharpe_ci_upper=passport.sharpe_ci_upper,
        n_obs_daily=passport.n_obs_daily,
        # Timestamps
        created_at=passport.created_at or datetime.now(UTC),
        updated_at=passport.updated_at or datetime.now(UTC),
    )
    record.paper_refs = _build_paper_refs(passport.id, passport.papers)
    session.add(record)
    session.flush()
    logger.info(
        "passport_loader: ingested %s (%s, %d papers, regime=%s)",
        passport.id,
        generation_method,
        len(passport.papers),
        passport.regime_tag,
    )
    return record


def _update_record(
    record: StrategyPassportRecord,
    passport: StrategyPassport,
    generation_method: str,
    content_hash: str,
) -> None:
    """Update an existing record's fields from a passport."""
    record.content_hash = content_hash
    record.generation_method = generation_method
    record.methodology_summary = passport.methodology_summary or ""
    record.methodology_text = passport.methodology_text
    record.methodology_hash = passport.methodology_hash or passport.compute_methodology_hash()
    record.asset_universe = json.dumps(passport.asset_universe)
    # universe_source was following the same missing-propagation shape dsr_p_value
    # had (#passport-honesty): only write it when the incoming passport actually
    # carries a value, so a force_update refresh that doesn't know the universe
    # source (e.g. the post-backtest metrics refresh) never clobbers it with None.
    if passport.universe_source is not None:
        record.universe_source = passport.universe_source
    record.status = passport.status.value if hasattr(passport.status, "value") else str(passport.status)
    record.regime_tag = passport.regime_tag or "regime_neutral"
    record.passes_rigor_gate = passport.passes_rigor_gate
    record.sharpe_ratio = passport.real_sharpe
    record.sortino_ratio = passport.real_sortino
    record.max_drawdown = passport.real_max_dd
    record.cagr = passport.real_cagr
    record.deflated_sharpe_ratio = passport.deflated_sharpe_ratio
    # dsr_p_value was not updated by _update_record (#passport-honesty): the
    # _refresh_passport_real_metrics call from _persist_real_returns sets it on
    # the StrategyPassport but _update_record never wrote it to the DB row,
    # so strategy_passports.dsr_p_value stayed NULL even after the backtest ran.
    record.dsr_p_value = passport.dsr_p_value
    record.pbo_score = passport.pbo_score
    record.out_of_sample_sharpe = passport.out_of_sample_sharpe
    record.kelly_fraction = passport.kelly_fraction


def ingest_all_curated(session: Session, strategies: list[StrategyPassport]) -> int:
    """Bulk-ingest curated strategies. Returns count ingested."""
    count = 0
    for s in strategies:
        ingest_passport(session, s, generation_method="curated", force_update=True)
        count += 1
    session.commit()
    logger.info("passport_loader: bulk-ingested %d curated strategies", count)
    return count


def get_passport(session: Session, strategy_id: str) -> StrategyPassportRecord | None:
    """Read a single passport by ID."""
    return session.query(StrategyPassportRecord).filter_by(id=strategy_id).first()


def list_passports(
    session: Session,
    *,
    status: str | None = None,
    regime_tag: str | None = None,
    generation_method: str | None = None,
) -> list[StrategyPassportRecord]:
    """List passports with optional filters."""
    q = session.query(StrategyPassportRecord)
    if status:
        q = q.filter(StrategyPassportRecord.status == status)
    if regime_tag:
        q = q.filter(StrategyPassportRecord.regime_tag == regime_tag)
    if generation_method:
        q = q.filter(StrategyPassportRecord.generation_method == generation_method)
    return q.all()

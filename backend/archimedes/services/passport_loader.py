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
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from archimedes.models.paper_ref import PaperRef
from archimedes.models.strategy import StrategyPassport
from archimedes.models.strategy_passport_record import (
    PassportPaperRef,
    StrategyPassportRecord,
)
from archimedes.services.rigor_gate_version import gate_version as _gate_version

logger = logging.getLogger(__name__)

# The four-state badge as it is STORED. Same vocabulary as
# ``services.live_rigor_gate`` (PASS/FAIL/PENDING/DEGENERATE) and as
# ``ui/src/rigorGateStatus.js`` — one word list, three surfaces.
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_PENDING = "pending"
STATUS_DEGENERATE = "degenerate"
RIGOR_GATE_STATES = (STATUS_PASS, STATUS_FAIL, STATUS_PENDING, STATUS_DEGENERATE)


@dataclass(frozen=True)
class RigorVerdictWrite:
    """The verdict of record, as a single indivisible write.

    ``docs/adr/rigor-verdict-of-record.md``: a strategy is graded ONCE, at
    backtest time, by the real gate, and that verdict is persisted on the
    passport with its provenance. This dataclass is the ONLY thing
    :func:`ingest_passport` will accept as a verdict, which is what makes the
    single-writer rule structural rather than a convention:

    * ``passes`` is a **derived property**, not a field. It is impossible to
      construct a ``RigorVerdictWrite`` whose boolean disagrees with its
      four-state ``status``, so the two columns cannot drift apart. (Before
      this, ``passes_rigor_gate=True`` beside a ``pending`` read-time status was
      a reachable pair — the generation-time fusion verdict wrote the boolean
      while nothing wrote a status at all.)
    * ``gate_version`` defaults to the CURRENT gate's digest
      (``rigor_gate_version.gate_version()``), so a stored verdict always names
      the gate that produced it. A caller cannot forget it.
    * ``graded_at`` defaults to now, for the same reason.

    ``cohort_n`` is the number of return series in the cohort that supplied the
    grade's cohort-scoped inputs (PBO, average pairwise correlation). The
    generation path grades a strategy against itself alone, so it passes 1.
    ``None`` means the cohort size was not recorded — never a guessed 1.
    """

    status: str
    graded_at: datetime | None = None
    gate_version: str | None = None
    cohort_n: int | None = None

    def __post_init__(self) -> None:
        if self.status not in RIGOR_GATE_STATES:
            raise ValueError(f"rigor_gate_status must be one of {RIGOR_GATE_STATES}, got {self.status!r}")
        # Fill the provenance a caller left blank. Done here (rather than with a
        # default_factory) so ``from_verdict`` and a hand-built instance behave
        # identically, and so a stored verdict can never carry a NULL gate.
        if self.graded_at is None:
            object.__setattr__(self, "graded_at", datetime.now(UTC))
        if self.gate_version is None:
            object.__setattr__(self, "gate_version", _gate_version())

    @property
    def passes(self) -> bool:
        """The fail-closed boolean. True for ``pass`` and nothing else."""
        return self.status == STATUS_PASS

    @classmethod
    def from_verdict(cls, verdict, *, cohort_n: int | None = None, graded_at: datetime | None = None):
        """Build from a live :class:`~archimedes.services.live_rigor_gate.RigorGateVerdict`.

        This is the intended construction path: the argument is the object
        ``verdict_from_returns`` returns after actually running ``run_rigor_gate``
        over a persisted return series. Anything else — a generation-time fusion
        dict, a fixture row, a hand-typed boolean — has to name a four-state
        string deliberately, in code, in a diff a reviewer reads.
        """
        return cls(status=verdict.status, cohort_n=cohort_n, graded_at=graded_at)


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
            )
        )
    return refs


def ingest_passport(
    session: Session,
    passport: StrategyPassport,
    *,
    generation_method: str = "curated",
    force_update: bool = False,
    owner_wallet: str | None = None,
    owner_user_id: str | None = None,
    rigor_verdict: RigorVerdictWrite | None = None,
) -> StrategyPassportRecord:
    """Ingest a StrategyPassport dataclass into the unified Postgres table.

    Idempotent: if a record with the same content hash exists, returns it
    (optionally updating fields if ``force_update=True``).

    **The rigor verdict does not travel on the passport dataclass.**
    ``passport.passes_rigor_gate`` is deliberately NOT read here. The five
    verdict-of-record columns (``passes_rigor_gate``, ``rigor_gate_status``,
    ``graded_at``, ``gate_version``, ``cohort_n``) are written only from an
    explicit ``rigor_verdict=RigorVerdictWrite(...)`` argument — see
    ``docs/adr/rigor-verdict-of-record.md``. Without one:

    * a NEW row is inserted **ungraded** — ``rigor_gate_status="pending"``,
      ``passes_rigor_gate=False``, no ``graded_at``, no ``gate_version``. That is
      the honest state of a strategy whose backtest has not run.
    * an EXISTING row keeps the verdict it already has. A ``force_update``
      refresh that does not carry a grade must not erase one, and must not
      silently overwrite one either: a re-grade is an explicit event that
      supplies its own ``RigorVerdictWrite``.

    This is what removes the mixed-vintage column #1746/#1747 were about. The
    generation-time fusion verdict used to reach this table through
    ``passport.passes_rigor_gate`` and sit there as the strategy's badge until
    (and only if) the post-backtest re-grade happened to run. It now stays where
    it belongs — on ``StrategyRecord.rigor_verdict``, as the debate record —
    and the passport carries only what a gate run produced.

    Args:
        session: SQLAlchemy session (caller manages commit/rollback).
        passport: The StrategyPassport dataclass to persist.
        generation_method: "curated", "fusion", or "architect".
        force_update: If True, overwrite existing record fields on hash match.
        owner_wallet: Optional verified-wallet provenance, lowercase.
        owner_user_id: Canonical Better Auth user. Both owner fields only
            backfill missing values and are never reassigned.
        rigor_verdict: The graded verdict of record, when this call IS the grade.

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
        # Update in place. _update_record touches no verdict column; a grade is
        # applied only when this call carries one (see the docstring).
        _update_record(existing, passport, generation_method, content_hash)
        if rigor_verdict is not None:
            _apply_rigor_verdict(existing, rigor_verdict)
        if owner_user_id and not existing.owner_user_id:
            existing.owner_user_id = owner_user_id
        if owner_wallet and not existing.owner_wallet:
            # Backfill only — an existing owner is never overwritten.
            existing.owner_wallet = owner_wallet
        # Replace paper refs
        session.query(PassportPaperRef).filter_by(passport_id=passport.id).delete()
        existing.paper_refs = _build_paper_refs(passport.id, passport.papers)
        existing.updated_at = datetime.now(UTC)
        session.flush()
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
        # Verdict of record: a new row starts UNGRADED. ``passport.passes_rigor_gate``
        # is not read — see the docstring and _apply_rigor_verdict below.
        passes_rigor_gate=False,
        rigor_gate_status=STATUS_PENDING,
        kelly_fraction=passport.kelly_fraction,
        sharpe_ci_lower=passport.sharpe_ci_lower,
        sharpe_ci_upper=passport.sharpe_ci_upper,
        n_obs_daily=passport.n_obs_daily,
        # Timestamps
        created_at=passport.created_at or datetime.now(UTC),
        updated_at=passport.updated_at or datetime.now(UTC),
    )
    if rigor_verdict is not None:
        _apply_rigor_verdict(record, rigor_verdict)
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


def _apply_rigor_verdict(record: StrategyPassportRecord, verdict: RigorVerdictWrite) -> None:
    """Write the five verdict-of-record columns, together, from one grade.

    The ONLY place any of these five columns is assigned. All five move as a
    unit — that is what makes ``passes_rigor_gate == (rigor_gate_status ==
    "pass")`` an invariant of the table rather than a hope, and what makes
    ``gate_version``/``graded_at`` real provenance: a verdict without them
    cannot be written, because ``RigorVerdictWrite`` fills them in its
    ``__post_init__``.

    A re-grade calls this again with a fresh ``RigorVerdictWrite``. That is the
    explicit, versioned event the ADR permits; a silent overwrite is prevented
    by nothing else calling it.
    """
    record.rigor_gate_status = verdict.status
    record.passes_rigor_gate = verdict.passes
    record.graded_at = verdict.graded_at
    record.gate_version = verdict.gate_version
    record.cohort_n = verdict.cohort_n


def _update_record(
    record: StrategyPassportRecord,
    passport: StrategyPassport,
    generation_method: str,
    content_hash: str,
) -> None:
    """Update an existing record's fields from a passport.

    Writes the descriptive/backtest columns only. The verdict of record is NOT
    among them — see :func:`_apply_rigor_verdict`.
    """
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
    # position_sizing / rebalance_frequency had NO writer on the update path
    # (#1769) — the create branch above wrote them and this one did not, so a
    # `force_update` refresh (the post-backtest metrics rebuild) left whatever
    # the row already held. That is how a spec-derived `monthly` /
    # `full_invested_when_in_market` reverted to the `weekly` / `equal_weight`
    # column defaults. Written unconditionally, exactly like `asset_universe`
    # two lines up and unlike `universe_source`: every caller builds these two
    # from its own source of truth (the candidate's validated DSL spec, or the
    # curated YAML's metadata), so there is no "refresh that doesn't know" case
    # for them to be defended against.
    record.position_sizing = (
        passport.position_sizing.value if hasattr(passport.position_sizing, "value") else str(passport.position_sizing)
    )
    record.rebalance_frequency = (
        passport.rebalance_frequency.value
        if hasattr(passport.rebalance_frequency, "value")
        else str(passport.rebalance_frequency)
    )
    record.status = passport.status.value if hasattr(passport.status, "value") else str(passport.status)
    record.regime_tag = passport.regime_tag or "regime_neutral"
    # NOTE: passes_rigor_gate is NOT written here any more. It used to be copied
    # straight off the passport dataclass, which is how the generation-time
    # fusion verdict became the badge for every strategy whose post-backtest
    # re-grade never ran (#1747) — the mixed-vintage column. The verdict of
    # record now moves only through _apply_rigor_verdict.
    record.sharpe_ratio = passport.real_sharpe
    record.sortino_ratio = passport.real_sortino
    record.max_drawdown = passport.real_max_dd
    record.cagr = passport.real_cagr
    # The rest of the real_* block. These eight were missing from the
    # force_update path entirely (they were only ever written by the INSERT
    # branch above), so on any refreshed row they stayed frozen at the value the
    # very first ingest happened to carry — usually NULL — while
    # `_refresh_passport_real_metrics` set them on the dataclass every time and
    # `_passport_to_strategy_response` served them. Same defect class as the
    # dsr_p_value note below, eight columns wider. Written unconditionally, like
    # their four siblings above, because they describe ONE backtest run: writing
    # some of a run's metrics and keeping others from a previous run would make
    # the row describe no run at all.
    record.win_rate = passport.real_win_rate
    record.calmar_ratio = passport.real_calmar
    record.correlation_to_spy = passport.real_corr_spy
    record.total_trades = passport.real_total_trades
    record.backtest_start = passport.real_backtest_start
    record.backtest_end = passport.real_backtest_end
    record.n_obs_daily = passport.n_obs_daily
    record.sharpe_ci_lower = passport.sharpe_ci_lower
    record.sharpe_ci_upper = passport.sharpe_ci_upper
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


#: The verdict a surface serves for a strategy ``strategy_passports`` has never
#: heard of, and the fail-closed answer when the read itself breaks.
#:
#: ``passes_rigor_gate`` is ``None``, never ``False``: ``False`` is a VERDICT
#: ("the gate ran and this lost") and no gate ran. That is the same distinction
#: ``rigor_gate_status == "pending"`` makes in words, and the same one
#: ``strategies_routes._UNGRADED_VERDICT_FIELDS`` makes for the Library page —
#: ``backend/tests/test_paper_deploy_verdict.py`` pins the two agree on the
#: three keys they share, so the paper surface and the library surface can never
#: start describing an ungraded row differently.
UNGRADED_RIGOR_VERDICT: dict = {
    "passes_rigor_gate": None,
    "rigor_gate_status": STATUS_PENDING,
    "graded_at": None,
    "gate_version": None,
}


def stored_rigor_verdict(session: Session, strategy_id: str) -> dict:
    """The STORED rigor verdict for one strategy, as JSON-ready fields.

    A pure read of ``strategy_passports`` — the verdict of record
    (``docs/adr/rigor-verdict-of-record.md``). It never runs the gate, never
    touches a return series, and never derives a verdict from metrics: a
    strategy is graded once, at backtest time, and every surface reads THAT
    row. A read-time recompute here would be a second gate answering the same
    question, which is exactly the split #1746/#1747 closed.

    ``passes_rigor_gate`` is derived from the stored four-state rather than
    copied from the stored boolean — the same rule
    ``strategies_routes._passport_verdicts_for`` follows, so a legacy row whose
    two columns were written apart cannot be served apart.

    ``gate_version`` rides along because a stored verdict is only comparable to
    another one graded by the same gate; the literal
    ``rigor_gate_version.LEGACY_DERIVED`` means the verdict was INFERRED by the
    verdict-of-record migration rather than produced by a gate run.

    Fails CLOSED and never raises: a missing row or a DB-level failure returns
    :data:`UNGRADED_RIGOR_VERDICT`. This feeds read paths that must keep serving
    the ledger they already have (``GET /api/paper/deployments``); degrading to
    "not graded" is honest, while 500ing would take a correct track record down
    with it. What is never produced on the failure path is a ``pass``.
    """
    try:
        row = get_passport(session, strategy_id)
    except Exception as exc:  # pragma: no cover — defensive; DB-level failure
        logger.warning(
            "passport verdict read failed for strategy %s (%s) — reported as ungraded, never as a pass",
            strategy_id,
            type(exc).__name__,
        )
        try:
            session.rollback()
        except Exception:
            logger.debug("rollback after a failed passport verdict read also failed", exc_info=True)
        return dict(UNGRADED_RIGOR_VERDICT)
    if row is None:
        return dict(UNGRADED_RIGOR_VERDICT)
    status = row.rigor_gate_status or STATUS_PENDING
    return {
        "passes_rigor_gate": status == STATUS_PASS,
        "rigor_gate_status": status,
        "graded_at": row.graded_at.isoformat() if row.graded_at else None,
        "gate_version": row.gate_version,
    }


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

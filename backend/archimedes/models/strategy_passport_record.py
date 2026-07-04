"""StrategyPassportRecord — unified Postgres ORM for all strategies.

Replaces the split between file-based StrategyPassport dataclass (curated)
and StrategyRecord ORM (fusion/architect). Every strategy — curated, fusion,
architect — lives in the same ``strategy_passports`` table with full passport
fields as typed columns.

Paper references are normalized into a ``passport_paper_refs`` FK table.
Rigor results and backtest results are stored inline as JSON columns
(denormalized for query simplicity; the source-of-truth for backtests
remains the ``backtest_results`` table via ``backtest_repository.py``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from archimedes.models.chat import Base
from archimedes.models.paper_ref import PaperRef

if TYPE_CHECKING:
    # Import only for the forward-reference type annotation on
    # to_strategy_passport(). Avoids a circular import at runtime
    # (StrategyPassport lives in archimedes.models.strategy which imports
    # back from this module in some paths).
    from archimedes.models.strategy import StrategyPassport


class StrategyPassportRecord(Base):
    """Unified strategy passport — one row per strategy, any source."""

    __tablename__ = "strategy_passports"

    id = Column(String(64), primary_key=True)
    methodology_hash = Column(String(64), nullable=True)
    content_hash = Column(String(66), nullable=True, unique=True)  # keccak256 for dedup

    # ── Source / provenance ──────────────────────────────────
    generation_method = Column(String(32), nullable=False, default="curated")  # curated|fusion|architect
    methodology_summary = Column(Text, nullable=False, default="")
    methodology_text = Column(Text, nullable=True)
    asset_universe = Column(Text, nullable=False, default="[]")  # JSON list
    # Provenance of the asset_universe pick (#857): "user" | "model" | "full".
    # NULL for rows written before this column existed (curated strategies,
    # pre-#857 fusion/architect rows) — never backfilled with a guess.
    universe_source = Column(String(16), nullable=True)
    position_sizing = Column(String(32), nullable=False, default="equal_weight")
    rebalance_frequency = Column(String(32), nullable=False, default="weekly")
    risk_constraints = Column(Text, nullable=True, default="{}")  # JSON dict
    risk_profiles = Column(Text, nullable=True, default="[]")  # JSON list

    # ── Status lifecycle ─────────────────────────────────────
    status = Column(String(16), nullable=False, default="candidate")
    regime_tag = Column(String(20), nullable=False, default="regime_neutral")

    # ── Curation trail ───────────────────────────────────────
    extraction_llm = Column(String(64), nullable=True)
    extraction_prompt_hash = Column(String(64), nullable=True)
    curator_wallet = Column(String(42), nullable=True)
    curator_note = Column(Text, nullable=True)

    # ── Ownership (mirror of strategy_store.owner_wallet) ────
    # SIWE-derived generating wallet, bound server-side; lowercase; NULL for
    # curated/legacy rows. Visibility gating reads strategy_store (source of
    # truth); this mirror keeps the passport row self-describing.
    owner_wallet = Column(String(42), nullable=True)

    # ── Code binding ─────────────────────────────────────────
    strategy_code_path = Column(String(512), nullable=True)
    strategy_code_hash = Column(String(64), nullable=True)

    # ── On-chain anchor ──────────────────────────────────────
    on_chain_registration_tx = Column(String(66), nullable=True)
    on_chain_registration_block = Column(String(32), nullable=True)

    # ── Paper claims ─────────────────────────────────────────
    paper_claimed_sharpe = Column(Float, nullable=True)
    paper_claimed_cagr = Column(Float, nullable=True)
    paper_claimed_max_dd = Column(Float, nullable=True)
    paper_claim_blended_sharpe = Column(Float, nullable=True)

    # ── Backtest results (denormalized for query speed) ──────
    sharpe_ratio = Column(Float, nullable=True)
    sortino_ratio = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    cagr = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)
    total_trades = Column(Integer, nullable=True)
    calmar_ratio = Column(Float, nullable=True)
    correlation_to_spy = Column(Float, nullable=True)
    backtest_start = Column(String(32), nullable=True)
    backtest_end = Column(String(32), nullable=True)

    # ── Rigor gate results ───────────────────────────────────
    deflated_sharpe_ratio = Column(Float, nullable=True)
    dsr_p_value = Column(Float, nullable=True)
    pbo_score = Column(Float, nullable=True)
    out_of_sample_sharpe = Column(Float, nullable=True)
    passes_rigor_gate = Column(Boolean, nullable=False, default=False)
    kelly_fraction = Column(Float, nullable=True)
    sharpe_ci_lower = Column(Float, nullable=True)
    sharpe_ci_upper = Column(Float, nullable=True)
    n_obs_daily = Column(Integer, nullable=True)

    # ── Timestamps ───────────────────────────────────────────
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    # ── Relations ────────────────────────────────────────────
    paper_refs = relationship(
        "PassportPaperRef", back_populates="passport", cascade="all, delete-orphan", lazy="joined"
    )

    __table_args__ = (
        Index("ix_passport_status", "status"),
        Index("ix_passport_regime", "regime_tag"),
        Index("ix_passport_method", "generation_method"),
    )

    def to_strategy_passport(self) -> StrategyPassport:
        """Convert ORM record to the StrategyPassport dataclass."""
        from archimedes.models.strategy import (
            PositionSizing,
            RebalanceFrequency,
            StrategyPassport,
            StrategyStatus,
        )

        papers = [ref.to_paper_ref() for ref in (self.paper_refs or [])]

        return StrategyPassport(
            id=self.id,
            papers=papers,
            methodology_summary=self.methodology_summary or "",
            methodology_text=self.methodology_text,
            asset_universe=json.loads(self.asset_universe) if self.asset_universe else [],
            universe_source=self.universe_source,
            signals=[],
            position_sizing=PositionSizing(self.position_sizing or "equal_weight"),
            rebalance_frequency=RebalanceFrequency(self.rebalance_frequency or "weekly"),
            risk_constraints=json.loads(self.risk_constraints) if self.risk_constraints else {},
            risk_profiles=json.loads(self.risk_profiles) if self.risk_profiles else [],
            status=StrategyStatus(self.status or "candidate"),
            regime_tag=self.regime_tag or "regime_neutral",
            methodology_hash=self.methodology_hash,
            extraction_llm=self.extraction_llm,
            curator_wallet=self.curator_wallet,
            curator_note=self.curator_note,
            strategy_code_path=self.strategy_code_path,
            strategy_code_hash=self.strategy_code_hash,
            on_chain_registration_tx=self.on_chain_registration_tx,
            paper_claimed_sharpe=self.paper_claimed_sharpe,
            paper_claimed_cagr=self.paper_claimed_cagr,
            paper_claimed_max_dd=self.paper_claimed_max_dd,
            paper_claim_blended_sharpe=self.paper_claim_blended_sharpe,
            real_sharpe=self.sharpe_ratio,
            real_sortino=self.sortino_ratio,
            real_cagr=self.cagr,
            real_max_dd=self.max_drawdown,
            real_win_rate=self.win_rate,
            real_calmar=self.calmar_ratio,
            real_corr_spy=self.correlation_to_spy,
            real_total_trades=self.total_trades,
            real_backtest_start=self.backtest_start,
            real_backtest_end=self.backtest_end,
            deflated_sharpe_ratio=self.deflated_sharpe_ratio,
            dsr_p_value=self.dsr_p_value,
            pbo_score=self.pbo_score,
            out_of_sample_sharpe=self.out_of_sample_sharpe,
            passes_rigor_gate=self.passes_rigor_gate or False,
            kelly_fraction=self.kelly_fraction,
            sharpe_ci_lower=self.sharpe_ci_lower,
            sharpe_ci_upper=self.sharpe_ci_upper,
            n_obs_daily=self.n_obs_daily,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses / debugging."""
        return {
            "id": self.id,
            "generation_method": self.generation_method,
            "methodology_summary": self.methodology_summary,
            "asset_universe": json.loads(self.asset_universe) if self.asset_universe else [],
            "universe_source": self.universe_source,
            "status": self.status,
            "regime_tag": self.regime_tag,
            "owner_wallet": self.owner_wallet,
            "passes_rigor_gate": self.passes_rigor_gate,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "max_drawdown": self.max_drawdown,
            "paper_refs": [r.to_dict() for r in (self.paper_refs or [])],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PassportPaperRef(Base):
    """Paper reference linked to a strategy passport (N:1)."""

    __tablename__ = "passport_paper_refs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    passport_id = Column(String(64), ForeignKey("strategy_passports.id", ondelete="CASCADE"), nullable=False)
    arxiv_id = Column(String(32), nullable=True)
    title = Column(String(512), nullable=False, default="")
    authors = Column(Text, nullable=True, default="[]")  # JSON list
    doi = Column(String(128), nullable=True)
    venue = Column(String(256), nullable=True)
    year = Column(Integer, nullable=True)
    citation_count = Column(Integer, nullable=True)
    contribution = Column(Text, nullable=True)  # Fusion: what this paper contributed

    passport = relationship("StrategyPassportRecord", back_populates="paper_refs")

    def to_paper_ref(self) -> PaperRef:
        return PaperRef(
            arxiv_id=self.arxiv_id,
            title=self.title or "",
            authors=json.loads(self.authors) if self.authors else [],
            doi=self.doi,
            venue=self.venue,
            year=self.year,
            citation_count=self.citation_count,
            contribution=self.contribution,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "authors": json.loads(self.authors) if self.authors else [],
            "doi": self.doi,
            "venue": self.venue,
            "year": self.year,
            "citation_count": self.citation_count,
        }

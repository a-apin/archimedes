"""Strategy data models — shared across all components.

Passport-aware: every strategy carries enough provenance metadata to be
independently auditable per `docs/specs/strategy-passport-spec.md`. Selection-
bias controls (DSR, PBO, OOS sharpe split) live on `BacktestResult` rather
than here — see `models/backtest.py`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class StrategyStatus(str, Enum):
    """Lifecycle state of a strategy."""

    CANDIDATE = "candidate"  # Extracted from paper, not yet validated
    VALIDATED = "validated"  # Passed backtesting validation gate
    LIVE = "live"  # Active in at least one portfolio
    RETIRED = "retired"  # Removed from active use


class PositionSizing(str, Enum):
    EQUAL_WEIGHT = "equal_weight"
    RISK_PARITY = "risk_parity"
    KELLY = "kelly"
    INVERSE_VOL = "inverse_vol"


class RebalanceFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True)
class SignalDefinition:
    """A single entry/exit signal used by a strategy.

    Kept intentionally simple for the hackathon — a human-readable
    description + the key parameters. Post-hackathon this becomes
    executable code.
    """

    name: str  # e.g. "50/200 MA crossover"
    direction: str  # "entry" | "exit"
    description: str  # Human-readable explanation
    parameters: dict[str, float] = field(default_factory=dict)  # e.g. {"fast_period": 50}


@dataclass
class Strategy:
    """A trading strategy extracted from an academic paper.

    Produced by: Dan (curated library + LLM extraction)
    Consumed by: Önder (backtest evaluation, portfolio construction),
                 Chuan (strategy DB, API), Daniel (strategy explorer UI)

    Passport reference: docs/specs/strategy-passport-spec.md
    """

    id: str  # Deterministic hash of paper + methodology
    paper_arxiv_id: str  # e.g. "2509.11420" — may be empty for non-arxiv papers
    paper_title: str
    paper_authors: list[str] = field(default_factory=list)
    methodology_summary: str = ""  # 2-3 sentence plain English
    asset_universe: list[str] = field(default_factory=list)  # Ticker symbols
    signals: list[SignalDefinition] = field(default_factory=list)
    position_sizing: PositionSizing = PositionSizing.EQUAL_WEIGHT
    rebalance_frequency: RebalanceFrequency = RebalanceFrequency.WEEKLY
    risk_constraints: dict[str, float] = field(
        default_factory=dict
    )  # e.g. {"max_drawdown": 0.20, "max_leverage": 1.0}
    risk_profiles: list[str] = field(
        default_factory=list
    )  # Risk-tier tags: "conservative" | "moderate" | "aggressive" | "hyper_risky"
    status: StrategyStatus = StrategyStatus.CANDIDATE
    extraction_reasoning: str = ""  # Full LLM reasoning for extraction
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # ── Paper provenance (passport fields) ──────────────────
    paper_venue: str | None = None  # Journal, conference, or "arxiv only"
    paper_year: int | None = None
    paper_doi: str | None = None
    paper_citation_count: int | None = None  # Snapshot at curation time

    # ── Methodology integrity ───────────────────────────────
    # SHA-256 over the canonical methodology — methodology_text when populated,
    # otherwise methodology_summary. See compute_methodology_hash() for the rule.
    methodology_hash: str | None = None
    methodology_text: str | None = None  # Full extracted methodology (longer than summary)
    extraction_llm: str | None = None  # e.g. "claude-opus-4-7" — null if hand-curated
    extraction_prompt_hash: str | None = None  # SHA-256 of the extraction prompt

    # ── Curation trail (v1: Dan is the sole curator) ────────
    curator_wallet: str | None = None  # Wallet of human curator who validated
    curator_validation_at: datetime | None = None
    curator_note: str | None = None  # Per-paper rationale

    # ── Backtest engine binding ─────────────────────────────
    strategy_code_path: str | None = None  # Path to the analytics-engine strategy file
    strategy_code_hash: str | None = None  # SHA-256 of the strategy file contents

    # ── On-chain anchor ─────────────────────────────────────
    on_chain_registration_tx: str | None = None  # StrategyRegistry contract tx hash

    # ── Paper claims (for paper-vs-actual delta in UI) ───────
    # Parsed from PAPER_CLAIMED_* constants in strategy files.
    paper_claimed_sharpe: float | None = None
    paper_claimed_cagr: float | None = None
    paper_claimed_max_dd: float | None = None

    # ── Placeholder backtest stubs (pre-analytics-engine-run) ──
    # Sourced from BACKTEST_* constants in strategy files.
    # Replaced by real BacktestResult when IBacktestEvaluator runs.
    stub_sharpe: float | None = None
    stub_cagr: float | None = None
    stub_max_dd: float | None = None
    stub_win_rate: float | None = None
    stub_calmar: float | None = None
    stub_corr_spy: float | None = None

    @property
    def is_active(self) -> bool:
        return self.status in (StrategyStatus.VALIDATED, StrategyStatus.LIVE)

    @property
    def is_paper_grounded(self) -> bool:
        """True iff the strategy traces back to a real published paper.

        Tier-1 vaults require paper-grounded strategies per
        architectural-principles.md. Tier-2 vaults do not.
        """
        return bool(self.paper_arxiv_id or self.paper_doi)

    def compute_methodology_hash(self) -> str:
        """Compute deterministic SHA-256 hash of the methodology.

        Hashes `methodology_text` if present, else `methodology_summary`.
        UTF-8 with stripped leading/trailing whitespace; the canonical form
        anyone can recompute from the stored content.
        """
        source = (self.methodology_text or self.methodology_summary).strip()
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

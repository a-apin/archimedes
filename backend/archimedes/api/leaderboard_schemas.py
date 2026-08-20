"""Leaderboard API schemas — /api/leaderboard.

The public, gamified strategy leaderboard (North Star §5 — the testnet
engagement engine). It ranks every library strategy by a **transparent**
conviction score built from *real* passport data (rigor gate + backtest), and
pairs that validation axis with a clearly-labelled **forward axis** (per-strategy
StockBench + live paper-P&L) that is honest about what is live now vs pending.

Design rule (the #1 rule — claims must be true): the leaderboard NEVER invents a
number. Every ranking input is a real passport field; the score weights are
explicit and echoed in the response; and the StockBench / live-P&L axis carries an explicit "pending" value per strategy until that data actually
flows — the UI omits the axis rather than rendering it (#1365). (A prior
marketplace surface was removed for "hardcoded fees + invented math", #381 — we
do not repeat that.)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from archimedes.api.schemas import PaperRefResponse


class LeaderboardScoreComponents(BaseModel):
    """The four real, [0,1]-normalised inputs to the conviction score. Surfaced
    per-entry so the gamified score is never a black box — the user sees exactly
    what drove it. None inputs (e.g. a placeholder strategy with no DSR) score 0
    and are reflected in ``data_completeness``."""

    gate: float = Field(..., description="1.0 if passes_rigor_gate else 0.0")
    dsr_confidence: float = Field(
        ...,
        description=(
            "DSR confidence in [0,1] — the probability the Sharpe survives "
            "deflation/multiple-testing. HIGHER IS BETTER. (Sourced from the "
            "`dsr_p_value` field, which despite its legacy name holds a 0–1 "
            "confidence, NOT a classical p-value where lower is better.)"
        ),
    )
    oos_performance: float = Field(..., description="out_of_sample_sharpe / OOS_TARGET, clamped [0,1]")
    overfitting_resistance: float = Field(..., description="1 - pbo_score, clamped [0,1]")
    data_completeness: float = Field(..., description="Fraction of the four inputs backed by real data [0,1]")


class LeaderboardForwardAxis(BaseModel):
    """The forward-looking axis paired with validation in the scoring engine.
    Per-strategy StockBench and live paper-P&L are not tracked yet, so these
    carry an explicit ``pending`` value until the engagement-engine wiring
    lands. The API surfaces them (never fabricates them) so the pairing with
    validation stays visible to consumers; the UI omits the axis until real
    data flows (#1365)."""

    stockbench_status: str = Field(
        "pending",
        description="'pending' until per-strategy StockBench eval exists; the global benchmark context lives in the engine metadata",
    )
    stockbench_sortino: float | None = None
    live_pnl_status: str = Field(
        "pending", description="'pending' until live paper-P&L tracking is wired (testnet — paper/simulated)"
    )
    live_pnl_pct: float | None = None


class LeaderboardEntry(BaseModel):
    rank: int
    medal: str | None = Field(None, description="'gold' | 'silver' | 'bronze' for the top 3, else null")
    id: str
    name: str = Field(..., description="Paper title, else methodology summary — human label for the strategy")
    creator: str = Field(..., description="curator_wallet, else 'Archimedes' for the curated seed library")

    # The gamified, transparent score (0–100) and its real components.
    conviction_score: float
    score_components: LeaderboardScoreComponents

    # Validation axis — real backtest metrics (None = not yet evaluated).
    sharpe_ratio: float | None = None
    cagr: float | None = None
    sortino_ratio: float | None = None
    max_drawdown: float | None = None
    calmar_ratio: float | None = None

    # Rigor (selection-bias gate) — the credibility moat, surfaced honestly.
    deflated_sharpe_ratio: float | None = None
    dsr_p_value: float | None = Field(
        None,
        description=(
            "DSR confidence in [0,1] — HIGHER IS BETTER. Despite the legacy "
            "`p_value` name this is a confidence (probability the Sharpe survives "
            "deflation), not a classical p-value where lower is better."
        ),
    )
    pbo_score: float | None = None
    out_of_sample_sharpe: float | None = None
    passes_rigor_gate: bool = False
    is_backtest_placeholder: bool = False

    # Forward axis (paired, honest-pending).
    forward: LeaderboardForwardAxis

    # Provenance + context.
    regime_tag: str = "regime_neutral"
    return_source: str = "noise"
    status: str = "candidate"
    papers: list[PaperRefResponse] = []


class StockBenchGlobalContext(BaseModel):
    """The one real StockBench result we have: the *whole* agent pipeline run
    (Chen et al. 2026), not per-strategy. Surfaced as honest context so the
    forward axis means something today without faking per-strategy numbers."""

    scope: str = "agent_pipeline_global"
    sortino: float
    return_pct: float
    max_drawdown_pct: float
    rank: str
    window: str
    source: str


class LeaderboardScoringEngine(BaseModel):
    """Transparent metadata: the weights, the methodology, what's live vs pending,
    and the StockBench global context. Rendered alongside the board so the score
    is explainable and the testnet/paper framing is loud."""

    weights: dict[str, float]
    oos_target: float
    methodology: str
    validation_axis: str = "live"
    forward_axis: str = "pending"
    stockbench_global: StockBenchGlobalContext
    disclaimer: str


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]
    total: int
    sort_by: str
    order: str
    scope: str = Field(
        "curated",
        description=(
            "The cohort actually served: 'own' (the signed-in caller's own "
            "strategies, ranked against each other) or 'curated' (the curated "
            "seed library, shown as reference — never a competing cohort). May "
            "differ from a requested ?scope= — an anonymous caller requesting "
            "'own' is served 'curated' instead, and this field reports what was "
            "actually served, not what was asked for."
        ),
    )
    scoring_engine: LeaderboardScoringEngine
    # Honest degradation signal (#1356): True when the underlying strategy
    # provider raised, or the curated cohort came back empty for a reason
    # other than a legitimate filter (e.g. the corpus is missing from the
    # build). `degraded_reason` names which. The UI must never render "No
    # strategies match these filters yet." while this is True — that is a
    # different, false claim.
    degraded: bool = False
    degraded_reason: str = ""

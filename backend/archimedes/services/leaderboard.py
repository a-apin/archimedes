"""Leaderboard scoring engine — the testnet engagement engine (North Star §5).

Ranks library strategies by a **transparent conviction score** built from *real*
passport data: the rigor gate (DSR / PBO / OOS) plus backtest performance. The
score is a documented weighted sum of four [0,1] inputs — never a black box — and
every input is echoed per-entry so the user sees what drove the rank.

Two axes, honestly separated:
  • Validation axis (LIVE NOW): rigor gate + backtest — real passport fields.
  • Forward axis (PENDING): per-strategy StockBench + live paper-P&L — surfaced
    as honest "pending" until that data flows, so the engine visibly *pairs*
    them with validation (per Dan's call) without inventing values.

This module is pure: it takes ``StrategyResponse`` objects and returns schema
objects. No DB, no network — trivially unit-testable.
"""

from __future__ import annotations

from archimedes.api.leaderboard_schemas import (
    LeaderboardEntry,
    LeaderboardForwardAxis,
    LeaderboardResponse,
    LeaderboardScoreComponents,
    LeaderboardScoringEngine,
    StockBenchGlobalContext,
)
from archimedes.api.schemas import StrategyResponse

# ── Scoring weights (explicit + echoed in the response) ──────────────────────
# Rationale: passing the selection-bias gate is the single biggest *honest*
# credibility signal, so it carries the most weight; DSR confidence and
# out-of-sample performance are the next strongest "is the edge real?" signals;
# overfitting resistance (low PBO) rounds it out. Sum = 1.0.
WEIGHTS: dict[str, float] = {
    "gate": 0.35,
    "dsr_confidence": 0.25,
    "oos_performance": 0.25,
    "overfitting_resistance": 0.15,
}

#: An out-of-sample Sharpe of 1.0 earns full marks on the OOS component.
OOS_TARGET = 1.0

#: The one real StockBench datum we have — the whole agent pipeline run
#: (Chen et al. 2026), NOT per-strategy. Surfaced as honest global context.
#: Source: docs/benchmarks/stockbench-results.md.
STOCKBENCH_GLOBAL = StockBenchGlobalContext(
    sortino=-0.91,
    return_pct=-2.3,
    max_drawdown_pct=-6.2,
    rank="15/15",
    window="2025-03-03 → 2025-06-30 (82 trading days)",
    source="docs/benchmarks/stockbench-results.md",
)

_DISCLAIMER = (
    "Testnet — paper/simulated performance. Strategies are ranked on real, "
    "rigor-gated backtest results. Per-strategy StockBench and live paper-P&L "
    "are the next inputs to this engine and render as 'pending' until that data "
    "flows; no number here is fabricated."
)

# Sortable real fields → (StrategyResponse attribute, higher_is_better).
_SORTABLE: dict[str, tuple[str, bool]] = {
    "conviction_score": ("conviction_score", True),  # computed, handled specially
    "sharpe_ratio": ("sharpe_ratio", True),
    "cagr": ("cagr", True),
    "sortino_ratio": ("sortino_ratio", True),
    "calmar_ratio": ("calmar_ratio", True),
    "deflated_sharpe_ratio": ("deflated_sharpe_ratio", True),
    "dsr_p_value": ("dsr_p_value", True),
    "out_of_sample_sharpe": ("out_of_sample_sharpe", True),
    "pbo_score": ("pbo_score", False),  # lower is better
}

_MEDALS = {1: "gold", 2: "silver", 3: "bronze"}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_conviction(resp: StrategyResponse) -> tuple[float, LeaderboardScoreComponents]:
    """Return (score 0–100, the four real components). Missing inputs score 0 and
    lower ``data_completeness`` — so placeholders honestly sink, never inflate."""
    # A placeholder backtest carries NO real validation data. Even if DSR/OOS/PBO
    # fields happen to be populated (a seeded record can carry placeholder
    # numbers), they are not real backtest output — so EVERY component scores 0
    # and the entry sinks. Without this guard a placeholder could ride borrowed
    # DSR/OOS/PBO values (75% of the weight) above a real-but-partially-missing
    # strategy, which is exactly the inflation the docstring promises not to do.
    if resp.is_backtest_placeholder:
        zero = LeaderboardScoreComponents(
            gate=0.0,
            dsr_confidence=0.0,
            oos_performance=0.0,
            overfitting_resistance=0.0,
            data_completeness=0.0,
        )
        return 0.0, zero

    gate = 1.0 if resp.passes_rigor_gate else 0.0

    dsr_real = resp.dsr_p_value is not None
    dsr_confidence = _clamp01(resp.dsr_p_value) if dsr_real else 0.0

    oos_real = resp.out_of_sample_sharpe is not None
    oos_performance = _clamp01(resp.out_of_sample_sharpe / OOS_TARGET) if oos_real else 0.0

    pbo_real = resp.pbo_score is not None
    overfitting_resistance = _clamp01(1.0 - resp.pbo_score) if pbo_real else 0.0

    # gate is always a real signal for a non-placeholder strategy (+1); the other
    # three count only when their field is populated.
    real_count = 1 + int(dsr_real) + int(oos_real) + int(pbo_real)
    components = LeaderboardScoreComponents(
        gate=gate,
        dsr_confidence=dsr_confidence,
        oos_performance=oos_performance,
        overfitting_resistance=overfitting_resistance,
        data_completeness=real_count / 4.0,
    )

    score = 100.0 * (
        WEIGHTS["gate"] * gate
        + WEIGHTS["dsr_confidence"] * dsr_confidence
        + WEIGHTS["oos_performance"] * oos_performance
        + WEIGHTS["overfitting_resistance"] * overfitting_resistance
    )
    return round(score, 1), components


def _entry(resp: StrategyResponse) -> LeaderboardEntry:
    score, components = compute_conviction(resp)
    name = resp.paper_title or (resp.methodology_summary or resp.id)[:80]
    creator = resp.curator_wallet or "Archimedes"
    return LeaderboardEntry(
        rank=0,  # assigned after sort
        medal=None,
        id=resp.id,
        name=name,
        creator=creator,
        conviction_score=score,
        score_components=components,
        sharpe_ratio=resp.sharpe_ratio,
        cagr=resp.cagr,
        sortino_ratio=resp.sortino_ratio,
        max_drawdown=resp.max_drawdown,
        calmar_ratio=resp.calmar_ratio,
        deflated_sharpe_ratio=resp.deflated_sharpe_ratio,
        dsr_p_value=resp.dsr_p_value,
        pbo_score=resp.pbo_score,
        out_of_sample_sharpe=resp.out_of_sample_sharpe,
        passes_rigor_gate=resp.passes_rigor_gate,
        is_backtest_placeholder=resp.is_backtest_placeholder,
        forward=LeaderboardForwardAxis(),
        regime_tag=resp.regime_tag,
        return_source=resp.return_source,
        status=resp.status,
        papers=resp.papers,
    )


def _sort_key(entry: LeaderboardEntry, field: str):
    """Raw value of the sort field for an entry (None if not evaluated).
    None-handling (push to bottom regardless of order) is done by the caller."""
    if field == "conviction_score":
        return entry.conviction_score
    attr, _ = _SORTABLE[field]
    return getattr(entry, attr)


def build_leaderboard(
    responses: list[StrategyResponse],
    *,
    sort_by: str = "conviction_score",
    order: str = "desc",
    regime_tag: str | None = None,
    min_rigor: bool = False,
    limit: int = 50,
    scope: str = "curated",
) -> LeaderboardResponse:
    """Rank strategies into a leaderboard. Pure — no I/O.

    ``scope`` is echoed back verbatim (never derived here) — it is the caller's
    statement of what cohort ``responses`` actually is ('own': the signed-in
    caller's own strategies; 'curated': the curated seed library), so the UI
    can label the board honestly even when the caller's requested scope was
    silently coerced (e.g. an anonymous request for 'own').
    """
    if sort_by not in _SORTABLE:
        sort_by = "conviction_score"
    order = "asc" if order == "asc" else "desc"

    entries = [_entry(r) for r in responses]

    if regime_tag:
        entries = [e for e in entries if e.regime_tag == regime_tag]
    if min_rigor:
        entries = [e for e in entries if e.passes_rigor_gate and not e.is_backtest_placeholder]

    # Split present vs missing so None always lands at the bottom, whatever the
    # order. Present values sort by the requested direction.
    def value_of(e: LeaderboardEntry):
        return _sort_key(e, sort_by)

    present = [e for e in entries if value_of(e) is not None]
    missing = [e for e in entries if value_of(e) is None]
    present.sort(key=value_of, reverse=(order == "desc"))
    ranked = present + missing

    for i, e in enumerate(ranked, start=1):
        e.rank = i
        e.medal = _MEDALS.get(i)

    total = len(ranked)
    ranked = ranked[:limit]

    engine = LeaderboardScoringEngine(
        weights=WEIGHTS,
        oos_target=OOS_TARGET,
        methodology=(
            "conviction_score = 100 × (0.35·gate + 0.25·DSR_confidence + "
            "0.25·OOS_performance + 0.15·overfitting_resistance); every input is a "
            "real passport field, clamped to [0,1]."
        ),
        stockbench_global=STOCKBENCH_GLOBAL,
        disclaimer=_DISCLAIMER,
    )
    return LeaderboardResponse(
        entries=ranked,
        total=total,
        sort_by=sort_by,
        order=order,
        scope=scope,
        scoring_engine=engine,
    )

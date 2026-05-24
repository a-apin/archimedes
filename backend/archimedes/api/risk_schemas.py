"""Risk analysis API response schemas.

Dedicated risk endpoints for the Risk Analysis page. These aggregate
strategy-level backtest metrics into portfolio-level risk summaries
and expose the four risk-profile bands used for the risk-vs-actual
visualization.
"""

from __future__ import annotations

from pydantic import BaseModel

# ═══════════════════════════════════════════════════════════════
# Risk Profile Bands
# ═══════════════════════════════════════════════════════════════


class RiskProfileBand(BaseModel):
    """A single risk-profile tier with its threshold boundaries."""

    label: str  # "conservative" | "moderate" | "aggressive" | "hyper_risky"
    max_dd: float  # Maximum acceptable drawdown (as fraction, e.g. 0.10 = 10%)
    target_sharpe: float  # Minimum target Sharpe ratio
    max_vol: float  # Maximum acceptable annualized volatility (as fraction)
    color: str  # UI color token, e.g. "#22C55E"


class RiskProfileBandsResponse(BaseModel):
    """All four risk-profile bands for the risk-vs-actual visualization."""

    bands: list[RiskProfileBand]


# ═══════════════════════════════════════════════════════════════
# Portfolio-Level Risk
# ═══════════════════════════════════════════════════════════════


class StrategyRiskSummary(BaseModel):
    """Per-strategy risk metrics for the risk comparison table."""

    id: str
    paper_title: str
    status: str
    sharpe_ratio: float | None = None
    volatility: float | None = None  # Derived: abs(cagr)/sharpe when both present
    max_drawdown: float | None = None
    cagr: float | None = None
    win_rate: float | None = None
    calmar_ratio: float | None = None
    correlation_to_spy: float | None = None
    risk_level: str  # "Low" | "Medium" | "High"


class PortfolioRiskResponse(BaseModel):
    """Aggregated portfolio-level risk metrics.

    Combines strategy backtest stubs and on-chain vault holdings into
    a single risk summary for the Risk Analysis dashboard.
    """

    # Strategy-aggregate metrics
    strategy_count: int
    avg_sharpe: float
    worst_max_dd: float  # As a positive fraction
    avg_correlation_spy: float
    best_calmar: float
    avg_volatility: float  # Average derived volatility across strategies

    # Portfolio structure
    concentration_hhi: float  # Herfindahl-Hirschman Index (0 = perfectly diversified, 1 = single asset)
    concentration_label: str  # "diversified" | "moderate" | "concentrated"
    holding_count: int  # Number of distinct holdings

    # Risk profile classification
    actual_risk_profile: str  # "conservative" | "moderate" | "aggressive" | "hyper_risky"

    # Per-strategy breakdown
    strategies: list[StrategyRiskSummary]

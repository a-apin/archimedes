"""Portfolio data models — shared across agent, math, and frontend."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class RiskProfile(str, Enum):
    """User-selected risk profile — design.md § 4.3.1."""

    FIXED_INCOME = "fixed_income"  # <2% vol, 5% max DD, 70-95% USYC floor
    CONSERVATIVE = "conservative"  # 5-8% vol, 10% max DD, 40-60% USYC floor
    MODERATE = "moderate"  # 10-15% vol, 20% max DD, 20-40% USYC floor
    AGGRESSIVE = "aggressive"  # 20-30% vol, 35% max DD, 5-15% USYC floor
    HYPER_RISKY = "hyper_risky"  # 30%+ vol, 50% max DD, 0-5% USYC floor


# Risk profile parameters — Önder uses these for portfolio construction
RISK_PROFILE_PARAMS: dict[RiskProfile, dict[str, float]] = {
    RiskProfile.FIXED_INCOME: {
        "target_vol_min": 0.00,
        "target_vol_max": 0.02,
        "max_drawdown": 0.05,
        "usyc_floor": 0.70,
        "usyc_ceiling": 0.95,
    },
    RiskProfile.CONSERVATIVE: {
        "target_vol_min": 0.05,
        "target_vol_max": 0.08,
        "max_drawdown": 0.10,
        "usyc_floor": 0.40,
        "usyc_ceiling": 0.60,
    },
    RiskProfile.MODERATE: {
        "target_vol_min": 0.10,
        "target_vol_max": 0.15,
        "max_drawdown": 0.20,
        "usyc_floor": 0.20,
        "usyc_ceiling": 0.40,
    },
    RiskProfile.AGGRESSIVE: {
        "target_vol_min": 0.20,
        "target_vol_max": 0.30,
        "max_drawdown": 0.35,
        "usyc_floor": 0.05,
        "usyc_ceiling": 0.15,
    },
    RiskProfile.HYPER_RISKY: {
        "target_vol_min": 0.30,
        "target_vol_max": 0.50,
        "max_drawdown": 0.50,
        "usyc_floor": 0.00,
        "usyc_ceiling": 0.05,
    },
}


class TradeDirection(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class PortfolioHolding:
    """A single position in a portfolio."""

    symbol: str  # e.g. "sTSLA"
    token_address: str  # On-chain address
    amount: float  # Token amount held
    value_usdc: float  # Current USDC value
    weight: float  # Fraction of total portfolio (0-1)
    # False when the oracle price could not be read (#1080): value_usdc/weight
    # are then 0 BY CONSTRUCTION, not real zeros — trade sizing must treat the
    # holding as unknown-value (skip), never as absent (buy more every tick).
    priced: bool = True


@dataclass(frozen=True)
class TargetAllocation:
    """A target weight for an asset in the portfolio.

    This is the OUTPUT of Önder's portfolio construction.
    The agent compares current holdings to target allocations to decide trades.
    """

    symbol: str
    token_address: str
    weight: float  # Target fraction (0-1). All weights must sum to 1.0.
    strategy_ids: list[str] = field(default_factory=list)  # Which strategies drive this allocation


@dataclass
class Portfolio:
    """Current state of a vault's portfolio.

    Produced by: Chuan (read from on-chain vault + price data)
    Consumed by: Önder (portfolio construction compares current vs target),
                 Daniel (portfolio dashboard display),
                 Marten (rebalance execution needs current state)
    """

    vault_address: str
    holdings: list[PortfolioHolding] = field(default_factory=list)
    total_value_usdc: float = 0.0
    risk_profile: RiskProfile = RiskProfile.MODERATE
    strategy_ids: list[str] = field(default_factory=list)  # Active strategies
    last_rebalance: datetime | None = None
    created_at: datetime | None = None

    @property
    def weights_dict(self) -> dict[str, float]:
        """Symbol → current weight mapping."""
        return {h.symbol: h.weight for h in self.holdings}


@dataclass(frozen=True)
class TradeOrder:
    """A single trade to execute during rebalance.

    Produced by: Chuan (agent orchestrator diffs current vs target)
    Consumed by: Marten (on-chain executor submits to AMM/vault)
    """

    symbol: str
    token_address: str
    direction: TradeDirection
    amount: float  # Token amount to trade
    estimated_usdc_value: float  # Estimated cost/proceeds in USDC
    max_slippage_bps: int = 100  # Max slippage tolerance (basis points)


@dataclass
class RebalanceDecision:
    """The agent's decision about whether and how to rebalance.

    Produced by: Chuan (agent orchestrator)
    Consumed by: Marten (executes trades if approved),
                 Chuan (publishes reasoning trace),
                 Daniel (shows rebalance events in UI)
    """

    vault_address: str
    should_rebalance: bool
    trigger: str  # What caused the evaluation: "drift", "regime_change", "strategy_decay", "calendar"
    current_portfolio: Portfolio
    target_allocations: list[TargetAllocation]
    trades: list[TradeOrder] = field(default_factory=list)
    estimated_cost_usdc: float = 0.0  # Total trading cost (fees + slippage)
    estimated_benefit: float = 0.0  # Expected portfolio improvement
    reasoning: str = ""  # Human-readable explanation
    timestamp: datetime | None = None

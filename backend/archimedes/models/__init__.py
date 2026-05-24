# Archimedes — Shared Data Models
# These types cross component boundaries. Every team member depends on them.
# Change policy: announce in Discord before modifying any model here.

from archimedes.models.asset import AssetInfo, AssetPrice, AssetType
from archimedes.models.backtest import BacktestResult
from archimedes.models.paper_ref import PaperRef
from archimedes.models.portfolio import (
    Portfolio,
    PortfolioHolding,
    RebalanceDecision,
    RiskProfile,
    TargetAllocation,
    TradeDirection,
    TradeOrder,
)
from archimedes.models.regime import Regime, RegimeClassification, RegimeSignals
from archimedes.models.strategy import (
    PositionSizing,
    RebalanceFrequency,
    SignalDefinition,
    Strategy,  # Backwards-compat alias
    StrategyPassport,
    StrategyStatus,
)
from archimedes.models.trace import DecisionType, ReasoningTrace
from archimedes.models.vault import VaultInfo, VaultMetrics, VaultTier

__all__ = [
    # Asset
    "AssetInfo",
    "AssetPrice",
    "AssetType",
    # Backtest
    "BacktestResult",
    "DecisionType",
    # Paper reference
    "PaperRef",
    "Portfolio",
    "PortfolioHolding",
    "PositionSizing",
    # Trace
    "ReasoningTrace",
    "RebalanceDecision",
    "RebalanceFrequency",
    # Regime
    "Regime",
    "RegimeClassification",
    "RegimeSignals",
    # Portfolio
    "RiskProfile",
    "SignalDefinition",
    "Strategy",  # Backwards-compat alias
    # Strategy passport
    "StrategyPassport",
    "StrategyStatus",
    "TargetAllocation",
    "TradeDirection",
    "TradeOrder",
    # Vault
    "VaultInfo",
    "VaultMetrics",
    "VaultTier",
]

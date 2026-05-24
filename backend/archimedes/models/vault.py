"""Vault data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class VaultTier(int, Enum):
    """Vault classification — ecosystem-design-spec.md § 2."""

    TIER_1 = 1  # Archimedes-curated, paper-grounded, AI-managed
    TIER_2 = 2  # Community-created, freestyle allocations


@dataclass
class VaultInfo:
    """Metadata for a vault in the marketplace.

    Produced by: Chuan (VaultFactory contract events + backend indexer)
    Consumed by: Daniel (marketplace leaderboard, vault detail page),
                 Önder (portfolio construction needs vault context),
                 Marten (executor needs vault address + tier for tx routing)
    """

    address: str  # On-chain vault contract address
    name: str  # e.g. "Momentum Alpha"
    symbol: str  # e.g. "vMOMENTUM"
    tier: VaultTier
    creator: str  # Creator wallet address
    management_fee_bps: int  # e.g. 150 = 1.5%
    performance_fee_bps: int  # e.g. 2000 = 20%
    is_agent_assisted: bool  # True if agent manages this vault
    target_allocations: dict[str, float] = field(default_factory=dict)  # symbol → weight (bps)
    created_at: datetime | None = None


@dataclass
class VaultMetrics:
    """Live performance metrics for a vault.

    Produced by: Chuan (backend computes from on-chain data + price history)
    Consumed by: Daniel (leaderboard ranking, vault detail charts),
                 Önder (strategy evaluation references vault performance)
    """

    vault_address: str
    total_aum_usdc: float  # Assets under management in USDC
    share_price: float  # Current price per vault token in USDC
    high_water_mark: float  # HWM for performance fee calculation

    # Performance metrics
    return_24h: float = 0.0  # 24-hour return (fraction)
    return_7d: float = 0.0  # 7-day return
    return_30d: float = 0.0  # 30-day return
    return_inception: float = 0.0  # Since inception return
    sharpe_ratio: float | None = None  # Rolling Sharpe (30d)
    max_drawdown: float | None = None  # Max drawdown since inception
    total_depositors: int = 0

    # Timestamps
    last_rebalance: datetime | None = None
    last_updated: datetime | None = None

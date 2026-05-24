"""Asset data models — shared across all components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class AssetType(str, Enum):
    """Classification of assets in the ecosystem."""

    SYNTHETIC = "synthetic"  # Oracle-priced, USDC-collateralized (sTSLA, sSPY, etc.)
    BRIDGED = "bridged"  # Bridged from another chain via CCTP/Gateway
    NATIVE = "native"  # Native on Arc (USDC, USYC)
    VAULT_TOKEN = "vault_token"  # ERC-4626 vault share token


@dataclass(frozen=True)
class AssetInfo:
    """Static metadata for a registered asset.

    Produced by: Chuan (AssetRegistry contract + backend)
    Consumed by: Daniel (frontend display), Önder (portfolio math),
                 Marten (oracle updater needs to know which assets to price)
    """

    address: str  # On-chain contract address (checksummed)
    symbol: str  # e.g. "sTSLA", "USDC", "vMOMENTUM"
    name: str  # e.g. "Synthetic Tesla", "USD Coin"
    asset_type: AssetType
    decimals: int = 18  # ERC-20 decimals
    oracle_address: str | None = None  # PriceOracle address (None for USDC)
    source_chain_id: int | None = None  # For bridged assets
    source_address: str | None = None  # For bridged assets


@dataclass(frozen=True)
class AssetPrice:
    """A single price observation for an asset.

    Produced by: Marten (oracle updater fetches from yfinance/CoinGecko)
    Consumed by: Önder (regime detection, portfolio math), Daniel (frontend),
                 Chuan (AMM pool pricing reference)
    """

    symbol: str  # e.g. "sTSLA"
    price_usd: float  # Price in USD (8 decimal precision convention)
    timestamp: datetime  # When the price was observed
    source: str = "yfinance"  # Data source identifier

    def __post_init__(self) -> None:
        if self.price_usd <= 0:
            raise ValueError(f"Price must be positive, got {self.price_usd}")


@dataclass
class MarketSnapshot:
    """A point-in-time snapshot of all asset prices + market indicators.

    Produced by: Marten (oracle updater collects all prices)
    Consumed by: Önder (regime detection input), Chuan (agent orchestrator)
    """

    timestamp: datetime
    prices: dict[str, float]  # symbol → price_usd
    vix: float | None = None  # VIX index level
    sp500_ma50: float | None = None  # S&P 500 50-day MA
    sp500_ma200: float | None = None  # S&P 500 200-day MA
    credit_spread_ig: float | None = None  # Investment grade spread
    credit_spread_hy: float | None = None  # High yield spread
    btc_dominance: float | None = None  # BTC market dominance %
    usyc_yield: float | None = None  # Current USYC yield

    @property
    def has_regime_signals(self) -> bool:
        """Whether this snapshot has enough data for regime classification."""
        return self.vix is not None and self.sp500_ma50 is not None

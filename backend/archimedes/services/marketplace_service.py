"""Marketplace service — manages community strategy data and discovery.

Provides seed data for the MVP marketplace with realistic community strategies.
Eventually this will connect to a database for user-created strategies.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from archimedes.api.marketplace_schemas import (
    AllocationBreakdown,
    AuthorInfo,
    Category,
    CategoryInfo,
    MarketplaceStrategyDetail,
    MonthlyPerformance,
    RiskAssessment,
    RiskLevel,
    StrategyCard,
)


# ═══════════════════════════════════════════════════════════════
# Seed Data — Community Strategies
# ═══════════════════════════════════════════════════════════════

_AUTHORS: dict[str, AuthorInfo] = {
    "alpha_whale": AuthorInfo(
        name="AlphaWhale",
        address="0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb9",
        avatar_url=None,
        verified=True,
        strategies_count=3,
    ),
    "yield_farmer": AuthorInfo(
        name="YieldFarmer42",
        address="0x1a2B3c4D5e6F7a8B9c0D1e2F3a4B5c6D7e8F9a0B",
        avatar_url=None,
        verified=False,
        strategies_count=2,
    ),
    "defi_ninja": AuthorInfo(
        name="DeFiNinja",
        address="0x9f8e7D6C5B4A3f2E1d0c9B8a7F6e5D4C3B2A1f0e9",
        avatar_url=None,
        verified=True,
        strategies_count=4,
    ),
    "momentum_trader": AuthorInfo(
        name="MomentumTrader",
        address="0x3a4B5c6D7e8F9a0B1c2D3e4F5a6B7c8D9e0F1a2B",
        avatar_url=None,
        verified=False,
        strategies_count=1,
    ),
    "quant_researcher": AuthorInfo(
        name="QuantResearcher",
        address="0x5c6D7e8F9a0B1c2D3e4F5a6B7c8D9e0F1a2B3c4D",
        avatar_url=None,
        verified=True,
        strategies_count=2,
    ),
    "stable_yields": AuthorInfo(
        name="StableYields",
        address="0x7e8F9a0B1c2D3e4F5a6B7c8D9e0F1a2B3c4D5e6F",
        avatar_url=None,
        verified=True,
        strategies_count=3,
    ),
    "arbitrage_bot": AuthorInfo(
        name="ArbitrageKing",
        address="0x9a0B1c2D3e4F5a6B7c8D9e0F1a2B3c4D5e6F7a8B",
        avatar_url=None,
        verified=True,
        strategies_count=2,
    ),
    "option_greek": AuthorInfo(
        name="OptionGreek",
        address="0xB1c2D3e4F5a6B7c8D9e0F1a2B3c4D5e6F7a8B9c0D",
        avatar_url=None,
        verified=False,
        strategies_count=1,
    ),
}

_STRATEGIES: list[MarketplaceStrategyDetail] = [
    # 1. Momentum — Tech Leaders
    MarketplaceStrategyDetail(
        id="momentum-tech-leaders",
        name="Tech Leaders Momentum",
        description_short="Captures momentum in leading tech stocks using adaptive lookback periods.",
        description_long=(
            "This strategy identifies strong momentum trends in leading technology stocks "
            "by analyzing price movements over multiple timeframes. It uses a proprietary "
            "adaptive algorithm that adjusts its lookback period based on market volatility, "
            "allowing it to stay in trends longer while exiting quickly when momentum fades."
        ),
        author=_AUTHORS["alpha_whale"],
        risk_level=RiskLevel.HIGH,
        category=Category.MOMENTUM,
        tags=["Tech", "Growth", "Adaptive", "Multi-timeframe"],
        tvl_usdc=5_250_000,
        apy_pct=28.5,
        apy_7d_pct=32.1,
        apy_30d_pct=29.8,
        return_7d_pct=2.1,
        return_30d_pct=8.5,
        return_90d_pct=22.3,
        return_inception_pct=145.2,
        performance_history=[
            MonthlyPerformance(month="2024-01", return_pct=3.2, volatility_pct=12.5, sharpe_ratio=1.8),
            MonthlyPerformance(month="2024-02", return_pct=-1.8, volatility_pct=14.2, sharpe_ratio=0.9),
            MonthlyPerformance(month="2024-03", return_pct=5.5, volatility_pct=11.8, sharpe_ratio=2.3),
            MonthlyPerformance(month="2024-04", return_pct=2.1, volatility_pct=13.1, sharpe_ratio=1.4),
            MonthlyPerformance(month="2024-05", return_pct=8.2, volatility_pct=15.5, sharpe_ratio=2.8),
            MonthlyPerformance(month="2024-06", return_pct=1.5, volatility_pct=12.9, sharpe_ratio=1.1),
        ],
        allocation_breakdown=[
            AllocationBreakdown(asset="sTSLA", weight_pct=35, type="synthetic"),
            AllocationBreakdown(asset="sNVDA", weight_pct=30, type="synthetic"),
            AllocationBreakdown(asset="sAAPL", weight_pct=20, type="synthetic"),
            AllocationBreakdown(asset="USDC", weight_pct=15, type="stablecoin"),
        ],
        risk_assessment=RiskAssessment(
            max_drawdown=-18.5,
            volatility=22.3,
            var_95=-8.2,
            beta=1.45,
            correlation_to_market=0.85,
        ),
        users_count=234,
        rating=4.6,
        reviews_count=42,
        management_fee_pct=1.5,
        performance_fee_pct=15.0,
        min_deposit_usdc=1000,
        featured=True,
        trending=True,
        verified=True,
        related_strategies=["momentum-cross-sector", "trend-follower-pro"],
        contract_address="0x1234...5678",
        audit_link="https://example.com/audit/momentum-tech",
        docs_link="https://docs.example.com/strategies/momentum-tech",
        created_at="2024-01-15T10:00:00Z",
        updated_at="2024-06-01T14:30:00Z",
    ),
    # 2. Yield Farming — Stablecoin Optimizer
    MarketplaceStrategyDetail(
        id="stable-yield-optimizer",
        name="Stablecoin Yield Optimizer",
        description_short="Automatically allocates across the best stablecoin yield sources on Arc.",
        description_long=(
            "This strategy continuously monitors and allocates capital to the highest-yielding "
            "stablecoin opportunities on the Arc network. It diversifies across multiple protocols "
            "to minimize smart contract risk while maximizing yield through automatic compounding."
        ),
        author=_AUTHORS["yield_farmer"],
        risk_level=RiskLevel.LOW,
        category=Category.STABLECOIN,
        tags=["Stablecoin", "Yield", "Auto-compound", "Low Risk"],
        tvl_usdc=12_800_000,
        apy_pct=8.5,
        apy_7d_pct=8.2,
        apy_30d_pct=8.4,
        return_7d_pct=0.15,
        return_30d_pct=0.65,
        return_90d_pct=1.95,
        return_inception_pct=24.5,
        performance_history=[
            MonthlyPerformance(month="2024-01", return_pct=0.65, volatility_pct=0.8, sharpe_ratio=4.5),
            MonthlyPerformance(month="2024-02", return_pct=0.62, volatility_pct=0.7, sharpe_ratio=4.8),
            MonthlyPerformance(month="2024-03", return_pct=0.71, volatility_pct=0.9, sharpe_ratio=4.2),
            MonthlyPerformance(month="2024-04", return_pct=0.68, volatility_pct=0.8, sharpe_ratio=4.6),
            MonthlyPerformance(month="2024-05", return_pct=0.73, volatility_pct=1.0, sharpe_ratio=4.0),
            MonthlyPerformance(month="2024-06", return_pct=0.70, volatility_pct=0.8, sharpe_ratio=4.7),
        ],
        allocation_breakdown=[
            AllocationBreakdown(asset="USDC", weight_pct=100, type="stablecoin"),
        ],
        risk_assessment=RiskAssessment(
            max_drawdown=-0.8,
            volatility=1.2,
            var_95=-0.3,
            beta=0.02,
            correlation_to_market=0.05,
        ),
        users_count=567,
        rating=4.8,
        reviews_count=128,
        management_fee_pct=0.5,
        performance_fee_pct=10.0,
        min_deposit_usdc=100,
        auto_compound=True,
        featured=True,
        trending=False,
        verified=True,
        related_strategies=["defi-yield-aggregator", "multi-chain-yield"],
        contract_address="0xabcd...ef01",
        audit_link="https://example.com/audit/stable-yield",
        created_at="2023-11-01T08:00:00Z",
        updated_at="2024-06-02T09:15:00Z",
    ),
    # 3. Mean Reversion — Statistical Arb
    MarketplaceStrategyDetail(
        id="stat-arb-pairs",
        name="Statistical Arbitrage Pairs",
        description_short="Exploits mean reversion in cointegrated asset pairs on Arc.",
        description_long=(
            "Uses statistical cointegration analysis to identify asset pairs that move together. "
            "When pairs diverge beyond historical norms, the strategy takes positions expecting "
            "convergence. Includes dynamic hedging and position sizing based on cointegration strength."
        ),
        author=_AUTHORS["quant_researcher"],
        risk_level=RiskLevel.MEDIUM,
        category=Category.MEAN_REVERSION,
        tags=["Pairs Trading", "Statistical Arb", "Quant", "Hedged"],
        tvl_usdc=2_150_000,
        apy_pct=15.8,
        apy_7d_pct=14.2,
        apy_30d_pct=16.5,
        return_7d_pct=0.85,
        return_30d_pct=3.2,
        return_90d_pct=9.8,
        return_inception_pct=67.3,
        performance_history=[
            MonthlyPerformance(month="2024-01", return_pct=1.8, volatility_pct=6.5, sharpe_ratio=2.1),
            MonthlyPerformance(month="2024-02", return_pct=1.2, volatility_pct=5.8, sharpe_ratio=2.3),
            MonthlyPerformance(month="2024-03", return_pct=2.5, volatility_pct=7.2, sharpe_ratio=2.4),
            MonthlyPerformance(month="2024-04", return_pct=0.9, volatility_pct=5.5, sharpe_ratio=1.8),
            MonthlyPerformance(month="2024-05", return_pct=1.8, volatility_pct=6.8, sharpe_ratio=2.0),
            MonthlyPerformance(month="2024-06", return_pct=1.5, volatility_pct=6.2, sharpe_ratio=2.2),
        ],
        allocation_breakdown=[
            AllocationBreakdown(asset="sBTC", weight_pct=25, type="synthetic"),
            AllocationBreakdown(asset="sETH", weight_pct=25, type="synthetic"),
            AllocationBreakdown(asset="USDC", weight_pct=50, type="stablecoin"),
        ],
        risk_assessment=RiskAssessment(
            max_drawdown=-8.2,
            volatility=9.5,
            var_95=-3.5,
            beta=0.35,
            correlation_to_market=0.25,
        ),
        users_count=89,
        rating=4.4,
        reviews_count=23,
        management_fee_pct=1.0,
        performance_fee_pct=12.0,
        min_deposit_usdc=500,
        featured=False,
        trending=True,
        verified=True,
        related_strategies=["market-neutral-equity", "volatility-harvest"],
        created_at="2024-02-20T14:00:00Z",
        updated_at="2024-06-01T16:45:00Z",
    ),
    # 4. Trend Following — Dual MA
    MarketplaceStrategyDetail(
        id="trend-dual-ma",
        name="Dual Moving Average Trend",
        description_short="Classic dual moving average crossover strategy with dynamic parameters.",
        description_long=(
            "A refined implementation of the classic dual moving average crossover. The strategy "
            "optimizes lookback periods based on market regime detection and incorporates trend "
            "strength filters to reduce whipsaw in ranging markets. Works across all synthetic assets."
        ),
        author=_AUTHORS["momentum_trader"],
        risk_level=RiskLevel.MEDIUM,
        category=Category.TREND_FOLLOWING,
        tags=["Trend Following", "Moving Average", "Regime Aware", "Classic"],
        tvl_usdc=890_000,
        apy_pct=18.2,
        apy_7d_pct=22.5,
        apy_30d_pct=19.8,
        return_7d_pct=2.8,
        return_30d_pct=6.5,
        return_90d_pct=18.2,
        return_inception_pct=89.5,
        performance_history=[
            MonthlyPerformance(month="2024-01", return_pct=4.2, volatility_pct=10.5, sharpe_ratio=2.0),
            MonthlyPerformance(month="2024-02", return_pct=-2.8, volatility_pct=12.2, sharpe_ratio=0.6),
            MonthlyPerformance(month="2024-03", return_pct=3.5, volatility_pct=9.8, sharpe_ratio=2.2),
            MonthlyPerformance(month="2024-04", return_pct=6.8, volatility_pct=11.5, sharpe_ratio=2.9),
            MonthlyPerformance(month="2024-05", return_pct=4.2, volatility_pct=10.2, sharpe_ratio=2.3),
            MonthlyPerformance(month="2024-06", return_pct=2.8, volatility_pct=9.5, sharpe_ratio=1.9),
        ],
        allocation_breakdown=[
            AllocationBreakdown(asset="sBTC", weight_pct=40, type="synthetic"),
            AllocationBreakdown(asset="sETH", weight_pct=30, type="synthetic"),
            AllocationBreakdown(asset="sSOL", weight_pct=15, type="synthetic"),
            AllocationBreakdown(asset="USDC", weight_pct=15, type="stablecoin"),
        ],
        risk_assessment=RiskAssessment(
            max_drawdown=-14.2,
            volatility=16.8,
            var_95=-6.5,
            beta=0.95,
            correlation_to_market=0.75,
        ),
        users_count=156,
        rating=4.2,
        reviews_count=38,
        management_fee_pct=1.0,
        performance_fee_pct=15.0,
        min_deposit_usdc=250,
        featured=False,
        trending=True,
        verified=False,
        related_strategies=["momentum-tech-leaders", "breakout-momentum"],
        created_at="2024-01-10T11:00:00Z",
        updated_at="2024-06-01T12:00:00Z",
    ),
    # 5. DeFi Yield — Multi-Protocol
    MarketplaceStrategyDetail(
        id="defi-yield-multi",
        name="DeFi Yield Multi-Strategy",
        description_short="Aggregates yield opportunities across multiple DeFi protocols on Arc.",
        description_long=(
            "This strategy scans and allocates capital across the highest-yielding DeFi protocols "
            "on the Arc network. It includes automated risk scoring, impermanent loss protection, "
            "and protocol diversification. Yield sources include lending, staking, and liquidity pools."
        ),
        author=_AUTHORS["defi_ninja"],
        risk_level=RiskLevel.MEDIUM,
        category=Category.DEFI_YIELD,
        tags=["DeFi", "Yield Aggregator", "Multi-protocol", "IL Protection"],
        tvl_usdc=4_580_000,
        apy_pct=12.5,
        apy_7d_pct=11.8,
        apy_30d_pct=12.2,
        return_7d_pct=0.55,
        return_30d_pct=2.8,
        return_90d_pct=8.5,
        return_inception_pct=52.8,
        performance_history=[
            MonthlyPerformance(month="2024-01", return_pct=1.8, volatility_pct=4.5, sharpe_ratio=2.8),
            MonthlyPerformance(month="2024-02", return_pct=1.5, volatility_pct=4.2, sharpe_ratio=2.9),
            MonthlyPerformance(month="2024-03", return_pct=2.2, volatility_pct=5.1, sharpe_ratio=2.9),
            MonthlyPerformance(month="2024-04", return_pct=1.9, volatility_pct=4.8, sharpe_ratio=2.8),
            MonthlyPerformance(month="2024-05", return_pct=2.5, volatility_pct=5.5, sharpe_ratio=2.7),
            MonthlyPerformance(month="2024-06", return_pct=2.1, volatility_pct=5.0, sharpe_ratio=2.8),
        ],
        allocation_breakdown=[
            AllocationBreakdown(asset="USDC", weight_pct=70, type="stablecoin"),
            AllocationBreakdown(asset="sUSDC", weight_pct=30, type="synthetic"),
        ],
        risk_assessment=RiskAssessment(
            max_drawdown=-5.8,
            volatility=6.5,
            var_95=-2.5,
            beta=0.15,
            correlation_to_market=0.12,
        ),
        users_count=312,
        rating=4.5,
        reviews_count=67,
        management_fee_pct=1.0,
        performance_fee_pct=10.0,
        min_deposit_usdc=500,
        auto_compound=True,
        featured=False,
        trending=False,
        verified=True,
        related_strategies=["stable-yield-optimizer", "yield-farming-pro"],
        contract_address="0xfee1...dead",
        audit_link="https://example.com/audit/defi-yield",
        created_at="2023-12-15T09:30:00Z",
        updated_at="2024-06-02T10:00:00Z",
    ),
    # 6. Leverage — Enhanced Yield
    MarketplaceStrategyDetail(
        id="enhanced-yield-lev",
        name="Enhanced Yield with Leverage",
        description_short="Amplifies stablecoin yields using calibrated leverage up to 3x.",
        description_long=(
            "This strategy uses controlled leverage (1.5x - 3x) to enhance yield from stablecoin "
            "positions. Includes automatic deleveraging during high volatility, real-time collateral "
            "monitoring, and circuit breakers. Designed for users comfortable with higher risk."
        ),
        author=_AUTHORS["alpha_whale"],
        risk_level=RiskLevel.HIGH,
        category=Category.LEVERAGE,
        tags=["Leverage", "Enhanced Yield", "Risk Managed", "Stablecoin"],
        tvl_usdc=1_850_000,
        apy_pct=22.8,
        apy_7d_pct=25.5,
        apy_30d_pct=21.2,
        return_7d_pct=1.85,
        return_30d_pct=4.8,
        return_90d_pct=14.5,
        return_inception_pct=98.5,
        performance_history=[
            MonthlyPerformance(month="2024-01", return_pct=3.5, volatility_pct=8.5, sharpe_ratio=2.5),
            MonthlyPerformance(month="2024-02", return_pct=2.8, volatility_pct=9.2, sharpe_ratio=2.2),
            MonthlyPerformance(month="2024-03", return_pct=4.2, volatility_pct=10.1, sharpe_ratio=2.5),
            MonthlyPerformance(month="2024-04", return_pct=1.5, volatility_pct=11.5, sharpe_ratio=1.1),
            MonthlyPerformance(month="2024-05", return_pct=3.8, volatility_pct=9.8, sharpe_ratio=2.6),
            MonthlyPerformance(month="2024-06", return_pct=2.9, volatility_pct=8.9, sharpe_ratio=2.4),
        ],
        allocation_breakdown=[
            AllocationBreakdown(asset="USDC", weight_pct=85, type="stablecoin"),
            AllocationBreakdown(asset="sUSDC", weight_pct=15, type="synthetic"),
        ],
        risk_assessment=RiskAssessment(
            max_drawdown=-12.5,
            volatility=14.2,
            var_95=-5.8,
            beta=0.25,
            correlation_to_market=0.15,
        ),
        users_count=78,
        rating=4.1,
        reviews_count=19,
        management_fee_pct=1.5,
        performance_fee_pct=20.0,
        min_deposit_usdc=2500,
        featured=False,
        trending=True,
        verified=True,
        related_strategies=["stable-yield-optimizer", "defi-yield-multi"],
        contract_address="0x1337...c0de",
        audit_link="https://example.com/audit/enhanced-lev",
        created_at="2024-03-01T13:00:00Z",
        updated_at="2024-06-01T15:20:00Z",
    ),
    # 7. Diversified — All Weather
    MarketplaceStrategyDetail(
        id="all-weather-port",
        name="All Weather Portfolio",
        description_short="Balanced portfolio designed to perform across market conditions.",
        description_long=(
            "Inspired by Ray Dalio's All Weather strategy, this portfolio balances assets to "
            "perform in both inflationary and deflationary environments. Uses a mix of growth "
            "assets, stablecoins, and inflation hedges with periodic rebalancing."
        ),
        author=_AUTHORS["stable_yields"],
        risk_level=RiskLevel.LOW,
        category=Category.DIVERSIFIED,
        tags=["Diversified", "All Weather", "Low Volatility", "Balanced"],
        tvl_usdc=7_420_000,
        apy_pct=9.8,
        apy_7d_pct=9.2,
        apy_30d_pct=9.5,
        return_7d_pct=0.35,
        return_30d_pct=1.5,
        return_90d_pct=4.5,
        return_inception_pct=38.5,
        performance_history=[
            MonthlyPerformance(month="2024-01", return_pct=1.2, volatility_pct=3.5, sharpe_ratio=2.2),
            MonthlyPerformance(month="2024-02", return_pct=0.8, volatility_pct=3.2, sharpe_ratio=2.0),
            MonthlyPerformance(month="2024-03", return_pct=1.5, volatility_pct=3.8, sharpe_ratio=2.3),
            MonthlyPerformance(month="2024-04", return_pct=1.1, volatility_pct=3.4, sharpe_ratio=2.2),
            MonthlyPerformance(month="2024-05", return_pct=1.6, volatility_pct=4.0, sharpe_ratio=2.4),
            MonthlyPerformance(month="2024-06", return_pct=1.3, volatility_pct=3.6, sharpe_ratio=2.3),
        ],
        allocation_breakdown=[
            AllocationBreakdown(asset="sTSLA", weight_pct=15, type="synthetic"),
            AllocationBreakdown(asset="sBTC", weight_pct=20, type="synthetic"),
            AllocationBreakdown(asset="USDC", weight_pct=50, type="stablecoin"),
            AllocationBreakdown(asset="sUSDC", weight_pct=15, type="synthetic"),
        ],
        risk_assessment=RiskAssessment(
            max_drawdown=-4.2,
            volatility=5.8,
            var_95=-2.0,
            beta=0.45,
            correlation_to_market=0.55,
        ),
        users_count=445,
        rating=4.7,
        reviews_count=89,
        management_fee_pct=0.75,
        performance_fee_pct=10.0,
        min_deposit_usdc=100,
        featured=True,
        trending=False,
        verified=True,
        related_strategies=["balanced-growth", "conservative-income"],
        contract_address="0x4321...8765",
        created_at="2023-10-20T10:00:00Z",
        updated_at="2024-06-02T08:30:00Z",
    ),
    # 8. Momentum — Cross Sector
    MarketplaceStrategyDetail(
        id="momentum-cross-sector",
        name="Cross-Sector Momentum",
        description_short="Rotates across sectors based on relative momentum strength.",
        description_long=(
            "This strategy identifies the strongest trending sectors and rotates capital accordingly. "
            "Uses relative strength comparisons and cross-sectional momentum to avoid weak sectors "
            "even when the overall market is trending. Includes defensive allocation during "
            "market stress."
        ),
        author=_AUTHORS["alpha_whale"],
        risk_level=RiskLevel.MEDIUM,
        category=Category.MOMENTUM,
        tags=["Sector Rotation", "Momentum", "Relative Strength", "Defensive"],
        tvl_usdc=3_250_000,
        apy_pct=19.5,
        apy_7d_pct=21.8,
        apy_30d_pct=18.5,
        return_7d_pct=2.2,
        return_30d_pct=5.8,
        return_90d_pct=16.8,
        return_inception_pct=112.5,
        performance_history=[
            MonthlyPerformance(month="2024-01", return_pct=4.5, volatility_pct=11.2, sharpe_ratio=2.2),
            MonthlyPerformance(month="2024-02", return_pct=-1.2, volatility_pct=13.5, sharpe_ratio=1.1),
            MonthlyPerformance(month="2024-03", return_pct=6.2, volatility_pct=12.8, sharpe_ratio=2.8),
            MonthlyPerformance(month="2024-04", return_pct=3.8, volatility_pct=10.5, sharpe_ratio=2.3),
            MonthlyPerformance(month="2024-05", return_pct=5.5, volatility_pct=11.8, sharpe_ratio=2.6),
            MonthlyPerformance(month="2024-06", return_pct=2.8, volatility_pct=10.2, sharpe_ratio=1.9),
        ],
        allocation_breakdown=[
            AllocationBreakdown(asset="sTSLA", weight_pct=25, type="synthetic"),
            AllocationBreakdown(asset="sNVDA", weight_pct=25, type="synthetic"),
            AllocationBreakdown(asset="sXLE", weight_pct=20, type="synthetic"),
            AllocationBreakdown(asset="USDC", weight_pct=30, type="stablecoin"),
        ],
        risk_assessment=RiskAssessment(
            max_drawdown=-11.5,
            volatility=15.2,
            var_95=-5.2,
            beta=0.85,
            correlation_to_market=0.72,
        ),
        users_count=198,
        rating=4.4,
        reviews_count=45,
        management_fee_pct=1.25,
        performance_fee_pct=15.0,
        min_deposit_usdc=500,
        featured=False,
        trending=False,
        verified=True,
        related_strategies=["momentum-tech-leaders", "sector-rotation-pro"],
        created_at="2024-01-25T15:00:00Z",
        updated_at="2024-06-01T17:00:00Z",
    ),
    # 9. Options — Covered Call
    MarketplaceStrategyDetail(
        id="covered-call-override",
        name="Covered Call Override",
        description_short="Generates income by selling covered calls on synthetic assets.",
        description_long=(
            "This strategy holds long positions in synthetic assets while selling out-of-the-money "
            "call options to generate premium income. The strategy aims to enhance returns in "
            "flat-to-slightly-up markets while providing some downside protection. Includes "
            "dynamic strike adjustment based on volatility."
        ),
        author=_AUTHORS["option_greek"],
        risk_level=RiskLevel.MEDIUM,
        category=Category.OPTIONS,
        tags=["Options", "Covered Calls", "Income", "Volatility Aware"],
        tvl_usdc=680_000,
        apy_pct=14.2,
        apy_7d_pct=13.5,
        apy_30d_pct=14.8,
        return_7d_pct=0.65,
        return_30d_pct=2.8,
        return_90d_pct=8.2,
        return_inception_pct=58.5,
        performance_history=[
            MonthlyPerformance(month="2024-01", return_pct=2.5, volatility_pct=7.5, sharpe_ratio=2.1),
            MonthlyPerformance(month="2024-02", return_pct=0.8, volatility_pct=8.2, sharpe_ratio=1.4),
            MonthlyPerformance(month="2024-03", return_pct=3.2, volatility_pct=9.1, sharpe_ratio=2.3),
            MonthlyPerformance(month="2024-04", return_pct=1.5, volatility_pct=7.8, sharpe_ratio=1.7),
            MonthlyPerformance(month="2024-05", return_pct=2.8, volatility_pct=8.5, sharpe_ratio=2.2),
            MonthlyPerformance(month="2024-06", return_pct=1.9, volatility_pct=7.2, sharpe_ratio=1.8),
        ],
        allocation_breakdown=[
            AllocationBreakdown(asset="sSPY", weight_pct=50, type="synthetic"),
            AllocationBreakdown(asset="sQQQ", weight_pct=30, type="synthetic"),
            AllocationBreakdown(asset="USDC", weight_pct=20, type="stablecoin"),
        ],
        risk_assessment=RiskAssessment(
            max_drawdown=-9.5,
            volatility=11.8,
            var_95=-4.2,
            beta=0.65,
            correlation_to_market=0.82,
        ),
        users_count=67,
        rating=4.0,
        reviews_count=15,
        management_fee_pct=1.5,
        performance_fee_pct=15.0,
        min_deposit_usdc=1000,
        featured=False,
        trending=False,
        verified=False,
        related_strategies=["cash-secured-puts", "iron-condor-income"],
        created_at="2024-02-10T12:00:00Z",
        updated_at="2024-06-01T11:30:00Z",
    ),
    # 10. Arbitrage — Triangular
    MarketplaceStrategyDetail(
        id="triangular-arb",
        name="Triangular Arbitrage",
        description_short="Exploits price discrepancies across synthetic asset trading pairs.",
        description_long=(
            "Monitors price relationships across trading pairs to identify and execute profitable "
            "triangular arbitrage opportunities. Uses fast execution and minimal slippage routing. "
            "Low risk strategy with frequent small profits that compound over time."
        ),
        author=_AUTHORS["arbitrage_bot"],
        risk_level=RiskLevel.LOW,
        category=Category.ARBITRAGE,
        tags=["Arbitrage", "Low Risk", "High Frequency", "Market Neutral"],
        tvl_usdc=920_000,
        apy_pct=6.8,
        apy_7d_pct=7.2,
        apy_30d_pct=6.5,
        return_7d_pct=0.25,
        return_30d_pct=1.2,
        return_90d_pct=3.5,
        return_inception_pct=28.5,
        performance_history=[
            MonthlyPerformance(month="2024-01", return_pct=0.85, volatility_pct=2.5, sharpe_ratio=3.5),
            MonthlyPerformance(month="2024-02", return_pct=0.78, volatility_pct=2.2, sharpe_ratio=3.8),
            MonthlyPerformance(month="2024-03", return_pct=0.92, volatility_pct=2.8, sharpe_ratio=3.6),
            MonthlyPerformance(month="2024-04", return_pct=0.88, volatility_pct=2.4, sharpe_ratio=3.9),
            MonthlyPerformance(month="2024-05", return_pct=0.95, volatility_pct=2.6, sharpe_ratio=3.8),
            MonthlyPerformance(month="2024-06", return_pct=0.82, volatility_pct=2.3, sharpe_ratio=3.7),
        ],
        allocation_breakdown=[
            AllocationBreakdown(asset="USDC", weight_pct=95, type="stablecoin"),
            AllocationBreakdown(asset="sBTC", weight_pct=5, type="synthetic"),
        ],
        risk_assessment=RiskAssessment(
            max_drawdown=-1.5,
            volatility=3.2,
            var_95=-0.8,
            beta=0.05,
            correlation_to_market=0.02,
        ),
        users_count=112,
        rating=4.6,
        reviews_count=28,
        management_fee_pct=1.0,
        performance_fee_pct=25.0,
        min_deposit_usdc=500,
        featured=False,
        trending=False,
        verified=True,
        related_strategies=["stat-arb-pairs", "dex-arb-aggregator"],
        contract_address="0xa1b2...c3d4",
        audit_link="https://example.com/audit/triangular-arb",
        created_at="2023-11-15T14:00:00Z",
        updated_at="2024-06-02T07:00:00Z",
    ),
    # 11. Yield Farming — Protocol A
    MarketplaceStrategyDetail(
        id="yield-protocol-a",
        name="Protocol A Yield Maximizer",
        description_short="Optimized yield farming strategy for Protocol A on Arc.",
        description_long=(
            "Specialized strategy that maximizes yield from Protocol A's liquidity pools. "
            "Includes automatic reward harvesting, strategic compounding, and impermanent "
            "loss monitoring. Focuses on the most established pools for lower risk."
        ),
        author=_AUTHORS["yield_farmer"],
        risk_level=RiskLevel.MEDIUM,
        category=Category.YIELD_FARMING,
        tags=["Yield Farming", "Protocol A", "Auto Compound", "IL Monitoring"],
        tvl_usdc=1_580_000,
        apy_pct=16.5,
        apy_7d_pct=15.8,
        apy_30d_pct=17.2,
        return_7d_pct=0.75,
        return_30d_pct=3.5,
        return_90d_pct=10.5,
        return_inception_pct=72.5,
        performance_history=[
            MonthlyPerformance(month="2024-01", return_pct=2.8, volatility_pct=5.5, sharpe_ratio=2.9),
            MonthlyPerformance(month="2024-02", return_pct=2.5, volatility_pct=5.2, sharpe_ratio=3.0),
            MonthlyPerformance(month="2024-03", return_pct=3.5, volatility_pct=6.1, sharpe_ratio=3.1),
            MonthlyPerformance(month="2024-04", return_pct=3.2, volatility_pct=5.8, sharpe_ratio=3.0),
            MonthlyPerformance(month="2024-05", return_pct=3.8, volatility_pct=6.5, sharpe_ratio=3.2),
            MonthlyPerformance(month="2024-06", return_pct=3.4, volatility_pct=6.0, sharpe_ratio=3.1),
        ],
        allocation_breakdown=[
            AllocationBreakdown(asset="USDC", weight_pct=80, type="stablecoin"),
            AllocationBreakdown(asset="sETH", weight_pct=20, type="synthetic"),
        ],
        risk_assessment=RiskAssessment(
            max_drawdown=-6.8,
            volatility=8.5,
            var_95=-3.2,
            beta=0.22,
            correlation_to_market=0.18,
        ),
        users_count=145,
        rating=4.3,
        reviews_count=31,
        management_fee_pct=1.0,
        performance_fee_pct=15.0,
        min_deposit_usdc=250,
        auto_compound=True,
        featured=False,
        trending=False,
        verified=False,
        related_strategies=["defi-yield-multi", "stable-yield-optimizer"],
        created_at="2024-01-05T10:30:00Z",
        updated_at="2024-06-01T13:15:00Z",
    ),
    # 12. Custom — Community Pick
    MarketplaceStrategyDetail(
        id="community-pick-alpha",
        name="Community Pick Alpha",
        description_short="Community-vetted strategy combining multiple proven approaches.",
        description_long=(
            "This strategy was developed through community collaboration, combining the best "
            "elements from several popular strategies. It underwent extensive backtesting "
            "and community review before being added to the marketplace. Proceeds from "
            "management fees support ongoing community development."
        ),
        author=_AUTHORS["defi_ninja"],
        risk_level=RiskLevel.MEDIUM,
        category=Category.CUSTOM,
        tags=["Community", "Crowdsourced", "Multi-Strategy", "Verified"],
        tvl_usdc=2_850_000,
        apy_pct=17.8,
        apy_7d_pct=19.2,
        apy_30d_pct=16.5,
        return_7d_pct=1.85,
        return_30d_pct=4.2,
        return_90d_pct=12.8,
        return_inception_pct=85.5,
        performance_history=[
            MonthlyPerformance(month="2024-01", return_pct=3.8, volatility_pct=9.5, sharpe_ratio=2.5),
            MonthlyPerformance(month="2024-02", return_pct=1.2, volatility_pct=10.2, sharpe_ratio=1.6),
            MonthlyPerformance(month="2024-03", return_pct=4.5, volatility_pct=11.5, sharpe_ratio=2.6),
            MonthlyPerformance(month="2024-04", return_pct=3.2, volatility_pct=9.8, sharpe_ratio=2.4),
            MonthlyPerformance(month="2024-05", return_pct=4.8, volatility_pct=12.1, sharpe_ratio=2.7),
            MonthlyPerformance(month="2024-06", return_pct=2.9, volatility_pct=10.5, sharpe_ratio=2.1),
        ],
        allocation_breakdown=[
            AllocationBreakdown(asset="sTSLA", weight_pct=20, type="synthetic"),
            AllocationBreakdown(asset="sBTC", weight_pct=25, type="synthetic"),
            AllocationBreakdown(asset="sETH", weight_pct=20, type="synthetic"),
            AllocationBreakdown(asset="USDC", weight_pct=35, type="stablecoin"),
        ],
        risk_assessment=RiskAssessment(
            max_drawdown=-10.5,
            volatility=14.2,
            var_95=-5.0,
            beta=0.72,
            correlation_to_market=0.68,
        ),
        users_count=287,
        rating=4.8,
        reviews_count=73,
        management_fee_pct=1.0,
        performance_fee_pct=12.0,
        min_deposit_usdc=100,
        featured=True,
        trending=True,
        verified=True,
        related_strategies=["momentum-tech-leaders", "all-weather-port", "stat-arb-pairs"],
        contract_address="0xc0mm...un1ty",
        audit_link="https://example.com/audit/community-alpha",
        docs_link="https://docs.example.com/strategies/community-alpha",
        telegram_link="https://t.me/communityalpha",
        discord_link="https://discord.gg/communityalpha",
        created_at="2024-02-01T12:00:00Z",
        updated_at="2024-06-01T18:00:00Z",
    ),
]

# Build lookup dict
_STRATEGIES_BY_ID: dict[str, MarketplaceStrategyDetail] = {s.id: s for s in _STRATEGIES}


# ═══════════════════════════════════════════════════════════════
# Category Data
# ═══════════════════════════════════════════════════════════════

_CATEGORIES: list[CategoryInfo] = [
    CategoryInfo(
        id=Category.MOMENTUM,
        name="Momentum",
        description="Strategies that follow prevailing market trends",
        icon="📈",
        strategies_count=2,
        avg_apy_pct=23.2,
        total_tvl_usdc=8_500_000,
    ),
    CategoryInfo(
        id=Category.STABLECOIN,
        name="Stablecoin",
        description="Low-risk strategies focused on stablecoin yields",
        icon="💰",
        strategies_count=1,
        avg_apy_pct=8.5,
        total_tvl_usdc=12_800_000,
    ),
    CategoryInfo(
        id=Category.MEAN_REVERSION,
        name="Mean Reversion",
        description="Strategies betting on price return to average",
        icon="🔄",
        strategies_count=1,
        avg_apy_pct=15.8,
        total_tvl_usdc=2_150_000,
    ),
    CategoryInfo(
        id=Category.TREND_FOLLOWING,
        name="Trend Following",
        description="Classic trend-following using technical indicators",
        icon="📊",
        strategies_count=1,
        avg_apy_pct=18.2,
        total_tvl_usdc=890_000,
    ),
    CategoryInfo(
        id=Category.DEFI_YIELD,
        name="DeFi Yield",
        description="Yield aggregation across DeFi protocols",
        icon="🌾",
        strategies_count=1,
        avg_apy_pct=12.5,
        total_tvl_usdc=4_580_000,
    ),
    CategoryInfo(
        id=Category.LEVERAGE,
        name="Leverage",
        description="Enhanced returns using calibrated leverage",
        icon="⚡",
        strategies_count=1,
        avg_apy_pct=22.8,
        total_tvl_usdc=1_850_000,
    ),
    CategoryInfo(
        id=Category.DIVERSIFIED,
        name="Diversified",
        description="Balanced portfolios across multiple assets",
        icon="🌐",
        strategies_count=1,
        avg_apy_pct=9.8,
        total_tvl_usdc=7_420_000,
    ),
    CategoryInfo(
        id=Category.OPTIONS,
        name="Options",
        description="Income generation through options strategies",
        icon="📋",
        strategies_count=1,
        avg_apy_pct=14.2,
        total_tvl_usdc=680_000,
    ),
    CategoryInfo(
        id=Category.ARBITRAGE,
        name="Arbitrage",
        description="Exploit price inefficiencies across markets",
        icon="⚖️",
        strategies_count=1,
        avg_apy_pct=6.8,
        total_tvl_usdc=920_000,
    ),
    CategoryInfo(
        id=Category.YIELD_FARMING,
        name="Yield Farming",
        description="Protocol-specific yield optimization",
        icon="🚜",
        strategies_count=1,
        avg_apy_pct=16.5,
        total_tvl_usdc=1_580_000,
    ),
    CategoryInfo(
        id=Category.CUSTOM,
        name="Custom",
        description="Community-created and verified strategies",
        icon="🎨",
        strategies_count=1,
        avg_apy_pct=17.8,
        total_tvl_usdc=2_850_000,
    ),
]


# ═══════════════════════════════════════════════════════════════
# Service Class
# ═══════════════════════════════════════════════════════════════


class MarketplaceService:
    """Service for marketplace strategy data and discovery."""

    def __init__(self) -> None:
        self._strategies = _STRATEGIES
        self._strategies_by_id = _STRATEGIES_BY_ID
        self._categories = _CATEGORIES

    def _to_card(self, strategy: MarketplaceStrategyDetail) -> StrategyCard:
        """Convert full strategy detail to a list view card."""
        return StrategyCard(
            id=strategy.id,
            name=strategy.name,
            description=strategy.description_short,
            author=strategy.author,
            risk_level=strategy.risk_level,
            category=strategy.category,
            tags=strategy.tags,
            tvl_usdc=strategy.tvl_usdc,
            apy_pct=strategy.apy_pct,
            apy_7d_pct=strategy.apy_7d_pct,
            return_30d_pct=strategy.return_30d_pct,
            return_inception_pct=strategy.return_inception_pct,
            users_count=strategy.users_count,
            rating=strategy.rating,
            reviews_count=strategy.reviews_count,
            featured=strategy.featured,
            trending=strategy.trending,
            created_at=strategy.created_at,
            updated_at=strategy.updated_at,
        )

    def list_strategies(
        self,
        category: Category | None = None,
        risk_level: RiskLevel | None = None,
        sort_by: str = "tvl",
        search: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[StrategyCard], int]:
        """List marketplace strategies with filtering and pagination.

        Returns:
            (strategies, total_count)
        """
        # Start with all strategies
        filtered = list(self._strategies)

        # Apply filters
        if category:
            filtered = [s for s in filtered if s.category == category]

        if risk_level:
            filtered = [s for s in filtered if s.risk_level == risk_level]

        if search:
            search_lower = search.lower()
            filtered = [
                s
                for s in filtered
                if search_lower in s.name.lower()
                or search_lower in s.description_short.lower()
                or any(search_lower in tag.lower() for tag in s.tags)
            ]

        # Sort
        sort_key = {
            "tvl": lambda s: s.tvl_usdc,
            "apy": lambda s: s.apy_pct,
            "rating": lambda s: s.rating or 0,
            "newest": lambda s: s.created_at,
            "users": lambda s: s.users_count,
        }.get(sort_by, lambda s: s.tvl_usdc)

        reverse = sort_by != "newest"
        filtered.sort(key=sort_key, reverse=reverse)

        total = len(filtered)

        # Paginate
        offset = (page - 1) * limit
        paginated = filtered[offset : offset + limit]

        return [self._to_card(s) for s in paginated], total

    def get_strategy(self, strategy_id: str) -> MarketplaceStrategyDetail | None:
        """Get a single strategy by ID."""
        return self._strategies_by_id.get(strategy_id)

    def get_featured(self) -> list[StrategyCard]:
        """Get featured strategies."""
        featured = [s for s in self._strategies if s.featured]
        return [self._to_card(s) for s in featured]

    def get_trending(self) -> list[StrategyCard]:
        """Get trending strategies."""
        trending = [s for s in self._strategies if s.trending]
        # Sort by users count (recent growth indicator)
        trending.sort(key=lambda s: s.users_count, reverse=True)
        return [self._to_card(s) for s in trending]

    def list_categories(self) -> list[CategoryInfo]:
        """Get all available categories."""
        return self._categories


# Singleton instance
_marketplace_service: MarketplaceService | None = None


def marketplace_service() -> MarketplaceService:
    """Get the singleton marketplace service instance."""
    global _marketplace_service
    if _marketplace_service is None:
        _marketplace_service = MarketplaceService()
    return _marketplace_service

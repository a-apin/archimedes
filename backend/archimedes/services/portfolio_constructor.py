"""Portfolio constructor — computes target allocations from strategies + regime.

Given the current regime, a set of active strategies, and a risk profile,
produce target allocation weights for the portfolio.

When price_histories are supplied to construct(), weights are computed via
mean-variance optimization (portfolio_optimizer.py):
  - CONSERVATIVE  → Global Minimum Variance
  - MODERATE / AGGRESSIVE → Max Sharpe
  - HYPER_RISKY   → Max Expected Return

Without price histories the constructor falls back to equal weight across
the strategy asset universe.

Design reference: design.md § 4.3, ecosystem-design-spec.md § 3.3
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from archimedes.models.portfolio import (
    Portfolio,
    RiskProfile,
    RISK_PROFILE_PARAMS,
    TargetAllocation,
    TradeDirection,
    TradeOrder,
)
from archimedes.models.regime import Regime, RegimeClassification
from archimedes.models.strategy import Strategy
from archimedes.chain.client import chain_client
from archimedes.services.portfolio_optimizer import optimize_weights

logger = logging.getLogger(__name__)


def _equal_weights(symbols: list[str], budget: float) -> dict[str, float]:
    n = len(symbols)
    if n == 0:
        return {}
    return {s: round(budget / n, 6) for s in symbols}


# Regime-based allocation tilts — how much to shift from synthetics → USDC
_REGIME_USDC_FLOOR: dict[Regime, float] = {
    Regime.RISK_ON: 0.05,
    Regime.TRANSITION: 0.20,
    Regime.RISK_OFF: 0.50,
    Regime.CRISIS: 0.80,
}

# The synth tokens we allocate to (in priority order)
_DEFAULT_SYNTHS = ["sSPY", "sTSLA", "sNVDA", "sBTC", "sGOLD", "sOIL", "sNKY"]

# Drift threshold for rebalance trigger
_DRIFT_THRESHOLD = 0.05  # 5% absolute weight deviation


class PortfolioConstructor:
    """Computes target allocations and trade diffs."""

    def construct(
        self,
        current: Portfolio,
        strategies: list[Strategy],
        regime: RegimeClassification,
        risk_profile: RiskProfile = RiskProfile.MODERATE,
        price_histories: dict[str, pd.Series] | None = None,
    ) -> list[TargetAllocation]:
        """Build target allocations.

        Steps:
          1. Determine USDC floor from regime + risk profile
          2. Collect synth asset universe from active strategies
          3. If price_histories provided: compute MVO weights via
             portfolio_optimizer; otherwise fall back to equal weight
          4. Emit TargetAllocation list (weights sum to 1.0)

        Args:
            price_histories: Optional {synth_symbol: pd.Series of closing prices}.
                             When provided, MVO replaces equal-weight distribution.
        """
        params = RISK_PROFILE_PARAMS.get(risk_profile, RISK_PROFILE_PARAMS[RiskProfile.MODERATE])

        # USDC floor: stricter of regime and risk-profile floors
        regime_floor = _REGIME_USDC_FLOOR.get(regime.regime, 0.20)
        profile_floor = params["usyc_floor"]
        usdc_weight = max(regime_floor, profile_floor)

        # Collect asset universe from active strategies
        if strategies:
            assets: list[str] = []
            seen: set[str] = set()
            for s in strategies:
                for a in s.asset_universe:
                    if a not in seen:
                        seen.add(a)
                        assets.append(a)
            if not assets:
                assets = _DEFAULT_SYNTHS[:3]
        else:
            assets = _DEFAULT_SYNTHS[:3]

        synth_assets = self._map_to_synths(assets)
        synth_budget = 1.0 - usdc_weight

        # Weight computation: MVO if price histories available, else equal weight
        if price_histories and synth_assets:
            daily_returns = {
                sym: list(price_histories[sym].pct_change().dropna())
                for sym in synth_assets
                if sym in price_histories and not price_histories[sym].empty
            }
            available = [s for s in synth_assets if s in daily_returns]
            if available:
                mvo_weights = optimize_weights(
                    symbols=available,
                    daily_returns=daily_returns,
                    risk_profile=risk_profile,
                    synth_budget=synth_budget,
                )
                synth_weights = mvo_weights
            else:
                synth_weights = _equal_weights(synth_assets, synth_budget)
        else:
            synth_weights = _equal_weights(synth_assets, synth_budget)

        allocations: list[TargetAllocation] = []

        # USDC allocation
        usdc_addr = chain_client.settings.usdc_address
        allocations.append(
            TargetAllocation(
                symbol="USDC",
                token_address=usdc_addr,
                weight=round(usdc_weight, 4),
                strategy_ids=[s.id for s in strategies],
            )
        )

        # Synth allocations
        synth_addrs = chain_client.settings.synth_addresses
        for sym in synth_assets:
            addr = synth_addrs.get(sym, "")
            w = synth_weights.get(sym, 0.0)
            allocations.append(
                TargetAllocation(
                    symbol=sym,
                    token_address=addr,
                    weight=round(w, 4),
                    strategy_ids=[
                        s.id for s in strategies
                        if sym in self._map_to_synths(s.asset_universe)
                    ],
                )
            )

        logger.info(
            "Target allocations: %s (regime=%s, usdc_floor=%.0f%%, method=%s)",
            {a.symbol: f"{a.weight:.0%}" for a in allocations},
            regime.regime.value,
            usdc_weight * 100,
            "MVO" if price_histories else "equal-weight",
        )
        return allocations

    def compute_trades(
        self,
        current: Portfolio,
        targets: list[TargetAllocation],
    ) -> list[TradeOrder]:
        """Diff current holdings vs target allocations → trade list.

        For each target:
          - If current weight < target - threshold → BUY
          - If current weight > target + threshold → SELL
          - Otherwise → no trade
        """
        current_weights = current.weights_dict
        target_weights = {t.symbol: t for t in targets}

        trades: list[TradeOrder] = []

        # Check all target symbols + any current holding not in targets
        all_symbols = set(target_weights.keys()) | set(current_weights.keys())

        for sym in all_symbols:
            current_w = current_weights.get(sym, 0.0)
            target = target_weights.get(sym)
            target_w = target.weight if target else 0.0
            token_addr = target.token_address if target else ""

            drift = target_w - current_w

            if abs(drift) < _DRIFT_THRESHOLD:
                continue

            # Compute trade size in USDC terms
            usdc_value = abs(drift) * current.total_value_usdc

            # Determine direction
            direction = TradeDirection.BUY if drift > 0 else TradeDirection.SELL

            # Estimate token amount (rough: will be refined at execution)
            amount = usdc_value  # Simplified; executor handles conversion

            trades.append(
                TradeOrder(
                    symbol=sym,
                    token_address=token_addr,
                    direction=direction,
                    amount=round(amount, 6),
                    estimated_usdc_value=round(usdc_value, 2),
                )
            )

        if trades:
            logger.info(
                "Computed %d trades (drifts: %s)",
                len(trades),
                ", ".join(f"{t.symbol} {t.direction.value} ${t.estimated_usdc_value:.0f}" for t in trades),
            )
        return trades

    def should_trigger_rebalance(
        self,
        current: Portfolio,
        targets: list[TargetAllocation],
        regime: RegimeClassification,
        last_rebalance: datetime | None = None,
    ) -> str | None:
        """Check if rebalance is needed. Returns trigger reason or None.

        Triggers:
          - "regime_change" — regime shifted
          - "drift" — any allocation drifted > 5% from target
          - "calendar" — more than 7 days since last rebalance
        """
        # 1. Regime change is always a trigger
        if regime.regime_changed:
            return "regime_change"

        # 2. Drift check
        current_weights = current.weights_dict
        target_weights = {t.symbol: t.weight for t in targets}

        for sym in set(current_weights.keys()) | set(target_weights.keys()):
            drift = abs(target_weights.get(sym, 0.0) - current_weights.get(sym, 0.0))
            if drift > _DRIFT_THRESHOLD:
                return "drift"

        # 3. Calendar check — weekly
        if last_rebalance is None:
            return "calendar"

        age = (datetime.now(timezone.utc) - last_rebalance).total_seconds()
        if age > 7 * 24 * 3600:  # 7 days
            return "calendar"

        return None

    def _map_to_synths(self, tickers: list[str]) -> list[str]:
        """Map yfinance tickers to synth token symbols."""
        mapping = {
            "SPY": "sSPY",
            "TSLA": "sTSLA",
            "NVDA": "sNVDA",
            "BTC": "sBTC",
            "GOLD": "sGOLD",
            "OIL": "sOIL",
            "NIKKEI": "sNKY",
            "TREASURY": "sGOLD",  # Proxy
        }
        synths: list[str] = []
        seen: set[str] = set()
        for t in tickers:
            s = mapping.get(t, "")
            if s and s not in seen:
                seen.add(s)
                synths.append(s)
        return synths or ["sSPY", "sTSLA", "sNVDA"]

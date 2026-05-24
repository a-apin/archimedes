"""Kelly Criterion / Risk-Parity portfolio constructor.

Upgrades the v1 equal-weight constructor to:
1. Kelly Criterion position sizing based on strategy Sharpe ratios
2. Risk-parity weighting across strategies (inverse-vol allocation)
3. USDC floor enforcement per risk profile
4. Regime-aware deleveraging (shift to USDC in risk_off / crisis)

Uses the IBacktestEvaluator results (when available) or falls back to
stub metrics from strategy files.

Design reference: design.md § 4.3, Issue #51
Owner: Önder (math), Dan (backup)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from archimedes.models.backtest import BacktestResult
from archimedes.models.portfolio import (
    RISK_PROFILE_PARAMS,
    Portfolio,
    RiskProfile,
    TargetAllocation,
    TradeDirection,
    TradeOrder,
)
from archimedes.models.regime import Regime, RegimeClassification
from archimedes.models.strategy import Strategy

logger = logging.getLogger(__name__)

# ─── Regime-aware USDC floor multipliers ──────────────────────
# Base USDC floor from risk profile is multiplied by this factor
# depending on the regime. Crisis regime dramatically increases cash allocation.

_REGIME_DELEVERAGE_FACTORS: dict[Regime, float] = {
    Regime.RISK_ON: 0.5,  # Use only 50% of profile's base floor in risk_on
    Regime.TRANSITION: 1.0,  # Use full profile floor
    Regime.RISK_OFF: 2.5,  # 2.5x the profile floor
    Regime.CRISIS: 5.0,  # 5x the profile floor (flight to safety)
}

# Maximum single-asset weight cap
_MAX_SINGLE_ASSET_WEIGHT = 0.35

# Drift threshold for rebalance trigger
_DRIFT_THRESHOLD = 0.05

# Kelly fraction cap — never bet more than half-Kelly for safety
_KELLY_FRACTION_CAP = 0.5

# Minimum Sharpe to receive Kelly allocation
_MIN_SHARPE_FOR_KELLY = 0.3


@dataclass(frozen=True)
class StrategyScore:
    """Scored strategy for portfolio construction."""

    strategy_id: str
    symbol: str  # Primary synth token
    sharpe: float
    volatility: float  # Annualized
    kelly_fraction: float  # Optimal Kelly bet size
    risk_parity_weight: float  # Inverse-vol weight
    composite_score: float  # Final blended score


class KellyRiskParityConstructor:
    """Kelly Criterion + Risk-Parity portfolio constructor.

    Algorithm:
      1. Score each strategy: Kelly fraction from Sharpe + variance
      2. Risk-parity: weight inversely proportional to strategy vol
      3. Blend Kelly (60%) and risk-parity (40%) for final weights
      4. Apply regime-aware deleveraging to compute USDC floor
      5. Scale synth weights to (1 - USDC_floor)
      6. Cap single-asset exposure at MAX_SINGLE_ASSET_WEIGHT
      7. Normalize weights to sum to 1.0
    """

    def construct(
        self,
        current: Portfolio,
        strategies: list[Strategy],
        regime: RegimeClassification,
        risk_profile: RiskProfile = RiskProfile.MODERATE,
        backtest_results: dict[str, BacktestResult] | None = None,
        usdc_address: str | None = None,
        synth_addresses: dict[str, str] | None = None,
    ) -> list[TargetAllocation]:
        """Build target allocations using Kelly + risk-parity.

        Args:
            current: Current portfolio state.
            strategies: Active strategies.
            regime: Current regime classification.
            risk_profile: User's risk profile.
            backtest_results: Map of strategy_id → BacktestResult.
                If None, falls back to stub metrics.
            usdc_address: USDC token address (auto-loaded if None).
            synth_addresses: Synth token addresses (auto-loaded if None).

        Returns:
            List of TargetAllocation with weights summing to ~1.0.
        """
        params = RISK_PROFILE_PARAMS.get(risk_profile, RISK_PROFILE_PARAMS[RiskProfile.MODERATE])

        # ── Step 1: Score each strategy ────────────────────────
        scores = self._score_strategies(strategies, backtest_results)

        if not scores:
            logger.warning("No strategy scores — falling back to equal weight")
            return self._equal_weight_fallback(
                current, strategies, regime, risk_profile, usdc_address or "", synth_addresses
            )

        # ── Step 2: Compute USDC floor with regime deleveraging ──
        usdc_floor = self._compute_usdc_floor(regime, params)
        synth_budget = max(0.0, 1.0 - usdc_floor)

        logger.info(
            "Kelly/RP: USDC floor=%.1f%% (regime=%s, profile=%s, base=%.1f%%)",
            usdc_floor * 100,
            regime.regime.value,
            risk_profile.value,
            params["usyc_floor"] * 100,
        )

        # ── Step 3: Compute Kelly fractions ─────────────────────
        kelly_weights = self._kelly_weights(scores)

        # ── Step 4: Compute risk-parity weights ─────────────────
        rp_weights = self._risk_parity_weights(scores)

        # ── Step 5: Blend Kelly + risk-parity (60/40) ───────────
        blended = self._blend_weights(kelly_weights, rp_weights)

        # ── Step 6: Scale to synth budget ───────────────────────
        scaled = {sym: w * synth_budget for sym, w in blended.items()}

        # ── Step 7: Cap single-asset exposure ───────────────────
        capped = self._cap_weights(scaled, _MAX_SINGLE_ASSET_WEIGHT)

        # ── Step 8: Normalize ───────────────────────────────────
        total = sum(capped.values()) + usdc_floor
        if total > 0 and abs(total - 1.0) > 0.001:
            for sym in capped:
                capped[sym] = capped[sym] / total
            usdc_floor = usdc_floor / total

        # Resolve addresses
        if usdc_address is None or synth_addresses is None:
            from archimedes.chain.client import chain_client

            usdc_address = usdc_address or chain_client.settings.usdc_address
            synth_addresses = synth_addresses or chain_client.settings.synth_addresses

        # ── Build allocations ───────────────────────────────────
        allocations: list[TargetAllocation] = []

        # USDC
        allocations.append(
            TargetAllocation(
                symbol="USDC",
                token_address=usdc_address,
                weight=round(usdc_floor, 4),
                strategy_ids=[s.strategy_id for s in scores],
            )
        )

        # Synth tokens
        score_by_sym = {s.symbol: s for s in scores}

        for sym, weight in sorted(capped.items(), key=lambda x: -x[1]):
            if weight < 0.001:
                continue
            addr = synth_addresses.get(sym, "")
            score = score_by_sym.get(sym)
            allocations.append(
                TargetAllocation(
                    symbol=sym,
                    token_address=addr,
                    weight=round(weight, 4),
                    strategy_ids=[score.strategy_id] if score else [],
                )
            )

        logger.info(
            "Kelly/RP allocations: %s",
            " | ".join(f"{a.symbol}={a.weight:.0%}" for a in allocations),
        )
        return allocations

    def score_strategy(
        self,
        strategy: Strategy,
        result: BacktestResult | None = None,
        risk_profile: RiskProfile = RiskProfile.MODERATE,
    ) -> float:
        """Score a single strategy for a given risk profile.

        Higher = better fit. Blends:
          - Sharpe ratio (normalized)
          - Risk-parity weight (inverse-vol contribution)
          - Profile compatibility (does strategy match the profile's vol target?)
        """
        if result is not None:
            sharpe = result.sharpe_ratio
            max_dd = result.max_drawdown
            cagr = result.cagr
        else:
            sharpe = strategy.stub_sharpe or 0.0
            max_dd = strategy.stub_max_dd or 0.5
            cagr = strategy.stub_cagr or 0.0

        if sharpe <= 0:
            return 0.0

        params = RISK_PROFILE_PARAMS.get(risk_profile, RISK_PROFILE_PARAMS[RiskProfile.MODERATE])

        # Normalize Sharpe to [0, 1] range (cap at 3.0)
        sharpe_score = min(sharpe / 3.0, 1.0)

        # Drawdown penalty: penalize if DD exceeds profile's max
        dd_penalty = 1.0 if max_dd <= params["max_drawdown"] else (params["max_drawdown"] / max_dd)

        # CAGR bonus: reward positive returns
        cagr_score = min(max(cagr, 0.0) / 0.5, 1.0)  # 50% CAGR → 1.0

        # Composite: 50% Sharpe, 25% DD penalty, 25% CAGR
        return sharpe_score * 0.50 + dd_penalty * 0.25 + cagr_score * 0.25

    # ─── Private scoring methods ────────────────────────────────

    def _score_strategies(
        self,
        strategies: list[Strategy],
        backtest_results: dict[str, BacktestResult] | None,
    ) -> list[StrategyScore]:
        """Score each strategy using Kelly and risk-parity metrics."""
        scores: list[StrategyScore] = []

        for s in strategies:
            result = backtest_results.get(s.id) if backtest_results else None

            # Get metrics from backtest or stubs
            if result is not None:
                sharpe = result.sharpe_ratio
                # Estimate annualized vol from max_dd and Sharpe
                # vol ≈ max_dd / z_score ≈ max_dd / 2.0 (rough)
                vol = result.max_drawdown / 2.0 if result.max_drawdown > 0 else 0.15
            else:
                sharpe = s.stub_sharpe or 0.0
                # Estimate vol: if Sharpe = 1.0 and CAGR = 10%, vol ≈ 10%
                vol = (s.stub_cagr or 0.10) / max(sharpe, 0.1) if sharpe > 0 else 0.20

            if sharpe < _MIN_SHARPE_FOR_KELLY:
                continue  # Skip strategies with very weak signals

            # Map to primary synth symbol
            symbol = self._primary_synth(s)
            if not symbol:
                continue

            # Kelly fraction: f = (mu / sigma^2) * fraction_cap
            # Using Sharpe ≈ mu/sigma, so f ≈ Sharpe / sigma * fraction_cap
            kelly_f = min(
                (sharpe * vol) / (vol**2 + 1e-10) * _KELLY_FRACTION_CAP,
                1.0,
            )

            # Risk-parity weight: proportional to 1/vol
            rp_w = 1.0 / (vol + 1e-6)

            scores.append(
                StrategyScore(
                    strategy_id=s.id,
                    symbol=symbol,
                    sharpe=sharpe,
                    volatility=vol,
                    kelly_fraction=kelly_f,
                    risk_parity_weight=rp_w,
                    composite_score=0.0,  # Set after all scores computed
                )
            )

        # Normalize risk-parity weights to sum to 1.0
        if scores:
            total_rp = sum(s.risk_parity_weight for s in scores)
            for s in scores:
                s_new = StrategyScore(
                    strategy_id=s.strategy_id,
                    symbol=s.symbol,
                    sharpe=s.sharpe,
                    volatility=s.volatility,
                    kelly_fraction=s.kelly_fraction,
                    risk_parity_weight=s.risk_parity_weight / total_rp,
                    composite_score=s.sharpe * 0.6 + (s.risk_parity_weight / total_rp) * 0.4,
                )
                scores[scores.index(s)] = s_new

        return scores

    def _kelly_weights(self, scores: list[StrategyScore]) -> dict[str, float]:
        """Compute Kelly Criterion weights for each strategy's asset."""
        weights: dict[str, float] = {}

        # Aggregate Kelly fractions by symbol (multiple strategies → same asset)
        for s in scores:
            if s.symbol not in weights:
                weights[s.symbol] = 0.0
            weights[s.symbol] += s.kelly_fraction

        # Normalize to sum to 1.0
        total = sum(weights.values())
        if total > 0:
            weights = {sym: w / total for sym, w in weights.items()}

        return weights

    def _risk_parity_weights(self, scores: list[StrategyScore]) -> dict[str, float]:
        """Compute risk-parity (inverse-vol) weights by symbol."""
        weights: dict[str, float] = {}

        for s in scores:
            if s.symbol not in weights:
                weights[s.symbol] = s.risk_parity_weight
            else:
                # Sum inverse-vol contributions
                weights[s.symbol] += s.risk_parity_weight

        # Normalize
        total = sum(weights.values())
        if total > 0:
            weights = {sym: w / total for sym, w in weights.items()}

        return weights

    def _blend_weights(
        self,
        kelly: dict[str, float],
        risk_parity: dict[str, float],
        kelly_weight: float = 0.6,
    ) -> dict[str, float]:
        """Blend Kelly and risk-parity weights."""
        all_symbols = set(kelly.keys()) | set(risk_parity.keys())
        blended: dict[str, float] = {}

        for sym in all_symbols:
            k = kelly.get(sym, 0.0)
            r = risk_parity.get(sym, 0.0)
            blended[sym] = kelly_weight * k + (1 - kelly_weight) * r

        # Normalize
        total = sum(blended.values())
        if total > 0:
            blended = {sym: w / total for sym, w in blended.items()}

        return blended

    def _compute_usdc_floor(self, regime: RegimeClassification, params: dict) -> float:
        """Compute USDC floor with regime-aware deleveraging."""
        base_floor = params["usyc_floor"]
        factor = _REGIME_DELEVERAGE_FACTORS.get(regime.regime, 1.0)
        floor = base_floor * factor

        # Clamp to [0, 0.95]
        return min(max(floor, 0.0), 0.95)

    def _cap_weights(self, weights: dict[str, float], cap: float) -> dict[str, float]:
        """Cap individual weights and redistribute excess iteratively."""
        capped = dict(weights)

        # Iterate until all weights are within cap (max 10 rounds to prevent infinite loop)
        for _ in range(10):
            excess = 0.0
            uncapped_syms: list[str] = []

            for sym, w in capped.items():
                if w > cap:
                    excess += w - cap
                    capped[sym] = cap
                else:
                    uncapped_syms.append(sym)

            if excess < 1e-10 or not uncapped_syms:
                break

            # Redistribute excess proportionally to uncapped symbols
            uncapped_total = sum(capped[s] for s in uncapped_syms)
            if uncapped_total > 0:
                for sym in uncapped_syms:
                    capped[sym] += excess * (capped[sym] / uncapped_total)

        # Final clamp to ensure no floating-point overshoot
        capped = {sym: min(w, cap) for sym, w in capped.items()}
        return capped

    def _primary_synth(self, strategy: Strategy) -> str | None:
        """Map strategy's primary asset to a synth symbol."""
        mapping = {
            "SPY": "sSPY",
            "TSLA": "sTSLA",
            "NVDA": "sNVDA",
            "BTC": "sBTC",
            "GOLD": "sGOLD",
            "OIL": "sOIL",
            "NIKKEI": "sNKY",
            "TREASURY": "sGOLD",
        }
        if strategy.asset_universe:
            for a in strategy.asset_universe:
                s = mapping.get(a)
                if s:
                    return s
        return None

    def _equal_weight_fallback(
        self,
        current: Portfolio,
        strategies: list[Strategy],
        regime: RegimeClassification,
        risk_profile: RiskProfile,
        usdc_address: str = "",
        synth_addresses: dict[str, str] | None = None,
    ) -> list[TargetAllocation]:
        """Fallback to equal weight when no strategy scores are available."""
        params = RISK_PROFILE_PARAMS.get(risk_profile, RISK_PROFILE_PARAMS[RiskProfile.MODERATE])
        usdc_floor = self._compute_usdc_floor(regime, params)
        synth_budget = 1.0 - usdc_floor

        # Default synths
        synths = ["sSPY", "sTSLA", "sNVDA", "sBTC", "sGOLD"][:3]
        per_asset = synth_budget / len(synths)

        allocations: list[TargetAllocation] = [
            TargetAllocation(
                symbol="USDC",
                token_address=usdc_address,
                weight=round(usdc_floor, 4),
                strategy_ids=[s.id for s in strategies],
            )
        ]

        if synth_addresses is None:
            synth_addresses = {}
        for sym in synths:
            allocations.append(
                TargetAllocation(
                    symbol=sym,
                    token_address=synth_addresses.get(sym, ""),
                    weight=round(per_asset, 4),
                )
            )

        return allocations

    # ─── Trade computation (inherited from v1) ─────────────────

    def compute_trades(
        self,
        current: Portfolio,
        targets: list[TargetAllocation],
    ) -> list[TradeOrder]:
        """Diff current holdings vs target allocations → trade list."""
        current_weights = current.weights_dict
        target_weights = {t.symbol: t for t in targets}

        trades: list[TradeOrder] = []
        all_symbols = set(target_weights.keys()) | set(current_weights.keys())

        for sym in all_symbols:
            current_w = current_weights.get(sym, 0.0)
            target = target_weights.get(sym)
            target_w = target.weight if target else 0.0
            token_addr = target.token_address if target else ""

            drift = target_w - current_w
            if abs(drift) < _DRIFT_THRESHOLD:
                continue

            usdc_value = abs(drift) * current.total_value_usdc
            direction = TradeDirection.BUY if drift > 0 else TradeDirection.SELL

            trades.append(
                TradeOrder(
                    symbol=sym,
                    token_address=token_addr,
                    direction=direction,
                    amount=round(usdc_value, 6),
                    estimated_usdc_value=round(usdc_value, 2),
                )
            )

        return trades

    def should_trigger_rebalance(
        self,
        current: Portfolio,
        targets: list[TargetAllocation],
        regime: RegimeClassification,
        last_rebalance: float | None = None,
    ) -> str | None:
        """Check if rebalance is needed."""
        if regime.regime_changed:
            return "regime_change"

        current_weights = current.weights_dict
        target_weights = {t.symbol: t.weight for t in targets}

        for sym in set(current_weights.keys()) | set(target_weights.keys()):
            drift = abs(target_weights.get(sym, 0.0) - current_weights.get(sym, 0.0))
            if drift > _DRIFT_THRESHOLD:
                return "drift"

        if last_rebalance is None:
            return "calendar"

        return None

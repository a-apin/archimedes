"""Regime detector — classifies market regime from live signals.

Implements the heuristic classifier described in design.md § 4.3.3.
VIX thresholds + S&P MA positioning → one of four Regime states.

Owner: Önder owns the math; this is a v1 heuristic until the full
statistical classifier lands from his lane.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from archimedes.models.asset import MarketSnapshot
from archimedes.models.regime import (
    Regime,
    RegimeClassification,
    RegimeSignals,
)

logger = logging.getLogger(__name__)

# VIX thresholds — empirically reasonable for hackathon demo
_VIX_RISK_ON = 18.0  # Below this → risk-on
_VIX_TRANSITION = 25.0  # Between risk-on and this → transition
_VIX_CRISIS = 35.0  # Above this → crisis


class RegimeDetector:
    """Classify market regime from VIX + S&P moving-average signals."""

    def __init__(self, previous_regime: Regime | None = None) -> None:
        self._previous_regime = previous_regime

    def classify(self, snapshot: MarketSnapshot) -> RegimeClassification:
        """Produce a RegimeClassification from a MarketSnapshot.

        Logic (v1 heuristic):
          1. Base regime from VIX level:
             - VIX < 18 → RISK_ON
             - 18 ≤ VIX < 25 → TRANSITION
             - 25 ≤ VIX < 35 → RISK_OFF
             - VIX ≥ 35 → CRISIS
          2. If S&P below both MAs → nudge one step more defensive
          3. If S&P above both MAs and VIX < 18 → confirm RISK_ON
        """
        vix = snapshot.vix or 20.0  # default to transition if missing
        sp500_price = snapshot.prices.get("sSPY", 0)
        sp500_ma50 = snapshot.sp500_ma50
        sp500_ma200 = snapshot.sp500_ma200

        # Base regime from VIX
        if vix < _VIX_RISK_ON:
            regime = Regime.RISK_ON
        elif vix < _VIX_TRANSITION:
            regime = Regime.TRANSITION
        elif vix < _VIX_CRISIS:
            regime = Regime.RISK_OFF
        else:
            regime = Regime.CRISIS

        # Nudge based on S&P MA positioning
        if sp500_ma50 and sp500_ma200 and sp500_price > 0:
            below_both = sp500_price < sp500_ma50 and sp500_price < sp500_ma200
            above_both = sp500_price > sp500_ma50 and sp500_price > sp500_ma200

            if below_both and regime == Regime.RISK_ON:
                regime = Regime.TRANSITION
            elif below_both and regime == Regime.TRANSITION:
                regime = Regime.RISK_OFF
            elif above_both and regime == Regime.TRANSITION:
                regime = Regime.RISK_ON

        # Confidence: how clear-cut is the signal?
        if regime == Regime.RISK_ON:
            confidence = max(0.5, min(1.0, (_VIX_RISK_ON - vix) / _VIX_RISK_ON + 0.5))
        elif regime == Regime.CRISIS:
            confidence = max(0.7, min(1.0, (vix - _VIX_CRISIS) / 20.0 + 0.7))
        else:
            # Transition / risk-off: moderate confidence by default
            confidence = 0.6

        # Build signals
        signals = RegimeSignals(
            vix_level=vix,
            vix_rate_of_change=0.0,  # Would need historical VIX; skip for v1
            sp500_above_ma50=sp500_price > sp500_ma50 if sp500_ma50 and sp500_price else True,
            sp500_above_ma200=sp500_price > sp500_ma200 if sp500_ma200 and sp500_price else True,
        )

        changed = self._previous_regime is not None and regime != self._previous_regime

        classification = RegimeClassification(
            regime=regime,
            confidence=round(confidence, 2),
            signals=signals,
            timestamp=datetime.now(UTC),
            previous_regime=self._previous_regime,
            regime_changed=changed,
        )

        self._previous_regime = regime
        logger.info(
            "Regime: %s (confidence=%.2f, changed=%s, VIX=%.1f)",
            regime.value,
            confidence,
            changed,
            vix,
        )
        return classification

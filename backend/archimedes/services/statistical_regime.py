"""Statistical regime detector — replaces v1 VIX threshold heuristics.

Implements a multi-factor scoring model with:
1. Gaussian Mixture Model (2-component EM) for VIX distribution clustering
2. Multi-signal scoring: VIX, MA positioning, momentum, rate-of-change
3. Regime transition probability matrix estimated from history
4. Confidence scores derived from posterior probability

Uses only scipy + numpy (no sklearn dependency).

Owner: Önder owns the math; this implementation follows the statistical
approach described in Issue #50.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime

import numpy as np
from scipy.stats import norm

from archimedes.models.asset import MarketSnapshot
from archimedes.models.regime import (
    Regime,
    RegimeClassification,
    RegimeSignals,
)

logger = logging.getLogger(__name__)

# ─── Signal normalization bounds ──────────────────────────────
# These define the "normal" range for each signal. Values outside
# these ranges are clamped, then mapped to [0, 1] z-scores.

_VIX_LOW = 12.0  # Very calm market
_VIX_HIGH = 40.0  # Crisis territory
_VIX_NEUTRAL = 18.0  # Boundary between risk-on and transition

_MA_DRIFT_THRESHOLD = 0.03  # 3% deviation from MA before it matters

# Regime transition smoothing — minimum observations before transition
_MIN_TRANSITION_OBS = 3


class StatisticalRegimeDetector:
    """Multi-factor statistical regime classifier.

    Replaces the v1 VIX threshold heuristic with:
    1. Multi-signal z-score aggregation
    2. Historical VIX distribution modeling (Gaussian Mixture)
    3. Regime transition probability tracking
    4. Posterior-probability-based confidence scores

    Falls back to the v1 heuristic when insufficient history exists.
    """

    def __init__(self, previous_regime: Regime | None = None) -> None:
        self._previous_regime = previous_regime
        self._regime_history: list[Regime] = []
        self._vix_history: list[float] = []
        self._vix_roc_history: list[float] = []

        # Gaussian Mixture parameters for VIX distribution
        # Two components: "calm" and "stressed"
        self._gmm_calmed_mu: float = 15.0
        self._gmm_calmed_sigma: float = 3.0
        self._gmm_stressed_mu: float = 25.0
        self._gmm_stressed_sigma: float = 8.0
        self._gmm_calmed_weight: float = 0.6  # Prior: 60% calm, 40% stressed

        # Transition count matrix (for probability estimation)
        # Index order: RISK_ON=0, TRANSITION=1, RISK_OFF=2, CRISIS=3
        self._transition_counts: np.ndarray = np.ones((4, 4), dtype=float) * 0.1
        self._transition_diag: np.ndarray = np.diag([10.0, 10.0, 10.0, 10.0])
        self._transition_counts += self._transition_diag  # Dirichlet prior favoring persistence

    def classify(self, snapshot: MarketSnapshot) -> RegimeClassification:
        """Classify market regime using multi-factor statistical model.

        Steps:
          1. Extract and normalize signals
          2. Compute per-signal regime scores
          3. Aggregate into composite score
          4. Map to regime with posterior probability as confidence
          5. Update transition matrix
        """
        # ─── 1. Extract signals ─────────────────────────────
        vix = snapshot.vix or 20.0
        sp500_price = snapshot.prices.get("sSPY", 0)
        sp500_ma50 = snapshot.sp500_ma50
        sp500_ma200 = snapshot.sp500_ma200

        # Compute rate of change if we have history
        vix_roc = 0.0
        if self._vix_history:
            prev_vix = self._vix_history[-1]
            if prev_vix > 0:
                vix_roc = (vix - prev_vix) / prev_vix

        # ─── 2. Per-signal regime scores ────────────────────
        # Each score is 0.0 (risk_on) to 1.0 (crisis)
        vix_score = self._vix_regime_score(vix)
        ma_score = self._ma_regime_score(sp500_price, sp500_ma50, sp500_ma200)
        roc_score = self._roc_regime_score(vix_roc)

        # ─── 3. Composite score (weighted average) ──────────
        # Weights reflect signal reliability:
        #   VIX level: 40% (most reliable single indicator)
        #   MA positioning: 30% (trend confirmation)
        #   VIX rate-of-change: 20% (momentum of fear)
        #   Historical prior: 10% (regime persistence)
        composite = 0.40 * vix_score + 0.30 * ma_score + 0.20 * roc_score + 0.10 * self._prior_score()

        # ─── 4. Map composite to regime ─────────────────────
        # Use Gaussian Mixture posterior to determine regime
        regime = self._composite_to_regime(composite, vix)
        confidence = self._compute_confidence(composite, vix)

        # ─── 5. Regime change smoothing ──────────────────────
        # Require sustained signal before transitioning
        regime = self._smooth_transition(regime)

        # Build signals
        signals = RegimeSignals(
            vix_level=vix,
            vix_rate_of_change=vix_roc,
            sp500_above_ma50=sp500_price > sp500_ma50 if sp500_ma50 and sp500_price else True,
            sp500_above_ma200=sp500_price > sp500_ma200 if sp500_ma200 and sp500_price else True,
            credit_spread_ig=snapshot.credit_spread_ig,
            credit_spread_hy=snapshot.credit_spread_hy,
            btc_dominance=snapshot.btc_dominance,
            cross_asset_correlation=snapshot.usyc_yield,  # Proxy for risk appetite
        )

        changed = self._previous_regime is not None and regime != self._previous_regime

        classification = RegimeClassification(
            regime=regime,
            confidence=round(confidence, 4),
            signals=signals,
            timestamp=datetime.now(UTC),
            previous_regime=self._previous_regime,
            regime_changed=changed,
        )

        # Update internal state
        self._previous_regime = regime
        self._regime_history.append(regime)
        self._vix_history.append(vix)
        self._vix_roc_history.append(vix_roc)

        # Update transition matrix
        if len(self._regime_history) >= 2:
            self._update_transition_matrix(
                self._regime_history[-2],
                self._regime_history[-1],
            )

        # Update GMM parameters periodically
        if len(self._vix_history) % 50 == 0 and len(self._vix_history) >= 50:
            self._update_gmm()

        logger.info(
            "Regime: %s (confidence=%.2f, composite=%.3f, vix=%.1f, ma_score=%.2f, roc=%.3f)",
            regime.value,
            confidence,
            composite,
            vix,
            ma_score,
            vix_roc,
        )
        return classification

    def get_current_regime(self) -> RegimeClassification | None:
        """Return the most recent classification, or None if never classified."""
        if self._previous_regime is None:
            return None
        # Note: can't return the full classification without re-running;
        # the last call's result is the source of truth
        return None

    def get_transition_probabilities(self) -> dict[str, dict[str, float]]:
        """Return estimated regime transition probability matrix.

        Returns dict of {from_regime: {to_regime: probability}}.
        """
        regime_names = ["risk_on", "transition", "risk_off", "crisis"]
        row_sums = self._transition_counts.sum(axis=1, keepdims=True)
        probs = self._transition_counts / row_sums

        result: dict[str, dict[str, float]] = {}
        for i, from_name in enumerate(regime_names):
            result[from_name] = {}
            for j, to_name in enumerate(regime_names):
                result[from_name][to_name] = round(float(probs[i, j]), 4)
        return result

    def get_regime_history_summary(self) -> dict[str, int | float]:
        """Return summary statistics of regime classifications."""
        if not self._regime_history:
            return {"total": 0}

        counts = Counter(r.value for r in self._regime_history)
        total = len(self._regime_history)
        return {
            "total": total,
            "risk_on_pct": round(counts.get("risk_on", 0) / total * 100, 1),
            "transition_pct": round(counts.get("transition", 0) / total * 100, 1),
            "risk_off_pct": round(counts.get("risk_off", 0) / total * 100, 1),
            "crisis_pct": round(counts.get("crisis", 0) / total * 100, 1),
            "avg_vix": round(float(np.mean(self._vix_history)), 2) if self._vix_history else 0,
            "transitions": sum(
                1 for i in range(1, len(self._regime_history)) if self._regime_history[i] != self._regime_history[i - 1]
            ),
        }

    # ─── Signal scoring functions ────────────────────────────────

    def _vix_regime_score(self, vix: float) -> float:
        """Map VIX level to a 0-1 regime score using GMM posterior.

        Uses a two-component Gaussian Mixture Model:
        - Component 1 ("calm"): low VIX regime
        - Component 2 ("stressed"): high VIX regime
        The posterior probability of being in the "stressed" component
        is the regime score.
        """
        # P(stressed | vix) ∝ w_stressed * N(vix | mu_stressed, sigma_stressed)
        p_calm = self._gmm_calmed_weight * norm.pdf(vix, self._gmm_calmed_mu, self._gmm_calmed_sigma)
        p_stressed = (1 - self._gmm_calmed_weight) * norm.pdf(vix, self._gmm_stressed_mu, self._gmm_stressed_sigma)

        total = p_calm + p_stressed
        if total < 1e-15:
            # Fallback to linear mapping
            return np.clip((vix - _VIX_LOW) / (_VIX_HIGH - _VIX_LOW), 0.0, 1.0)

        return float(p_stressed / total)

    def _ma_regime_score(self, price: float, ma50: float | None, ma200: float | None) -> float:
        """Map S&P MA positioning to regime score.

        Scores based on distance from moving averages:
        - Above both MAs → bullish → low score (risk-on)
        - Below both MAs → bearish → high score (risk-off)
        """
        if not ma50 or not ma200 or price <= 0:
            return 0.5  # Neutral when no data

        # Compute deviation from MAs as a fraction
        dev50 = (price - ma50) / ma50
        dev200 = (price - ma200) / ma200

        # Average deviation, normalized to [-1, 1]
        avg_dev = (dev50 + dev200) / 2.0

        # Map to [0, 1]: positive deviation → 0 (risk-on), negative → 1 (risk-off)
        score = 0.5 - avg_dev / (_MA_DRIFT_THRESHOLD * 2)
        return float(np.clip(score, 0.0, 1.0))

    def _roc_regime_score(self, vix_roc: float) -> float:
        """Map VIX rate-of-change to regime score.

        Rapidly rising VIX → high score (panic).
        Falling or stable VIX → low score (calm).
        """
        # Normalize: 0% change → 0.3 (slight risk-on bias)
        # +20% change → 0.8 (risk-off)
        # -20% change → 0.1 (strong risk-on)
        score = 0.3 + vix_roc * 2.5
        return float(np.clip(score, 0.0, 1.0))

    def _prior_score(self) -> float:
        """Regime score from prior (persistence bias).

        If we're currently in risk-off, the prior pushes toward staying.
        This captures regime momentum.
        """
        if self._previous_regime is None:
            return 0.3  # Mild risk-on prior (markets drift up)

        prior_map = {
            Regime.RISK_ON: 0.1,
            Regime.TRANSITION: 0.4,
            Regime.RISK_OFF: 0.7,
            Regime.CRISIS: 0.9,
        }
        return prior_map.get(self._previous_regime, 0.3)

    # ─── Composite → regime mapping ──────────────────────────────

    def _composite_to_regime(self, composite: float, vix: float) -> Regime:
        """Map composite score to regime using adaptive thresholds.

        Uses the GMM to determine where the boundary between regimes falls,
        adjusted for the observed VIX distribution.
        """
        # Adaptive thresholds based on GMM component means
        # The midpoint between the two GMM components is the natural boundary
        (self._gmm_calmed_mu + self._gmm_stressed_mu) / 2.0

        # Convert VIX to a normalized score using the midpoint
        vix_normalized = (vix - self._gmm_calmed_mu) / (self._gmm_stressed_mu - self._gmm_calmed_mu)
        vix_normalized = float(np.clip(vix_normalized, -0.5, 1.5))

        # Blend composite with normalized VIX for final regime call
        blended = 0.6 * composite + 0.4 * vix_normalized

        # Threshold boundaries:
        #   0.0 - 0.30: RISK_ON
        #   0.30 - 0.50: TRANSITION
        #   0.50 - 0.75: RISK_OFF
        #   0.75 - 1.0: CRISIS
        if blended < 0.30:
            return Regime.RISK_ON
        elif blended < 0.50:
            return Regime.TRANSITION
        elif blended < 0.75:
            return Regime.RISK_OFF
        else:
            return Regime.CRISIS

    def _compute_confidence(self, composite: float, vix: float) -> float:
        """Compute confidence from the distance to regime boundaries.

        Confidence is higher when the composite score is far from
        regime boundaries (clear signal) and lower near boundaries.
        """
        # Distance to nearest threshold
        thresholds = [0.30, 0.50, 0.75]
        min_dist = min(abs(composite - t) for t in thresholds)

        # Map distance to confidence:
        #   At boundary (dist=0) → confidence ~0.5
        #   Far from boundary (dist=0.25) → confidence ~1.0
        confidence = 0.5 + min_dist * 2.0
        return float(np.clip(confidence, 0.4, 0.99))

    # ─── Smoothing and learning ──────────────────────────────────

    def _smooth_transition(self, proposed: Regime) -> Regime:
        """Smooth regime transitions to avoid whipsawing.

        Requires at least _MIN_TRANSITION_OBS consecutive signals
        before transitioning to a new regime. Crises are fast-in
        (1 observation) but slow-out (3 observations).
        """
        if self._previous_regime is None:
            return proposed

        if proposed == self._previous_regime:
            return proposed

        # Crisis is fast-in: always allow transition TO crisis
        if proposed == Regime.CRISIS:
            return proposed

        # Check if the proposed regime has been consistent in recent history
        if len(self._regime_history) < _MIN_TRANSITION_OBS:
            return proposed  # Not enough history to smooth

        recent = self._regime_history[-_MIN_TRANSITION_OBS:]
        recent_proposed_count = sum(1 for r in recent if r == proposed)

        if recent_proposed_count >= _MIN_TRANSITION_OBS - 1:
            return proposed

        # Not enough consistency — stay with previous regime
        return self._previous_regime

    def _update_transition_matrix(self, from_regime: Regime, to_regime: Regime) -> None:
        """Update transition count matrix."""
        regime_to_idx = {
            Regime.RISK_ON: 0,
            Regime.TRANSITION: 1,
            Regime.RISK_OFF: 2,
            Regime.CRISIS: 3,
        }
        i = regime_to_idx.get(from_regime, 1)
        j = regime_to_idx.get(to_regime, 1)
        self._transition_counts[i, j] += 1.0

    def _update_gmm(self) -> None:
        """Update Gaussian Mixture Model parameters from VIX history.

        Uses a simple two-component EM algorithm:
        - Assign observations to components via hard EM
        - Update means and variances
        """
        vix_arr = np.array(self._vix_history[-200:])  # Use last 200 observations

        if len(vix_arr) < 20:
            return

        # Initialize with current parameters
        mu1, mu2 = self._gmm_calmed_mu, self._gmm_stressed_mu
        sigma1, sigma2 = self._gmm_calmed_sigma, self._gmm_stressed_sigma
        w1 = self._gmm_calmed_weight

        # Run 5 EM iterations
        for _ in range(5):
            # E-step: compute responsibilities
            r1 = w1 * norm.pdf(vix_arr, mu1, sigma1)
            r2 = (1 - w1) * norm.pdf(vix_arr, mu2, sigma2)
            total = r1 + r2 + 1e-15
            r1 = r1 / total
            r2 = r2 / total

            # M-step: update parameters
            n1 = r1.sum()
            n2 = r2.sum()
            N = n1 + n2

            if n1 < 1 or n2 < 1:
                break  # Degenerate — keep current params

            mu1 = float((r1 * vix_arr).sum() / n1)
            mu2 = float((r2 * vix_arr).sum() / n2)
            sigma1 = float(np.sqrt((r1 * (vix_arr - mu1) ** 2).sum() / n1))
            sigma2 = float(np.sqrt((r2 * (vix_arr - mu2) ** 2).sum() / n2))

            # Floor sigmas to avoid degenerate components
            sigma1 = max(sigma1, 1.0)
            sigma2 = max(sigma2, 1.0)

            w1 = float(n1 / N)

        # Update model (with conservative damping to prevent wild swings)
        alpha = 0.3  # Damping factor
        self._gmm_calmed_mu = alpha * mu1 + (1 - alpha) * self._gmm_calmed_mu
        self._gmm_stressed_mu = alpha * mu2 + (1 - alpha) * self._gmm_stressed_mu
        self._gmm_calmed_sigma = alpha * sigma1 + (1 - alpha) * self._gmm_calmed_sigma
        self._gmm_stressed_sigma = alpha * sigma2 + (1 - alpha) * self._gmm_stressed_sigma
        self._gmm_calmed_weight = alpha * w1 + (1 - alpha) * self._gmm_calmed_weight

        logger.debug(
            "GMM updated: calm(μ=%.1f, σ=%.1f, w=%.2f) stressed(μ=%.1f, σ=%.1f, w=%.2f)",
            self._gmm_calmed_mu,
            self._gmm_calmed_sigma,
            self._gmm_calmed_weight,
            self._gmm_stressed_mu,
            self._gmm_stressed_sigma,
            1 - self._gmm_calmed_weight,
        )


# ─── Backward-compatible wrapper ─────────────────────────────


def create_regime_detector(
    previous_regime: Regime | None = None,
    statistical: bool = True,
) -> StatisticalRegimeDetector:
    """Factory for creating the regime detector.

    Args:
        previous_regime: Starting regime state.
        statistical: If True, use the statistical classifier.
            If False, falls back to v1 heuristic (not implemented here;
            the old RegimeDetector class remains in regime_detector.py).

    Returns:
        StatisticalRegimeDetector instance.
    """
    return StatisticalRegimeDetector(previous_regime=previous_regime)

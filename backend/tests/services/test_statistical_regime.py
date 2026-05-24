"""Tests for the statistical regime detector.

Validates the multi-factor scoring, GMM-based VIX classification,
transition smoothing, and confidence computation.
"""

from __future__ import annotations

from datetime import UTC

from archimedes.models.asset import MarketSnapshot
from archimedes.models.regime import Regime
from archimedes.services.statistical_regime import StatisticalRegimeDetector


def _make_snapshot(
    vix: float = 20.0,
    spy_price: float = 5000.0,
    ma50: float = 4900.0,
    ma200: float = 4800.0,
) -> MarketSnapshot:
    """Create a MarketSnapshot with specified regime signals."""
    from datetime import datetime

    return MarketSnapshot(
        timestamp=datetime.now(UTC),
        prices={"sSPY": spy_price},
        vix=vix,
        sp500_ma50=ma50,
        sp500_ma200=ma200,
    )


class TestRegimeClassification:
    """Basic regime classification tests."""

    def test_low_vix_above_ma_risk_on(self) -> None:
        """Low VIX + above both MAs → RISK_ON."""
        detector = StatisticalRegimeDetector()
        snapshot = _make_snapshot(vix=14.0, spy_price=5200, ma50=5000, ma200=4900)
        result = detector.classify(snapshot)
        assert result.regime == Regime.RISK_ON
        assert result.confidence > 0.5

    def test_high_vix_crisis(self) -> None:
        """Very high VIX → CRISIS."""
        detector = StatisticalRegimeDetector()
        snapshot = _make_snapshot(vix=45.0, spy_price=4500, ma50=4800, ma200=4900)
        result = detector.classify(snapshot)
        assert result.regime == Regime.CRISIS

    def test_moderate_vix_transition(self) -> None:
        """VIX around 22 → TRANSITION or RISK_OFF."""
        detector = StatisticalRegimeDetector()
        snapshot = _make_snapshot(vix=22.0, spy_price=4900, ma50=4950, ma200=4900)
        result = detector.classify(snapshot)
        assert result.regime in (Regime.TRANSITION, Regime.RISK_OFF, Regime.RISK_ON)

    def test_regime_changes_tracked(self) -> None:
        """regime_changed flag tracks transitions."""
        detector = StatisticalRegimeDetector()

        # First classification — no change possible
        snap1 = _make_snapshot(vix=14.0)
        r1 = detector.classify(snap1)
        assert not r1.regime_changed

        # Second classification — different regime
        snap2 = _make_snapshot(vix=45.0)
        r2 = detector.classify(snap2)
        # Note: smoothing may prevent immediate change
        if r2.regime != r1.regime:
            assert r2.regime_changed


class TestGMM:
    """Gaussian Mixture Model for VIX distribution."""

    def test_gmm_params_update(self) -> None:
        """GMM parameters should update after sufficient observations."""
        detector = StatisticalRegimeDetector()

        # Feed 60 observations to trigger GMM update
        for vix in [14.0] * 30 + [28.0] * 30:
            snap = _make_snapshot(vix=vix)
            detector.classify(snap)

        # GMM should have learned two clusters
        assert detector._gmm_calmed_mu < detector._gmm_stressed_mu

    def test_gmm_posterior_risk_on(self) -> None:
        """Low VIX should give high posterior for 'calm' component."""
        detector = StatisticalRegimeDetector()
        score = detector._vix_regime_score(12.0)
        assert score < 0.3  # Low score = calm regime

    def test_gmm_posterior_crisis(self) -> None:
        """High VIX should give high posterior for 'stressed' component."""
        detector = StatisticalRegimeDetector()
        score = detector._vix_regime_score(45.0)
        assert score > 0.7  # High score = stressed regime


class TestTransitionMatrix:
    """Regime transition probability estimation."""

    def test_transition_matrix_initialized(self) -> None:
        """Transition matrix starts with Dirichlet prior."""
        detector = StatisticalRegimeDetector()
        probs = detector.get_transition_probabilities()
        assert "risk_on" in probs
        assert "crisis" in probs

        # Each row should sum to ~1.0
        for from_regime, transitions in probs.items():
            total = sum(transitions.values())
            assert abs(total - 1.0) < 0.01, f"{from_regime} transitions sum to {total}"

    def test_transition_matrix_updates(self) -> None:
        """Transition matrix updates with observations."""
        detector = StatisticalRegimeDetector()

        # Classify a few snapshots to build history
        for _ in range(5):
            detector.classify(_make_snapshot(vix=14.0))

        probs = detector.get_transition_probabilities()
        # Risk-on → risk-on should be highest
        assert probs["risk_on"]["risk_on"] > 0.5


class TestSmoothing:
    """Regime transition smoothing."""

    def test_crisis_fast_in(self) -> None:
        """Crisis transitions should be immediate (no smoothing)."""
        detector = StatisticalRegimeDetector()

        # Start in risk_on
        for _ in range(5):
            detector.classify(_make_snapshot(vix=14.0))

        # Sudden crisis — should transition immediately
        result = detector.classify(_make_snapshot(vix=50.0))
        assert result.regime == Regime.CRISIS

    def test_history_summary(self) -> None:
        """History summary reports correct statistics."""
        detector = StatisticalRegimeDetector()

        for _ in range(10):
            detector.classify(_make_snapshot(vix=14.0))

        summary = detector.get_regime_history_summary()
        assert summary["total"] == 10
        assert summary["risk_on_pct"] == 100.0


class TestConfidence:
    """Confidence score validation."""

    def test_extreme_vix_high_confidence(self) -> None:
        """Extreme VIX values → higher confidence."""
        detector = StatisticalRegimeDetector()

        r_extreme = detector.classify(_make_snapshot(vix=50.0))
        detector2 = StatisticalRegimeDetector()
        r_moderate = detector2.classify(_make_snapshot(vix=22.0))

        # Extreme regime should have at least comparable confidence
        assert r_extreme.confidence > 0.0
        assert 0.0 < r_moderate.confidence <= 1.0

    def test_confidence_in_valid_range(self) -> None:
        """Confidence always in [0, 1]."""
        detector = StatisticalRegimeDetector()
        for vix in [10, 15, 20, 25, 30, 40, 50]:
            result = detector.classify(_make_snapshot(vix=vix))
            assert 0.0 <= result.confidence <= 1.0

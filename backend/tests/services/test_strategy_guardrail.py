"""Hermetic unit tests for strategy_guardrail — no network, no Redis, no DB."""

from __future__ import annotations

import pytest
from archimedes.agents.strategy_architect import ArchitectProposal, StrategySelection
from archimedes.services.strategy_guardrail import (
    DEFAULT_MAX_STRATEGY_WEIGHT,
    GuardrailResult,
    apply_guardrail,
)


def _proposal(
    raw_weights: dict[str, float],
    risk_profile: str = "moderate",
) -> ArchitectProposal:
    selected = [StrategySelection(strategy_id=sid, weight=w, rationale="test") for sid, w in raw_weights.items()]
    return ArchitectProposal(
        intent="test",
        risk_profile=risk_profile,
        capital_usdc=10000.0,
        regime=None,
        selected=selected,
        overall_reasoning="test",
        risk_notes="",
        model_id="test",
    )


# ─── GuardrailResult ─────────────────────────────────────────────


class TestGuardrailResult:
    def test_total_sums_to_one(self):
        result = GuardrailResult(
            strategy_weights={"s1": 0.5, "s2": 0.3},
            usyc_weight=0.2,
            risk_profile="moderate",
        )
        assert result.total == pytest.approx(1.0)

    def test_total_with_empty_strategies(self):
        result = GuardrailResult(
            strategy_weights={},
            usyc_weight=1.0,
            risk_profile="moderate",
        )
        assert result.total == pytest.approx(1.0)


# ─── apply_guardrail — happy path ────────────────────────────────


class TestApplyGuardrailHappyPath:
    def test_moderate_profile_reserves_usyc_floor(self):
        result = apply_guardrail(_proposal({"s1": 1.0}, "moderate"))
        assert result.usyc_weight >= 0.20
        assert result.total == pytest.approx(1.0, abs=1e-6)

    def test_conservative_profile_higher_floor(self):
        result = apply_guardrail(_proposal({"s1": 1.0}, "conservative"))
        assert result.usyc_weight >= 0.40
        assert result.total == pytest.approx(1.0, abs=1e-6)

    def test_aggressive_profile_lower_floor(self):
        result = apply_guardrail(_proposal({"s1": 1.0}, "aggressive"))
        assert result.usyc_weight >= 0.05
        assert result.total == pytest.approx(1.0, abs=1e-6)

    def test_hyper_risky_profile_minimal_floor(self):
        result = apply_guardrail(_proposal({"s1": 1.0}, "hyper_risky"))
        assert result.usyc_weight >= 0.0
        assert result.total == pytest.approx(1.0, abs=1e-6)

    def test_two_strategies_normalizes(self):
        result = apply_guardrail(_proposal({"s1": 0.6, "s2": 0.4}))
        assert result.total == pytest.approx(1.0, abs=1e-6)
        assert "s1" in result.strategy_weights
        assert "s2" in result.strategy_weights


# ─── apply_guardrail — cap enforcement ────────────────────────────


class TestApplyGuardrailCap:
    def test_single_strategy_capped_at_max(self):
        result = apply_guardrail(_proposal({"s1": 1.0}, "hyper_risky"))
        # With usyc_floor=0.0, investable=1.0, single strat gets 1.0 → capped at 0.30
        assert result.strategy_weights["s1"] <= DEFAULT_MAX_STRATEGY_WEIGHT + 1e-6
        assert result.total == pytest.approx(1.0, abs=1e-6)

    def test_many_strategies_each_under_cap(self):
        n = 10
        weights = {f"s{i}": 1.0 for i in range(n)}
        result = apply_guardrail(_proposal(weights, "hyper_risky"))
        for sid, w in result.strategy_weights.items():
            assert w <= DEFAULT_MAX_STRATEGY_WEIGHT + 1e-6, f"{sid} = {w}"
        assert result.total == pytest.approx(1.0, abs=1e-6)

    def test_spill_goes_to_usyc_when_all_capped(self):
        # 2 strategies, both get capped, excess → USYC
        result = apply_guardrail(_proposal({"s1": 1.0, "s2": 1.0}, "hyper_risky"))
        assert result.total == pytest.approx(1.0, abs=1e-6)
        assert result.usyc_weight > 0  # Some spill to USYC


# ─── apply_guardrail — edge cases ────────────────────────────────


class TestApplyGuardrailEdgeCases:
    def test_all_zero_weights_goes_full_usyc(self):
        result = apply_guardrail(_proposal({"s1": 0.0, "s2": 0.0}))
        assert result.strategy_weights == {}
        assert result.usyc_weight == 1.0
        assert len(result.dropped) == 2

    def test_negative_weights_dropped(self):
        result = apply_guardrail(_proposal({"s1": -0.5, "s2": 1.0}))
        assert "s1" in result.dropped
        assert "s2" in result.strategy_weights
        assert result.total == pytest.approx(1.0, abs=1e-6)

    def test_empty_weights(self):
        result = apply_guardrail(_proposal({}))
        assert result.usyc_weight == 1.0

    def test_very_small_weight_treated_as_zero(self):
        result = apply_guardrail(_proposal({"s1": 1e-12, "s2": 1.0}))
        assert "s1" in result.dropped

    def test_adjustments_logged(self):
        result = apply_guardrail(_proposal({"s1": -0.1, "s2": 1.0}))
        assert len(result.adjustments) > 0
        assert any("Dropped" in a for a in result.adjustments)

    def test_cap_adjustment_logged(self):
        result = apply_guardrail(_proposal({"s1": 1.0}, "hyper_risky"))
        assert any("Capped" in a or "USYC" in a for a in result.adjustments)


# ─── apply_guardrail — risk profile parameters ───────────────────


class TestRiskProfileParams:
    @pytest.mark.parametrize(
        "profile,floor_min",
        [
            ("conservative", 0.40),
            ("moderate", 0.20),
            ("aggressive", 0.05),
            ("hyper_risky", 0.00),
        ],
    )
    def test_usyc_floor_per_profile(self, profile, floor_min):
        result = apply_guardrail(_proposal({"s1": 1.0}, profile))
        assert result.usyc_weight >= floor_min - 1e-6
        assert result.risk_profile == profile


# ─── apply_guardrail — boundary conditions ────────────────────────


class TestApplyGuardrailBoundaries:
    def test_just_below_cap(self):
        weight = DEFAULT_MAX_STRATEGY_WEIGHT - 0.01
        result = apply_guardrail(_proposal({"s1": weight}, "hyper_risky"))
        assert result.strategy_weights["s1"] <= DEFAULT_MAX_STRATEGY_WEIGHT + 1e-6

    def test_exactly_at_cap(self):
        weight = DEFAULT_MAX_STRATEGY_WEIGHT
        result = apply_guardrail(_proposal({"s1": weight}, "hyper_risky"))
        # Single strategy gets all investable, capped at 0.30
        assert result.strategy_weights.get("s1", 0) <= weight + 1e-6

    def test_just_above_cap(self):
        weight = DEFAULT_MAX_STRATEGY_WEIGHT + 0.01
        result = apply_guardrail(_proposal({"s1": weight}, "hyper_risky"))
        assert result.strategy_weights["s1"] <= DEFAULT_MAX_STRATEGY_WEIGHT + 1e-6

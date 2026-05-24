"""Tests for the strategy DSL schema validator."""

from __future__ import annotations

import pytest
from archimedes.services.strategy_dsl import (
    FABER_2007_SPEC,
    REFERENCE_EXAMPLES,
    VOL_MANAGED_SPEC,
    DSLError,
    validate_strategy_spec,
)


class TestValidatesReferenceExamples:
    """All reference examples must pass schema validation."""

    @pytest.mark.parametrize("spec", REFERENCE_EXAMPLES, ids=lambda s: s["name"])
    def test_validates_reference_examples(self, spec):
        result = validate_strategy_spec(spec)
        assert result.name == spec["name"]
        assert result.asset_universe == spec["asset_universe"]
        assert result.look_ahead_safe is True

    def test_faber_spec(self):
        result = validate_strategy_spec(FABER_2007_SPEC)
        assert result.name == "SMA-200 Tactical Allocation"
        assert "SPY" in result.asset_universe
        assert result.rebalance_frequency == "monthly"
        assert result.entry == {"gt": ["close", "sma_200"]}
        assert result.exit == {"lt": ["close", "sma_200"]}
        assert "sma_200" in result.indicators

    def test_vol_managed_spec(self):
        result = validate_strategy_spec(VOL_MANAGED_SPEC)
        assert result.position_sizing["type"] == "volatility_target"
        assert result.position_sizing["annual_pct"] == 0.15


class TestValidationRejectsInvalidSpecs:
    """Invalid specs must be rejected with DSLError."""

    def test_missing_required_field(self):
        with pytest.raises(DSLError, match="missing required fields"):
            validate_strategy_spec({"name": "test"})

    def test_empty_name(self):
        spec = {**FABER_2007_SPEC, "name": ""}
        with pytest.raises(DSLError, match="name must be a non-empty string"):
            validate_strategy_spec(spec)

    def test_empty_asset_universe(self):
        spec = {**FABER_2007_SPEC, "asset_universe": []}
        with pytest.raises(DSLError, match="asset_universe must be a non-empty list"):
            validate_strategy_spec(spec)

    def test_invalid_rebalance_frequency(self):
        spec = {**FABER_2007_SPEC, "rebalance_frequency": "quarterly"}
        with pytest.raises(DSLError, match="rebalance_frequency"):
            validate_strategy_spec(spec)

    def test_look_ahead_unsafe_rejected(self):
        spec = {**FABER_2007_SPEC, "look_ahead_safe": False}
        with pytest.raises(DSLError, match="look_ahead_safe=false"):
            validate_strategy_spec(spec)

    def test_unknown_condition_operator(self):
        spec = {**FABER_2007_SPEC, "entry": {"xor": ["close", "sma_200"]}}
        with pytest.raises(DSLError, match="unknown operator"):
            validate_strategy_spec(spec)

    def test_invalid_position_sizing_type(self):
        spec = {**FABER_2007_SPEC, "position_sizing": {"type": "kelly"}}
        with pytest.raises(DSLError, match=r"position_sizing\.type"):
            validate_strategy_spec(spec)

    def test_volatility_target_missing_pct(self):
        spec = {**FABER_2007_SPEC, "position_sizing": {"type": "volatility_target"}}
        with pytest.raises(DSLError, match="annual_pct"):
            validate_strategy_spec(spec)

    def test_not_a_dict(self):
        with pytest.raises(DSLError, match="spec must be a JSON object"):
            validate_strategy_spec("not a dict")

    def test_and_needs_list(self):
        spec = {**FABER_2007_SPEC, "entry": {"and": "close"}}
        with pytest.raises(DSLError, match="'and' needs a list"):
            validate_strategy_spec(spec)

    def test_or_needs_two_conditions(self):
        spec = {**FABER_2007_SPEC, "entry": {"or": [{"gt": ["close", 1]}]}}
        with pytest.raises(DSLError, match="'or' needs a list of >= 2"):
            validate_strategy_spec(spec)


class TestConditionTree:
    """Test complex condition trees."""

    def test_nested_and_or(self):
        spec = {
            **FABER_2007_SPEC,
            "entry": {
                "and": [
                    {"gt": ["close", "sma_200"]},
                    {
                        "or": [
                            {"gt": ["rsi_14", 30]},
                            {"lt": ["realized_vol_20", 0.25]},
                        ]
                    },
                ],
            },
        }
        result = validate_strategy_spec(spec)
        assert "sma_200" in result.indicators
        assert "rsi_14" in result.indicators
        assert "realized_vol_20" in result.indicators

    def test_not_condition(self):
        spec = {
            **FABER_2007_SPEC,
            "entry": {"not": {"lt": ["close", "sma_50"]}},
        }
        result = validate_strategy_spec(spec)
        assert "sma_50" in result.indicators


class TestSpecRoundTrip:
    """Spec can be serialized and re-validated."""

    def test_to_dict_round_trip(self):
        result = validate_strategy_spec(FABER_2007_SPEC)
        d = result.to_dict()
        result2 = validate_strategy_spec(d)
        assert result2.name == result.name
        assert result2.entry == result.entry

    def test_to_json_round_trip(self):
        import json

        result = validate_strategy_spec(FABER_2007_SPEC)
        j = result.to_json()
        d = json.loads(j)
        result2 = validate_strategy_spec(d)
        assert result2.name == result.name


class TestParameterVariants:
    """Validation of the optional parameter_variants field."""

    def test_valid_variants(self):
        spec = {**FABER_2007_SPEC, "parameter_variants": {"sma_200": [150, 175, 200, 225, 250]}}
        result = validate_strategy_spec(spec)
        assert result.parameter_variants is not None
        assert result.parameter_variants["sma_200"] == [150, 175, 200, 225, 250]

    def test_no_variants_is_none(self):
        result = validate_strategy_spec(FABER_2007_SPEC)
        assert result.parameter_variants is None

    def test_variant_key_not_in_indicators(self):
        spec = {**FABER_2007_SPEC, "parameter_variants": {"rsi_14": [10, 14, 20]}}
        with pytest.raises(DSLError, match="must reference an indicator alias"):
            validate_strategy_spec(spec)

    def test_variant_values_not_list(self):
        spec = {**FABER_2007_SPEC, "parameter_variants": {"sma_200": "bad"}}
        with pytest.raises(DSLError, match="must be a list"):
            validate_strategy_spec(spec)

    def test_variant_empty_list(self):
        spec = {**FABER_2007_SPEC, "parameter_variants": {"sma_200": []}}
        with pytest.raises(DSLError, match="at least 2 entries"):
            validate_strategy_spec(spec)

    def test_variant_single_entry(self):
        spec = {**FABER_2007_SPEC, "parameter_variants": {"sma_200": [200]}}
        with pytest.raises(DSLError, match="at least 2 entries"):
            validate_strategy_spec(spec)

    def test_variant_too_many_entries(self):
        spec = {**FABER_2007_SPEC, "parameter_variants": {"sma_200": list(range(9))}}
        with pytest.raises(DSLError, match="at most 8 entries"):
            validate_strategy_spec(spec)

    def test_variant_non_numeric_values(self):
        spec = {**FABER_2007_SPEC, "parameter_variants": {"sma_200": [200, "bad"]}}
        with pytest.raises(DSLError, match="must be numeric"):
            validate_strategy_spec(spec)

    def test_variants_not_dict(self):
        spec = {**FABER_2007_SPEC, "parameter_variants": "bad"}
        with pytest.raises(DSLError, match="must be a dict"):
            validate_strategy_spec(spec)

    def test_variants_round_trip(self):
        import json

        spec = {**FABER_2007_SPEC, "parameter_variants": {"sma_200": [150, 200, 250]}}
        result = validate_strategy_spec(spec)
        d = result.to_dict()
        assert "parameter_variants" in d
        assert d["parameter_variants"]["sma_200"] == [150, 200, 250]
        j = result.to_json()
        parsed = json.loads(j)
        result2 = validate_strategy_spec(parsed)
        assert result2.parameter_variants == result.parameter_variants

    def test_variants_omitted_from_to_dict_when_none(self):
        result = validate_strategy_spec(FABER_2007_SPEC)
        d = result.to_dict()
        assert "parameter_variants" not in d

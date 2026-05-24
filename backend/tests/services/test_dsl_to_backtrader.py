"""Tests for the DSL → backtrader interpreter."""

from __future__ import annotations

import pytest
from archimedes.services.dsl_to_backtrader import (
    _eval_condition,
    interpret_spec,
    interpret_variant,
)
from archimedes.services.strategy_dsl import (
    FABER_2007_SPEC,
    DSLError,
    validate_strategy_spec,
)


class TestInterpretsReferenceExamples:
    """Interpreter must produce valid backtrader.Strategy subclasses."""

    @pytest.fixture
    def faber_spec(self):
        return validate_strategy_spec(FABER_2007_SPEC)

    def test_interprets_reference_examples(self, faber_spec):
        cls = interpret_spec(faber_spec)
        assert cls is not None
        assert issubclass(cls, _get_bt_strategy_class())
        assert cls.__name__.startswith("DSL_")

    def test_strategy_has_params(self, faber_spec):
        cls = interpret_spec(faber_spec)
        params_dict = dict(cls.params._getitems())
        assert "dsl_spec" in params_dict
        assert params_dict["dsl_spec"]["name"] == faber_spec.name

    def test_faber_class_name(self, faber_spec):
        cls = interpret_spec(faber_spec)
        assert "SMA_200" in cls.__name__ or "Tactical" in cls.__name__


class TestRejectsLookAheadUnsafe:
    """Interpreter must reject specs with look_ahead_safe=false."""

    def test_rejects_look_ahead_unsafe(self):
        unsafe_spec = {**FABER_2007_SPEC, "look_ahead_safe": False}
        with pytest.raises(DSLError, match="look_ahead_safe=false"):
            validate_strategy_spec(unsafe_spec)


class TestConditionEvaluation:
    """Condition tree evaluation logic."""

    def test_gt_true(self):
        assert _eval_condition({"gt": ["close", 100]}, {"close": 110}) is True

    def test_gt_false(self):
        assert _eval_condition({"gt": ["close", 100]}, {"close": 90}) is False

    def test_lt(self):
        assert _eval_condition({"lt": ["close", 100]}, {"close": 90}) is True

    def test_gte(self):
        assert _eval_condition({"gte": ["close", 100]}, {"close": 100}) is True

    def test_lte(self):
        assert _eval_condition({"lte": ["close", 100]}, {"close": 100}) is True

    def test_and(self):
        cond = {
            "and": [
                {"gt": ["close", 100]},
                {"lt": ["close", 200]},
            ]
        }
        assert _eval_condition(cond, {"close": 150}) is True
        assert _eval_condition(cond, {"close": 50}) is False

    def test_or(self):
        cond = {
            "or": [
                {"gt": ["close", 200]},
                {"lt": ["close", 50]},
            ]
        }
        assert _eval_condition(cond, {"close": 30}) is True
        assert _eval_condition(cond, {"close": 100}) is False

    def test_not(self):
        cond = {"not": {"gt": ["close", 100]}}
        assert _eval_condition(cond, {"close": 90}) is True
        assert _eval_condition(cond, {"close": 110}) is False


def _get_bt_strategy_class():
    """Import backtrader and return the Strategy base class."""
    import backtrader as bt

    return bt.Strategy


class TestInterpretVariant:
    """interpret_variant produces distinct strategy classes per override."""

    @pytest.fixture
    def faber_spec(self):
        return validate_strategy_spec(FABER_2007_SPEC)

    def test_variant_produces_strategy_class(self, faber_spec):
        cls = interpret_variant(faber_spec, {"sma_200": 150})
        assert cls is not None
        assert issubclass(cls, _get_bt_strategy_class())

    def test_variants_produce_distinct_classes(self, faber_spec):
        cls_150 = interpret_variant(faber_spec, {"sma_200": 150})
        cls_250 = interpret_variant(faber_spec, {"sma_200": 250})
        assert cls_150 is not cls_250
        assert cls_150.__name__ != cls_250.__name__

    def test_variant_class_name_reflects_override(self, faber_spec):
        cls = interpret_variant(faber_spec, {"sma_200": 150})
        assert "150" in cls.__name__

    def test_variant_rewrites_condition_tree(self, faber_spec):
        """The variant strategy uses the overridden period in conditions."""
        cls = interpret_variant(faber_spec, {"sma_200": 50})
        params_dict = dict(cls.params._getitems())
        spec = params_dict["dsl_spec"]
        assert "sma_50" in str(spec["entry"])
        assert "sma_50" in str(spec["exit"])

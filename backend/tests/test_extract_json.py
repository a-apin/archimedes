"""Unit tests for extract_json (#911).

extract_json must always return a dict or raise ValueError — never hand a
list/scalar back to a caller that immediately does ``.get()`` on it. Hermetic:
no LLM, no network, no .env.
"""

from __future__ import annotations

import pytest

from archimedes.agents.strategy_architect import extract_json


def test_plain_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_object_in_json_fence():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_array_wrapped_object_is_salvaged():
    # Weak-JSON models sometimes wrap the single object in a one-element array.
    assert extract_json('[{"a": 1}]') == {"a": 1}


def test_first_object_of_multi_element_array():
    assert extract_json('[{"a": 1}, {"b": 2}]') == {"a": 1}


def test_object_embedded_in_prose():
    assert extract_json('here you go: {"a": 1} thanks') == {"a": 1}


@pytest.mark.parametrize("raw", ['"just a string"', "42", "[1, 2, 3]", "[]", "not json at all"])
def test_non_object_raises_valueerror(raw):
    # A bare scalar or an object-free array can't become a dict, so the caller
    # gets the explicit ValueError it already handles rather than a bad type.
    with pytest.raises(ValueError):
        extract_json(raw)

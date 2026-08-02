"""Unit tests for strategy visibility predicate (#1120 / #850)."""

from __future__ import annotations

from unittest.mock import MagicMock

from archimedes.services.strategy_visibility import is_strategy_visible


def test_is_strategy_visible_none():
    assert is_strategy_visible(None, "0x123") is False


def test_is_strategy_visible_example():
    row = MagicMock(is_example=True, is_published=False, owner_wallet="0x456")
    assert is_strategy_visible(row, None) is True
    assert is_strategy_visible(row, "0x789") is True


def test_is_strategy_visible_published():
    row = MagicMock(is_example=False, is_published=True, owner_wallet="0x456")
    assert is_strategy_visible(row, None) is True
    assert is_strategy_visible(row, "0x789") is True


def test_is_strategy_visible_owner_match():
    row = MagicMock(is_example=False, is_published=False, owner_wallet="0xABCdef123")
    # Matching owner (case-insensitive, whitespace-insensitive)
    assert is_strategy_visible(row, "0xabcdef123") is True
    assert is_strategy_visible(row, " 0xABCDEF123  ") is True
    # Non-owner caller
    assert is_strategy_visible(row, "0x999999999") is False
    # Empty caller
    assert is_strategy_visible(row, None) is False
    assert is_strategy_visible(row, "") is False


def test_is_strategy_visible_dict_support():
    row_dict = {"is_example": False, "is_published": False, "owner_wallet": "0xOwner123"}
    assert is_strategy_visible(row_dict, "0xowner123") is True
    assert is_strategy_visible(row_dict, "0xother") is False

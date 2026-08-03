"""Tests for engine.required_feeds() and the REQUIRED_FEEDS_UNIVERSE sentinel.

Hermetic: hand-built bt.Strategy subclasses, no network. Covers the feed-count
contract cli.run_command's router depends on (backtest-vol audit item 1b): a
plain int (1 default, 2 for pairs), the "UNIVERSE" sentinel for cross-sectional
strategies, and the fail-loud behavior for a malformed declaration — the whole
point being that the router must never silently downgrade a multi-feed
strategy to a single-feed run.
"""

from __future__ import annotations

import backtrader as bt
import pytest
from archimedes_analytics_engine.engine import (
    REQUIRED_FEEDS_UNIVERSE,
    FeedArityError,
    required_feeds,
)


class _NoDeclaration(bt.Strategy):
    def next(self) -> None:
        pass


class _ExplicitOne(bt.Strategy):
    REQUIRED_FEEDS = 1

    def next(self) -> None:
        pass


class _PairsTwo(bt.Strategy):
    REQUIRED_FEEDS = 2

    def next(self) -> None:
        pass


class _CrossSectional(bt.Strategy):
    REQUIRED_FEEDS = "UNIVERSE"

    def next(self) -> None:
        pass


class _CrossSectionalLowercase(bt.Strategy):
    # Case-insensitive: "universe" (lowercase) must still resolve to the sentinel.
    REQUIRED_FEEDS = "universe"

    def next(self) -> None:
        pass


class _Typo(bt.Strategy):
    REQUIRED_FEEDS = "Univers"  # plausible typo — must fail loud, not silently become 1

    def next(self) -> None:
        pass


class _MalformedNone(bt.Strategy):
    REQUIRED_FEEDS = None

    def next(self) -> None:
        pass


class _ExplicitFive(bt.Strategy):
    # A hypothetical strategy declaring an exact N > 2 rather than the sentinel.
    REQUIRED_FEEDS = 5

    def next(self) -> None:
        pass


def test_undeclared_defaults_to_one() -> None:
    assert required_feeds(_NoDeclaration) == 1


def test_explicit_one_is_one() -> None:
    assert required_feeds(_ExplicitOne) == 1


def test_pairs_two_is_two() -> None:
    assert required_feeds(_PairsTwo) == 2


def test_universe_sentinel_returns_sentinel() -> None:
    assert required_feeds(_CrossSectional) == REQUIRED_FEEDS_UNIVERSE


def test_universe_sentinel_is_case_insensitive() -> None:
    assert required_feeds(_CrossSectionalLowercase) == REQUIRED_FEEDS_UNIVERSE


def test_explicit_int_greater_than_two_passes_through() -> None:
    assert required_feeds(_ExplicitFive) == 5


def test_malformed_none_falls_back_to_one() -> None:
    # `REQUIRED_FEEDS = None` predates this contract's typed-string handling;
    # preserve the pre-existing graceful fallback for non-string garbage.
    assert required_feeds(_MalformedNone) == 1


def test_typo_string_fails_loud_instead_of_silently_downgrading() -> None:
    """The whole point of the sentinel: a near-miss string must never
    silently collapse to the single-feed default of 1 — that would be
    exactly the "plausible-looking but wrong" defect this module exists to
    eliminate."""
    with pytest.raises(FeedArityError, match="REQUIRED_FEEDS='Univers'"):
        required_feeds(_Typo)

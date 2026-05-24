"""Tests for regime_tag on strategy passport — issue #162.

Validates:
  1. Strategy dataclass accepts regime_tag field
  2. All 6 curated strategy files declare REGIME_TAG
  3. strategy_provider parses REGIME_TAG and surfaces it on Strategy instances
  4. Invalid/missing REGIME_TAG raises ValueError (anti-goal: no silent default)
  5. API response includes regime_tag via _to_strategy_response
"""

from __future__ import annotations

from pathlib import Path

import pytest
from archimedes.models.paper_ref import PaperRef
from archimedes.models.strategy import Strategy
from archimedes.services.strategy_provider import _read_module_constants, _to_strategy

STRATEGIES_DIR = Path(__file__).resolve().parents[3] / "analytics-engine" / "strategies"
VALID_TAGS = {"bull", "bear", "regime_neutral"}


# ── Strategy dataclass field ────────────────────────────────────────────────


def test_strategy_dataclass_has_regime_tag():
    s = Strategy(
        id="test",
        papers=[PaperRef(title="Test")],
        regime_tag="bull",
    )
    assert s.regime_tag == "bull"


def test_strategy_dataclass_regime_tag_default():
    s = Strategy(id="test", papers=[PaperRef(title="Test")])
    assert s.regime_tag == "regime_neutral"


# ── All curated files declare REGIME_TAG ────────────────────────────────────


@pytest.fixture(params=sorted(STRATEGIES_DIR.glob("*.py")))
def strategy_file(request):
    return request.param


def test_all_curated_files_have_regime_tag(strategy_file):
    metadata = _read_module_constants(strategy_file)
    assert "REGIME_TAG" in metadata, f"{strategy_file.name} missing REGIME_TAG"


def test_all_regime_tags_are_valid(strategy_file):
    metadata = _read_module_constants(strategy_file)
    tag = metadata.get("REGIME_TAG")
    assert tag in VALID_TAGS, f"{strategy_file.name} has invalid REGIME_TAG={tag!r}"


def test_regime_tags_distribution():
    """At least one 'bear' and one 'bull' tag exists across curated strategies."""
    tags = []
    for path in sorted(STRATEGIES_DIR.glob("*.py")):
        metadata = _read_module_constants(path)
        tag = metadata.get("REGIME_TAG")
        if tag:
            tags.append(tag)
    assert "bull" in tags, "Expected at least one 'bull' strategy"
    assert "bear" in tags, "Expected at least one 'bear' strategy"


# ── strategy_provider._to_strategy parses REGIME_TAG ────────────────────────


def test_to_strategy_parses_regime_tag_from_metadata(tmp_path):
    """_to_strategy reads REGIME_TAG from metadata and sets it on Strategy."""
    fake_strategy = tmp_path / "test_strategy.py"
    fake_strategy.write_text(
        'PAPER_TITLE = "Test"\n'
        "PAPER_AUTHORS = []\n"
        'METHODOLOGY_SUMMARY = "test"\n'
        'METHODOLOGY_TEXT = "test methodology"\n'
        'REGIME_TAG = "bull"\n'
    )
    metadata = _read_module_constants(fake_strategy)
    code_hash = "abc123"
    s = _to_strategy(fake_strategy, metadata, code_hash)
    assert s.regime_tag == "bull"


def test_to_strategy_rejects_invalid_regime_tag(tmp_path):
    """Missing/invalid REGIME_TAG raises ValueError, not silent default."""
    fake_strategy = tmp_path / "bad_strategy.py"
    fake_strategy.write_text(
        'PAPER_TITLE = "Bad"\n'
        "PAPER_AUTHORS = []\n"
        'METHODOLOGY_SUMMARY = "bad"\n'
        'METHODOLOGY_TEXT = "bad methodology"\n'
        'REGIME_TAG = "invalid_tag"\n'
    )
    metadata = _read_module_constants(fake_strategy)
    with pytest.raises(ValueError, match="Invalid or missing REGIME_TAG"):
        _to_strategy(fake_strategy, metadata, "abc123")


def test_to_strategy_rejects_missing_regime_tag(tmp_path):
    """Missing REGIME_TAG raises ValueError per anti-goal."""
    fake_strategy = tmp_path / "no_tag_strategy.py"
    fake_strategy.write_text(
        'PAPER_TITLE = "No Tag"\n'
        "PAPER_AUTHORS = []\n"
        'METHODOLOGY_SUMMARY = "no tag"\n'
        'METHODOLOGY_TEXT = "no tag methodology"\n'
    )
    metadata = _read_module_constants(fake_strategy)
    with pytest.raises(ValueError, match="Invalid or missing REGIME_TAG"):
        _to_strategy(fake_strategy, metadata, "abc123")


# ── API response includes regime_tag ────────────────────────────────────────


def test_strategy_response_includes_regime_tag():
    """StrategyResponse schema accepts and serializes regime_tag."""
    from archimedes.api.schemas import StrategyResponse

    resp = StrategyResponse(
        id="test",
        paper_arxiv_id="",
        paper_title="Test",
        methodology_summary="test",
        asset_universe=[],
        position_sizing="equal_weight",
        rebalance_frequency="weekly",
        status="candidate",
        regime_tag="bear",
    )
    assert resp.regime_tag == "bear"
    data = resp.model_dump()
    assert "regime_tag" in data
    assert data["regime_tag"] == "bear"


def test_to_strategy_response_wires_regime_tag():
    """_to_strategy_response maps Strategy.regime_tag to the response."""
    from archimedes.api.strategies_routes import _to_strategy_response

    s = Strategy(
        id="test-regime",
        papers=[PaperRef(title="Test Regime")],
        methodology_summary="test",
        regime_tag="bear",
    )
    resp = _to_strategy_response(s)
    assert resp.regime_tag == "bear"

"""Tests for strategy provider and response schema.

Covers:
- LocalStrategyProvider loading the seed strategy files
- STATUS constant parsed correctly (live / candidate)
- BACKTEST_* stub constants parsed and exposed on Strategy
- Paper claim fields parsed
- StrategyResponse schema — field presence and value mapping

Note: does NOT import archimedes.api.routes (which transitively imports chain/
web3 services requiring eth_account/web3). The mapping logic is tested via
schema construction directly.
"""

from __future__ import annotations

import pytest

from archimedes.api.schemas import StrategyResponse
from archimedes.models.strategy import Strategy, StrategyStatus


# ── Provider loading ──────────────────────────────────────────


def test_provider_loads_strategies(provider):
    strategies = provider.list_strategies()
    assert len(strategies) >= 3, "Expected at least 3 seeded strategies"


def test_faber_strategy_loaded(provider):
    titles = {s.paper_title for s in provider.list_strategies()}
    assert "A Quantitative Approach to Tactical Asset Allocation" in titles


def test_tsmom_strategy_loaded(provider):
    titles = {s.paper_title for s in provider.list_strategies()}
    assert "Time Series Momentum" in titles


def test_volatility_managed_strategy_loaded(provider):
    titles = {s.paper_title for s in provider.list_strategies()}
    assert "Volatility-Managed Portfolios" in titles


# ── STATUS parsing ────────────────────────────────────────────


def test_faber_status_is_live(provider):
    faber = next(s for s in provider.list_strategies() if "Faber" in " ".join(s.paper_authors))
    assert faber.status == StrategyStatus.LIVE


def test_tsmom_status_is_live(provider):
    tsmom = next(s for s in provider.list_strategies() if s.paper_title == "Time Series Momentum")
    assert tsmom.status == StrategyStatus.LIVE


def test_buy_hold_status_is_candidate(provider):
    baseline = next((s for s in provider.list_strategies() if s.paper_title == "Buy-and-Hold Baseline"), None)
    if baseline is not None:
        assert baseline.status == StrategyStatus.CANDIDATE


# ── BACKTEST_* stub constants ─────────────────────────────────


def test_faber_has_stub_sharpe(provider):
    faber = next(s for s in provider.list_strategies() if "Tactical Asset Allocation" in s.paper_title)
    assert faber.stub_sharpe is not None
    assert isinstance(faber.stub_sharpe, float)
    assert 0 < faber.stub_sharpe < 3.0


def test_tsmom_has_stub_calmar(provider):
    tsmom = next(s for s in provider.list_strategies() if s.paper_title == "Time Series Momentum")
    assert tsmom.stub_calmar is not None


def test_vol_managed_has_stub_corr_spy(provider):
    vol = next(s for s in provider.list_strategies() if s.paper_title == "Volatility-Managed Portfolios")
    assert vol.stub_corr_spy is not None
    assert 0 <= vol.stub_corr_spy <= 1.0


# ── Paper claim fields ────────────────────────────────────────


def test_faber_paper_claimed_sharpe(provider):
    faber = next(s for s in provider.list_strategies() if "Tactical Asset Allocation" in s.paper_title)
    assert faber.paper_claimed_sharpe == pytest.approx(0.78)


def test_tsmom_paper_claimed_sharpe(provider):
    tsmom = next(s for s in provider.list_strategies() if s.paper_title == "Time Series Momentum")
    assert tsmom.paper_claimed_sharpe == pytest.approx(1.43)


# ── Passport fields ───────────────────────────────────────────


def test_faber_passport_fields(provider):
    faber = next(s for s in provider.list_strategies() if "Tactical Asset Allocation" in s.paper_title)
    assert faber.paper_venue == "The Journal of Wealth Management"
    assert faber.paper_year == 2007
    assert faber.paper_doi is not None
    assert faber.paper_citation_count == 850
    assert faber.methodology_hash is not None
    assert faber.curator_note is not None


def test_strategy_id_is_deterministic(provider):
    ids1 = {s.id for s in provider.list_strategies()}
    ids2 = {s.id for s in provider.list_strategies()}
    assert ids1 == ids2, "Strategy IDs must be deterministic across loads"


# ── StrategyResponse schema mapping (no routes import) ───────


def _map_to_response(s: Strategy) -> StrategyResponse:
    """Inline mapping logic matching _to_strategy_response in routes.py."""
    has_stubs = s.stub_sharpe is not None
    return StrategyResponse(
        id=s.id,
        paper_arxiv_id=s.paper_arxiv_id,
        paper_title=s.paper_title,
        paper_authors=s.paper_authors,
        methodology_summary=s.methodology_summary,
        asset_universe=s.asset_universe,
        position_sizing=s.position_sizing.value,
        rebalance_frequency=s.rebalance_frequency.value,
        status=s.status.value,
        paper_venue=s.paper_venue,
        paper_year=s.paper_year,
        paper_doi=s.paper_doi,
        paper_citation_count=s.paper_citation_count,
        methodology_hash=s.methodology_hash,
        extraction_llm=s.extraction_llm,
        curator_wallet=s.curator_wallet,
        curator_note=s.curator_note,
        on_chain_registration_tx=s.on_chain_registration_tx,
        paper_claimed_sharpe=s.paper_claimed_sharpe,
        sharpe_ratio=s.stub_sharpe,
        cagr=s.stub_cagr,
        max_drawdown=s.stub_max_dd,
        win_rate=s.stub_win_rate,
        calmar_ratio=s.stub_calmar,
        correlation_to_spy=s.stub_corr_spy,
        is_backtest_placeholder=has_stubs,
    )


def test_response_includes_passport_fields(provider):
    faber = next(s for s in provider.list_strategies() if "Tactical Asset Allocation" in s.paper_title)
    resp = _map_to_response(faber)
    assert resp.paper_venue == "The Journal of Wealth Management"
    assert resp.paper_year == 2007
    assert resp.paper_citation_count == 850
    assert resp.methodology_hash is not None
    assert resp.curator_note is not None


def test_response_backtest_stubs_populated(provider):
    faber = next(s for s in provider.list_strategies() if "Tactical Asset Allocation" in s.paper_title)
    resp = _map_to_response(faber)
    assert resp.sharpe_ratio is not None
    assert resp.cagr is not None
    assert resp.max_drawdown is not None
    assert resp.calmar_ratio is not None
    assert resp.correlation_to_spy is not None
    assert resp.is_backtest_placeholder is True


def test_response_paper_claimed_sharpe(provider):
    faber = next(s for s in provider.list_strategies() if "Tactical Asset Allocation" in s.paper_title)
    resp = _map_to_response(faber)
    assert resp.paper_claimed_sharpe == pytest.approx(0.78)


def test_response_status_live(provider):
    live = [s for s in provider.list_strategies() if s.status == StrategyStatus.LIVE]
    assert len(live) >= 2, "Expected at least 2 live strategies"
    for s in live:
        resp = _map_to_response(s)
        assert resp.status == "live"


def test_response_buy_hold_no_stubs(provider):
    baseline = next((s for s in provider.list_strategies() if s.paper_title == "Buy-and-Hold Baseline"), None)
    if baseline is None:
        pytest.skip("Buy-and-Hold baseline not loaded")
    resp = _map_to_response(baseline)
    assert resp.is_backtest_placeholder is False


# ── Filtering ─────────────────────────────────────────────────


def test_filter_by_live_status(provider):
    live = provider.list_strategies(status=StrategyStatus.LIVE)
    assert all(s.status == StrategyStatus.LIVE for s in live)
    assert len(live) >= 2


def test_filter_by_candidate_status(provider):
    candidates = provider.list_strategies(status=StrategyStatus.CANDIDATE)
    assert all(s.status == StrategyStatus.CANDIDATE for s in candidates)

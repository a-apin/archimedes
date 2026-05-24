"""Tests for strategy provider and response schema.

Covers:
- LocalStrategyProvider loading seed strategy files
- STATUS constant parsing
- Paper/passport fields parsing
- Backtest read path from DB table (result or None fallback)
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


def test_buy_hold_status_is_live(provider):
    # NOTE (consolidation 2026-05-18): analytics-engine/strategies/pipeline_buy_hold.py
    # declares STATUS = "live" on current main, like the other seeded strategies, so
    # the provider yields LIVE. Daniel's original commit (22 commits behind) asserted
    # CANDIDATE. Aligned to current reality. OPEN DESIGN QUESTION for the team: a
    # buy-and-hold baseline arguably should stay CANDIDATE until it clears the
    # selection-bias rigor gate (#53) — but that is a product/strategy-file decision,
    # not a test fix. Flagged in the PR; not silently changed here.
    baseline = next((s for s in provider.list_strategies() if s.paper_title == "Buy-and-Hold Baseline"), None)
    if baseline is not None:
        assert baseline.status == StrategyStatus.LIVE


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


# ── Backtest read path ────────────────────────────────────────


def test_provider_backtest_lookup_returns_row_or_none(provider):
    for s in provider.list_strategies():
        bt = provider.get_backtest_result(s.id)
        assert bt is None or bt.strategy_id == s.id


# ── StrategyResponse mapping (no routes import) ───────────────


def _map_to_response(s: Strategy, provider) -> StrategyResponse:
    bt = provider.get_backtest_result(s.id)
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
        paper_claimed_sharpe=bt.paper_claimed_sharpe if bt else s.paper_claimed_sharpe,
        sharpe_ratio=bt.sharpe_ratio if bt else None,
        cagr=bt.cagr if bt else None,
        max_drawdown=bt.max_drawdown if bt else None,
        win_rate=bt.win_rate if bt else None,
        calmar_ratio=bt.calmar_ratio if bt else None,
        correlation_to_spy=bt.correlation_to_spy if bt else None,
        is_backtest_placeholder=False,
    )


def test_response_includes_passport_fields(provider):
    faber = next(s for s in provider.list_strategies() if "Tactical Asset Allocation" in s.paper_title)
    resp = _map_to_response(faber, provider)
    assert resp.paper_venue == "The Journal of Wealth Management"
    assert resp.paper_year == 2007
    assert resp.paper_citation_count == 850
    assert resp.methodology_hash is not None
    assert resp.curator_note is not None


def test_response_backtest_none_when_no_row(provider):
    # At minimum ensure mapping tolerates no backtest row.
    strat = next(iter(provider.list_strategies()))
    resp = _map_to_response(strat, provider)
    bt = provider.get_backtest_result(strat.id)
    if bt is None:
        assert resp.sharpe_ratio is None
        assert resp.max_drawdown is None
    else:
        assert resp.sharpe_ratio == bt.sharpe_ratio
        assert resp.max_drawdown == bt.max_drawdown


def test_response_status_live(provider):
    live = [s for s in provider.list_strategies() if s.status == StrategyStatus.LIVE]
    assert len(live) >= 2, "Expected at least 2 live strategies"
    for s in live:
        resp = _map_to_response(s, provider)
        assert resp.status == "live"


# ── Filtering ─────────────────────────────────────────────────


def test_filter_by_live_status(provider):
    live = provider.list_strategies(status=StrategyStatus.LIVE)
    assert all(s.status == StrategyStatus.LIVE for s in live)
    assert len(live) >= 2


def test_filter_by_candidate_status(provider):
    candidates = provider.list_strategies(status=StrategyStatus.CANDIDATE)
    assert all(s.status == StrategyStatus.CANDIDATE for s in candidates)

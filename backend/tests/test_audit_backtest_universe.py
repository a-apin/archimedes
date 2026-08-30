"""Hermetic integration tests for scripts/audit_backtest_universe.py (Deliverable 1).

sqlite-in-memory DB + a temp analytics-engine/strategies/ tree, mirroring the
pattern in test_run_backtests_script.py. Mocks at the boundary — the DB session
factory and the strategies directory — never internals.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from archimedes.models.backtest import BacktestResult
from archimedes.models.chat import Base
from archimedes.scripts import audit_backtest_universe as audit_mod
from archimedes.services.backtest_repository import insert_backtest_if_missing
from archimedes.services.strategy_provider import default_provider
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _write_strategy(path: Path, *, asset_universe: list[str], regime_tag: str = "regime_neutral") -> None:
    universe_literal = json.dumps(asset_universe)
    path.write_text(
        "import backtrader as bt\n\n"
        f"PAPER_TITLE = {path.stem!r}\n"
        "PAPER_AUTHORS = ['Test']\n"
        "METHODOLOGY_SUMMARY = 'Test summary'\n"
        f"ASSET_UNIVERSE = {universe_literal}\n"
        "STATUS = 'candidate'\n"
        f"REGIME_TAG = {regime_tag!r}\n\n"
        "class TestStrategy(bt.Strategy):\n"
        "    def next(self):\n"
        "        if not self.position:\n"
        "            self.buy(size=1)\n"
    )


def _sample_result(strategy_id: str, equity_curve: list[float]) -> BacktestResult:
    return BacktestResult(
        strategy_id=strategy_id,
        sharpe_ratio=0.5,
        sortino_ratio=0.5,
        max_drawdown=0.2,
        cagr=0.1,
        calmar_ratio=0.5,
        win_rate=0.5,
        profit_factor=1.2,
        total_trades=10,
        avg_holding_period_days=5.0,
        correlation_to_spy=0.3,
        correlation_to_btc=0.1,
        equity_curve=equity_curve,
        monthly_returns=[0.01],
        # Required: insert_backtest_if_missing refuses an unattributed row, so
        # a row can always be traced to the engine that produced it.
        backtest_engine="backtrader",
    )


def _equity_curve_from_returns(daily_returns: list[float], start: float = 100_000.0) -> list[float]:
    curve = [start]
    for r in daily_returns:
        curve.append(curve[-1] * (1.0 + r))
    return curve


def _artifact_json_with_daily_returns(daily_returns: list[float]) -> str:
    """A minimal artifact payload shaped like get_daily_returns expects:
    results[0].metrics.daily_returns."""
    return json.dumps({"results": [{"operation": "SPY", "metrics": {"daily_returns": daily_returns}}]})


def _spy_like_returns(n: int = 5659, seed: int = 20260727) -> list[float]:
    rng = np.random.default_rng(seed)
    return rng.normal(loc=0.00045, scale=0.01175, size=n).tolist()


def _near_zero_returns(n: int = 5659, seed: int = 7) -> list[float]:
    rng = np.random.default_rng(seed)
    return rng.normal(loc=0.00007, scale=0.0006, size=n).tolist()


@pytest.fixture
def _db(monkeypatch):
    """In-memory sqlite wired into every module that touches the DB, matching
    the mock-at-the-boundary convention (never internals)."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    def fake_init_db() -> None:
        Base.metadata.create_all(bind=engine)

    def fake_get_session():
        return SessionLocal()

    monkeypatch.setattr(audit_mod, "get_session", fake_get_session)
    monkeypatch.setattr(audit_mod, "init_db", fake_init_db)

    # strategy_provider.py does its own `from archimedes.db import get_session`
    # import (a separately-bound reference) for fixture/backtest-stub loading —
    # patch it too so the provider degrades cleanly against the SAME fake DB
    # instead of a real one that doesn't exist in this test process.
    from archimedes.services import strategy_provider as strategy_provider_mod

    monkeypatch.setattr(strategy_provider_mod, "get_session", fake_get_session)

    return SessionLocal


def test_clean_strategy_passes(_db, tmp_path) -> None:
    strategies_dir = tmp_path / "analytics-engine" / "strategies"
    strategies_dir.mkdir(parents=True)
    _write_strategy(strategies_dir / "pipeline_buy_hold.py", asset_universe=["SPY"], regime_tag="bull")

    returns = _spy_like_returns()
    SessionLocal = _db
    provider = default_provider(repo_root=tmp_path)
    (strategy,) = provider.list_strategies()

    with SessionLocal() as session:
        insert_backtest_if_missing(
            session,
            strategy_id=strategy.id,
            content_hash="h1",
            result=_sample_result(strategy.id, _equity_curve_from_returns(returns)),
            artifact_json=_artifact_json_with_daily_returns(returns),
            source_pipeline="test",
        )
        session.commit()

    rows = audit_mod.run_audit(repo_root=tmp_path)

    assert len(rows) == 1
    assert rows[0].status == "clean"
    assert rows[0].needs_attention is False


def test_bil_declared_strategy_with_equity_vol_is_flagged(_db, tmp_path) -> None:
    """The confirmed defect, end to end through the audit script: a BIL-declared
    strategy whose persisted returns are actually ~18-19% (equity) vol."""
    strategies_dir = tmp_path / "analytics-engine" / "strategies"
    strategies_dir.mkdir(parents=True)
    _write_strategy(strategies_dir / "pipeline_buy_hold.py", asset_universe=["SPY"], regime_tag="bull")
    _write_strategy(strategies_dir / "capital_preservation_tbill.py", asset_universe=["BIL"])

    equity_returns = _spy_like_returns()
    SessionLocal = _db
    provider = default_provider(repo_root=tmp_path)
    strategies = {Path(s.strategy_code_path).stem: s for s in provider.list_strategies()}

    with SessionLocal() as session:
        for stem, strategy in strategies.items():
            insert_backtest_if_missing(
                session,
                strategy_id=strategy.id,
                content_hash=f"h-{stem}",
                result=_sample_result(strategy.id, _equity_curve_from_returns(equity_returns)),
                artifact_json=_artifact_json_with_daily_returns(equity_returns),
                source_pipeline="test",
            )
        session.commit()

    rows = audit_mod.run_audit(repo_root=tmp_path)
    by_stem = {r.stem: r for r in rows}

    assert by_stem["pipeline_buy_hold"].status == "clean"
    tbill_row = by_stem["capital_preservation_tbill"]
    assert tbill_row.status == "flagged"
    assert tbill_row.needs_attention is True
    assert any("plausible band" in r for r in tbill_row.verdict.reasons)
    assert any("equity baseline" in r for r in tbill_row.verdict.reasons)

    # Table rendering + exit-code plumbing both surface the flag.
    table = audit_mod.render_table(rows)
    assert "capital_preservation_tbill" in table
    assert "flagged" in table

    exit_code = audit_mod.main(["--json", "--repo-root", str(tmp_path)])
    assert exit_code == 1


def test_strategy_with_no_persisted_returns_is_skipped_not_crashed(_db, tmp_path) -> None:
    strategies_dir = tmp_path / "analytics-engine" / "strategies"
    strategies_dir.mkdir(parents=True)
    _write_strategy(strategies_dir / "brand_new_strategy.py", asset_universe=["SPY"])

    # No insert_backtest_if_missing call at all — nothing persisted yet.
    rows = audit_mod.run_audit(repo_root=tmp_path)

    assert len(rows) == 1
    assert rows[0].status == "skipped"
    assert rows[0].verdict is None
    assert rows[0].needs_attention is False

    # Must not raise, and must not count as a failure.
    exit_code = audit_mod.main(["--repo-root", str(tmp_path)])
    assert exit_code == 0


def test_degenerate_all_zero_series_is_reported_not_passed_or_crashed(_db, tmp_path) -> None:
    """5 real strategies persist an exact-zero, 5,659-observation constant
    series (per the audit) — compute_dsr returns None for these; the audit
    tool must report them as a distinct 'degenerate' category."""
    strategies_dir = tmp_path / "analytics-engine" / "strategies"
    strategies_dir.mkdir(parents=True)
    _write_strategy(strategies_dir / "flatlined_strategy.py", asset_universe=["SPY"])

    zero_returns = [0.0] * 5659
    SessionLocal = _db
    provider = default_provider(repo_root=tmp_path)
    (strategy,) = provider.list_strategies()

    with SessionLocal() as session:
        insert_backtest_if_missing(
            session,
            strategy_id=strategy.id,
            content_hash="h1",
            result=_sample_result(strategy.id, _equity_curve_from_returns(zero_returns)),
            artifact_json=_artifact_json_with_daily_returns(zero_returns),
            source_pipeline="test",
        )
        session.commit()

    rows = audit_mod.run_audit(repo_root=tmp_path)

    assert len(rows) == 1
    assert rows[0].status == "degenerate"
    assert rows[0].needs_attention is True

    exit_code = audit_mod.main(["--repo-root", str(tmp_path)])
    assert exit_code == 1


def test_maillard_style_diversified_strategy_matching_baseline_is_flagged(_db, tmp_path) -> None:
    """The second confirmed defect: a 5-asset declared universe whose realized
    vol lands within tolerance of the pipeline_buy_hold equity baseline."""
    strategies_dir = tmp_path / "analytics-engine" / "strategies"
    strategies_dir.mkdir(parents=True)
    _write_strategy(strategies_dir / "pipeline_buy_hold.py", asset_universe=["SPY"], regime_tag="bull")
    _write_strategy(
        strategies_dir / "maillard_2010_risk_parity.py",
        asset_universe=["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"],
    )

    baseline_returns = _spy_like_returns(seed=1)
    near_match_returns = _spy_like_returns(seed=2)

    SessionLocal = _db
    provider = default_provider(repo_root=tmp_path)
    strategies = {Path(s.strategy_code_path).stem: s for s in provider.list_strategies()}

    with SessionLocal() as session:
        insert_backtest_if_missing(
            session,
            strategy_id=strategies["pipeline_buy_hold"].id,
            content_hash="h-baseline",
            result=_sample_result(strategies["pipeline_buy_hold"].id, _equity_curve_from_returns(baseline_returns)),
            artifact_json=_artifact_json_with_daily_returns(baseline_returns),
            source_pipeline="test",
        )
        insert_backtest_if_missing(
            session,
            strategy_id=strategies["maillard_2010_risk_parity"].id,
            content_hash="h-maillard",
            result=_sample_result(
                strategies["maillard_2010_risk_parity"].id, _equity_curve_from_returns(near_match_returns)
            ),
            artifact_json=_artifact_json_with_daily_returns(near_match_returns),
            source_pipeline="test",
        )
        session.commit()

    rows = audit_mod.run_audit(repo_root=tmp_path)
    by_stem = {r.stem: r for r in rows}

    maillard_row = by_stem["maillard_2010_risk_parity"]
    assert maillard_row.status == "flagged"
    assert any("equity baseline" in r for r in maillard_row.verdict.reasons)


def test_cross_store_delta_is_reported_when_stores_disagree(_db, tmp_path) -> None:
    """strategy_daily_returns (keyed by stem) disagreeing with backtest_results
    (keyed by strategy_id) is itself a defect signal — must be surfaced, not
    silently ignored, without being the thing that drives the primary verdict."""
    strategies_dir = tmp_path / "analytics-engine" / "strategies"
    strategies_dir.mkdir(parents=True)
    _write_strategy(strategies_dir / "disagreeing_strategy.py", asset_universe=["SPY"])

    primary_returns = _spy_like_returns(seed=3)
    other_store_returns = _near_zero_returns(seed=4)  # wildly different vol in the other store

    SessionLocal = _db
    provider = default_provider(repo_root=tmp_path)
    (strategy,) = provider.list_strategies()

    with SessionLocal() as session:
        insert_backtest_if_missing(
            session,
            strategy_id=strategy.id,
            content_hash="h1",
            result=_sample_result(strategy.id, _equity_curve_from_returns(primary_returns)),
            artifact_json=_artifact_json_with_daily_returns(primary_returns),
            source_pipeline="test",
        )
        session.commit()

        from datetime import date, timedelta

        from archimedes.models.daily_returns_store import StrategyDailyReturn

        base_date = date(2004, 1, 2)
        for i, r in enumerate(other_store_returns):
            session.add(
                StrategyDailyReturn(
                    stem="disagreeing_strategy",
                    date=base_date + timedelta(days=i),
                    daily_return=r,
                    data_vintage="2026-07-27",
                )
            )
        session.commit()

    rows = audit_mod.run_audit(repo_root=tmp_path)

    assert len(rows) == 1
    row = rows[0]
    assert row.cross_store_vol is not None
    assert row.cross_store_delta is not None
    assert abs(row.cross_store_delta) > 0.05  # near-zero vs equity vol — a large, real gap
    assert row.cross_store_large_delta is True
    assert row.needs_attention is True


def test_render_table_does_not_crash_on_empty_or_mixed_rows(_db, tmp_path) -> None:
    strategies_dir = tmp_path / "analytics-engine" / "strategies"
    strategies_dir.mkdir(parents=True)
    _write_strategy(strategies_dir / "only_strategy.py", asset_universe=["SPY"])

    rows = audit_mod.run_audit(repo_root=tmp_path)
    table = audit_mod.render_table(rows)
    assert "only_strategy" in table
    assert "skipped" in table


def test_before_after_rigor_columns_are_populated_from_backtest_results(_db, tmp_path) -> None:
    """docs/sprint/a6-rerun.md's named deliverable: the before/after table must
    carry Sharpe / DSR p / PBO / OOS straight off the persisted backtest_results
    row (real field names: sharpe_ratio, dsr_p_value, pbo_score,
    out_of_sample_sharpe) — never fabricated, never left off silently."""
    strategies_dir = tmp_path / "analytics-engine" / "strategies"
    strategies_dir.mkdir(parents=True)
    _write_strategy(strategies_dir / "pipeline_buy_hold.py", asset_universe=["SPY"], regime_tag="bull")

    returns = _spy_like_returns()
    SessionLocal = _db
    provider = default_provider(repo_root=tmp_path)
    (strategy,) = provider.list_strategies()

    result = BacktestResult(
        strategy_id=strategy.id,
        sharpe_ratio=1.2345,
        sortino_ratio=0.5,
        max_drawdown=0.2,
        cagr=0.1,
        calmar_ratio=0.5,
        win_rate=0.5,
        profit_factor=1.2,
        total_trades=10,
        avg_holding_period_days=5.0,
        correlation_to_spy=0.3,
        correlation_to_btc=0.1,
        equity_curve=_equity_curve_from_returns(returns),
        monthly_returns=[0.01],
        dsr_p_value=0.9678,
        pbo_score=0.4321,
        out_of_sample_sharpe=0.789,
        backtest_engine="backtrader",
    )

    with SessionLocal() as session:
        insert_backtest_if_missing(
            session,
            strategy_id=strategy.id,
            content_hash="h1",
            result=result,
            artifact_json=_artifact_json_with_daily_returns(returns),
            source_pipeline="test",
        )
        session.commit()

    rows = audit_mod.run_audit(repo_root=tmp_path)
    assert len(rows) == 1
    row = rows[0]

    # Real values, read from the persisted row, not from the vol-plausibility verdict.
    assert row.sharpe_ratio == pytest.approx(1.2345)
    assert row.dsr_p_value == pytest.approx(0.9678)
    assert row.pbo_score == pytest.approx(0.4321)
    assert row.out_of_sample_sharpe == pytest.approx(0.789)

    d = row.to_dict()
    assert d["sharpe_ratio"] == pytest.approx(1.2345)
    assert d["dsr_p_value"] == pytest.approx(0.9678)
    assert d["pbo_score"] == pytest.approx(0.4321)
    assert d["out_of_sample_sharpe"] == pytest.approx(0.789)

    table = audit_mod.render_table(rows)
    assert "sharpe" in table and "dsr_p" in table and "pbo" in table and "oos" in table
    assert "1.23" in table  # sharpe_ratio, 2dp
    assert "0.968" in table  # dsr_p_value, 3dp
    assert "0.43" in table  # pbo_score, 2dp
    assert "0.79" in table  # out_of_sample_sharpe, 2dp


def test_before_after_rigor_columns_render_em_dash_when_never_persisted(_db, tmp_path) -> None:
    """A strategy with no persisted backtest_results row at all (status
    'skipped') must render '—' for the new columns, never a fabricated 0."""
    strategies_dir = tmp_path / "analytics-engine" / "strategies"
    strategies_dir.mkdir(parents=True)
    _write_strategy(strategies_dir / "brand_new_strategy.py", asset_universe=["SPY"])

    rows = audit_mod.run_audit(repo_root=tmp_path)
    assert len(rows) == 1
    row = rows[0]

    assert row.sharpe_ratio is None
    assert row.dsr_p_value is None
    assert row.pbo_score is None
    assert row.out_of_sample_sharpe is None

    table = audit_mod.render_table(rows)
    lines = [ln for ln in table.splitlines() if "brand_new_strategy" in ln]
    assert len(lines) == 1
    # The row's sharpe/dsr_p/pbo/oos cells must all be the em-dash placeholder,
    # not "0.00" or "None".
    assert "0.00" not in lines[0]
    assert "None" not in lines[0]
    assert "—" in lines[0]

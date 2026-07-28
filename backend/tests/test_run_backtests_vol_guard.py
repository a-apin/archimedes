"""Hermetic tests for the Deliverable-2 permanent guard wired into
scripts/run_backtests.py (audit 2026-07-27): a regenerated artifact whose
realized vol is wildly inconsistent with its strategy's declared
``ASSET_UNIVERSE`` must FAIL rather than be silently persisted.

Same sqlite-in-memory + monkeypatch pattern as test_run_backtests_script.py
(mocks at the boundary: ``_repo_root``, ``_load_run_command``, ``init_db``,
``get_session`` — never internals).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from archimedes.models.backtest_store import BacktestResultRecord
from archimedes.models.chat import Base
from archimedes.scripts import run_backtests as run_backtests_mod
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


def _equity_curve_from_returns(daily_returns: list[float], start: float = 100_000.0) -> list[float]:
    curve = [start]
    for r in daily_returns:
        curve.append(curve[-1] * (1.0 + r))
    return curve


def _spy_like_returns(n: int = 5659, seed: int = 20260727) -> list[float]:
    rng = np.random.default_rng(seed)
    return rng.normal(loc=0.00045, scale=0.01175, size=n).tolist()


def _artifact_payload(equity_curve: list[float], daily_returns: list[float], *, operation: str = "SPY") -> dict:
    return {
        "run_id": "20260727T000000Z",
        "strategy": {
            "backtest_code_hash": "a" * 64,
            "paper_claimed_sharpe": None,
            "paper_claimed_cagr": None,
            "paper_claimed_max_dd": None,
        },
        "assumptions": {
            "transaction_cost_bps": 10,
            "walk_forward_split": None,
            "backtest_engine": "backtrader",
        },
        "integrity_flags": {
            "lookahead_audit_passed": True,
        },
        "results": [
            {
                "operation": operation,
                "symbol": operation,
                "metrics": {
                    "sharpe_ratio": 0.5,
                    "sortino_ratio": 0.5,
                    "calmar_ratio": 0.3,
                    "max_drawdown_pct": 20.0,
                    "cagr": 0.08,
                    "total_trades": 0,
                    "win_rate": None,
                    "profit_factor": None,
                    "avg_holding_period_days": None,
                    "correlation_to_spy": None,
                    "correlation_to_btc": None,
                    "equity_curve": equity_curve,
                    "monthly_returns": [0.01],
                    "daily_returns": daily_returns,
                    "transaction_cost_bps": 10,
                    "slippage_bps": 5,
                    "look_ahead_audit_passed": True,
                    "backtest_engine": "backtrader",
                    "backtest_start": "2004-01-02T00:00:00",
                    "backtest_end": "2026-07-27T00:00:00",
                },
            }
        ],
    }


def _wire_hermetic_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(run_backtests_mod, "init_db", lambda: Base.metadata.create_all(bind=engine))
    monkeypatch.setattr(run_backtests_mod, "get_session", lambda: SessionLocal())

    from archimedes.services import strategy_provider as strategy_provider_mod

    monkeypatch.setattr(strategy_provider_mod, "get_session", lambda: SessionLocal())

    return SessionLocal


def test_wildly_inconsistent_artifact_is_refused_not_persisted(monkeypatch, tmp_path) -> None:
    """The confirmed defect, at the exact write path: a BIL-declared strategy
    whose freshly-computed artifact reads ~18-19% (equity) vol must be REFUSED
    — no row written — rather than silently persisted."""
    repo_root = tmp_path
    strategies_dir = repo_root / "analytics-engine" / "strategies"
    artifacts_dir = repo_root / "analytics-engine" / "artifacts"
    strategies_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)

    _write_strategy(strategies_dir / "capital_preservation_tbill.py", asset_universe=["BIL"])

    SessionLocal = _wire_hermetic_db(monkeypatch)
    equity_returns = _spy_like_returns()

    def fake_run_command(**kwargs):
        artifact_path = kwargs["artifact_dir"] / "20260727T000000Z.json"
        curve = _equity_curve_from_returns(equity_returns)
        artifact_path.write_text(json.dumps(_artifact_payload(curve, equity_returns)), encoding="utf-8")
        return {"run_id": "20260727T000000Z", "artifact_path": str(artifact_path)}

    monkeypatch.setattr(run_backtests_mod, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(run_backtests_mod, "_load_run_command", lambda _repo: fake_run_command)

    summary = run_backtests_mod.run_backtests()

    assert summary["inserted"] == 0
    assert summary["failed"] == 1
    assert "VolPlausibilityError" in next(iter(summary["errors"].values()))

    with SessionLocal() as session:
        assert session.query(BacktestResultRecord).count() == 0


def test_wildly_inconsistent_artifact_logs_loudly_no_traceback(monkeypatch, tmp_path, caplog) -> None:
    repo_root = tmp_path
    strategies_dir = repo_root / "analytics-engine" / "strategies"
    artifacts_dir = repo_root / "analytics-engine" / "artifacts"
    strategies_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)

    _write_strategy(strategies_dir / "capital_preservation_tbill.py", asset_universe=["BIL"])

    _wire_hermetic_db(monkeypatch)
    equity_returns = _spy_like_returns()

    def fake_run_command(**kwargs):
        artifact_path = kwargs["artifact_dir"] / "20260727T000000Z.json"
        curve = _equity_curve_from_returns(equity_returns)
        artifact_path.write_text(json.dumps(_artifact_payload(curve, equity_returns)), encoding="utf-8")
        return {"run_id": "20260727T000000Z", "artifact_path": str(artifact_path)}

    monkeypatch.setattr(run_backtests_mod, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(run_backtests_mod, "_load_run_command", lambda _repo: fake_run_command)

    with caplog.at_level(logging.INFO, logger=run_backtests_mod.logger.name):
        run_backtests_mod.run_backtests()

    records = [r for r in caplog.records if r.name == run_backtests_mod.logger.name]
    error_records = [r for r in records if r.levelno == logging.ERROR]
    assert any("REFUSING to persist" in r.getMessage() for r in error_records)
    assert not any(r.exc_info for r in records)  # explanatory error, no dumped traceback


def test_plausible_artifact_is_persisted_normally(monkeypatch, tmp_path) -> None:
    """A strategy whose declared universe and realized vol agree must NOT be
    touched by the guard — no false positives on the happy path."""
    repo_root = tmp_path
    strategies_dir = repo_root / "analytics-engine" / "strategies"
    artifacts_dir = repo_root / "analytics-engine" / "artifacts"
    strategies_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)

    _write_strategy(strategies_dir / "pipeline_buy_hold.py", asset_universe=["SPY"], regime_tag="bull")

    SessionLocal = _wire_hermetic_db(monkeypatch)
    equity_returns = _spy_like_returns()

    def fake_run_command(**kwargs):
        artifact_path = kwargs["artifact_dir"] / "20260727T000000Z.json"
        curve = _equity_curve_from_returns(equity_returns)
        artifact_path.write_text(json.dumps(_artifact_payload(curve, equity_returns)), encoding="utf-8")
        return {"run_id": "20260727T000000Z", "artifact_path": str(artifact_path)}

    monkeypatch.setattr(run_backtests_mod, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(run_backtests_mod, "_load_run_command", lambda _repo: fake_run_command)

    summary = run_backtests_mod.run_backtests()

    assert summary["inserted"] == 1
    assert summary["failed"] == 0

    with SessionLocal() as session:
        assert session.query(BacktestResultRecord).count() == 1


def test_degenerate_artifact_is_refused_not_persisted(monkeypatch, tmp_path) -> None:
    repo_root = tmp_path
    strategies_dir = repo_root / "analytics-engine" / "strategies"
    artifacts_dir = repo_root / "analytics-engine" / "artifacts"
    strategies_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)

    _write_strategy(strategies_dir / "flatlined_strategy.py", asset_universe=["SPY"])

    SessionLocal = _wire_hermetic_db(monkeypatch)
    zero_returns = [0.0] * 5659

    def fake_run_command(**kwargs):
        artifact_path = kwargs["artifact_dir"] / "20260727T000000Z.json"
        curve = _equity_curve_from_returns(zero_returns)
        artifact_path.write_text(json.dumps(_artifact_payload(curve, zero_returns)), encoding="utf-8")
        return {"run_id": "20260727T000000Z", "artifact_path": str(artifact_path)}

    monkeypatch.setattr(run_backtests_mod, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(run_backtests_mod, "_load_run_command", lambda _repo: fake_run_command)

    summary = run_backtests_mod.run_backtests()

    assert summary["inserted"] == 0
    assert summary["failed"] == 1
    assert "degenerate" in next(iter(summary["errors"].values()))

    with SessionLocal() as session:
        assert session.query(BacktestResultRecord).count() == 0


def test_baseline_missing_degrades_to_rule_a_only_without_crashing(monkeypatch, tmp_path) -> None:
    """Before pipeline_buy_hold itself has ANY persisted backtest (e.g. a brand
    new clone's first ever run), the guard must degrade gracefully to Rule A
    rather than raising or blocking every other strategy's insert."""
    repo_root = tmp_path
    strategies_dir = repo_root / "analytics-engine" / "strategies"
    artifacts_dir = repo_root / "analytics-engine" / "artifacts"
    strategies_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)

    # No pipeline_buy_hold.py at all in this tree.
    _write_strategy(strategies_dir / "some_equity_strategy.py", asset_universe=["SPY"])

    SessionLocal = _wire_hermetic_db(monkeypatch)
    equity_returns = _spy_like_returns()

    def fake_run_command(**kwargs):
        artifact_path = kwargs["artifact_dir"] / "20260727T000000Z.json"
        curve = _equity_curve_from_returns(equity_returns)
        artifact_path.write_text(json.dumps(_artifact_payload(curve, equity_returns)), encoding="utf-8")
        return {"run_id": "20260727T000000Z", "artifact_path": str(artifact_path)}

    monkeypatch.setattr(run_backtests_mod, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(run_backtests_mod, "_load_run_command", lambda _repo: fake_run_command)

    summary = run_backtests_mod.run_backtests()

    assert summary["inserted"] == 1
    assert summary["failed"] == 0

    with SessionLocal() as session:
        assert session.query(BacktestResultRecord).count() == 1

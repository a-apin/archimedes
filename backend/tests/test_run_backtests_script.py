from __future__ import annotations

import json
from pathlib import Path

from archimedes.models.chat import Base
from archimedes.scripts import run_backtests as run_backtests_mod
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _write_strategy(path: Path) -> None:
    # REGIME_TAG is required per strategy_provider.py:228 (issue #162 enforcement).
    # Without it, load_strategy raises ValueError → list_strategies skips the file →
    # strategy_by_path is empty → no inserts happen → assertions fail with 0 == 1.
    path.write_text(
        "import backtrader as bt\n\n"
        "PAPER_TITLE = 'Test Strategy'\n"
        "PAPER_AUTHORS = ['Test']\n"
        "METHODOLOGY_SUMMARY = 'Test summary'\n"
        "ASSET_UNIVERSE = ['SPY']\n"
        "STATUS = 'candidate'\n"
        "REGIME_TAG = 'bull'\n\n"
        "class TestStrategy(bt.Strategy):\n"
        "    def next(self):\n"
        "        if not self.position:\n"
        "            self.buy(size=1)\n"
    )


def _artifact_payload() -> dict:
    return {
        "run_id": "20260518T000000Z",
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
                "operation": "SPY",
                "symbol": "SPY",
                "metrics": {
                    "sharpe_ratio": 0.7135863248834242,
                    "sortino_ratio": 0.66,
                    "calmar_ratio": 0.37,
                    "max_drawdown_pct": 34.07931346227104,
                    "cagr": 0.12,
                    "total_trades": 0,
                    "win_rate": None,
                    "profit_factor": None,
                    "avg_holding_period_days": None,
                    "correlation_to_spy": None,
                    "correlation_to_btc": None,
                    "equity_curve": [100000.0, 101000.0],
                    "monthly_returns": [0.01],
                    "transaction_cost_bps": 10,
                    "slippage_bps": 5,
                    "look_ahead_audit_passed": True,
                    "backtest_engine": "backtrader",
                    "backtest_start": "2018-01-02T00:00:00",
                    "backtest_end": "2026-05-15T00:00:00",
                },
            }
        ],
    }


def test_run_backtests_is_idempotent(monkeypatch, tmp_path) -> None:
    repo_root = tmp_path
    strategies_dir = repo_root / "analytics-engine" / "strategies"
    artifacts_dir = repo_root / "analytics-engine" / "artifacts"
    strategies_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)

    _write_strategy(strategies_dir / "test_strategy.py")

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    def fake_init_db() -> None:
        Base.metadata.create_all(bind=engine)

    def fake_get_session():
        return SessionLocal()

    def fake_run_command(**kwargs):
        artifact_path = kwargs["artifact_dir"] / "20260518T000000Z.json"
        artifact_path.write_text(json.dumps(_artifact_payload()), encoding="utf-8")
        return {"run_id": "20260518T000000Z", "artifact_path": str(artifact_path)}

    monkeypatch.setattr(run_backtests_mod, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(run_backtests_mod, "_load_run_command", lambda _repo: fake_run_command)
    monkeypatch.setattr(run_backtests_mod, "init_db", fake_init_db)
    monkeypatch.setattr(run_backtests_mod, "get_session", fake_get_session)

    first = run_backtests_mod.run_backtests()
    second = run_backtests_mod.run_backtests()

    assert first["inserted"] == 1
    assert first["failed"] == 0
    assert second["inserted"] == 0
    assert second["skipped"] == 1

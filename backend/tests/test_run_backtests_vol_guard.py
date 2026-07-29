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


def _multi_op_artifact_payload(entries: list[tuple[str, list[float], list[float]]]) -> dict:
    """Like ``_artifact_payload`` but with N result entries — one per
    ``(operation, equity_curve, daily_returns)`` tuple, in the given order.
    Lets a test put the CHOSEN operation somewhere other than ``results[0]``
    (e.g. a ``BACKTEST_OPERATIONS=NIKKEI,SPY`` run where select_operation_result
    finds "SPY" at index 1, not index 0)."""
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
            for operation, equity_curve, daily_returns in entries
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


def test_guard_validates_chosen_operations_raw_series_not_results_zero_or_curve(monkeypatch, tmp_path) -> None:
    """Regression for the write-time guard validating a different series than
    downstream reads (audit 2026-07-27 follow-up): with a multi-operation
    artifact (e.g. BACKTEST_OPERATIONS=NIKKEI,SPY), select_operation_result
    picks the row by NAME — it finds "SPY" at index 1, not index 0 — so the
    CHOSEN operation is NOT results[0].

    results[0] ("NIKKEI") is a degenerate all-zero series that is never the
    chosen operation and must be ignored entirely. results[1] ("SPY", the
    chosen operation) deliberately carries an equity_curve that pct-changes to
    a CLEAN low-vol series but a raw daily_returns field that is wildly
    high-vol (annualized ~190%, far outside even the 1.5x-margined
    broad_equity band of 8-35%) — an artificial split that isolates exactly
    one thing: does the guard read the chosen operation's raw daily_returns
    (correct — must flag) or silently re-derive a different, clean-looking
    series from its equity_curve (the bug — would wrongly pass)?
    """
    repo_root = tmp_path
    strategies_dir = repo_root / "analytics-engine" / "strategies"
    artifacts_dir = repo_root / "analytics-engine" / "artifacts"
    strategies_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)

    _write_strategy(strategies_dir / "multi_op_strategy.py", asset_universe=["SPY"])

    SessionLocal = _wire_hermetic_db(monkeypatch)

    nikkei_returns = [0.0] * 20  # degenerate — must never be read (not chosen)
    nikkei_curve = _equity_curve_from_returns(nikkei_returns)

    spy_clean_returns = _spy_like_returns(n=300, seed=123)  # ~18% vol — passes Rule A
    spy_clean_curve = _equity_curve_from_returns(spy_clean_returns)

    rng = np.random.default_rng(456)
    spy_raw_extreme_returns = rng.normal(loc=0.0, scale=0.12, size=300).tolist()  # ~190% vol — fails Rule A

    def fake_run_command(**kwargs):
        artifact_path = kwargs["artifact_dir"] / "20260727T000000Z.json"
        payload = _multi_op_artifact_payload(
            [
                ("NIKKEI", nikkei_curve, nikkei_returns),
                ("SPY", spy_clean_curve, spy_raw_extreme_returns),
            ]
        )
        artifact_path.write_text(json.dumps(payload), encoding="utf-8")
        return {"run_id": "20260727T000000Z", "artifact_path": str(artifact_path)}

    monkeypatch.setattr(run_backtests_mod, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(run_backtests_mod, "_load_run_command", lambda _repo: fake_run_command)

    summary = run_backtests_mod.run_backtests()

    assert summary["inserted"] == 0
    assert summary["failed"] == 1
    message = next(iter(summary["errors"].values()))
    assert "VolPlausibilityError" in message
    assert "plausible band" in message  # Rule A upper-bound violation, not "degenerate"

    with SessionLocal() as session:
        assert session.query(BacktestResultRecord).count() == 0


# ── The guard must validate the SAME series downstream reads back ──────────
#
# Making the guard read the chosen operation closed only half the gap:
# backtest_repository.get_daily_returns() ignores the `operation` column and
# returns the FIRST non-empty daily_returns in artifact_json["results"]. With
# BACKTEST_OPERATIONS="NIKKEI,SPY" the guard validated SPY while the rigor gate
# graded NIKKEI. A guard that certifies a different series than the gate reads
# is worse than none. Write-time reordering makes them the same row.


def test_persisted_payload_puts_selected_operation_first():
    from archimedes.scripts.run_backtests import _payload_with_selected_operation_first

    payload = {
        "run_id": "r1",
        "results": [
            {"operation": "NIKKEI", "metrics": {"daily_returns": [0.9, -0.9]}},
            {"operation": "SPY", "metrics": {"daily_returns": [0.01, -0.01]}},
        ],
    }
    out = _payload_with_selected_operation_first(payload, "SPY")

    assert [r["operation"] for r in out["results"]] == ["SPY", "NIKKEI"]
    # What get_daily_returns() would return (first non-empty) is now the
    # chosen operation's series, not NIKKEI's.
    assert out["results"][0]["metrics"]["daily_returns"] == [0.01, -0.01]
    # Non-results keys survive untouched.
    assert out["run_id"] == "r1"
    # Original is not mutated.
    assert [r["operation"] for r in payload["results"]] == ["NIKKEI", "SPY"]


def test_persisted_payload_is_untouched_in_the_single_operation_case():
    """No content_hash churn for the current default (BACKTEST_OPERATIONS=SPY)."""
    from archimedes.scripts.run_backtests import _payload_with_selected_operation_first

    payload = {"results": [{"operation": "SPY", "metrics": {"daily_returns": [0.01]}}]}
    assert _payload_with_selected_operation_first(payload, "SPY") is payload
    # Already-first, multi-result case is also a pass-through.
    two = {
        "results": [
            {"operation": "SPY", "metrics": {"daily_returns": [0.01]}},
            {"operation": "NIKKEI", "metrics": {"daily_returns": [0.9]}},
        ]
    }
    assert _payload_with_selected_operation_first(two, "SPY") is two
    # Unknown/absent operation must not reorder or raise.
    assert _payload_with_selected_operation_first(two, "GOLD") is two
    assert _payload_with_selected_operation_first(two, None) is two

from __future__ import annotations

import json
from pathlib import Path

import pytest
from archimedes.services.backtest_mapper import (
    AnalyticsArtifactModel,
    map_artifact_to_backtest_result,
    select_operation_result,
)


def test_artifact_schema_round_trip() -> None:
    artifact_path = Path(__file__).resolve().parent / "fixtures" / "analytics_artifact_buy_hold.json"
    payload = artifact_path.read_text(encoding="utf-8")

    parsed = AnalyticsArtifactModel.model_validate_json(payload)
    dumped = parsed.model_dump_json()
    reparsed = AnalyticsArtifactModel.model_validate_json(dumped)

    assert reparsed.run_id == parsed.run_id
    assert reparsed.strategy.backtest_code_hash == parsed.strategy.backtest_code_hash
    assert len(reparsed.results) == len(parsed.results)


def test_mapper_preserves_buy_hold_sharpe() -> None:
    artifact_path = Path(__file__).resolve().parent / "fixtures" / "analytics_artifact_buy_hold.json"
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact = AnalyticsArtifactModel.model_validate(payload)

    result, operation = map_artifact_to_backtest_result(
        artifact,
        strategy_id="test_strategy",
        operation="SPY",
    )

    assert operation == "SPY"
    assert result.sharpe_ratio == pytest.approx(0.7135863248834242)
    assert result.max_drawdown == pytest.approx(0.3407931346227104)
    assert result.backtest_code_hash == artifact.strategy.backtest_code_hash


def _minimal_metrics(sharpe: float) -> dict:
    return {
        "sharpe_ratio": sharpe,
        "sortino_ratio": None,
        "calmar_ratio": None,
        "max_drawdown_pct": None,
        "cagr": None,
        "win_rate": None,
        "profit_factor": None,
        "total_trades": 0,
        "avg_holding_period_days": None,
        "correlation_to_spy": None,
        "correlation_to_btc": None,
        "equity_curve": [100000.0, 101000.0],
        "monthly_returns": [],
        "backtest_start": "2018-01-01",
        "backtest_end": "2026-01-01",
        "look_ahead_audit_passed": True,
        "backtest_engine": "backtrader",
        "transaction_cost_bps": 10,
    }


def _universe_artifact(*, universe_present: bool) -> dict:
    results = [
        {"operation": "SPY", "symbol": "SPY", "metrics": _minimal_metrics(0.7)},
        {"operation": "NIKKEI", "symbol": "^N225", "metrics": _minimal_metrics(0.3)},
    ]
    if universe_present:
        results.append(
            {
                "operation": "UNIVERSE",
                "symbol": "SPY/NIKKEI",
                "constituent_operations": ["SPY", "NIKKEI"],
                "metrics": _minimal_metrics(0.5),
            }
        )
    return {
        "run_id": "r1",
        "strategy": {"backtest_code_hash": "a" * 64, "paper_claimed_sharpe": None},
        "assumptions": {"transaction_cost_bps": 10},
        "integrity_flags": {"lookahead_audit_passed": True},
        "results": results,
    }


def test_select_operation_result_prefers_universe_composite_over_spy() -> None:
    """The regression this exists for: once a strategy declares a real,
    non-trivial universe, "SPY" is just one of N declared assets — the
    UNIVERSE composite (representing the whole declared universe) must be
    selected ahead of it, not the other way around. Must FAIL without the fix
    (old code always picked "SPY" first)."""
    artifact = AnalyticsArtifactModel.model_validate(_universe_artifact(universe_present=True))

    chosen = select_operation_result(artifact)

    assert chosen.operation == "UNIVERSE"
    assert chosen.metrics.sharpe_ratio == pytest.approx(0.5)
    assert chosen.constituent_operations == ["SPY", "NIKKEI"]


def test_select_operation_result_falls_back_to_spy_when_no_universe_row() -> None:
    """Legacy/no-composite artifacts (e.g. the ad-hoc CLI fallback path) keep
    today's SPY-first behavior unchanged."""
    artifact = AnalyticsArtifactModel.model_validate(_universe_artifact(universe_present=False))

    chosen = select_operation_result(artifact)

    assert chosen.operation == "SPY"


def test_select_operation_result_explicit_operation_still_wins_over_universe() -> None:
    artifact = AnalyticsArtifactModel.model_validate(_universe_artifact(universe_present=True))

    chosen = select_operation_result(artifact, operation="NIKKEI")

    assert chosen.operation == "NIKKEI"


def _multi_feed_artifact() -> dict:
    """Shape cli.run_command's N-feed (cross-sectional) branch produces: a
    SINGLE result row whose operation is the "/"-joined declared universe —
    not "UNIVERSE" and not "SPY" — because the joint N-asset run IS the
    strategy result, with no per-asset rows or averaged composite on top."""
    return {
        "run_id": "r2",
        "strategy": {"backtest_code_hash": "b" * 64, "paper_claimed_sharpe": None},
        "assumptions": {"transaction_cost_bps": 10},
        "integrity_flags": {"lookahead_audit_passed": True},
        "results": [
            {
                "operation": "SPY/NIKKEI/GOLD/TREASURY/OIL",
                "symbol": "SPY/^N225/GC=F/TLT/CL=F",
                "constituent_operations": ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"],
                "metrics": _minimal_metrics(0.42),
            }
        ],
    }


def test_select_operation_result_picks_the_sole_multi_feed_row() -> None:
    """Regression for backtest-vol-audit item 1d: a cross-sectional strategy's
    single run_multi_backtest result — named by its whole joined universe,
    never "UNIVERSE" or "SPY" — must still be selected via the same "only row"
    fallback pairs results already rely on, with no special-casing needed."""
    artifact = AnalyticsArtifactModel.model_validate(_multi_feed_artifact())

    chosen = select_operation_result(artifact)

    assert chosen.operation == "SPY/NIKKEI/GOLD/TREASURY/OIL"
    assert chosen.metrics.sharpe_ratio == pytest.approx(0.42)
    assert chosen.constituent_operations == ["SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"]


def test_map_artifact_to_backtest_result_returns_multi_feed_operation_label() -> None:
    artifact = AnalyticsArtifactModel.model_validate(_multi_feed_artifact())

    mapped, operation = map_artifact_to_backtest_result(artifact, strategy_id="strat")

    assert operation == "SPY/NIKKEI/GOLD/TREASURY/OIL"
    assert mapped.sharpe_ratio == pytest.approx(0.42)

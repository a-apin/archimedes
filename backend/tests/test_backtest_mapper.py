from __future__ import annotations

import json
from pathlib import Path

import pytest
from archimedes.services.backtest_mapper import (
    AnalyticsArtifactModel,
    map_artifact_to_backtest_result,
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

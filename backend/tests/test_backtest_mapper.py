from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from archimedes.services.backtest_mapper import (
    AnalyticsArtifactModel,
    canonical_artifact_hash,
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


# ── canonical_artifact_hash volatile-field exclusion (issue #1347) ──────────
#
# Root cause: canonical_artifact_hash used to hash the WHOLE payload including
# "run_id" and "timestamp_utc", both minted fresh on every run regardless of
# content, so every run's hash was unique by construction and
# insert_backtest_if_missing's content-hash dedupe could never match — the
# table grew ~30 rows/strategy per container restart. These tests are
# mutation-proven: reverting canonical_artifact_hash to `json.dumps(payload,
# sort_keys=True, ...)` (no exclusion) makes
# test_canonical_artifact_hash_ignores_run_id_and_timestamp FAIL, because two
# payloads differing only in the excluded keys would then hash differently.
# See the PR body for the revert/re-apply transcript.


def _full_artifact_payload() -> dict:
    """A raw artifact payload with BOTH volatile fields present, shaped like
    what cli.py / portfolio_backtester.py actually emit (run_id and
    timestamp_utc as siblings at the top level, alongside content)."""
    return {
        "run_id": "20260518T223743Z",
        "timestamp_utc": "2026-05-18T22:37:43.964677+00:00",
        "operations": ["SPY"],
        "strategy": {
            "path": "strategies/pipeline_buy_hold.py",
            "class_name": "BuyAndHold",
            "backtest_code_hash": "sha256:deadbeef",
            "paper_claimed_sharpe": 0.5,
        },
        "assumptions": {
            "start": "2018-01-01",
            "end": "2026-01-01",
            "transaction_cost_bps": 10,
            "backtest_engine": "backtrader",
        },
        "results": [
            {
                "operation": "SPY",
                "symbol": "SPY",
                "metrics": {"sharpe_ratio": 0.71, "total_trades": 12},
            }
        ],
        "data_hashes": ["f4096541e95c439b4cc82cd8660309f63855130e66c4e60f65942ffb96088384"],
        "integrity_flags": {"lookahead_audit_passed": True},
    }


def test_canonical_artifact_hash_ignores_run_id_and_timestamp() -> None:
    """Two payloads differing ONLY in run_id/timestamp_utc must hash equal.

    Mutation check: this assertion fails against the unfixed
    canonical_artifact_hash (plain sort_keys dump of the whole payload,
    no exclusion) because run_id/timestamp_utc differ between the two
    payloads below and would then perturb the hash.
    """
    run_a = _full_artifact_payload()
    run_b = copy.deepcopy(run_a)
    run_b["run_id"] = "20260519T010101Z"
    run_b["timestamp_utc"] = "2026-05-19T01:01:01.000000+00:00"

    assert run_a != run_b  # sanity: the two payloads are not byte-identical
    assert canonical_artifact_hash(run_a) == canonical_artifact_hash(run_b)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("results", 0, "metrics", "sharpe_ratio"), 0.99),
        (("strategy", "backtest_code_hash"), "sha256:different"),
        (("assumptions", "transaction_cost_bps"), 25),
        (("data_hashes",), ["a-completely-different-input-data-hash"]),
    ],
)
def test_canonical_artifact_hash_changes_on_content_mutation(path: tuple, value: object) -> None:
    """Any CONTENT field — including data_hashes, which is a hash of the
    INPUT DATA and therefore not volatile — must still change the hash.
    Guards against an over-broad exclusion swallowing real content."""
    base = _full_artifact_payload()
    mutated = copy.deepcopy(base)

    node = mutated
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value

    assert canonical_artifact_hash(base) != canonical_artifact_hash(mutated)


def test_canonical_artifact_hash_stable_without_volatile_keys() -> None:
    """A payload that never had run_id/timestamp_utc (e.g. the DSL-fusion
    artifact shape, which never included them) hashes the same before and
    after the exclusion — the fix must be a no-op when the keys are absent."""
    payload = {"results": [{"metrics": {"daily_returns": [0.01, -0.02]}}], "source": "dsl_fusion"}

    assert canonical_artifact_hash(payload) == canonical_artifact_hash(copy.deepcopy(payload))

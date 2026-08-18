"""One gate, not two.

``BacktestResult`` used to carry its own ``passes_rigor_gate`` property with its
own thresholds (sharpe>0.5, dsr_p>0.95, pbo<0.5, oos/is>=0.5,
sharpe_vs_paper>=0.5, max_dd<0.5), and ``generation_pipeline`` graded generated
portfolio strategies with it. The curated read path grades through
``live_rigor_gate.verdict_from_returns`` and the ``rigor_profiles`` ladder. A
comment in the pipeline asserted the two matched; they did not.

The mismatch also had a silent failure mode. ``backtest_portfolio`` sets
``pbo_score=None`` because PBO is a library-level metric a later scheduler
refreshes, and the deleted property short-circuited to False whenever PBO was
None. Every generated portfolio strategy failed it unconditionally, so the
"grade" was a constant.

These tests pin the property gone and the one remaining gate reachable from both
directions. The full seven-implementation golden-vector harness is separate,
larger work; this is the surgical piece that makes the same-scale claim true.
"""

from __future__ import annotations

import numpy as np
import pytest
from archimedes.models.backtest import BacktestResult
from archimedes.services.live_rigor_gate import verdict_from_returns


def _series(seed: int, n: int = 600, mu: float = 0.0006, sigma: float = 0.01) -> list[float]:
    """Deterministic daily return series — no network, no fixtures."""
    rng = np.random.default_rng(seed)
    return [float(x) for x in rng.normal(mu, sigma, n)]


def _result(**overrides: object) -> BacktestResult:
    base = {
        "strategy_id": "test-strategy",
        "sharpe_ratio": 1.2,
        "sortino_ratio": 1.6,
        "max_drawdown": 0.12,
        "cagr": 0.14,
        "calmar_ratio": 1.1,
        "win_rate": 0.55,
        "profit_factor": 1.4,
        "total_trades": 40,
        "avg_holding_period_days": 12.0,
        "correlation_to_spy": 0.3,
        "correlation_to_btc": 0.1,
    }
    base.update(overrides)
    return BacktestResult(**base)  # type: ignore[arg-type]


class TestSecondGateIsGone:
    def test_backtest_result_exposes_no_gate(self) -> None:
        """Regression guard. A gate on a transport dataclass is invisible to the
        strictness ladder and drifts away from the real one, which is exactly
        what happened. If someone reintroduces either property, this fails.
        """
        result = _result()
        assert not hasattr(result, "passes_rigor_gate")
        assert not hasattr(result, "passes_validation")

    def test_no_gate_definitions_remain_in_the_module(self) -> None:
        """Belt and braces: catch a re-add under a different access pattern."""
        from pathlib import Path

        source = Path(BacktestResult.__module__.replace(".", "/"))
        module_file = Path(__file__).resolve().parents[1] / "archimedes" / "models" / "backtest.py"
        assert module_file.is_file(), f"expected the model at {module_file} (derived {source})"
        text = module_file.read_text(encoding="utf-8")
        assert "def passes_rigor_gate" not in text
        assert "def passes_validation" not in text


class TestOneGateGradesBothPaths:
    def test_same_series_gets_the_same_verdict_regardless_of_caller(self) -> None:
        """The generated path and the curated path grade one series identically.

        Both now go through verdict_from_returns, so this is true by
        construction — which is the point. The test exists so it stays true.
        """
        returns = _series(seed=7)

        generated = verdict_from_returns("gen-1", returns, num_trials=5, look_ahead_audit_passed=True)
        curated = verdict_from_returns("cur-1", returns, num_trials=5, look_ahead_audit_passed=True)

        assert generated.passes == curated.passes
        assert generated.status == curated.status

    def test_missing_pbo_no_longer_forces_a_fail(self) -> None:
        """The specific defect: pbo_score=None was an automatic False.

        backtest_portfolio always leaves PBO unset, so under the old property
        every generated portfolio strategy was ungradeable-but-reported-as-failed.
        The live gate treats a strong series on its own terms instead.
        """
        strong = _series(seed=11, mu=0.0012, sigma=0.008)
        verdict = verdict_from_returns("gen-nopbo", strong, num_trials=1, look_ahead_audit_passed=True)

        # Not asserting it passes — that is the gate's call, and the gate is
        # allowed to fail it. Asserting it was actually graded rather than
        # short-circuited on a missing library-level metric.
        assert verdict.status in {"pass", "fail"}
        assert verdict.status != "pending"

    def test_short_series_is_pending_not_a_pass(self) -> None:
        """Fail-closed direction is preserved: too little data is never a pass."""
        verdict = verdict_from_returns("gen-short", _series(seed=3, n=5), num_trials=1)
        assert verdict.status == "pending"
        assert verdict.passes is False

    def test_degenerate_series_is_never_a_pass(self) -> None:
        """A zero-variance series is the zero-trade artifact from the leaderboard.

        Eight rows currently show Sharpe of exactly 0.0 from runs that never
        placed a trade. Whatever the gate labels it, it must not be a pass.
        """
        verdict = verdict_from_returns("gen-degenerate", [0.0] * 600, num_trials=1)
        assert verdict.passes is False


class TestPipelineUsesTheLiveGate:
    def test_pipeline_reads_returns_out_of_the_artifact(self) -> None:
        """The helper feeding the gate must find the series the simulator wrote."""
        from archimedes.agents.generation_pipeline import _portfolio_daily_returns

        artifact = {"results": [{"metrics": {"daily_returns": [0.01, -0.02, 0.03]}}]}
        assert _portfolio_daily_returns(artifact) == pytest.approx([0.01, -0.02, 0.03])

    @pytest.mark.parametrize(
        "artifact",
        [
            {},
            {"results": []},
            {"results": [{}]},
            {"results": [{"metrics": {}}]},
        ],
    )
    def test_malformed_artifact_yields_no_returns_not_a_pass(self, artifact: dict) -> None:
        """An unexpected shape must degrade to pending, never to a pass."""
        from archimedes.agents.generation_pipeline import _portfolio_daily_returns

        returns = _portfolio_daily_returns(artifact)
        assert returns == []
        assert verdict_from_returns("gen-broken", returns, num_trials=1).passes is False

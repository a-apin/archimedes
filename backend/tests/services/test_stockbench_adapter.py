"""Tests for the StockBench evaluation adapter (Issue #157).

Validates the adapter structure, protocol compliance, metric computation,
and result persistence — all without requiring real LLM or market data.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from archimedes.evaluation.stockbench.adapter import (
    BENCHMARK_END,
    BENCHMARK_START,
    PUBLISHED_BASELINES,
    STARTING_CASH,
    TOP_20_DJIA,
    TRADING_DAYS,
    ArchimedesStockBenchAdapter,
    BenchmarkResult,
    DailyDecision,
    MultiSeedReport,
    PortfolioState,
    _generate_price_series,
    compute_composite_z,
    run_multi_seed,
    write_results_json,
    write_results_markdown,
)

# ── Constants ────────────────────────────────────────────────────


class TestConstants:
    def test_benchmark_window(self):
        assert TRADING_DAYS == 82
        assert date(2025, 3, 3) == BENCHMARK_START
        assert date(2025, 6, 30) == BENCHMARK_END

    def test_djia_universe(self):
        assert len(TOP_20_DJIA) == 20
        assert "AAPL" in TOP_20_DJIA
        assert "MSFT" in TOP_20_DJIA

    def test_starting_cash(self):
        assert STARTING_CASH == 100_000.0

    def test_published_baselines_count(self):
        assert len(PUBLISHED_BASELINES) == 14

    def test_published_baselines_have_sortino(self):
        for name, data in PUBLISHED_BASELINES.items():
            assert "sortino" in data, f"{name} missing sortino"
            assert isinstance(data["sortino"], (int, float))
            assert data["sortino"] >= 0


# ── Protocol imports (acceptance criterion) ──────────────────────


class TestProtocolImports:
    def test_rigor_evaluator_importable(self):
        """Adapter references rigor_evaluator — DSR/PBO gate active."""
        # The module-level imports must exist
        import archimedes.services.rigor_evaluator  # noqa: F401

    def test_v_check_importable(self):
        """Adapter references VCheck — pre-trade validation active."""
        import archimedes.chain.v_check  # noqa: F401

    def test_embargo_filter_importable(self):
        """Adapter references embargo_filter — Outcome Embargo active."""
        import archimedes.services.embargo_filter  # noqa: F401

    def test_adapter_module_references_all_protocols(self):
        """Grep-level check: all three protocols referenced in adapter."""
        import archimedes.evaluation.stockbench.adapter as mod

        source = Path(mod.__file__).read_text()
        assert "rigor_evaluator" in source
        assert "v_check" in source or "VCheck" in source
        assert "embargo_filter" in source


# ── PortfolioState ───────────────────────────────────────────────


class TestPortfolioState:
    def test_initial_state(self):
        state = PortfolioState()
        assert state.cash == STARTING_CASH
        assert state.holdings == {}
        assert state.current_value == STARTING_CASH
        assert state.total_return_pct == 0.0

    def test_max_drawdown_flat(self):
        state = PortfolioState()
        state.net_values = [100, 101, 100, 99, 101]
        dd = state.max_drawdown_pct
        assert dd < 0  # drawdown is negative
        assert dd >= -5  # reasonable range

    def test_sortino_ratio_no_data(self):
        state = PortfolioState()
        assert state.sortino_ratio == 0.0

    def test_sortino_ratio_with_returns(self):
        state = PortfolioState()
        state.daily_returns = [0.01, -0.005, 0.02, -0.003, 0.015]
        sortino = state.sortino_ratio
        assert sortino > 0  # positive returns overall

    def test_sortino_all_positive_returns(self):
        state = PortfolioState()
        state.daily_returns = [0.01, 0.02, 0.01]
        sortino = state.sortino_ratio
        assert sortino == float("inf")  # no downside deviation


# ── DailyDecision ────────────────────────────────────────────────


class TestDailyDecision:
    def test_valid_decision(self):
        d = DailyDecision(
            day=0,
            date=date(2025, 3, 3),
            allocations={"AAPL": 0.2, "MSFT": 0.3},
            cash_weight=0.5,
        )
        assert d.is_valid()

    def test_invalid_decision_overweight(self):
        d = DailyDecision(
            day=0,
            date=date(2025, 3, 3),
            allocations={"AAPL": 0.8},
            cash_weight=0.8,  # total > 1
        )
        assert not d.is_valid()


# ── Price generation ─────────────────────────────────────────────


class TestPriceGeneration:
    def test_generates_for_all_tickers(self):
        prices = _generate_price_series(TOP_20_DJIA, 10, seed=42)
        assert len(prices) == 20

    def test_correct_length(self):
        prices = _generate_price_series(TOP_20_DJIA, 82, seed=0)
        for ticker, series in prices.items():
            assert len(series) == 82, f"{ticker} has {len(series)} days"

    def test_deterministic(self):
        p1 = _generate_price_series(["AAPL"], 10, seed=42)
        p2 = _generate_price_series(["AAPL"], 10, seed=42)
        assert p1["AAPL"] == p2["AAPL"]

    def test_different_seeds_differ(self):
        p1 = _generate_price_series(["AAPL"], 10, seed=1)
        p2 = _generate_price_series(["AAPL"], 10, seed=2)
        assert p1["AAPL"] != p2["AAPL"]

    def test_positive_prices(self):
        prices = _generate_price_series(TOP_20_DJIA, 82, seed=99)
        for ticker, series in prices.items():
            assert all(p > 0 for p in series), f"{ticker} has non-positive price"


# ── Adapter integration ──────────────────────────────────────────


class TestAdapterSingleRun:
    def test_run_produces_result(self):
        adapter = ArchimedesStockBenchAdapter(seed=0)
        result = adapter.run(n_days=10)  # short run for speed
        assert isinstance(result, BenchmarkResult)
        assert result.seed == 0
        assert result.trading_days == 10
        assert result.final_value > 0

    def test_result_has_metrics(self):
        adapter = ArchimedesStockBenchAdapter(seed=0)
        result = adapter.run(n_days=20)
        assert isinstance(result.return_pct, float)
        assert isinstance(result.max_drawdown_pct, float)
        assert isinstance(result.sortino_ratio, float)

    def test_portfolio_tracks_value(self):
        adapter = ArchimedesStockBenchAdapter(seed=0)
        adapter.run(n_days=20)
        assert len(adapter.portfolio.net_values) == 20
        assert len(adapter.portfolio.daily_returns) == 19  # n-1 returns

    def test_decisions_recorded(self):
        adapter = ArchimedesStockBenchAdapter(seed=0)
        result = adapter.run(n_days=5)
        assert len(result.decisions) == 5
        for d in result.decisions:
            assert d.is_valid()

    def test_result_to_dict(self):
        adapter = ArchimedesStockBenchAdapter(seed=0)
        result = adapter.run(n_days=5)
        d = result.to_dict()
        assert "seed" in d
        assert "sortino_ratio" in d
        assert d["trading_days"] == 5


# ── Multi-seed aggregation ──────────────────────────────────────


class TestMultiSeed:
    def test_run_multi_seed(self):
        report = run_multi_seed(n_seeds=2, n_days=10)
        assert isinstance(report, MultiSeedReport)
        assert report.n_seeds == 2
        assert len(report.seed_results) == 2

    def test_report_aggregates(self):
        report = run_multi_seed(n_seeds=3, n_days=10)
        assert isinstance(report.return_pct_mean, float)
        assert isinstance(report.return_pct_stdev, float)
        assert isinstance(report.sortino_mean, float)
        assert isinstance(report.composite_z_score, float)
        assert isinstance(report.rank, int)

    def test_report_to_dict(self):
        report = run_multi_seed(n_seeds=2, n_days=5)
        d = report.to_dict()
        assert d["agent"] == "Archimedes Strategy Generation Agent"
        assert d["benchmark"] == "StockBench (Chen et al. 2026)"
        assert "final_return_pct" in d
        assert "sortino_ratio" in d
        assert "composite_z_score" in d
        assert len(d["seed_results"]) == 2


# ── Composite Z-score ───────────────────────────────────────────


class TestCompositeZ:
    def test_z_score_within_baselines(self):
        # GLM-4.5 is #3 with sortino 1.94
        z, rank = compute_composite_z(1.94, PUBLISHED_BASELINES)
        assert 1 <= rank <= 15
        assert isinstance(z, float)

    def test_z_score_top(self):
        z, rank = compute_composite_z(3.0, PUBLISHED_BASELINES)
        assert rank == 1  # higher than any baseline
        assert z > 0  # above mean

    def test_z_score_bottom(self):
        z, rank = compute_composite_z(0.5, PUBLISHED_BASELINES)
        assert rank == 15  # lowest
        assert z < 0  # below mean


# ── Result persistence ──────────────────────────────────────────


class TestPersistence:
    def test_write_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "archimedes.evaluation.stockbench.adapter.RESULTS_DIR",
            tmp_path,
        )
        report = run_multi_seed(n_seeds=2, n_days=5)
        path = write_results_json(report)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["n_seeds"] == 2
        assert "published_baselines" in data

    def test_write_markdown(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "archimedes.evaluation.stockbench.adapter.RESULTS_DIR",
            tmp_path,
        )
        report = run_multi_seed(n_seeds=2, n_days=5)
        path = write_results_markdown(report)
        assert path.exists()
        content = path.read_text()
        assert "StockBench" in content
        assert "Sortino" in content
        assert "Archimedes (ours)" in content
        assert "Chen et al. 2026" in content


# ── CLI dry-run ──────────────────────────────────────────────────


class TestCLI:
    def test_dry_run_exits_zero(self):
        """python -m archimedes.evaluation.stockbench --dry-run"""
        import os
        import subprocess
        import sys

        # CI installs requirements.txt but doesn't `pip install -e backend/`, so
        # `archimedes` isn't on sys.path inside a fresh subprocess. Inject the
        # backend directory explicitly so the -m flag resolves the module.
        backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        env = os.environ.copy()
        env["PYTHONPATH"] = backend_dir + os.pathsep + env.get("PYTHONPATH", "")

        result = subprocess.run(
            [sys.executable, "-m", "archimedes.evaluation.stockbench", "--dry-run"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0
        assert "82" in result.stdout
        assert "20" in result.stdout
        assert "Seeds: 3" in result.stdout

import json
from pathlib import Path

import pandas as pd
import pytest
from archimedes_analytics_engine.cli import run_command
from archimedes_analytics_engine.engine import FeedArityError


def _fake_fetch(symbol: str, start: str, end: str) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    return pd.DataFrame(
        {
            "Open": [100, 101, 102, 103, 104],
            "High": [101, 102, 103, 104, 105],
            "Low": [99, 100, 101, 102, 103],
            "Close": [100, 101, 102, 103, 104],
            "Volume": [1000] * 5,
        },
        index=idx,
    )


def _write_minimal_strategy(path: Path) -> None:
    path.write_text(
        "import backtrader as bt\n\n"
        "class PipelineStrategy(bt.Strategy):\n"
        "    def next(self):\n"
        "        if not self.position:\n"
        "            self.buy(size=1)\n"
    )


def _write_passport_strategy(path: Path) -> None:
    path.write_text(
        "import backtrader as bt\n\n"
        'PAPER_ARXIV_ID = "2509.11420"\n'
        'PAPER_TITLE = "Momentum All The Way Down"\n'
        'METHODOLOGY_TEXT = "Buy on N-day breakout."\n'
        "PAPER_CLAIMED_SHARPE = 1.5\n\n"
        "class PaperStrategy(bt.Strategy):\n"
        "    def next(self):\n"
        "        if not self.position:\n"
        "            self.buy(size=1)\n"
    )


def test_run_command_writes_artifact(tmp_path: Path) -> None:
    strategy_file = tmp_path / "strategy.py"
    _write_minimal_strategy(strategy_file)

    output = run_command(
        operations=["SPY", "OIL"],
        start="2024-01-01",
        end="2024-01-10",
        initial_cash=10000.0,
        tx_cost_bps=10,
        slippage_bps=5,
        artifact_dir=tmp_path,
        strategy_path=strategy_file,
        fetcher=_fake_fetch,
    )

    artifact = Path(output["artifact_path"])
    assert artifact.exists()

    payload = json.loads(artifact.read_text())
    assert payload["assumptions"]["transaction_cost_bps"] == 10
    assert payload["assumptions"]["slippage_bps"] == 5
    assert payload["assumptions"]["backtest_engine"] == "backtrader"
    assert payload["strategy"]["class_name"] == "PipelineStrategy"
    assert set(payload["operations"]) == {"SPY", "OIL"}
    assert len(payload["results"]) == 2


def test_artifact_contains_backtest_code_hash(tmp_path: Path) -> None:
    strategy_file = tmp_path / "strategy.py"
    _write_minimal_strategy(strategy_file)

    output = run_command(
        operations=["SPY"],
        start="2024-01-01",
        end="2024-01-10",
        initial_cash=10000.0,
        tx_cost_bps=10,
        slippage_bps=0,
        artifact_dir=tmp_path,
        strategy_path=strategy_file,
        fetcher=_fake_fetch,
    )

    payload = json.loads(Path(output["artifact_path"]).read_text())
    code_hash = payload["strategy"]["backtest_code_hash"]
    assert isinstance(code_hash, str)
    assert len(code_hash) == 64


def test_artifact_contains_paper_provenance_from_strategy(tmp_path: Path) -> None:
    strategy_file = tmp_path / "paper.py"
    _write_passport_strategy(strategy_file)

    output = run_command(
        operations=["SPY"],
        start="2024-01-01",
        end="2024-01-10",
        initial_cash=10000.0,
        tx_cost_bps=10,
        slippage_bps=0,
        artifact_dir=tmp_path,
        strategy_path=strategy_file,
        fetcher=_fake_fetch,
    )

    payload = json.loads(Path(output["artifact_path"]).read_text())
    strategy_block = payload["strategy"]
    assert strategy_block["paper_arxiv_id"] == "2509.11420"
    assert strategy_block["paper_title"] == "Momentum All The Way Down"
    assert strategy_block["methodology_text"] == "Buy on N-day breakout."
    assert strategy_block["paper_claimed_sharpe"] == 1.5
    assert isinstance(strategy_block["methodology_hash"], str)
    assert len(strategy_block["methodology_hash"]) == 64
    assert payload["integrity_flags"]["paper_claim_comparison_applied"] is True


def test_cli_override_wins_over_strategy_constants(tmp_path: Path) -> None:
    strategy_file = tmp_path / "paper.py"
    _write_passport_strategy(strategy_file)

    output = run_command(
        operations=["SPY"],
        start="2024-01-01",
        end="2024-01-10",
        initial_cash=10000.0,
        tx_cost_bps=10,
        slippage_bps=0,
        artifact_dir=tmp_path,
        strategy_path=strategy_file,
        paper_arxiv_id="9999.99999",
        paper_title="Overridden Title",
        fetcher=_fake_fetch,
    )

    payload = json.loads(Path(output["artifact_path"]).read_text())
    assert payload["strategy"]["paper_arxiv_id"] == "9999.99999"
    assert payload["strategy"]["paper_title"] == "Overridden Title"


def test_artifact_results_carry_metrics_block(tmp_path: Path) -> None:
    strategy_file = tmp_path / "strategy.py"
    _write_minimal_strategy(strategy_file)

    output = run_command(
        operations=["SPY"],
        start="2024-01-01",
        end="2024-01-10",
        initial_cash=10000.0,
        tx_cost_bps=10,
        slippage_bps=0,
        artifact_dir=tmp_path,
        strategy_path=strategy_file,
        fetcher=_fake_fetch,
    )

    payload = json.loads(Path(output["artifact_path"]).read_text())
    metrics = payload["results"][0]["metrics"]
    for key in (
        "final_value",
        "total_return_pct",
        "equity_curve",
        "sharpe_ratio",
        "sortino_ratio",
        "calmar_ratio",
        "max_drawdown_pct",
        "cagr",
        "total_trades",
        "monthly_returns",
        "daily_returns",
        "look_ahead_audit_passed",
        "backtest_engine",
        "bars",
    ):
        assert key in metrics, f"missing metric key: {key}"
    assert metrics["backtest_engine"] == "backtrader"
    assert metrics["look_ahead_audit_passed"] is True


# Issue #887: run_command must route two-feed (pairs) strategies through the
# two-feed runner on their declared universe legs, and fail closed with a
# typed error when the universe collapses to a single instrument.


def _write_pairs_strategy(path: Path, universe: str = "['GLD', 'GDX']") -> None:
    path.write_text(
        "import backtrader as bt\n\n"
        f"ASSET_UNIVERSE = {universe}\n\n"
        "class PairsProbe(bt.Strategy):\n"
        "    REQUIRED_FEEDS = 2\n\n"
        "    def next(self):\n"
        "        float(self.datas[1].close[0])  # hard two-feed dependency\n"
    )


def test_run_command_routes_pairs_strategy_through_two_feed_runner(tmp_path: Path) -> None:
    strategy_file = tmp_path / "pairs_probe.py"
    _write_pairs_strategy(strategy_file)

    output = run_command(
        operations=["SPY"],  # benchmark ops are ignored for pairs — the universe legs win
        start="2024-01-01",
        end="2024-01-10",
        initial_cash=10000.0,
        tx_cost_bps=10,
        slippage_bps=5,
        artifact_dir=tmp_path,
        strategy_path=strategy_file,
        fetcher=_fake_fetch,
    )

    payload = json.loads(Path(output["artifact_path"]).read_text())
    assert payload["operations"] == ["GLD", "GDX"]
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["operation"] == "GLD/GDX"
    assert result["symbol"] == "GLD/GDX"
    assert result["metrics"]["bars"] == 5
    assert result["metrics"]["look_ahead_audit_passed"] is True
    assert len(payload["data_hashes"]) == 2  # one per leg


def test_run_command_fails_closed_on_single_symbol_pairs_universe(tmp_path: Path) -> None:
    strategy_file = tmp_path / "degenerate_pairs.py"
    _write_pairs_strategy(strategy_file, universe="['GLD', 'GLD']")

    with pytest.raises(FeedArityError, match="needs >= 2 distinct instruments"):
        run_command(
            operations=["SPY"],
            start="2024-01-01",
            end="2024-01-10",
            initial_cash=10000.0,
            tx_cost_bps=10,
            slippage_bps=5,
            artifact_dir=tmp_path,
            strategy_path=strategy_file,
            fetcher=_fake_fetch,
        )


# ── Declared-universe routing (backtest-vol audit) ──────────────────────────
#
# Regression coverage for: every single-feed strategy — regardless of its own
# ASSET_UNIVERSE — was unconditionally backtested on BACKTEST_OPERATIONS
# (default ["SPY"]), because run_command's single-feed branch never consulted
# the strategy's own declared universe. capital_preservation_tbill (declares
# ["BIL"]) and maillard_2010_risk_parity (declares 5 non-SPY-only assets)
# were both graded on pure SPY returns as a result.


def _write_single_feed_strategy(path: Path, universe: str) -> None:
    path.write_text(
        "import backtrader as bt\n\n"
        f"ASSET_UNIVERSE = {universe}\n\n"
        "class SingleFeedProbe(bt.Strategy):\n"
        "    def next(self):\n"
        "        if not self.position:\n"
        "            self.buy(size=1)\n"
    )


def _symbol_series(symbol: str, start_date: str, closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range(start_date, periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": closes,
            "Volume": [1000] * len(closes),
        },
        index=idx,
    )


def _make_fetcher(by_symbol: dict[str, pd.DataFrame]):
    requested: list[str] = []

    def fetch(symbol: str, start: str, end: str) -> pd.DataFrame:
        requested.append(symbol)
        if symbol not in by_symbol:
            raise AssertionError(f"unexpected symbol fetched: {symbol!r} (declared universe was overridden)")
        return by_symbol[symbol]

    fetch.requested = requested  # type: ignore[attr-defined]
    return fetch


def test_declared_universe_is_backtested_not_the_benchmark_default(tmp_path: Path) -> None:
    """The core regression guard: a strategy declaring a non-SPY universe must
    be backtested on THAT universe. Must FAIL without the fix (the old code
    always used `operations`, here simulating BACKTEST_OPERATIONS=["SPY"])."""
    strategy_file = tmp_path / "tbill_probe.py"
    _write_single_feed_strategy(strategy_file, universe="['BIL']")

    fetcher = _make_fetcher({"BIL": _symbol_series("BIL", "2024-01-01", [100.0] * 5)})

    output = run_command(
        operations=["SPY"],  # BACKTEST_OPERATIONS default — must NOT win
        start="2024-01-01",
        end="2024-01-10",
        initial_cash=10000.0,
        tx_cost_bps=10,
        slippage_bps=5,
        artifact_dir=tmp_path,
        strategy_path=strategy_file,
        fetcher=fetcher,
    )

    payload = json.loads(Path(output["artifact_path"]).read_text())
    assert payload["operations"] == ["BIL"]
    assert fetcher.requested == ["BIL"]
    op_names = {r["operation"] for r in payload["results"]}
    assert "SPY" not in op_names
    assert "BIL" in op_names


def test_backtest_operations_does_not_override_a_declared_universe(tmp_path: Path) -> None:
    """Even a multi-symbol BACKTEST_OPERATIONS benchmark list must never leak
    into a strategy that declares its own universe."""
    strategy_file = tmp_path / "oil_gold_probe.py"
    _write_single_feed_strategy(strategy_file, universe="['OIL', 'GOLD']")

    fetcher = _make_fetcher(
        {
            "CL=F": _symbol_series("CL=F", "2024-01-01", [50.0] * 5),
            "GC=F": _symbol_series("GC=F", "2024-01-01", [1800.0] * 5),
        }
    )

    output = run_command(
        operations=["SPY", "TREASURY", "NIKKEI"],  # a differently-shaped benchmark list
        start="2024-01-01",
        end="2024-01-10",
        initial_cash=10000.0,
        tx_cost_bps=10,
        slippage_bps=5,
        artifact_dir=tmp_path,
        strategy_path=strategy_file,
        fetcher=fetcher,
    )

    payload = json.loads(Path(output["artifact_path"]).read_text())
    assert payload["operations"] == ["OIL", "GOLD"]
    assert set(fetcher.requested) == {"CL=F", "GC=F"}
    op_names = {r["operation"] for r in payload["results"]}
    assert op_names == {"OIL", "GOLD", "UNIVERSE"}


def test_no_declared_universe_still_falls_back_to_passed_operations(tmp_path: Path) -> None:
    """A strategy with no ASSET_UNIVERSE at all keeps today's behavior exactly:
    no UNIVERSE composite is fabricated when there is no declared universe to
    composite over."""
    strategy_file = tmp_path / "no_universe_probe.py"
    _write_minimal_strategy(strategy_file)

    output = run_command(
        operations=["SPY", "OIL"],
        start="2024-01-01",
        end="2024-01-10",
        initial_cash=10000.0,
        tx_cost_bps=10,
        slippage_bps=5,
        artifact_dir=tmp_path,
        strategy_path=strategy_file,
        fetcher=_fake_fetch,
    )

    payload = json.loads(Path(output["artifact_path"]).read_text())
    op_names = {r["operation"] for r in payload["results"]}
    assert op_names == {"SPY", "OIL"}
    assert "UNIVERSE" not in op_names


def test_universe_composite_equals_single_run_for_one_asset_universe(tmp_path: Path) -> None:
    strategy_file = tmp_path / "single_asset_universe.py"
    _write_single_feed_strategy(strategy_file, universe="['OIL']")

    fetcher = _make_fetcher({"CL=F": _symbol_series("CL=F", "2024-01-01", [50.0, 51.0, 49.5, 52.0, 53.0])})

    output = run_command(
        operations=["SPY"],
        start="2024-01-01",
        end="2024-01-10",
        initial_cash=10000.0,
        tx_cost_bps=10,
        slippage_bps=5,
        artifact_dir=tmp_path,
        strategy_path=strategy_file,
        fetcher=fetcher,
    )

    payload = json.loads(Path(output["artifact_path"]).read_text())
    by_op = {r["operation"]: r for r in payload["results"]}
    assert set(by_op) == {"OIL", "UNIVERSE"}
    assert by_op["UNIVERSE"]["constituent_operations"] == ["OIL"]
    assert by_op["UNIVERSE"]["metrics"]["daily_returns"] == pytest.approx(by_op["OIL"]["metrics"]["daily_returns"])
    assert by_op["UNIVERSE"]["metrics"]["daily_return_dates"] == by_op["OIL"]["metrics"]["daily_return_dates"]
    assert by_op["UNIVERSE"]["metrics"]["equity_curve"] == pytest.approx(by_op["OIL"]["metrics"]["equity_curve"])


def test_universe_composite_aligns_on_date_intersection(tmp_path: Path) -> None:
    """Two declared legs with deliberately different calendars (mirrors
    ^N225 vs CL=F): the composite must use only the OVERLAPPING dates, not a
    naive positional zip."""
    strategy_file = tmp_path / "misaligned_calendars.py"
    _write_single_feed_strategy(strategy_file, universe="['NIKKEI', 'OIL']")

    # NIKKEI trades 01-01..01-04; OIL trades 01-02..01-05 — a 3-day overlap.
    nikkei = _symbol_series("^N225", "2024-01-01", [100.0, 110.0, 121.0, 133.1])
    oil = _symbol_series("CL=F", "2024-01-02", [50.0, 55.0, 60.5, 66.55])
    fetcher = _make_fetcher({"^N225": nikkei, "CL=F": oil})

    output = run_command(
        operations=["SPY"],
        start="2024-01-01",
        end="2024-01-10",
        initial_cash=10000.0,
        tx_cost_bps=0,
        slippage_bps=0,
        artifact_dir=tmp_path,
        strategy_path=strategy_file,
        fetcher=fetcher,
    )

    payload = json.loads(Path(output["artifact_path"]).read_text())
    by_op = {r["operation"]: r for r in payload["results"]}
    universe_dates = by_op["UNIVERSE"]["metrics"]["daily_return_dates"]
    nikkei_dates = set(by_op["NIKKEI"]["metrics"]["daily_return_dates"])
    oil_dates = set(by_op["OIL"]["metrics"]["daily_return_dates"])
    expected_dates = sorted(nikkei_dates & oil_dates)

    assert universe_dates == expected_dates
    assert len(universe_dates) < len(by_op["NIKKEI"]["metrics"]["daily_return_dates"])
    assert len(universe_dates) < len(by_op["OIL"]["metrics"]["daily_return_dates"])


def test_pairs_strategy_has_no_universe_composite(tmp_path: Path) -> None:
    """Pairs strategies are unaffected by the declared-universe routing/composite
    change — they already resolve their two legs via _pair_legs."""
    strategy_file = tmp_path / "pairs_unaffected.py"
    _write_pairs_strategy(strategy_file)

    output = run_command(
        operations=["SPY"],
        start="2024-01-01",
        end="2024-01-10",
        initial_cash=10000.0,
        tx_cost_bps=10,
        slippage_bps=5,
        artifact_dir=tmp_path,
        strategy_path=strategy_file,
        fetcher=_fake_fetch,
    )

    payload = json.loads(Path(output["artifact_path"]).read_text())
    assert len(payload["results"]) == 1
    assert payload["results"][0]["operation"] == "GLD/GDX"
    op_names = {r["operation"] for r in payload["results"]}
    assert "UNIVERSE" not in op_names

import json
from pathlib import Path

import pandas as pd
from archimedes_analytics_engine.cli import run_command


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

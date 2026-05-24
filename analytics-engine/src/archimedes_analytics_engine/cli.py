from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pandas as pd

from .data import fetch_ohlcv
from .engine import BACKTEST_ENGINE_TAG, run_backtest
from .instruments import OPERATION_TO_SYMBOL, resolve_operations
from .strategy_loader import load_strategy


def _hash_frame(df: pd.DataFrame) -> str:
    as_csv = df.to_csv(index=True).encode("utf-8")
    return sha256(as_csv).hexdigest()


def _hash_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _merge_metadata(
    file_metadata: dict[str, Any],
    *,
    paper_arxiv_id: str | None,
    paper_title: str | None,
    methodology_text: str | None,
    paper_claimed_sharpe: float | None,
    paper_claimed_cagr: float | None,
    paper_claimed_max_dd: float | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {**file_metadata}
    overrides = {
        "paper_arxiv_id": paper_arxiv_id,
        "paper_title": paper_title,
        "methodology_text": methodology_text,
        "paper_claimed_sharpe": paper_claimed_sharpe,
        "paper_claimed_cagr": paper_claimed_cagr,
        "paper_claimed_max_dd": paper_claimed_max_dd,
    }
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value
    return merged


def run_command(
    *,
    operations: list[str],
    start: str,
    end: str,
    initial_cash: float,
    tx_cost_bps: int,
    slippage_bps: int,
    artifact_dir: Path,
    strategy_path: Path,
    strategy_class: str | None = None,
    paper_arxiv_id: str | None = None,
    paper_title: str | None = None,
    methodology_text: str | None = None,
    paper_claimed_sharpe: float | None = None,
    paper_claimed_cagr: float | None = None,
    paper_claimed_max_dd: float | None = None,
    walk_forward_split: float | None = None,
    fetcher: Callable[[str, str, str], pd.DataFrame] = fetch_ohlcv,
) -> dict:
    ops = resolve_operations(operations)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    bundle = load_strategy(strategy_path, strategy_class)

    metadata = _merge_metadata(
        bundle.metadata,
        paper_arxiv_id=paper_arxiv_id,
        paper_title=paper_title,
        methodology_text=methodology_text,
        paper_claimed_sharpe=paper_claimed_sharpe,
        paper_claimed_cagr=paper_claimed_cagr,
        paper_claimed_max_dd=paper_claimed_max_dd,
    )

    methodology_text_val = metadata.get("methodology_text")
    methodology_hash = _hash_text(methodology_text_val) if methodology_text_val else None

    results: list[dict] = []
    data_hashes: list[str] = []

    for op in ops:
        symbol = OPERATION_TO_SYMBOL[op]
        prices = fetcher(symbol, start, end)
        data_hashes.append(_hash_frame(prices))

        bt_result = run_backtest(
            prices,
            strategy_cls=bundle.cls,
            initial_cash=initial_cash,
            transaction_cost_bps=tx_cost_bps,
            slippage_bps=slippage_bps,
        )

        results.append(
            {
                "operation": op,
                "symbol": symbol,
                "metrics": asdict(bt_result),
            }
        )

    lookahead_passed = all(r["metrics"]["look_ahead_audit_passed"] for r in results) if results else False
    paper_claim_applied = metadata.get("paper_claimed_sharpe") is not None

    artifact = {
        "run_id": run_id,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "operations": ops,
        "strategy": {
            "path": str(strategy_path),
            "class_name": bundle.cls.__name__,
            "backtest_code_hash": bundle.source_hash,
            "paper_arxiv_id": metadata.get("paper_arxiv_id"),
            "paper_title": metadata.get("paper_title"),
            "paper_authors": metadata.get("paper_authors"),
            "paper_venue": metadata.get("paper_venue"),
            "paper_year": metadata.get("paper_year"),
            "paper_doi": metadata.get("paper_doi"),
            "methodology_text": metadata.get("methodology_text"),
            "methodology_hash": methodology_hash,
            "paper_claimed_sharpe": metadata.get("paper_claimed_sharpe"),
            "paper_claimed_cagr": metadata.get("paper_claimed_cagr"),
            "paper_claimed_max_dd": metadata.get("paper_claimed_max_dd"),
        },
        "assumptions": {
            "start": start,
            "end": end,
            "transaction_cost_bps": tx_cost_bps,
            "slippage_bps": slippage_bps,
            "lookahead_guard": "signals_t_execute_t_plus_1",
            "walk_forward_split": walk_forward_split,
            "data_source": "yfinance",
            "backtest_engine": BACKTEST_ENGINE_TAG,
        },
        "results": results,
        "data_hashes": data_hashes,
        "integrity_flags": {
            "lookahead_audit_passed": lookahead_passed,
            "survivorship_bias_mitigated": False,
            "paper_claim_comparison_applied": paper_claim_applied,
        },
    }

    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{run_id}.json"
    artifact_path.write_text(json.dumps(artifact, indent=2))

    return {"run_id": run_id, "artifact_path": str(artifact_path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="archimedes-backtest")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run strategy-code pipeline backtests")
    run.add_argument(
        "--operations",
        nargs="+",
        default=list(OPERATION_TO_SYMBOL.keys()),
        help="Operations to run: SPY NIKKEI GOLD TREASURY OIL",
    )
    run.add_argument("--strategy-path", required=True, help="Path to python strategy file")
    run.add_argument("--strategy-class", default=None, help="Optional class name")
    run.add_argument("--start", default="2018-01-01")
    run.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    run.add_argument("--initial-cash", type=float, default=100000.0)
    run.add_argument("--tx-cost-bps", type=int, default=10)
    run.add_argument("--slippage-bps", type=int, default=5)
    run.add_argument("--artifact-dir", default="artifacts")
    run.add_argument("--paper-arxiv-id", default=None, help="Override paper arxiv id")
    run.add_argument("--paper-title", default=None, help="Override paper title")
    run.add_argument(
        "--methodology-text",
        default=None,
        help="Override extracted methodology text used for the methodology hash",
    )
    run.add_argument("--paper-claimed-sharpe", type=float, default=None)
    run.add_argument("--paper-claimed-cagr", type=float, default=None)
    run.add_argument("--paper-claimed-max-dd", type=float, default=None)
    run.add_argument(
        "--walk-forward-split",
        type=float,
        default=None,
        help="Train fraction for walk-forward split (e.g. 0.70). Not yet enforced.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        out = run_command(
            operations=args.operations,
            start=args.start,
            end=args.end,
            initial_cash=args.initial_cash,
            tx_cost_bps=args.tx_cost_bps,
            slippage_bps=args.slippage_bps,
            artifact_dir=Path(args.artifact_dir),
            strategy_path=Path(args.strategy_path),
            strategy_class=args.strategy_class,
            paper_arxiv_id=args.paper_arxiv_id,
            paper_title=args.paper_title,
            methodology_text=args.methodology_text,
            paper_claimed_sharpe=args.paper_claimed_sharpe,
            paper_claimed_cagr=args.paper_claimed_cagr,
            paper_claimed_max_dd=args.paper_claimed_max_dd,
            walk_forward_split=args.walk_forward_split,
        )
        print(json.dumps(out, indent=2))

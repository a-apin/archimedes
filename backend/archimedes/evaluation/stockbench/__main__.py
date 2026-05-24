"""CLI entry point for the StockBench evaluation harness.

Usage:
    python -m archimedes.evaluation.stockbench --dry-run
    python -m archimedes.evaluation.stockbench --execute --seeds 3
"""

from __future__ import annotations

import argparse
import sys

from .adapter import (
    TOP_20_DJIA,
    TRADING_DAYS,
    run_multi_seed,
    write_results_json,
    write_results_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="StockBench evaluation harness for Archimedes",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without executing",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the benchmark",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        default=3,
        help="Number of seeds to run (default: 3)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=TRADING_DAYS,
        help=f"Number of trading days (default: {TRADING_DAYS})",
    )

    args = parser.parse_args()

    if args.dry_run:
        print("StockBench dry-run:")
        print(f"  Window: {TRADING_DAYS} trading days")
        print(f"  Stocks: {len(TOP_20_DJIA)} (top-20 DJIA)")
        print(f"  Seeds: {args.seeds}")
        print("  Starting capital: $100,000")
        print()
        print("Would run the Archimedes Strategy Generation Agent through")
        print("StockBench's 4-step workflow (overview → analysis → decision → execution)")
        print("for each seed, then aggregate results.")
        sys.exit(0)

    if not args.execute:
        parser.print_help()
        print("\nError: specify --dry-run or --execute", file=sys.stderr)
        sys.exit(1)

    print(f"Running StockBench evaluation ({args.seeds} seeds, {args.days} days)...")

    report = run_multi_seed(n_seeds=args.seeds, n_days=args.days)

    # Persist results
    json_path = write_results_json(report)
    md_path = write_results_markdown(report)

    print("\nResults:")
    print(f"  Return: {report.return_pct_mean:+.2f}% ± {report.return_pct_stdev:.2f}")
    print(f"  Max DD: {report.max_dd_pct_mean:+.2f}% ± {report.max_dd_pct_stdev:.2f}")
    print(f"  Sortino: {report.sortino_mean:.4f} ± {report.sortino_stdev:.4f}")
    print(f"  Z-score: {report.composite_z_score:+.4f}")
    print(f"  Rank: #{report.rank} vs. 14 published baselines")
    print("\nArtifacts:")
    print(f"  JSON: {json_path}")
    print(f"  Markdown: {md_path}")


if __name__ == "__main__":
    main()

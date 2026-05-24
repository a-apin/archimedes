"""StockBench evaluation package.

Run with: python -m archimedes.evaluation.stockbench --dry-run
"""

from .adapter import (
    PUBLISHED_BASELINES,
    ArchimedesStockBenchAdapter,
    BenchmarkResult,
    MultiSeedReport,
    run_multi_seed,
    write_results_json,
    write_results_markdown,
)

__all__ = [
    "PUBLISHED_BASELINES",
    "ArchimedesStockBenchAdapter",
    "BenchmarkResult",
    "MultiSeedReport",
    "run_multi_seed",
    "write_results_json",
    "write_results_markdown",
]

from pathlib import Path

import backtrader as bt
import pytest
from archimedes_analytics_engine.strategy_loader import load_strategy

_STRATEGIES_DIR = Path(__file__).parent.parent / "strategies"

# Strategies added in the 2026-06 library expansion (pairs + single-asset wave).
_NEW_STRATEGY_FILES = [
    "gatev_2006_pairs_distance.py",
    "connors_alvarez_2009_rsi2.py",
    "bollinger_2001_band_reversion.py",
    "donchian_breakout.py",
    "appel_1979_macd.py",
    "brock_1992_dual_ma_crossover.py",
    "ariel_1987_turn_of_month.py",
]


def test_load_strategy_returns_class(tmp_path: Path) -> None:
    strategy_file = tmp_path / "my_strategy.py"
    strategy_file.write_text(
        "import backtrader as bt\n\n"
        "class MyStrategy(bt.Strategy):\n"
        "    def next(self):\n"
        "        if not self.position:\n"
        "            self.buy(size=1)\n"
    )

    bundle = load_strategy(strategy_file)

    assert issubclass(bundle.cls, bt.Strategy)
    assert bundle.cls.__name__ == "MyStrategy"
    assert len(bundle.source_hash) == 64  # sha256 hex
    assert bundle.metadata == {}


def test_load_strategy_extracts_module_metadata(tmp_path: Path) -> None:
    strategy_file = tmp_path / "paper_strategy.py"
    strategy_file.write_text(
        "import backtrader as bt\n\n"
        'PAPER_ARXIV_ID = "2509.11420"\n'
        'PAPER_TITLE = "Some Paper"\n'
        'METHODOLOGY_TEXT = "Buy on signal X; exit on signal Y."\n'
        "PAPER_CLAIMED_SHARPE = 1.42\n\n"
        "class PaperStrategy(bt.Strategy):\n"
        "    def next(self):\n"
        "        pass\n"
    )

    bundle = load_strategy(strategy_file)

    assert bundle.metadata["paper_arxiv_id"] == "2509.11420"
    assert bundle.metadata["paper_title"] == "Some Paper"
    assert bundle.metadata["methodology_text"].startswith("Buy on signal")
    assert bundle.metadata["paper_claimed_sharpe"] == 1.42


def test_load_strategy_extracts_class_attributes(tmp_path: Path) -> None:
    strategy_file = tmp_path / "class_metadata_strategy.py"
    strategy_file.write_text(
        "import backtrader as bt\n\n"
        "class ClassMetadataStrategy(bt.Strategy):\n"
        '    PAPER_ARXIV_ID = "1234.5678"\n'
        '    METHODOLOGY_TEXT = "Class-level methodology."\n\n'
        "    def next(self):\n"
        "        pass\n"
    )

    bundle = load_strategy(strategy_file)

    assert bundle.metadata["paper_arxiv_id"] == "1234.5678"
    assert bundle.metadata["methodology_text"] == "Class-level methodology."


def test_load_strategy_class_precedence_over_module(tmp_path: Path) -> None:
    strategy_file = tmp_path / "precedence.py"
    strategy_file.write_text(
        "import backtrader as bt\n\n"
        'PAPER_ARXIV_ID = "module-level"\n\n'
        "class S(bt.Strategy):\n"
        '    PAPER_ARXIV_ID = "class-level"\n\n'
        "    def next(self):\n"
        "        pass\n"
    )

    bundle = load_strategy(strategy_file)

    assert bundle.metadata["paper_arxiv_id"] == "class-level"


@pytest.mark.parametrize("filename", _NEW_STRATEGY_FILES)
def test_new_strategy_files_load_with_metadata(filename: str) -> None:
    """Every new strategy file loads, exposes a bt.Strategy, and declares a paper title + methodology."""
    bundle = load_strategy(_STRATEGIES_DIR / filename)

    assert issubclass(bundle.cls, bt.Strategy)
    assert bundle.cls is not bt.Strategy
    assert bundle.metadata.get("paper_title")  # required for backend provider discovery
    assert bundle.metadata.get("methodology_text")


def test_load_strategy_resolves_sibling_imports_from_any_cwd(tmp_path: Path, monkeypatch) -> None:
    """Regression (issue #887 bug 2): variant pair files import their sibling
    module by bare name (``from gatev_2006_pairs_distance import ...``). That
    only resolved when CWD happened to be the strategies dir; the loader must
    inject the strategy file's parent dir onto sys.path so the scheduler /
    backfill import path works from any CWD."""
    import sys

    monkeypatch.chdir(tmp_path)  # CWD far away from the strategies dir
    # Simulate the scheduler/backfill environment: strategies dir absent from
    # sys.path and no cached sibling module to fall back on.
    strat_variants = {str(_STRATEGIES_DIR), str(_STRATEGIES_DIR.resolve())}
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p not in strat_variants])
    monkeypatch.delitem(sys.modules, "gatev_2006_pairs_distance", raising=False)

    bundle = load_strategy(_STRATEGIES_DIR / "gatev_2006_pairs_ko_pep.py")

    assert bundle.cls.__name__ == "PairsDistanceTrading"
    assert bundle.metadata["asset_universe"] == ["KO", "PEP"]
    assert bundle.metadata["required_feeds"] == 2


def test_pairs_family_declares_required_feeds() -> None:
    """All three two-feed strategy classes must carry the REQUIRED_FEEDS=2
    runner contract so the single-feed runner fails closed (issue #887)."""
    for filename in (
        "gatev_2006_pairs_distance.py",
        "engle_granger_1987_cointegration_pairs.py",
        "elliott_2005_kalman_pairs.py",
    ):
        bundle = load_strategy(_STRATEGIES_DIR / filename)
        assert bundle.metadata.get("required_feeds") == 2, f"{filename} missing REQUIRED_FEEDS=2"


def test_pairs_strategy_declares_two_asset_universe() -> None:
    """The Gatev pairs strategy must declare exactly two assets (it is multi-asset)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("gatev_mod", _STRATEGIES_DIR / "gatev_2006_pairs_distance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert len(module.ASSET_UNIVERSE) == 2
    assert module.REGIME_TAG == "regime_neutral"

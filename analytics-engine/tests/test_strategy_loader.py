from pathlib import Path

import backtrader as bt
from archimedes_analytics_engine.strategy_loader import load_strategy


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

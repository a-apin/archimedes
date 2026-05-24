from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from typing import Any

import backtrader as bt

METADATA_KEYS: tuple[str, ...] = (
    "PAPER_ARXIV_ID",
    "PAPER_TITLE",
    "PAPER_AUTHORS",
    "PAPER_VENUE",
    "PAPER_YEAR",
    "PAPER_DOI",
    "METHODOLOGY_TEXT",
    "PAPER_CLAIMED_SHARPE",
    "PAPER_CLAIMED_CAGR",
    "PAPER_CLAIMED_MAX_DD",
)


@dataclass
class StrategyBundle:
    cls: type[bt.Strategy]
    metadata: dict[str, Any] = field(default_factory=dict)
    source_hash: str = ""


def _load_module(path: Path) -> ModuleType:
    if not path.exists():
        raise ValueError(f"Strategy file not found: {path}")
    if path.suffix != ".py":
        raise ValueError(f"Strategy file must be .py: {path}")

    module_name = f"archimedes_strategy_{path.stem}_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load strategy module from {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _strategy_candidates(module: ModuleType) -> list[type[bt.Strategy]]:
    return [
        value
        for _, value in vars(module).items()
        if isinstance(value, type) and issubclass(value, bt.Strategy) and value is not bt.Strategy
    ]


def _pick_strategy(
    candidates: list[type[bt.Strategy]],
    class_name: str | None,
    path: Path,
) -> type[bt.Strategy]:
    if class_name:
        for candidate in candidates:
            if candidate.__name__ == class_name:
                return candidate
        raise ValueError(f"Strategy class '{class_name}' not found in {path}")

    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"No backtrader strategy class found in {path}")
    raise ValueError(f"Multiple strategy classes found in {path}. Provide --strategy-class explicitly")


def _extract_metadata(module: ModuleType, cls: type[bt.Strategy]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in METADATA_KEYS:
        if hasattr(cls, key):
            metadata[key.lower()] = getattr(cls, key)
        elif hasattr(module, key):
            metadata[key.lower()] = getattr(module, key)
    return metadata


def _hash_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load_strategy(path: Path, class_name: str | None = None) -> StrategyBundle:
    module = _load_module(path)
    candidates = _strategy_candidates(module)
    cls = _pick_strategy(candidates, class_name, path)
    metadata = _extract_metadata(module, cls)
    return StrategyBundle(
        cls=cls,
        metadata=metadata,
        source_hash=_hash_file(path),
    )

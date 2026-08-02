"""Strategy visibility predicate implementing #850 privacy rules."""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from archimedes.models.strategy_store import StrategyRecord


def is_strategy_visible(row: StrategyRecord | dict | None, caller_wallet: str | None) -> bool:
    """Checks if a strategy is visible to the caller per #850 privacy rules.

    A strategy is visible if:
      1. row is not None AND row.is_example is True, OR
      2. row.is_published is True, OR
      3. caller_wallet is non-empty and matches row.owner_wallet (case-insensitive, whitespace-stripped).
    """
    if row is None:
        return False

    is_example = row.get("is_example", False) if isinstance(row, dict) else getattr(row, "is_example", False)
    is_published = row.get("is_published", False) if isinstance(row, dict) else getattr(row, "is_published", False)
    if is_example or is_published:
        return True

    owner = row.get("owner_wallet") if isinstance(row, dict) else getattr(row, "owner_wallet", None)
    if not caller_wallet or not owner:
        return False

    return str(owner).strip().lower() == str(caller_wallet).strip().lower()

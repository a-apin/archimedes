"""Strategy visibility predicate implementing #850 privacy rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from archimedes.models.strategy_store import StrategyRecord


def _field(row: StrategyRecord | dict, name: str, default: Any = None) -> Any:
    return row.get(name, default) if isinstance(row, dict) else getattr(row, name, default)


def is_strategy_visible(
    row: StrategyRecord | dict | None,
    caller_wallet: str | None,
    *,
    caller_user_id: str | None = None,
) -> bool:
    """Checks if a strategy is visible to the caller per #850 privacy rules.

    A strategy is visible if:
      1. row is not None AND row.is_example is True, OR
      2. row.is_published is True, OR
      3. the caller owns it.

    OWNERSHIP IS TWO-TIERED, because canonical identity was introduced after
    rows already existed (Better Auth ``auth_users.id``):

      - If the row carries an ``owner_user_id``, that is the ONLY thing that
        grants ownership. A matching wallet must NOT, or the canonical model is
        bypassable by anyone who controls a wallet the row happens to name --
        which would make the migration to canonical identity a downgrade in
        security rather than an upgrade.
      - If ``owner_user_id`` is NULL, the row predates canonical identity and
        falls back to the wallet comparison this function has always done.

    This predicate is the single source of truth for that rule. It is
    deliberately not re-implemented at call sites: this codebase's
    characteristic defect is a rule being fixed in the one function the current
    ticket touches while sibling readers keep the old behaviour, and a
    visibility rule that disagrees with itself across two routes is an
    authorization bug, not an inconsistency.
    """
    if row is None:
        return False

    if _field(row, "is_example", False) or _field(row, "is_published", False):
        return True

    owner_user_id = _field(row, "owner_user_id")
    if owner_user_id is not None:
        # Canonical ownership. Note both sides must be non-empty: a caller with
        # no resolved user must never match a row, and `None == None` is not
        # ownership.
        return bool(caller_user_id) and str(owner_user_id) == str(caller_user_id)

    owner = _field(row, "owner_wallet")
    if not caller_wallet or not owner:
        return False

    return str(owner).strip().lower() == str(caller_wallet).strip().lower()

"""Unit tests for strategy visibility predicate (#1120 / #850 / #1194).

NOTE on MagicMock: every row mock below sets ``owner_user_id`` EXPLICITLY, and
that is load-bearing rather than stylistic. A bare ``MagicMock()`` auto-creates
any attribute on access, so ``getattr(row, "owner_user_id", None)`` returns a
truthy Mock instead of ``None`` — which routes the predicate down the canonical
-ownership branch and silently disables the legacy wallet fallback. A test that
forgets it does not test what its name says.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from archimedes.services.strategy_visibility import is_strategy_visible


def _legacy_row(**kw):
    """A row created before canonical identity: owner_user_id IS NULL."""
    kw.setdefault("is_example", False)
    kw.setdefault("is_published", False)
    kw.setdefault("owner_user_id", None)
    return MagicMock(**kw)


def test_is_strategy_visible_none():
    assert is_strategy_visible(None, "0x123") is False


def test_is_strategy_visible_example():
    row = _legacy_row(is_example=True, owner_wallet="0x456")
    assert is_strategy_visible(row, None) is True
    assert is_strategy_visible(row, "0x789") is True


def test_is_strategy_visible_published():
    row = _legacy_row(is_published=True, owner_wallet="0x456")
    assert is_strategy_visible(row, None) is True
    assert is_strategy_visible(row, "0x789") is True


def test_is_strategy_visible_owner_match():
    row = _legacy_row(owner_wallet="0xABCdef123")
    # Matching owner (case-insensitive, whitespace-insensitive)
    assert is_strategy_visible(row, "0xabcdef123") is True
    assert is_strategy_visible(row, " 0xABCDEF123  ") is True
    # Non-owner caller
    assert is_strategy_visible(row, "0x999999999") is False
    # Empty caller
    assert is_strategy_visible(row, None) is False
    assert is_strategy_visible(row, "") is False


def test_is_strategy_visible_dict_support():
    row_dict = {"is_example": False, "is_published": False, "owner_wallet": "0xOwner123"}
    assert is_strategy_visible(row_dict, "0xowner123") is True
    assert is_strategy_visible(row_dict, "0xother") is False


# ── Canonical ownership (#1194: Better Auth auth_users.id) ──────────────────


def test_canonical_owner_grants_access():
    row = MagicMock(is_example=False, is_published=False, owner_user_id="user_abc", owner_wallet="0xowner")
    assert is_strategy_visible(row, None, caller_user_id="user_abc") is True


def test_canonical_owner_mismatch_denies():
    row = MagicMock(is_example=False, is_published=False, owner_user_id="user_abc", owner_wallet="0xowner")
    assert is_strategy_visible(row, None, caller_user_id="user_zzz") is False


def test_matching_wallet_does_NOT_grant_access_once_canonical_owner_is_set():
    """The security-critical case, and the reason this predicate is two-tiered.

    Once a row names a canonical owner, a caller who merely controls the wallet
    the row happens to reference must NOT get in. Allowing it would make the
    migration to canonical identity a security DOWNGRADE: the wallet becomes a
    second, weaker key to the same door, permanently. Wallet is only sufficient
    for rows that predate canonical identity (owner_user_id IS NULL).
    """
    row = MagicMock(is_example=False, is_published=False, owner_user_id="user_abc", owner_wallet="0xowner")
    assert is_strategy_visible(row, "0xowner", caller_user_id=None) is False
    assert is_strategy_visible(row, "0xowner", caller_user_id="user_someone_else") is False


def test_anonymous_caller_never_matches_an_unowned_row():
    """``None == None`` is not ownership. A row with a canonical owner and a
    caller with no resolved user must be denied, not accidentally matched."""
    row = MagicMock(is_example=False, is_published=False, owner_user_id=None, owner_wallet=None)
    assert is_strategy_visible(row, None, caller_user_id=None) is False


def test_published_still_wins_over_ownership():
    """Publication is checked before ownership, so a published row stays public
    regardless of who owns it — unchanged by the canonical-identity work."""
    row = MagicMock(is_example=False, is_published=True, owner_user_id="user_abc", owner_wallet="0xowner")
    assert is_strategy_visible(row, None, caller_user_id=None) is True


def test_dict_rows_support_canonical_ownership_too():
    row = {"is_example": False, "is_published": False, "owner_user_id": "user_abc", "owner_wallet": "0xowner"}
    assert is_strategy_visible(row, None, caller_user_id="user_abc") is True
    assert is_strategy_visible(row, "0xowner", caller_user_id=None) is False

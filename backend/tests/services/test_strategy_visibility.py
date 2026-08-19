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


# ── _visible_passports delegates to THIS predicate (#1120) ──────────────


class TestVisiblePassportsDelegation:
    """The passports listing's per-row visibility DECISION must be this
    module's predicate, not a re-implementation. The divergence test works by
    poisoning: force the shared predicate to answer differently and require
    the passport filter to follow it — a hand-rolled copy would ignore the
    poison and keep its own answer, failing here.
    """

    @staticmethod
    def _record(rid, generation_method="dsl-fusion", owner_user_id=None, owner_wallet=None):
        return MagicMock(
            id=rid,
            generation_method=generation_method,
            owner_user_id=owner_user_id,
            owner_wallet=owner_wallet,
        )

    @staticmethod
    def _session_with_flags(flags):
        """A session whose strategy_store flag query returns *flags* rows
        of (id, is_example, is_published)."""
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = flags
        return session

    def test_generated_rows_follow_the_shared_predicate_verbatim(self, monkeypatch):
        from archimedes.api import strategies_routes as sr

        published = self._record("pub-1")
        private_other = self._record("priv-1", owner_user_id="user_other")
        records = [published, private_other]
        session = self._session_with_flags([("pub-1", False, True), ("priv-1", False, False)])

        # Unpoisoned: the published row is visible, the foreign private row is not.
        out = sr._visible_passports(session, records, caller=None, caller_user_id="user_me")
        assert [r.id for r in out] == ["pub-1"]

        # Poison the shared predicate to the OPPOSITE verdicts. A delegating
        # implementation flips with it; a re-implementation would not.
        def inverted(row, caller_wallet, *, caller_user_id=None):
            return not is_strategy_visible(row, caller_wallet, caller_user_id=caller_user_id)

        monkeypatch.setattr("archimedes.services.strategy_visibility.is_strategy_visible", inverted)
        out = sr._visible_passports(session, records, caller=None, caller_user_id="user_me")
        assert [r.id for r in out] == ["priv-1"]

    def test_two_tier_rule_holds_through_the_passport_path(self):
        """The #850 canonical-tier poison case, end-to-end through
        _visible_passports: a generated passport whose row carries an
        owner_user_id must NOT be visible to a caller who merely matches its
        legacy owner_wallet — the wallet tier applies only when
        owner_user_id is NULL."""
        from archimedes.api import strategies_routes as sr

        canonical = self._record("canon-1", owner_user_id="user_real_owner", owner_wallet="0xwallet")
        legacy = self._record("legacy-1", owner_user_id=None, owner_wallet="0xwallet")
        records = [canonical, legacy]
        session = self._session_with_flags([("canon-1", False, False), ("legacy-1", False, False)])

        out = sr._visible_passports(session, records, caller="0xWALLET", caller_user_id=None)
        assert [r.id for r in out] == ["legacy-1"]

    def test_curated_passports_stay_public_without_store_rows(self):
        from archimedes.api import strategies_routes as sr

        curated = self._record("cur-1", generation_method="curated")
        session = self._session_with_flags([])

        out = sr._visible_passports(session, [curated], caller=None, caller_user_id=None)
        assert [r.id for r in out] == ["cur-1"]

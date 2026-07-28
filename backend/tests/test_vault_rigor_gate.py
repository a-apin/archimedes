"""Hermetic tests for the server-side rigor deploy gate (#818).

The guarantee "only rigor-passing strategies are deployed" must hold server-side,
where a direct (non-UI) API call cannot route around it. These pin: the resolver
reads the authoritative verdict across curated + generated sources, fails closed on
a DB error, and `create_vault` returns 422 (without spending gas) for a strategy
that failed the gate or was never validated.
"""

from __future__ import annotations

import contextlib
import time
from unittest.mock import AsyncMock, patch

import pytest
from archimedes.api.vaults_routes import (
    _assert_strategies_pass_rigor,
    _strategy_record_visible,
    _strategy_rigor_status,
)
from archimedes.services.live_rigor_gate import RigorGateVerdict
from fastapi import HTTPException

V = "archimedes.api.vaults_routes"
# The curated deploy-gate branch imports the live gate lazily (inside the function)
# to avoid a route<->service import cycle, so patch the SOURCE module here.
LRG = "archimedes.services.live_rigor_gate"


@contextlib.contextmanager
def _override_verified_wallet(app, wallet: str = "0x000000000000000000000000000000000000dEaD"):
    """Override the SIWE wallet dependency, then RESTORE the prior override on exit.

    Restoring (rather than a global ``app.dependency_overrides.clear()``) keeps this
    test from wiping overrides another test/fixture set on the shared ``app`` instance.
    """
    from archimedes.api.auth_siwe import require_verified_wallet

    prev = app.dependency_overrides.get(require_verified_wallet)
    app.dependency_overrides[require_verified_wallet] = lambda: wallet
    try:
        yield
    finally:
        if prev is None:
            app.dependency_overrides.pop(require_verified_wallet, None)
        else:
            app.dependency_overrides[require_verified_wallet] = prev


class _Strat:
    def __init__(self, passes, sid="s1"):
        self.passes_rigor_gate = passes
        # The cohort build dedupes on .id, so the stub needs one.
        self.id = sid


# ── _strategy_rigor_status ────────────────────────────────────────────────


def test_status_curated_passing():
    # strategy_provider is now a lazily-cached accessor (strategy_provider());
    # patch the name wholesale and configure the mocked callable's return_value.
    # The curated branch reads the LIVE gate (#1173), so stub that too.
    with (
        patch(f"{V}.strategy_provider") as mock_provider,
        patch(f"{LRG}.verdicts_for_strategies", return_value={"s1": RigorGateVerdict.passed()}),
    ):
        mock_provider.return_value.get_strategy.return_value = _Strat(True)
        assert _strategy_rigor_status("s1") == (True, True)


def test_status_curated_failing():
    with (
        patch(f"{V}.strategy_provider") as mock_provider,
        patch(f"{LRG}.verdicts_for_strategies", return_value={"s1": RigorGateVerdict.failed()}),
    ):
        mock_provider.return_value.get_strategy.return_value = _Strat(False)
        assert _strategy_rigor_status("s1") == (True, False)


def test_status_curated_uses_live_verdict_not_provider_attribute():
    """REGRESSION (#1173): a curated strategy must be deployable when the LIVE gate
    passes it, even though the provider object always carries
    ``passes_rigor_gate = False``.

    ``LocalStrategyProvider`` sets that attribute to False unconditionally
    (fail-closed by construction, 56cc9bde) and the real verdict is overlaid
    downstream. Reading the raw attribute here made EVERY curated strategy
    undeployable at the default strictness with a message asserting it "has not
    passed the rigor gate" — a false statement — while deploying at strictness >= 2
    worked, because that path consults the live gate. This test pins the live
    verdict as the source of truth.
    """
    with (
        patch(f"{V}.strategy_provider") as mock_provider,
        patch(f"{LRG}.verdicts_for_strategies", return_value={"s1": RigorGateVerdict.passed()}),
    ):
        # Provider says False — the historical bug source.
        mock_provider.return_value.get_strategy.return_value = _Strat(False)
        assert _strategy_rigor_status("s1") == (True, True)


def test_status_curated_grades_against_full_library_cohort():
    """REGRESSION (#1173): the deploy gate must grade against the shared FULL-library
    cohort, never a 1-item list.

    The verdict is cohort-dependent (cohort-scoped PBO/CSCV; a cohort under
    MIN_LIBRARY_N_FOR_PBO_GATING skips criterion 4), so grading a strategy alone
    would let the deploy gate disagree with the list badge and the passport.
    """
    strat = _Strat(False, sid="s1")
    library = [_Strat(False, sid=f"other{i}") for i in range(12)] + [strat]
    seen: dict = {}

    def _capture(strategies):
        seen["ids"] = [s.id for s in strategies]
        return {"s1": RigorGateVerdict.passed()}

    with (
        patch(f"{V}.strategy_provider") as mock_provider,
        patch(f"{LRG}.verdicts_for_strategies", side_effect=_capture),
    ):
        mock_provider.return_value.get_strategy.return_value = strat
        mock_provider.return_value.list_strategies.return_value = library
        assert _strategy_rigor_status("s1") == (True, True)

    assert seen["ids"] == [s.id for s in library], (
        "deploy gate must grade against the full library cohort, not the strategy alone; "
        f"got a {len(seen.get('ids', []))}-item cohort"
    )


def test_status_curated_fails_closed_when_live_gate_raises():
    """A live-gate failure must not wave a deploy through."""
    with (
        patch(f"{V}.strategy_provider") as mock_provider,
        patch(f"{LRG}.verdicts_for_strategies", side_effect=RuntimeError("boom")),
    ):
        mock_provider.return_value.get_strategy.return_value = _Strat(True)
        assert _strategy_rigor_status("s1") == (True, False)


def test_status_fails_closed_on_db_error():
    # Not curated → falls to the DB; if the session raises, we must report not-found
    # (False, False) so the caller blocks the deploy rather than waving it through.
    with (
        patch(f"{V}.strategy_provider") as mock_provider,
        patch("archimedes.db.get_session", side_effect=RuntimeError("db down")),
    ):
        mock_provider.return_value.get_strategy.return_value = None
        assert _strategy_rigor_status("missing") == (False, False)


# ── _assert_strategies_pass_rigor ─────────────────────────────────────────


def test_assert_raises_422_on_failing():
    with patch(f"{V}._strategy_rigor_status", return_value=(True, False)), pytest.raises(HTTPException) as exc:
        _assert_strategies_pass_rigor(["s1"])
    assert exc.value.status_code == 422
    assert "rigor gate" in exc.value.detail


def test_assert_raises_422_on_not_found():
    with patch(f"{V}._strategy_rigor_status", return_value=(False, False)), pytest.raises(HTTPException) as exc:
        _assert_strategies_pass_rigor(["ghost"])
    assert exc.value.status_code == 422
    assert "not found" in exc.value.detail


def test_assert_passes_on_passing():
    with patch(f"{V}._strategy_rigor_status", return_value=(True, True)):
        _assert_strategies_pass_rigor(["s1", "s2"])  # no raise


def test_assert_empty_list_is_noop():
    _assert_strategies_pass_rigor([])  # nothing to validate → no raise


def test_assert_blocks_if_any_one_fails():
    # All must pass; one failing id blocks the whole deploy.
    def _status(sid, cohort_cache=None):
        return (True, sid != "bad")

    with patch(f"{V}._strategy_rigor_status", side_effect=_status), pytest.raises(HTTPException) as exc:
        _assert_strategies_pass_rigor(["good", "bad", "good2"])
    assert exc.value.status_code == 422


# ── HTTP: the gate fires BEFORE the on-chain deploy ───────────────────────


async def test_create_vault_rejects_failing_strategy_before_deploy():
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    with (
        _override_verified_wallet(app),
        patch(f"{V}._strategy_rigor_status", return_value=(True, False)),
        patch(f"{V}.chain_executor.create_vault") as mock_create,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/vaults/create",
                json={"name": "V", "symbol": "V", "strategy_ids": ["failing"]},
            )

    assert resp.status_code == 422
    assert "rigor gate" in resp.json()["detail"]
    mock_create.assert_not_called()  # never spent gas — gate fired first


async def test_create_vault_proceeds_when_rigor_passes():
    # Complementary happy path: a passing strategy must NOT be blocked by the new
    # precondition — the deploy proceeds and chain_executor.create_vault is called.
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    with (
        _override_verified_wallet(app),
        patch(f"{V}._strategy_rigor_status", return_value=(True, True)),
        patch(f"{V}.chain_executor.create_vault", new=AsyncMock(return_value="0xVaultDeployedAddress")) as mock_create,
        # record_funnel touches Redis; stub it so the test stays hermetic.
        patch("archimedes.api.funnel_middleware.record_funnel", new=AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/vaults/create",
                json={"name": "V", "symbol": "V", "strategy_ids": ["passing"]},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["vault_address"] == "0xVaultDeployedAddress"
    assert body["strategy_ids"] == ["passing"]
    mock_create.assert_awaited_once()  # gate let it through → gas spent


async def test_create_vault_writes_the_identity_ledger_vault_created_event():
    """Issue #1028 (D2): the SIWE-verified deployer's ``vault_created`` event
    lands in the ledger — the bottom-of-funnel identity write, unlike
    Generate's flag-gated one, since ``require_verified_wallet`` always
    identifies the caller here."""
    from archimedes.db import get_session
    from archimedes.main import app
    from archimedes.models.identity import IdentityEvent, WalletIdentity
    from httpx import ASGITransport, AsyncClient

    wallet = "0x" + "de" * 20  # matches _override_verified_wallet's lowercased form

    with (
        _override_verified_wallet(app, wallet=wallet),
        patch(f"{V}._strategy_rigor_status", return_value=(True, True)),
        patch(f"{V}.chain_executor.create_vault", new=AsyncMock(return_value="0xLedgerVaultAddress")),
        patch("archimedes.api.funnel_middleware.record_funnel", new=AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/vaults/create",
                json={"name": "V", "symbol": "V", "strategy_ids": ["passing"]},
            )
    assert resp.status_code == 200

    session = get_session()
    try:
        events = session.query(IdentityEvent).filter(IdentityEvent.wallet == wallet).all()
        shapes = [(e.event_type, e.actor_class, e.meta) for e in events]
    finally:
        session.query(IdentityEvent).filter(IdentityEvent.wallet == wallet).delete(synchronize_session=False)
        session.query(WalletIdentity).filter(WalletIdentity.wallet_address == wallet).delete(synchronize_session=False)
        session.commit()
        session.close()

    matching = [s for s in shapes if s[0] == "vault_created"]
    assert len(matching) == 1
    _, actor_class, meta = matching[0]
    assert actor_class == "human"
    assert meta["vault_address"] == "0xLedgerVaultAddress"
    assert meta["strategy_ids"] == ["passing"]


# ── _strategy_record_visible (#1073) ──────────────────────────────────────

_OWNER = "0x000000000000000000000000000000000000dEaD"
_OTHER = "0x0000000000000000000000000000000000000BAD"


@contextlib.contextmanager
def _private_strategy_record(sid: str, *, owner: str):
    """Create a private (non-example, unpublished) StrategyRecord owned by
    `owner`, yield, then delete it — mirrors the identity-ledger test's
    real-DB-with-cleanup pattern above rather than a tmp-sqlite swap, since
    this file has no per-test DB fixture."""
    from archimedes.db import get_session, init_db
    from archimedes.models.strategy_store import StrategyRecord

    init_db()
    session = get_session()
    try:
        session.add(
            StrategyRecord(
                id=sid,
                content_hash=("0x" + sid).ljust(66, "0"),
                generation_method="fusion",
                source_papers="[]",
                strategy_name="Private Test Strategy",
                thesis="test thesis",
                asset_universe="[]",
                risk_profile="moderate",
                status="candidate",
                is_example=False,
                owner_wallet=owner.lower(),
                is_published=False,
            )
        )
        session.commit()
    finally:
        session.close()

    try:
        yield
    finally:
        session = get_session()
        try:
            session.query(StrategyRecord).filter_by(id=sid).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()


class _FakeRequest:
    """Minimal stand-in for a FastAPI Request carrying a SIWE session cookie,
    for calling `_strategy_record_visible` directly without a real HTTP call."""

    def __init__(self, wallet: str | None):
        self._wallet = wallet

    @property
    def cookies(self):
        if self._wallet is None:
            return {}
        from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session

        return {_COOKIE_NAME: _sign_session(self._wallet, time.time())}


def test_strategy_record_visible_none_when_no_record():
    assert _strategy_record_visible("no-such-strategy-id", _FakeRequest(_OWNER)) is None


def test_strategy_record_visible_true_for_owner():
    sid = "priv-visible-owner"
    with _private_strategy_record(sid, owner=_OWNER):
        assert _strategy_record_visible(sid, _FakeRequest(_OWNER)) is True


def test_strategy_record_visible_false_for_non_owner():
    sid = "priv-visible-nonowner"
    with _private_strategy_record(sid, owner=_OWNER):
        assert _strategy_record_visible(sid, _FakeRequest(_OTHER)) is False


def test_strategy_record_visible_false_for_anonymous():
    sid = "priv-visible-anon"
    with _private_strategy_record(sid, owner=_OWNER):
        assert _strategy_record_visible(sid, _FakeRequest(None)) is False


def test_strategy_record_visible_fails_closed_on_db_error():
    """A DB error during the ownership lookup must NOT return None (None means
    'no record — fall through to the ownership-blind badge', i.e. fail-open
    for private strategies exactly when the DB is down). It must raise a 503
    so the deploy is blocked loudly and recoverably."""
    with patch("archimedes.db.get_session", side_effect=RuntimeError("db down")):
        with pytest.raises(HTTPException) as exc_info:
            _strategy_record_visible("any-id", _FakeRequest(_OWNER))
    assert exc_info.value.status_code == 503


# ── HTTP: strictest-level fast path is ownership-gated (#1073) ────────────


def _siwe_cookies(wallet: str) -> dict[str, str]:
    """Real signed SIWE session cookie (same helper shape as
    test_strategy_ownership.py / test_user_routes.py). Needed here (rather
    than the `require_verified_wallet` dependency override above) because
    `_strategy_record_visible` reads the wallet directly off the request
    cookie via `get_verified_wallet(request)`, not via the FastAPI dependency."""
    from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session

    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


async def test_create_vault_hides_private_strategy_from_non_owner():
    """A non-owner deploying a vault bound to a private generated strategy's
    id gets 422 not-found — even though the (ownership-blind) passport badge
    would say it passes. This is the #1073 regression: the badge alone can't
    be trusted at the strictest-level fast path once a request is present."""
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    sid = "priv-deploy-nonowner"
    with (
        _private_strategy_record(sid, owner=_OWNER),
        patch(f"{V}._strategy_rigor_status", return_value=(True, True)),
        patch(f"{V}.chain_executor.create_vault") as mock_create,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", cookies=_siwe_cookies(_OTHER)
        ) as client:
            resp = await client.post(
                "/api/vaults/create",
                json={"name": "V", "symbol": "V", "strategy_ids": [sid]},
            )

    assert resp.status_code == 422
    assert "not found" in resp.json()["detail"]
    mock_create.assert_not_called()  # never spent gas — ownership gate fired first


async def test_create_vault_allows_owner_to_deploy_private_strategy():
    """Complementary happy path: the strategy's OWNER deploying the same
    private strategy id is unaffected by the new ownership check."""
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    sid = "priv-deploy-owner"
    with (
        _private_strategy_record(sid, owner=_OWNER),
        patch(f"{V}._strategy_rigor_status", return_value=(True, True)),
        patch(f"{V}.chain_executor.create_vault", new=AsyncMock(return_value="0xOwnerDeployedAddress")) as mock_create,
        patch("archimedes.api.funnel_middleware.record_funnel", new=AsyncMock()),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", cookies=_siwe_cookies(_OWNER)
        ) as client:
            resp = await client.post(
                "/api/vaults/create",
                json={"name": "V", "symbol": "V", "strategy_ids": [sid]},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["vault_address"] == "0xOwnerDeployedAddress"
    mock_create.assert_awaited_once()  # owner → gate lets it through


# ── Cohort map computed once per deploy (#1172 review follow-up) ───────────
#
# `verdicts_for_strategies` is UNCACHED: every call re-reads
# get_all_daily_returns and re-runs a full cohort CSCV/PBO pass (~6s by the
# route's own measurement). Grading the deploy against the full library — the
# correctness fix — therefore made a vault bound to k curated strategies pay
# that k times inside one request. The map must be built at most once.


def test_deploy_gate_computes_cohort_verdicts_once_for_many_strategies():
    """A 3-strategy deploy triggers exactly ONE verdicts_for_strategies call."""
    ids = ["s1", "s2", "s3"]
    cohort = [_Strat(True, sid=i) for i in ids]
    calls: list[int] = []

    def _verdicts(strategies):
        calls.append(len(strategies))
        return {s.id: RigorGateVerdict.passed() for s in strategies}

    with (
        patch(f"{V}.strategy_provider") as provider,
        patch(f"{LRG}.verdicts_for_strategies", side_effect=_verdicts),
    ):
        provider.return_value.list_strategies.return_value = cohort
        provider.return_value.get_strategy.side_effect = lambda sid: next(
            (s for s in cohort if s.id == sid), None
        )
        _assert_strategies_pass_rigor(ids)  # must not raise

    assert len(calls) == 1, f"expected 1 cohort computation, got {len(calls)}"
    assert calls[0] == len(cohort), "and it must be graded over the FULL library"


def test_deploy_gate_does_not_cache_a_one_off_cohort():
    """A strategy MISSING from list_strategies() is graded on a one-off cohort
    (itself appended). That map must NOT be cached and reused, or a second such
    strategy would miss the dict, degrade to `pending`, and be wrongly refused."""
    listed = [_Strat(True, sid="listed")]
    missing_a = _Strat(True, sid="missing-a")
    missing_b = _Strat(True, sid="missing-b")
    by_id = {s.id: s for s in (listed[0], missing_a, missing_b)}
    seen: list[list[str]] = []

    def _verdicts(strategies):
        seen.append([s.id for s in strategies])
        return {s.id: RigorGateVerdict.passed() for s in strategies}

    with (
        patch(f"{V}.strategy_provider") as provider,
        patch(f"{LRG}.verdicts_for_strategies", side_effect=_verdicts),
    ):
        provider.return_value.list_strategies.return_value = listed
        provider.return_value.get_strategy.side_effect = by_id.get
        # Neither unlisted strategy may be refused.
        _assert_strategies_pass_rigor(["missing-a", "missing-b"])

    assert len(seen) == 2, "each unlisted strategy needs its own cohort"
    assert seen[0] == ["listed", "missing-a"]
    assert seen[1] == ["listed", "missing-b"]

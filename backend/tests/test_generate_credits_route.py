"""Route test for GET /api/generate/credits (v8 Lane 1.3a: make credits visible).

The ledger's write-side transitions (claim/settle/consume/restore/void, and
the paywall window they close) are already covered end-to-end by
``test_generate_job_liveness.py``. This file's job is narrower: does the new
read-only listing endpoint return the CALLING user's own credits, shaped
honestly, and never another user's.

Hermetic: tmp-file SQLite (``redirect_to_tmp_sqlite``) + a dependency-
overridden ``require_current_user`` — the same two idioms
``test_generate_job_liveness.py`` and ``test_risk_routes.py`` use
respectively. No live DB/Redis/network.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from archimedes.api.account_auth import CurrentUser, require_current_user
from archimedes.db import get_session
from archimedes.models.generation_credit import (
    claim_credit,
    consume_credit,
    mark_credit_settled,
)
from httpx import ASGITransport, AsyncClient

from tests.db_isolation import redirect_to_tmp_sqlite

USER_A = "user-credits-a"
USER_B = "user-credits-b"


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path):
    yield from redirect_to_tmp_sqlite(tmp_path)


@contextmanager
def _as_user(user_id: str):
    """Dependency-override ``require_current_user`` for one request, then restore.

    Mirrors ``test_generate_job_liveness.py``'s ``_authenticated_account``,
    parameterized on user id so the ownership test below can act as two
    different callers.
    """
    from archimedes.main import app

    app.dependency_overrides[require_current_user] = lambda: CurrentUser(
        user_id, "Credits Test", f"{user_id}@example.com", True
    )
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_current_user, None)


def _make_credit(user_id: str, *, settle: bool = True, consume: bool = False, job_id: str = "job-x") -> int:
    with get_session() as session:
        _, credit = claim_credit(session, user_id=user_id, idempotency_key=None)
        if settle:
            mark_credit_settled(
                session,
                credit.id,
                payer_wallet="0x" + "aa" * 20,
                amount_base_units=2_000_000,  # $2.00 at USDC's 6 decimals
                price_usd="$2.00",
                network="eip155:5042002",
                settlement_ref="ref-1",
            )
        if consume:
            consume_credit(session, credit.id, job_id=job_id)
        session.commit()
        return credit.id


async def _get_credits():
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/api/generate/credits")


@pytest.mark.asyncio
async def test_requires_sign_in():
    """No auth override active: the router-level gate must 401, never
    default to an empty list for an anonymous caller."""
    resp = await _get_credits()
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_no_credits_is_an_empty_list_not_an_error():
    with _as_user(USER_A):
        resp = await _get_credits()
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_an_available_credit_is_shaped_honestly():
    credit_id = _make_credit(USER_A)
    with _as_user(USER_A):
        resp = await _get_credits()
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == credit_id
    assert row["status"] == "available"
    assert row["created_at"] is not None
    assert row["job_id"] is None
    assert row["amount_usdc"] == 2.0
    # Only the honest, UI-relevant subset ships — no payer_wallet/network/
    # settlement_ref leaking payment-plumbing internals into the response.
    assert set(row.keys()) == {"id", "status", "created_at", "job_id", "amount_usdc"}


@pytest.mark.asyncio
async def test_a_consumed_credit_still_lists_with_its_job_id():
    credit_id = _make_credit(USER_A, consume=True, job_id="job-consumed")
    with _as_user(USER_A):
        resp = await _get_credits()
    row = resp.json()[0]
    assert row["id"] == credit_id
    assert row["status"] == "consumed"
    assert row["job_id"] == "job-consumed"


@pytest.mark.asyncio
async def test_a_pending_unsettled_claim_reports_no_amount():
    """A ``pending`` row exists precisely because nothing has settled yet —
    the amount must read None, never a fabricated $0.00."""
    _make_credit(USER_A, settle=False)
    with _as_user(USER_A):
        resp = await _get_credits()
    row = resp.json()[0]
    assert row["status"] == "pending"
    assert row["amount_usdc"] is None


@pytest.mark.asyncio
async def test_never_leaks_another_users_credit():
    """The guard this file exists to pin: the endpoint must scope to the
    CALLING user (``user.id`` from the auth dependency), not return the
    whole ledger table."""
    _make_credit(USER_A)
    _make_credit(USER_B)

    with _as_user(USER_A):
        rows_a = (await _get_credits()).json()
    with _as_user(USER_B):
        rows_b = (await _get_credits()).json()

    assert len(rows_a) == 1
    assert len(rows_b) == 1
    assert rows_a[0]["id"] != rows_b[0]["id"]

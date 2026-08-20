"""``GET /api/generate/jobs/{job_id}`` — the single-job status poll (#1292).

An agent that never opened the SSE stream (or dropped past the event-log TTL)
needs to read one job's state without pulling the whole listing. The endpoint
carries the same auth rules as the other per-job reads — account session
required, owner-scoped, mismatch → **404** rather than 403 so it is not an
existence oracle — plus a ``type == "generate"`` filter mirroring
``GET /api/generate/jobs``.

Hermetic, mirroring ``test_generate_job_scoping.py``: the Redis-backed job store
is boundary-mocked, and sessions are real signed fixture cookies mapped onto
canonical test users by the autouse adapter in ``conftest.py``.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from fastapi.testclient import TestClient

_OWNER = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
_ATTACKER = "0x9999999999999999999999999999999999999999"
_JOB_ID = "job-status-1"

# The conftest legacy-SIWE adapter derives the canonical user id from the
# verified (lower-cased) wallet, so an account-owned fixture job can be tagged
# with the same value the route compares against.
_OWNER_USER_ID = f"legacy-test:{_OWNER.lower()}"
_ATTACKER_USER_ID = f"legacy-test:{_ATTACKER.lower()}"


def _cookies(wallet: str) -> dict[str, str]:
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


def _client() -> TestClient:
    from archimedes.main import app

    return TestClient(app)


def _job(
    *,
    owner_user_id: str | None = None,
    owner_wallet: str | None = None,
    status: str = "done",
    job_type: str = "generate",
    best_strategy_id: str | None = "strat-77",
) -> dict:
    return {
        "id": _JOB_ID,
        "type": job_type,
        "status": status,
        "created_at": "2026-08-19T00:00:00+00:00",
        "updated_at": "2026-08-19T00:00:05+00:00",
        "payload": {
            "owner_user_id": owner_user_id,
            "owner_wallet": owner_wallet,
            "brief": {"intent": "low-vol USDC carry"},
            "n_candidates": 3,
        },
        "result": {"best_strategy_id": best_strategy_id, "best_candidate_id": None, "candidates": []},
    }


def _mock_store(job: dict | None, *, recent: list[dict] | None = None) -> MagicMock:
    """Boundary-mocked job store — only ``.get`` / ``.list_recent_jobs`` are read
    by the surfaces under test here."""
    store = MagicMock()
    store.get = AsyncMock(return_value=job)
    store.list_recent_jobs = AsyncMock(return_value=recent if recent is not None else ([job] if job else []))
    return store


def _patched(store: MagicMock):
    return patch("archimedes.api.generate_routes.get_job_store", return_value=store)


# ── Session required, and required BEFORE the store lookup ───────────


def test_job_status_anonymous_401():
    """No session → 401. Auth is unconditional on this surface."""
    with _patched(_mock_store(_job(owner_user_id=_OWNER_USER_ID))) as _:
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}")
    assert resp.status_code == 401, resp.text


async def test_job_status_anonymous_missing_job_401_not_404():
    """An anonymous caller must get 401 for a MISSING job too — a
    404-for-missing / 401-for-existing split would let an unauthenticated caller
    enumerate live job ids. The dependency has to run before the store lookup."""
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/generate/jobs/nonexistent-job-id")
    assert resp.status_code == 401, resp.text


# ── Happy path: the shape an agent polls for ─────────────────────────


def test_job_status_owner_reads_state_updated_at_and_best_strategy():
    store = _mock_store(_job(owner_user_id=_OWNER_USER_ID))
    with _patched(store):
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}", cookies=_cookies(_OWNER))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["job_id"] == _JOB_ID
    assert body["state"] == "done"
    assert body["updated_at"] == "2026-08-19T00:00:05+00:00"
    assert body["best_strategy_id"] == "strat-77"
    assert body["brief_intent"] == "low-vol USDC carry"
    assert body["n_candidates"] == 3
    store.get.assert_awaited_once_with(_JOB_ID)


def test_job_status_running_job_reports_running_with_no_strategy_yet():
    """The whole point of polling: a job still in flight reads ``running`` and
    carries no strategy id — not a terminal state, not an error."""
    store = _mock_store(_job(owner_user_id=_OWNER_USER_ID, status="running", best_strategy_id=None))
    with _patched(store):
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}", cookies=_cookies(_OWNER))
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "running"
    assert resp.json()["best_strategy_id"] is None


def test_job_status_matches_the_listing_entry_for_the_same_job():
    """Single-job read and listing entry are the same record — switching from
    the list to the poll can never change what a client believes about a job."""
    job = _job(owner_user_id=_OWNER_USER_ID)
    with _patched(_mock_store(job)):
        client = _client()
        single = client.get(f"/api/generate/jobs/{_JOB_ID}", cookies=_cookies(_OWNER))
        listing = client.get("/api/generate/jobs", cookies=_cookies(_OWNER))
    assert single.status_code == 200 and listing.status_code == 200
    assert listing.json()["jobs"] == [single.json()]


def test_candidates_route_still_reachable_alongside_the_new_path():
    """``/jobs/{job_id}`` must not shadow ``/jobs/{job_id}/candidates``."""
    with _patched(_mock_store(_job(owner_user_id=_OWNER_USER_ID))):
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}/candidates", cookies=_cookies(_OWNER))
    assert resp.status_code == 200, resp.text
    assert resp.json()["candidates"] == []


# ── Guards: each rejects an input that must not be served ────────────


def test_job_status_other_account_404_identical_body_to_a_missing_job():
    """GUARD — cross-account read. A job owned by another ``owner_user_id`` is
    refused with a 404 whose body is byte-identical to a truly-missing job, so
    the caller cannot tell 'exists but not yours' from 'does not exist'."""
    with _patched(_mock_store(_job(owner_user_id=_OWNER_USER_ID))):
        mismatch = _client().get(f"/api/generate/jobs/{_JOB_ID}", cookies=_cookies(_ATTACKER))
    with _patched(_mock_store(None)):
        missing = _client().get(f"/api/generate/jobs/{_JOB_ID}", cookies=_cookies(_ATTACKER))
    assert mismatch.status_code == 404, mismatch.text
    assert missing.status_code == 404, missing.text
    assert mismatch.json()["detail"] == missing.json()["detail"]
    # Never 403 — that would confirm the job exists.
    assert mismatch.status_code != 403


def test_job_status_legacy_wallet_owned_job_of_another_wallet_404():
    """GUARD — legacy wallet-owned job (no ``owner_user_id``) whose
    ``owner_wallet`` is not the caller's linked wallet is refused."""
    with _patched(_mock_store(_job(owner_wallet=_OWNER.lower()))):
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}", cookies=_cookies(_ATTACKER))
    assert resp.status_code == 404, resp.text


def test_job_status_account_owner_beats_a_foreign_wallet_tag():
    """A job carrying the caller's ``owner_user_id`` stays readable even when its
    legacy ``owner_wallet`` names someone else — canonical ownership wins, and
    the wallet clause must not lock the real owner out."""
    with _patched(_mock_store(_job(owner_user_id=_OWNER_USER_ID, owner_wallet=_ATTACKER.lower()))):
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}", cookies=_cookies(_OWNER))
    assert resp.status_code == 200, resp.text


def test_job_status_ownerless_legacy_job_readable_by_any_authenticated_caller():
    """Pre-flip jobs (no owner at all) stay readable to authenticated callers —
    migration compatibility, matching the candidates route. Still never anonymous."""
    with _patched(_mock_store(_job())):
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}", cookies=_cookies(_ATTACKER))
    assert resp.status_code == 200, resp.text


def test_job_status_non_generate_job_404_even_when_ownerless():
    """GUARD — the type filter. A sibling job type (``fusion``, enqueued by
    ``strategies_routes``) is refused, exactly as ``GET /jobs`` refuses to list
    it. Without the filter this ownerless record would be served to any
    authenticated caller — see the companion test below for why that is not
    merely untidy."""
    with _patched(_mock_store(_job(job_type="fusion", status="failed"))):
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}", cookies=_cookies(_ATTACKER))
    assert resp.status_code == 404, resp.text


def test_type_filter_prevents_reporting_a_failed_sibling_job_as_queued():
    """GUARD, consequence side. ``strategies_routes`` writes the status
    ``failed`` for fusion jobs; ``_normalize_state`` has no such member and
    coerces it to ``queued``. Serving a non-generate job would therefore report
    a crashed job as still-waiting — an agent would poll it forever. Assert both
    halves: the coercion is real, and the endpoint never exposes it."""
    from archimedes.api.generate_routes import _normalize_state

    assert _normalize_state("failed") == "queued"  # the misreport this guard prevents

    with _patched(_mock_store(_job(job_type="fusion", status="failed"))):
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}", cookies=_cookies(_ATTACKER))
    assert resp.status_code == 404
    assert "queued" not in resp.text


def test_job_status_never_leaks_the_raw_error_string():
    """GUARD — the pipeline stores ``error=str(exc)``, unscrubbed internal
    detail. An errored job reports the ``error`` STATE and nothing more."""
    job = _job(owner_user_id=_OWNER_USER_ID, status="error", best_strategy_id=None)
    job["error"] = "psycopg2.OperationalError: could not connect to server at 10.0.3.14:5432"
    with _patched(_mock_store(job)):
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}", cookies=_cookies(_OWNER))
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "error"
    assert "psycopg2" not in resp.text
    assert "10.0.3.14" not in resp.text

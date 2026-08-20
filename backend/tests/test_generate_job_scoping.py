"""Owner scoping of the generate job endpoints (#788 slice 2, account-first).

Authentication on the generate surfaces is mandatory and unconditional: the
per-job read surfaces — ``GET /api/generate/stream/{job_id}``, ``GET
/api/generate/jobs`` and ``GET /api/generate/jobs/{job_id}/candidates`` — plus
``POST /api/generate/start`` all require a verified session, and per-job reads
are scoped to canonical user ID, with verified-wallet fallback for unclaimed
legacy jobs. Mismatches return **404** (no existence oracle). The retired
``REQUIRE_SIWE_FOR_GENERATION`` opt-out and its ``gate_generation`` dependency
were deleted 2026-08-19 — there is no flag that reopens anonymous access, so
these tests set nothing.

Hermetic: the Redis-backed job store is boundary-mocked (test_generate_cancel_scoping
precedent); SIWE sessions are real signed cookies (test_user_routes precedent);
the fire-and-forget pipeline is stubbed (test_model_gating precedent).
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from fastapi.testclient import TestClient

_OWNER = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
_ATTACKER = "0x9999999999999999999999999999999999999999"
_JOB_ID = "job-scope-1"


def _cookies(wallet: str) -> dict[str, str]:
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


def _client() -> TestClient:
    from archimedes.main import app

    return TestClient(app)


def _job(owner_wallet: str | None) -> dict:
    return {
        "id": _JOB_ID,
        "type": "generate",
        "status": "done",
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:05+00:00",
        "payload": {"owner_wallet": owner_wallet, "brief": {"intent": "x"}, "n_candidates": 1},
        "result": {"best_candidate_id": None, "candidates": []},
    }


def _mock_store(owner_wallet: str | None, *, job_exists: bool = True) -> MagicMock:
    """Boundary-mocked job store: .get, .list_events (one terminal event so the
    SSE generator finishes immediately), .list_recent_jobs, .enqueue."""
    store = MagicMock()
    store.get = AsyncMock(return_value=_job(owner_wallet) if job_exists else None)
    store.list_events = AsyncMock(return_value=[{"id": 1, "event": "done", "data": {"job_id": _JOB_ID}}])
    store.list_recent_jobs = AsyncMock(return_value=[_job(_OWNER.lower()), _job(_ATTACKER.lower()), _job(None)])
    store.enqueue = AsyncMock(return_value=_JOB_ID)
    return store


def _patched(store: MagicMock):
    return (
        patch("archimedes.api.generate_routes.get_job_store", return_value=store),
        patch("archimedes.api.generate_routes._run_with_cleanup", new=AsyncMock(return_value=None)),
    )


_START_BODY = {"brief": {"intent": "momentum on majors", "risk_appetite": "moderate"}, "n_candidates": 1}


# ── Session required everywhere ──────────────────────────────────────


def test_start_with_session_202():
    store = _mock_store(None)
    p1, p2 = _patched(store)
    with p1, p2:
        resp = _client().post("/api/generate/start", json=_START_BODY, cookies=_cookies(_OWNER))
    assert resp.status_code == 202, resp.text
    # The job is tagged with the verified wallet, not a header-spoofable value.
    assert store.enqueue.await_args.kwargs["payload"]["owner_wallet"] == _OWNER.lower()


def test_start_with_session_writes_the_identity_ledger_generation_started_event():
    """Issue #1028 (D2/D2a): only an IDENTIFIED start is ledgered — a
    verified caller's ``generation_started`` event must land in
    ``identity_events`` (the fire-and-forget pipeline itself is stubbed via
    ``_patched``, matching this file's existing hermetic precedent).

    Delta-based (not an absolute count) — ``_OWNER`` is reused by
    ``test_start_with_session_202`` above in the same shared
    test-process DB, matching this whole suite's established convention
    (see ``test_identity_metrics.py``'s module docstring)."""
    from archimedes.db import get_session
    from archimedes.models.identity import IdentityEvent

    wallet = _OWNER.lower()

    def _matching_events() -> list[tuple[str, str]]:
        session = get_session()
        try:
            events = session.query(IdentityEvent).filter(
                IdentityEvent.wallet == wallet, IdentityEvent.event_type == "generation_started"
            )
            return [(e.event_type, e.actor_class) for e in events]
        finally:
            session.close()

    before = _matching_events()

    store = _mock_store(None)
    p1, p2 = _patched(store)
    with p1, p2:
        resp = _client().post("/api/generate/start", json=_START_BODY, cookies=_cookies(_OWNER))
    assert resp.status_code == 202, resp.text

    after = _matching_events()
    assert len(after) == len(before) + 1
    assert after[-1][1] == "human"


def test_start_anonymous_401_and_never_ledgered():
    """Anonymous start is rejected (401) and leaves no identity-ledger trace."""
    from archimedes.db import get_session
    from archimedes.models.identity import IdentityEvent

    session = get_session()
    try:
        before = session.query(IdentityEvent).filter(IdentityEvent.event_type == "generation_started").count()
    finally:
        session.close()

    p1, p2 = _patched(_mock_store(None))
    with p1, p2:
        resp = _client().post("/api/generate/start", json=_START_BODY)
    assert resp.status_code == 401, resp.text

    session = get_session()
    try:
        after = session.query(IdentityEvent).filter(IdentityEvent.event_type == "generation_started").count()
    finally:
        session.close()
    assert after == before


def test_stream_anonymous_401():
    p1, p2 = _patched(_mock_store(_OWNER.lower()))
    with p1, p2:
        resp = _client().get(f"/api/generate/stream/{_JOB_ID}")
    assert resp.status_code == 401, resp.text


def test_stream_owner_can_read():
    p1, p2 = _patched(_mock_store(_OWNER.lower()))
    with p1, p2:
        resp = _client().get(f"/api/generate/stream/{_JOB_ID}", cookies=_cookies(_OWNER))
    assert resp.status_code == 200, resp.text
    assert "event: done" in resp.text


def test_stream_mismatch_404_no_existence_oracle():
    """Another wallet gets a 404 whose body is IDENTICAL to a truly-missing job —
    an attacker cannot distinguish 'exists but not mine' from 'does not exist'."""
    p1, p2 = _patched(_mock_store(_OWNER.lower()))
    with p1, p2:
        mismatch = _client().get(f"/api/generate/stream/{_JOB_ID}", cookies=_cookies(_ATTACKER))
    p1, p2 = _patched(_mock_store(None, job_exists=False))
    with p1, p2:
        missing = _client().get(f"/api/generate/stream/{_JOB_ID}", cookies=_cookies(_ATTACKER))
    assert mismatch.status_code == 404, mismatch.text
    assert missing.status_code == 404
    assert mismatch.json()["detail"] == missing.json()["detail"]


def test_candidates_anonymous_401():
    p1, p2 = _patched(_mock_store(_OWNER.lower()))
    with p1, p2:
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}/candidates")
    assert resp.status_code == 401, resp.text


def test_candidates_owner_can_read():
    p1, p2 = _patched(_mock_store(_OWNER.lower()))
    with p1, p2:
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}/candidates", cookies=_cookies(_OWNER))
    assert resp.status_code == 200, resp.text
    assert resp.json()["job_id"] == _JOB_ID


def test_candidates_mismatch_404():
    p1, p2 = _patched(_mock_store(_OWNER.lower()))
    with p1, p2:
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}/candidates", cookies=_cookies(_ATTACKER))
    assert resp.status_code == 404, resp.text


def test_ownerless_job_readable_by_any_authenticated_caller():
    """Pre-flip jobs (owner_wallet None) stay readable by authenticated callers —
    no lock-out, but still no anonymous access."""
    p1, p2 = _patched(_mock_store(None))
    with p1, p2:
        resp = _client().get(f"/api/generate/jobs/{_JOB_ID}/candidates", cookies=_cookies(_ATTACKER))
    assert resp.status_code == 200, resp.text


def test_jobs_list_anonymous_401():
    p1, p2 = _patched(_mock_store(None))
    with p1, p2:
        resp = _client().get("/api/generate/jobs")
    assert resp.status_code == 401, resp.text


def test_jobs_list_filtered_to_caller():
    """The listing shows the caller's own jobs + ownerless ones — never another
    wallet's job ids (that would be the existence oracle the 404 avoids)."""
    p1, p2 = _patched(_mock_store(None))
    with p1, p2:
        resp = _client().get("/api/generate/jobs", cookies=_cookies(_OWNER))
    assert resp.status_code == 200, resp.text
    jobs = resp.json()["jobs"]
    assert len(jobs) == 2  # own + ownerless; the attacker-owned job is filtered out


# ── No existence oracle on per-job reads (Copilot, #851) ─────────────


async def test_missing_job_reads_401_before_lookup_no_existence_oracle():
    """Anonymous + a MISSING job must yield 401 (same as an existing one),
    never 404 — a 404-for-missing / 401-for-existing split lets an
    unauthenticated caller probe which job_ids exist. The auth check must run
    before the store lookup on both the SSE stream and the candidates read.
    (Moved from the deleted test_generation_gating.py — the guard predates the
    account-first flip and survives it unchanged.)"""
    from archimedes.main import app
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in (
            "/api/generate/stream/nonexistent-job-id",
            "/api/generate/jobs/nonexistent-job-id/candidates",
        ):
            resp = await client.get(path)
            assert resp.status_code == 401, f"{path}: expected 401 pre-lookup, got {resp.status_code}"

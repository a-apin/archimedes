"""Streaming Generate API.

Endpoints (per ``docs/specs/generation-streaming-spec.md``):

  POST /api/generate/start                    — create a job (returns job_id)
  GET  /api/generate/stream/{job_id}          — SSE event stream
  POST /api/generate/jobs/{job_id}/cancel     — best-effort cancel
  GET  /api/generate/jobs                     — list recent jobs (status table)
  GET  /api/generate/jobs/{job_id}/candidates — N candidates incl. rejected

This router lives in its own file per the Spine+ v2 plan's cross-cutting
principle #2 — no new endpoints go into ``api/routes.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from archimedes.agents.generation_pipeline import run_generation
from archimedes.api.auth_siwe import _generation_auth_required, gate_generation, get_verified_wallet
from archimedes.api.funnel_middleware import record_funnel
from archimedes.api.generate_schemas import (
    CandidatesListResponse,
    CandidateSummary,
    GenerateBrief,
    GenerateStartRequest,
    GenerateStartResponse,
    JobsListResponse,
    JobSummary,
)
from archimedes.api.limiter import limiter
from archimedes.services.generation_quota import enforce_generation_quota
from archimedes.services.job_queue import EVENT_LOG_TTL, get_job_store
from archimedes.services.llm_backend import is_allowed_model
from archimedes.services.log_scrubber import sanitize_log_value
from archimedes.services.model_gate import enforce_model_entitlement

logger = logging.getLogger(__name__)

generate_router = APIRouter(prefix="/api/generate", tags=["generate"])

_TERMINAL_EVENTS = {"done", "error"}
_POLL_INTERVAL_SECONDS = 0.4
_STREAM_TIMEOUT_SECONDS = 300  # cap a single SSE connection at 5 min

# Live registry of in-flight asyncio tasks per job. Lets cancel_job actually
# stop the work — without this, /cancel only flips Redis status while the
# agent keeps burning LLM tokens to completion.
_RUNNING_TASKS: dict[str, asyncio.Task] = {}


def _register_task(job_id: str, task: asyncio.Task) -> None:
    _RUNNING_TASKS[job_id] = task
    task.add_done_callback(lambda _t, jid=job_id: _RUNNING_TASKS.pop(jid, None))


def _require_job_auth(caller_wallet: str | None) -> None:
    """Pre-lookup auth gate for per-job READ endpoints (gating ON only).

    Must run BEFORE the job-store lookup: answering 404 (missing) vs 401
    (exists, auth required) from a post-lookup check lets an unauthenticated
    caller probe job_id existence. With this guard an anonymous caller gets
    401 for every job_id — real or not — and learns nothing (Copilot, #851).
    """
    if _generation_auth_required() and not caller_wallet:
        raise HTTPException(status_code=401, detail="Authentication required. Connect your wallet and sign in.")


def _require_job_access(job: dict, caller_wallet: str | None, job_id: str) -> None:
    """Owner-scope per-job READ endpoints when SIWE-for-generation is ON.

    Gating OFF (explicit local-dev opt-out) → no-op, preserving the historical
    open behavior: anyone who knows a job_id can stream/read it.

    Gating ON (secure default):
      - no verified session → 401 (same contract as gate_generation);
      - session wallet ≠ ``payload.owner_wallet`` → **404, not 403** — a
        mismatched caller must not be able to confirm the job even exists
        (existence-oracle avoidance; message identical to the not-found path);
      - ownerless jobs (created while gating was off) stay readable by any
        *authenticated* caller — no lock-out of pre-flip jobs, no anon leak.

    Browser note: EventSource sends cookies on same-origin requests, so the
    SSE stream authenticates exactly like fetch() does — no client change.
    """
    if not _generation_auth_required():
        return
    if not caller_wallet:
        raise HTTPException(status_code=401, detail="Authentication required. Connect your wallet and sign in.")
    owner_wallet = (job.get("payload") or {}).get("owner_wallet")
    if owner_wallet and owner_wallet.lower() != caller_wallet.lower():
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")


@generate_router.post("/start", response_model=GenerateStartResponse, status_code=202)
@limiter.limit("5/minute")
async def start_generation(
    req: GenerateStartRequest,
    request: Request,  # used for slowapi rate-limit keying AND funnel attribution (#787)
    response: Response,  # noqa: ARG001
    _wallet: str | None = Depends(gate_generation),  # 401 when REQUIRE_SIWE_FOR_GENERATION is on
) -> GenerateStartResponse:
    """Create a generation job and start the pipeline in the background."""
    # Anti-abuse (Dan, 2026-06-28): a STRICT per-IP daily cap on WALLET-LESS
    # generations. The Generate path is open (value-before-wallet, #787), but an
    # open LLM-spending endpoint is a flood risk — an agent in a loop could burn
    # budget. Authenticated (SIWE) callers bypass it entirely; a wallet-less caller
    # gets a small free daily allowance, then a 429 steering them to connect a
    # wallet (so the cap doubles as a conversion prompt). Enforced BEFORE any work
    # so a capped request burns nothing. Disabled under TESTING (conftest sets it),
    # matching the slowapi limiter; the quota logic is unit-tested directly.
    if not os.getenv("TESTING"):
        await enforce_generation_quota(request, _wallet)

    # Paid-tier gating (T1.8): a premium (Anthropic) model requires a
    # wallet-connected entitlement. Enforced BEFORE the job is enqueued so a
    # non-entitled premium request is rejected (HTTP 402) without burning any
    # work — and is NOT silently downgraded to the free default model. Free
    # models (and the unset/default case) always pass.
    enforce_model_entitlement(req.model, _wallet)

    store = get_job_store()
    # Free-tier selection (defense in depth — the UI also restricts this).
    # Runs AFTER the entitlement gate above: a non-entitled premium request has
    # already been rejected with 402, so this only ever sees an allowlisted free
    # model, an entitled premium id, junk, or None. Only an allowlisted free-tier
    # id is honored for the pipeline; everything else (incl. entitled premium,
    # which cannot serve until Bedrock activation — roadmap T3.8) falls back to
    # the env default, so behavior is UNCHANGED when no valid free model is picked.
    selected_model = req.model if is_allowed_model(req.model) else None
    if req.model and selected_model is None:
        logger.info("generate: ignoring non-allowlisted model %r; using env default", sanitize_log_value(req.model))
    # Tag the job with its creator (audit 2026-06-14) so cancel can be scoped to
    # the owner. When no SIWE session exists (flag off / anonymous), owner_wallet
    # is None and the job stays cancellable by anyone — preserving today's open
    # behavior with no flag-day risk. The same wallet is also threaded into
    # run_generation below so persisted candidates carry per-user ownership.
    job_id = await store.enqueue(
        job_type="generate",
        payload={
            "brief": req.brief.model_dump(),
            "n_candidates": req.n_candidates,
            "owner_wallet": _wallet,
            # The allowlist-filtered model the pipeline will actually use (None →
            # env default). enforce_model_entitlement (above) has already rejected
            # a non-entitled premium request with 402, so anything reaching here is
            # either an allowlisted free model or None — auditable provenance for
            # the tier the run was authorized for.
            "model": selected_model,
        },
    )

    # Fire-and-forget the pipeline. The route doesn't await it; the SSE stream
    # below tails the event log written by the pipeline as it runs.
    # _wallet is the SIWE-derived owner from gate_generation — the SAME value
    # stored in the job payload above; threading it into the pipeline stamps
    # ownership onto every persisted candidate (never a client-supplied value).
    task = asyncio.create_task(
        _run_with_cleanup(job_id, req.brief, req.n_candidates, req.mode, selected_model, _wallet)
    )
    _register_task(job_id, task)

    # Conversion funnel (#787): a generation actually started for this visitor —
    # the key "tried the product" transition. Fail-safe; never blocks the response.
    await record_funnel(request, "generation_started")

    return GenerateStartResponse(
        job_id=job_id,
        stream_url=f"/api/generate/stream/{job_id}",
        ttl_seconds=EVENT_LOG_TTL,
    )


async def _run_with_cleanup(
    job_id: str,
    brief: GenerateBrief,
    n_candidates: int,
    mode: str | None = None,
    model: str | None = None,
    owner_wallet: str | None = None,
) -> None:
    try:
        await run_generation(
            job_id=job_id,
            brief=brief,
            n_candidates=n_candidates,
            mode=mode,
            model=model,
            owner_wallet=owner_wallet,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # safety net — run_generation already emits error events
        logger.exception("background job %s crashed outside run_generation", job_id)


@generate_router.get("/stream/{job_id}")
async def stream_events(
    job_id: str,
    request: Request,
    caller_wallet: str | None = Depends(get_verified_wallet),
) -> StreamingResponse:
    """Server-Sent Events. Tails the job's Redis event log.

    Honours ``Last-Event-ID`` for client resume after a disconnect (the spec
    calls this out — the frontend stashes ``currentJobId`` in localStorage
    and re-subscribes on mount). Owner-scoped when SIWE-for-generation is on
    (see ``_require_job_access``); open when the gate is explicitly off.
    """
    _require_job_auth(caller_wallet)  # 401 BEFORE the lookup — no existence oracle
    store = get_job_store()
    job = await store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")
    _require_job_access(job, caller_wallet, job_id)

    try:
        last_event_id = int(request.headers.get("Last-Event-ID", "0"))
    except (TypeError, ValueError):
        last_event_id = 0

    async def event_generator() -> AsyncIterator[str]:
        # Yield a comment to flush the response headers immediately so the
        # client's onopen fires within the spec's 500 ms target.
        yield ": stream opened\n\n"

        cursor = last_event_id
        elapsed = 0.0
        while elapsed < _STREAM_TIMEOUT_SECONDS:
            if await request.is_disconnected():
                logger.info("sse client disconnected (job=%s, after=%d)", sanitize_log_value(job_id), cursor)
                return

            new_events = await store.list_events(job_id, after_id=cursor)
            for ev in new_events:
                cursor = ev["id"]
                yield _format_sse(ev)
                if ev.get("event") in _TERMINAL_EVENTS:
                    return

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            elapsed += _POLL_INTERVAL_SECONDS

        # Heartbeat-timeout exit — client can reconnect with Last-Event-ID.
        yield ": stream timeout\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _format_sse(ev: dict) -> str:
    """Encode one event log entry as an SSE frame."""
    event_id = ev["id"]
    event_name = ev.get("event", "message")
    data = ev.get("data", {})
    return f"id: {event_id}\nevent: {event_name}\ndata: {json.dumps(data, default=str)}\n\n"


@generate_router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    caller_wallet: str | None = Depends(get_verified_wallet),
) -> dict[str, str]:
    """Cancel a running job. Idempotent.

    Hard cancellation: looks up the asyncio.Task driving the job and calls
    ``task.cancel()`` on it. The pipeline's ``except CancelledError`` branch
    emits the synthetic error event + flips Redis status to ``cancelled``.

    Caveat: if the agent is mid-``asyncio.to_thread(llm_call)``, the OS
    thread itself isn't cancellable (Python doesn't expose that). The
    awaiter unblocks immediately, the in-flight LLM call burns to
    completion but its result is discarded — no events emitted after the
    cancellation, no strategy persisted.
    """
    store = get_job_store()
    job = await store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")

    # Owner scoping (audit 2026-06-14): if the job was created by a verified
    # wallet, only that wallet may cancel it. Jobs created anonymously
    # (owner_wallet is None) stay cancellable by anyone — preserving the open
    # behavior when SIWE-for-generation is off. Prevents a third party who
    # learns a job_id from killing another user's in-flight generation.
    owner_wallet = (job.get("payload") or {}).get("owner_wallet")
    if owner_wallet and owner_wallet.lower() != (caller_wallet or "").lower():
        raise HTTPException(status_code=403, detail="This job belongs to a different wallet.")

    if job["status"] in ("done", "error", "cancelled"):
        return {"job_id": job_id, "status": job["status"]}

    # Flip status first so observers see "cancelled" even if the cancel
    # callback hasn't fully propagated yet.
    await store.update_status(job_id, "cancelled", error="cancelled by user")
    await store.push_event(
        job_id,
        {
            "event": "error",
            "data": {"job_id": job_id, "message": "cancelled by user", "recoverable": False, "code": "CANCELLED"},
        },
    )

    # Hard-cancel the task itself if we're still holding a reference.
    task = _RUNNING_TASKS.get(job_id)
    if task is not None and not task.done():
        task.cancel()
        logger.info("hard-cancelled job %s", sanitize_log_value(job_id))
    else:
        logger.info("cancel for %s: no live task (already finished or restart)", sanitize_log_value(job_id))

    return {"job_id": job_id, "status": "cancelled"}


@generate_router.get("/jobs", response_model=JobsListResponse)
async def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    caller_wallet: str | None = Depends(get_verified_wallet),
) -> JobsListResponse:
    """Recent jobs for the GenerationStatus UI.

    When SIWE-for-generation is ON the listing requires a session and is
    filtered to the caller's own jobs (plus ownerless pre-flip jobs) — the
    same no-existence-oracle stance as the per-job endpoints. When the gate
    is explicitly off, the historical open listing is preserved.
    """
    gated = _generation_auth_required()
    if gated and not caller_wallet:
        raise HTTPException(status_code=401, detail="Authentication required. Connect your wallet and sign in.")
    store = get_job_store()
    raw = await store.list_recent_jobs(limit=limit)
    summaries: list[JobSummary] = []
    for j in raw:
        if j.get("type") != "generate":
            continue
        payload = j.get("payload") or {}
        owner_wallet = payload.get("owner_wallet")
        if gated and owner_wallet and owner_wallet.lower() != (caller_wallet or "").lower():
            continue
        brief = payload.get("brief") or {}
        result = j.get("result") or {}
        summaries.append(
            JobSummary(
                job_id=j["id"],
                state=_normalize_state(j.get("status") or "queued"),
                brief_intent=brief.get("intent", ""),
                created_at=j.get("created_at", ""),
                updated_at=j.get("updated_at", ""),
                n_candidates=int(payload.get("n_candidates") or 1),
                best_strategy_id=result.get("best_strategy_id"),
            )
        )
    return JobsListResponse(jobs=summaries)


def _normalize_state(s: str) -> str:
    if s in ("queued", "running", "done", "error", "cancelled"):
        return s
    return "queued"


@generate_router.get("/jobs/{job_id}/candidates", response_model=CandidatesListResponse)
async def list_candidates(
    job_id: str,
    caller_wallet: str | None = Depends(get_verified_wallet),
) -> CandidatesListResponse:
    """Rejected-candidate viewer. Empty list until ``done``.

    Owner-scoped when SIWE-for-generation is on (``_require_job_access``).
    """
    _require_job_auth(caller_wallet)  # 401 BEFORE the lookup — no existence oracle
    store = get_job_store()
    job = await store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")
    _require_job_access(job, caller_wallet, job_id)
    result = job.get("result") or {}
    cands = result.get("candidates", []) or []
    return CandidatesListResponse(
        job_id=job_id,
        best_candidate_id=result.get("best_candidate_id"),
        candidates=[CandidateSummary(**c) for c in cands],
    )

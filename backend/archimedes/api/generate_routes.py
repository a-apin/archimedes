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
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from archimedes.agents.generation_pipeline import run_generation
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
from archimedes.services.job_queue import EVENT_LOG_TTL, get_job_store

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


@generate_router.post("/start", response_model=GenerateStartResponse, status_code=202)
@limiter.limit("5/minute")
async def start_generation(req: GenerateStartRequest, request: Request) -> GenerateStartResponse:
    """Create a generation job and start the pipeline in the background."""
    store = get_job_store()
    job_id = await store.enqueue(
        job_type="generate",
        payload={"brief": req.brief.model_dump(), "n_candidates": req.n_candidates},
    )

    # Fire-and-forget the pipeline. The route doesn't await it; the SSE stream
    # below tails the event log written by the pipeline as it runs.
    task = asyncio.create_task(_run_with_cleanup(job_id, req.brief, req.n_candidates))
    _register_task(job_id, task)

    return GenerateStartResponse(
        job_id=job_id,
        stream_url=f"/api/generate/stream/{job_id}",
        ttl_seconds=EVENT_LOG_TTL,
    )


async def _run_with_cleanup(job_id: str, brief: GenerateBrief, n_candidates: int) -> None:
    try:
        await run_generation(job_id=job_id, brief=brief, n_candidates=n_candidates)
    except asyncio.CancelledError:
        raise
    except Exception:  # safety net — run_generation already emits error events
        logger.exception("background job %s crashed outside run_generation", job_id)


@generate_router.get("/stream/{job_id}")
async def stream_events(job_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events. Tails the job's Redis event log.

    Honours ``Last-Event-ID`` for client resume after a disconnect (the spec
    calls this out — the frontend stashes ``currentJobId`` in localStorage
    and re-subscribes on mount).
    """
    store = get_job_store()
    job = await store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")

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
                logger.info("sse client disconnected (job=%s, after=%d)", job_id, cursor)
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
async def cancel_job(job_id: str) -> dict[str, str]:
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
        logger.info("hard-cancelled job %s", job_id)
    else:
        logger.info("cancel for %s: no live task (already finished or restart)", job_id)

    return {"job_id": job_id, "status": "cancelled"}


@generate_router.get("/jobs", response_model=JobsListResponse)
async def list_jobs(limit: int = 20) -> JobsListResponse:
    """Recent jobs for the GenerationStatus UI."""
    store = get_job_store()
    raw = await store.list_recent_jobs(limit=max(1, min(limit, 100)))
    summaries: list[JobSummary] = []
    for j in raw:
        if j.get("type") != "generate":
            continue
        payload = j.get("payload") or {}
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
async def list_candidates(job_id: str) -> CandidatesListResponse:
    """Rejected-candidate viewer. Empty list until ``done``."""
    store = get_job_store()
    job = await store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")
    result = job.get("result") or {}
    cands = result.get("candidates", []) or []
    return CandidatesListResponse(
        job_id=job_id,
        best_candidate_id=result.get("best_candidate_id"),
        candidates=[CandidateSummary(**c) for c in cands],
    )

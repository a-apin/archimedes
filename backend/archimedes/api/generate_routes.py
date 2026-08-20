"""Streaming Generate API.

Endpoints (per ``docs/specs/generation-streaming-spec.md``):

  POST /api/generate/start                    — create a job (returns job_id)
  GET  /api/generate/stream/{job_id}          — SSE event stream
  POST /api/generate/jobs/{job_id}/cancel     — best-effort cancel
  GET  /api/generate/jobs                     — list recent jobs (status table)
  GET  /api/generate/jobs/{job_id}            — one job's status (poll fallback)
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
from archimedes.api.account_auth import CurrentUser, require_current_user
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
from archimedes.api.wallet_routes import get_linked_wallet_address
from archimedes.services import generation_payment
from archimedes.services.generation_quota import enforce_generation_quota
from archimedes.services.identity_events import emit_identity_event
from archimedes.services.job_queue import EVENT_LOG_TTL, get_job_store
from archimedes.services.llm_backend import is_allowed_model
from archimedes.services.log_scrubber import sanitize_log_value
from archimedes.services.model_gate import enforce_model_entitlement

logger = logging.getLogger(__name__)

generate_router = APIRouter(prefix="/api/generate", tags=["generate"])

# Public sibling: main.py mounts generate_router behind require_current_user
# wholesale, but the price quote must be readable BEFORE sign-in — a human
# comparing cost, or an agent planning its approval flow (#1293), needs the
# number first. Only /quote lives here; everything stateful stays gated.
generate_public_router = APIRouter(prefix="/api/generate", tags=["generate"])

_TERMINAL_EVENTS = {"done", "error"}
_POLL_INTERVAL_SECONDS = 0.4
_STREAM_TIMEOUT_SECONDS = 300  # cap a single SSE connection at 5 min
# How long the connection may go byte-silent before we push a keep-alive
# comment (#891). Debate/fan-out compute (sequential adversarial LLM turns,
# then a parallel backtest gather across the whole candidate pool) can run
# for tens of seconds without producing a new job-store event — the poll
# loop previously just slept through that stretch and wrote nothing to the
# socket. Intermediaries with an idle-read timeout shorter than that stretch
# (CloudFront's origin idle timeout, corporate/browser proxies) will drop a
# chunked connection that's gone quiet that long, even though the origin is
# still alive and the job keeps running server-side. A ~15s heartbeat
# cadence gives multiple safety margins under any such timeout.
_HEARTBEAT_INTERVAL_SECONDS = 15.0

# Live registry of in-flight asyncio tasks per job. Lets cancel_job actually
# stop the work — without this, /cancel only flips Redis status while the
# agent keeps burning LLM tokens to completion.
_RUNNING_TASKS: dict[str, asyncio.Task] = {}


def _register_task(job_id: str, task: asyncio.Task) -> None:
    _RUNNING_TASKS[job_id] = task
    task.add_done_callback(lambda _t, jid=job_id: _RUNNING_TASKS.pop(jid, None))


def _require_job_access(job: dict, user_id: str, job_id: str, linked_wallet: str | None = None) -> None:
    """Hide account-owned and unclaimed legacy jobs from other users."""
    payload = job.get("payload") or {}
    owner_user_id = payload.get("owner_user_id")
    owner_wallet = payload.get("owner_wallet")
    if owner_user_id and owner_user_id != user_id:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")
    if not owner_user_id and owner_wallet and owner_wallet.lower() != (linked_wallet or "").lower():
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")


@generate_public_router.get("/quote")
async def get_generation_quote():
    """The upfront generation cost estimate — public, so a human can see the
    price before signing in and an agent can plan before paying (#1293's
    discoverability point). The SAME quote rides inside every 402 from
    /start, so the two surfaces can never disagree: both call
    ``generation_payment.quote()``, whose flat price is the seam #1217's
    measured budget later replaces."""
    return generation_payment.quote()


@generate_router.post("/start", response_model=GenerateStartResponse, status_code=202)
@limiter.limit("5/minute")
async def start_generation(
    req: GenerateStartRequest,
    request: Request,  # used for slowapi rate-limit keying AND funnel attribution (#787)
    response: Response,  # slowapi header injection (#1182) + PAYMENT-RESPONSE receipt
    user: CurrentUser = Depends(require_current_user),
) -> GenerateStartResponse:
    """Create account-owned generation job and start pipeline in background."""
    linked_wallet = get_linked_wallet_address(request)

    # Daily volume caps (per-account AND per-IP, both must pass) — the rebuilt
    # generation_quota (#1194 revision a). Runs FIRST: cheapest anti-abuse
    # check before any entitlement or enqueue work, same ordering the old
    # wallet-less cap had. Disabled under TESTING (conftest sets it), matching
    # the slowapi limiter; the quota logic is unit-tested directly in
    # test_generation_quota.py.
    if not os.getenv("TESTING"):
        await enforce_generation_quota(request, user.id)

    # Payment gate (flag: GENERATION_PAYMENT_REQUIRED — Dan flips deliberately,
    # see the #834 flip-list). Order is deliberate: AFTER the quota (a
    # quota-blocked caller is refused 429 before ever being asked to pay) and
    # BEFORE entitlement/enqueue (no work is burned for an unpaid request).
    # The 402 carries the full x402 requirements — that response IS the
    # quote-approval flow for humans and agents alike. Paper trading stays free.
    if generation_payment.payment_required():
        if not linked_wallet:
            # The wallet-connection precondition. 409 (not 402): the blocker is
            # account state, not a missing payment. NOTE the faucet is the one
            # human-only step (#1294) — an agent hitting this must have its
            # wallet funded by a human before payment can succeed.
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "wallet_link_required",
                    "message": (
                        "Generation requires a linked, funded wallet. Link a wallet to your account "
                        "(POST /api/wallets/challenge → /api/wallets/verify), fund it with testnet USDC "
                        "(the faucet currently requires a human), then retry. "
                        "See GET /api/generate/quote for the price."
                    ),
                },
            )
        payment = await generation_payment.enforce_generation_payment(request, linked_wallet)
        if payment is not None:
            # Surface the settlement receipt (PAYMENT-RESPONSE) to the payer.
            for name, value in (payment.response_headers or {}).items():
                response.headers[name] = value

    # Paid-tier gating (T1.8): a premium (Anthropic) model requires a
    # wallet-connected entitlement. Enforced BEFORE the job is enqueued so a
    # non-entitled premium request is rejected (HTTP 402) without burning any
    # work — and is NOT silently downgraded to the free default model. Free
    # models (and the unset/default case) always pass.
    # Normal account use and free models need no wallet.
    enforce_model_entitlement(req.model, linked_wallet)

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
    # Canonical Better Auth ownership is server-derived and follows the job
    # through persistence. Wallet provenance stays optional.
    job_id = await store.enqueue(
        job_type="generate",
        payload={
            "brief": req.brief.model_dump(),
            "n_candidates": req.n_candidates,
            "owner_user_id": user.id,
            "owner_wallet": linked_wallet,
            # The allowlist-filtered model the pipeline will actually use (None →
            # env default). enforce_model_entitlement (above) has already rejected
            # a non-entitled premium request with 402, so anything reaching here is
            # either an allowlisted free model or None — auditable provenance for
            # the tier the run was authorized for.
            "model": selected_model,
        },
    )

    # Fire-and-forget; the SSE stream tails events. User ownership is threaded
    # from this authenticated request, never client-supplied.
    task = asyncio.create_task(
        _run_with_cleanup(
            job_id,
            req.brief,
            req.n_candidates,
            req.mode,
            selected_model,
            owner_user_id=user.id,
            owner_wallet=linked_wallet,
        )
    )
    _register_task(job_id, task)

    # Conversion funnel (#787): a generation actually started for this visitor —
    # the key "tried the product" transition. Fail-safe; never blocks the response.
    await record_funnel(request, "generation_started")
    emit_identity_event(
        wallet=linked_wallet,
        event_type="generation_started",
        actor_class="human",
        meta={"job_id": job_id, "model": selected_model},
    )

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
    owner_user_id: str | None = None,
    owner_wallet: str | None = None,
) -> None:
    try:
        await run_generation(
            job_id=job_id,
            brief=brief,
            n_candidates=n_candidates,
            mode=mode,
            model=model,
            owner_user_id=owner_user_id,
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
    user: CurrentUser = Depends(require_current_user),
) -> StreamingResponse:
    """Server-Sent Events for one account-owned generation job."""
    store = get_job_store()
    job = await store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")
    _require_job_access(job, user.id, job_id, get_linked_wallet_address(request))

    try:
        last_event_id = int(request.headers.get("Last-Event-ID", "0"))
    except (TypeError, ValueError):
        last_event_id = 0

    async def event_generator() -> AsyncIterator[str]:
        # Yield a comment to flush the response headers immediately so the
        # client's onopen fires within the spec's 500 ms target.
        yield ": stream opened\n\n"

        cursor = last_event_id
        # Wall-clock, not accumulated sleep duration: list_events() latency or
        # scheduler jitter can make a single loop iteration take longer than
        # _POLL_INTERVAL_SECONDS, and summing the intended sleep length instead
        # of measuring real elapsed time would undercount silence and could
        # delay a heartbeat past _HEARTBEAT_INTERVAL_SECONDS -- reintroducing
        # the idle-disconnect this fix exists to prevent.
        loop = asyncio.get_running_loop()
        stream_start = loop.time()
        last_heartbeat_at = stream_start
        while loop.time() - stream_start < _STREAM_TIMEOUT_SECONDS:
            if await request.is_disconnected():
                logger.info("sse client disconnected (job=%s, after=%d)", sanitize_log_value(job_id), cursor)
                return

            new_events = await store.list_events(job_id, after_id=cursor)
            for ev in new_events:
                cursor = ev["id"]
                yield _format_sse(ev)
                last_heartbeat_at = loop.time()  # a real event resets the silence clock too
                if ev.get("event") in _TERMINAL_EVENTS:
                    return

            # No new events this cycle — a long-running compute step (debate
            # turns, candidate-fan-out backtests) may keep the job busy for a
            # while yet. Emit a keep-alive comment so the connection never
            # goes byte-silent long enough for an intermediary to decide it's
            # dead (#891). SSE comment lines are ignored by EventSource/any
            # spec-compliant parser, so this is invisible to application code.
            now = loop.time()
            if now - last_heartbeat_at >= _HEARTBEAT_INTERVAL_SECONDS:
                yield ": heartbeat\n\n"
                last_heartbeat_at = now

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

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
    request: Request,
    user: CurrentUser = Depends(require_current_user),
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

    _require_job_access(job, user.id, job_id, get_linked_wallet_address(request))

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
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(require_current_user),
) -> JobsListResponse:
    """Recent jobs for the GenerationStatus UI.

    Better Auth session is required. Results are filtered to canonical owner;
    linked wallet resolves pre-migration wallet-owned jobs. Cross-user jobs stay hidden.
    """
    store = get_job_store()
    raw = await store.list_recent_jobs(limit=limit)
    linked_wallet = get_linked_wallet_address(request)
    summaries: list[JobSummary] = []
    for j in raw:
        if j.get("type") != "generate":
            continue
        payload = j.get("payload") or {}
        owner_user_id = payload.get("owner_user_id")
        owner_wallet = payload.get("owner_wallet")
        if owner_user_id and owner_user_id != user.id:
            continue
        if not owner_user_id and owner_wallet and owner_wallet.lower() != (linked_wallet or "").lower():
            continue
        summaries.append(_job_summary(j))
    return JobsListResponse(jobs=summaries)


def _job_summary(job: dict, job_id: str | None = None) -> JobSummary:
    """Project one job-store record onto the wire shape.

    Shared by the listing and the single-job read so the two surfaces can never
    disagree about a job's state — an agent that switches from ``GET /jobs`` to
    ``GET /jobs/{job_id}`` reads the identical record.
    """
    payload = job.get("payload") or {}
    brief = payload.get("brief") or {}
    result = job.get("result") or {}
    return JobSummary(
        job_id=job.get("id") or job_id or "",
        state=_normalize_state(job.get("status") or "queued"),
        brief_intent=brief.get("intent", ""),
        created_at=job.get("created_at", ""),
        updated_at=job.get("updated_at", ""),
        n_candidates=int(payload.get("n_candidates") or 1),
        best_strategy_id=result.get("best_strategy_id"),
    )


def _normalize_state(s: str) -> str:
    if s in ("queued", "running", "done", "error", "cancelled"):
        return s
    return "queued"


@generate_router.get("/jobs/{job_id}", response_model=JobSummary)
async def get_job(
    job_id: str,
    request: Request,
    user: CurrentUser = Depends(require_current_user),
) -> JobSummary:
    """One job's current state — the poll fallback for a client with no live stream (#1292).

    An agent that never opened the SSE stream, or whose connection dropped past
    the 15-minute event-log TTL, previously had to pull ``GET /jobs`` and scan
    the whole listing to learn whether its single job had finished. This returns
    the same ``JobSummary`` record for one job.

    Three refusals, all rendered as the byte-identical 404 the other per-job
    reads use, so none of them is an existence oracle:

    * unknown / expired ``job_id``;
    * a job whose ``type`` is not ``generate`` — this endpoint is the generate
      surface, not a general job reader. The filter mirrors ``list_jobs`` and is
      load-bearing rather than cosmetic: sibling job types use states outside
      this router's vocabulary (``strategies_routes`` writes ``failed``), which
      ``_normalize_state`` would coerce to ``queued`` — reporting a crashed job
      as still-waiting;
    * a job owned by another account, or a legacy wallet-owned job whose owner
      is not the caller's linked wallet (``_require_job_access``).

    The stored ``error`` string is deliberately not surfaced: the pipeline
    writes raw ``str(exc)`` into it, which is unscrubbed internal detail. The
    ``error`` state plus the SSE ``error`` event remain the reporting path.
    """
    store = get_job_store()
    job = await store.get(job_id)
    if not job or job.get("type") != "generate":
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")
    _require_job_access(job, user.id, job_id, get_linked_wallet_address(request))
    return _job_summary(job, job_id)


@generate_router.get("/jobs/{job_id}/candidates", response_model=CandidatesListResponse)
async def list_candidates(
    job_id: str,
    request: Request,
    user: CurrentUser = Depends(require_current_user),
) -> CandidatesListResponse:
    """Rejected-candidate viewer. Empty list until ``done``."""
    store = get_job_store()
    job = await store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")
    _require_job_access(job, user.id, job_id, get_linked_wallet_address(request))
    result = job.get("result") or {}
    cands = result.get("candidates", []) or []
    return CandidatesListResponse(
        job_id=job_id,
        best_candidate_id=result.get("best_candidate_id"),
        candidates=[CandidateSummary(**c) for c in cands],
    )

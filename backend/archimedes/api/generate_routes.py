"""Streaming Generate API.

Endpoints (per ``docs/specs/generation-streaming-spec.md``):

  POST /api/generate/start                    — create a job (returns job_id)
  GET  /api/generate/stream/{job_id}          — SSE event stream
  POST /api/generate/jobs/{job_id}/cancel     — best-effort cancel
  GET  /api/generate/jobs                     — list recent jobs (status table)
  GET  /api/generate/jobs/{job_id}            — one job's status (poll fallback)
  GET  /api/generate/jobs/{job_id}/candidates — N candidates incl. rejected
  GET  /api/generate/jobs/{job_id}/cost       — raw measurement, no prices (#1217)

This router lives in its own file per the Spine+ v2 plan's cross-cutting
principle #2 — no new endpoints go into ``api/routes.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

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
    JobCostResponse,
    JobsListResponse,
    JobSummary,
)
from archimedes.api.limiter import limiter
from archimedes.api.wallet_routes import get_linked_wallet_address
from archimedes.services import generation_credits, generation_payment
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
# Terminal job-store statuses — mirrors `_normalize_state`'s known-status set
# minus "queued"/"running". Used by the SSE loop's dead-stream detection.
_TERMINAL_STATUSES = {"done", "error", "cancelled"}
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

# How often the run's independent heartbeat task touches the job's
# `heartbeat_at` (#1355). On its own clock, NOT gated on pipeline progress —
# see `_run_with_cleanup`/`_heartbeat_loop`. Comfortably inside
# `_STALLED_AFTER_SECONDS` so a live run never reads as stalled.
_JOB_HEARTBEAT_INTERVAL_SECONDS = 30.0

# A `running` job whose heartbeat is older than this is reported `stalled` on
# read (`_normalize_state`) and, if an SSE stream is open on it, gets one
# synthetic `error`/`STALLED` event so the stream stops claiming to be live.
# Value matches the spec's threshold (docs/specs/generation-streaming-spec.md
# § Failure modes: "Lock-without-progress for > 5 min").
_STALLED_AFTER_SECONDS = 300

# Live registry of in-flight asyncio tasks per job. Lets cancel_job actually
# stop the work — without this, /cancel only flips Redis status while the
# agent keeps burning LLM tokens to completion.
_RUNNING_TASKS: dict[str, asyncio.Task] = {}


def _register_task(job_id: str, task: asyncio.Task) -> None:
    _RUNNING_TASKS[job_id] = task
    task.add_done_callback(lambda _t, jid=job_id: _RUNNING_TASKS.pop(jid, None))


# ── Generation admission control ──────────────────────────────────────────
# The whole web tier shares one Fargate task; a single generation averages
# ~65% of its vCPU for ~48s (measured 2026-08-20), so unbounded parallel
# pipelines starve auth, SSE, and the ALB health check — the task gets
# killed and EVERY in-flight job dies with it. At most
# GENERATION_MAX_CONCURRENT pipelines run at once; up to
# GENERATION_MAX_QUEUE more wait their turn (the job stays `queued` and its
# SSE stream gets a `job_queued` event + heartbeats); beyond that /start
# refuses 429 BEFORE the payment gate, so nobody is ever charged for a slot
# that doesn't exist.

_GENERATION_GATE: asyncio.Semaphore | None = None
_GENERATION_GATE_LOOP: asyncio.AbstractEventLoop | None = None
_WAITING_GENERATIONS = 0


def _max_concurrent_generations() -> int:
    try:
        return max(1, int(os.getenv("GENERATION_MAX_CONCURRENT", "1")))
    except ValueError:
        return 1


def _max_queued_generations() -> int:
    try:
        return max(0, int(os.getenv("GENERATION_MAX_QUEUE", "10")))
    except ValueError:
        return 10


def _generation_gate() -> asyncio.Semaphore:
    """Per-event-loop singleton.

    asyncio primitives bind to the loop that first awaits them; a module-level
    singleton would leak a closed test loop into the next test. Recreating on
    loop change costs nothing in prod (one loop for the process lifetime).
    """
    global _GENERATION_GATE, _GENERATION_GATE_LOOP
    loop = asyncio.get_running_loop()
    if _GENERATION_GATE is None or _GENERATION_GATE_LOOP is not loop:
        _GENERATION_GATE = asyncio.Semaphore(_max_concurrent_generations())
        _GENERATION_GATE_LOOP = loop
    return _GENERATION_GATE


async def _emit_queued(job_id: str, position: int) -> None:
    """Tell the job's SSE stream it is waiting — informational, never fatal."""
    from datetime import UTC, datetime

    try:
        await get_job_store().push_event(
            job_id,
            {
                "event": "job_queued",
                "data": {
                    "ts": datetime.now(UTC).isoformat(),
                    "job_id": job_id,
                    "position": position,
                    "max_concurrent": _max_concurrent_generations(),
                },
            },
        )
    except Exception:
        logger.warning("could not emit job_queued for %s", job_id, exc_info=True)


def _require_job_access(job: dict, user_id: str, job_id: str, linked_wallet: str | None = None) -> None:
    """Hide account-owned and unclaimed legacy jobs from other users."""
    payload = job.get("payload") or {}
    owner_user_id = payload.get("owner_user_id")
    owner_wallet = payload.get("owner_wallet")
    if owner_user_id and owner_user_id != user_id:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")
    if not owner_user_id and owner_wallet and owner_wallet.lower() != (linked_wallet or "").lower():
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")


# USDC's on-chain decimal precision — matches marketplace.payments._USDC_DECIMALS.
# PaymentInfo.amount (circlekit) is a string of raw base units, e.g. "2000000"
# for $2.00; this is the sole place generate_routes converts that into a
# display dollar string for the receipt (see models/payment_receipt.py).
_RECEIPT_USDC_DECIMALS = 6


def _format_receipt_usd(amount_base_units: int) -> str:
    from decimal import Decimal

    return f"${Decimal(amount_base_units) / Decimal(10**_RECEIPT_USDC_DECIMALS):.2f}"


def _write_payment_receipt(*, user_id: str, payment, job_id: str) -> None:
    """The persistence boundary — no try/except here. The caller
    (``_persist_payment_receipt``) wraps this; tests patch this exact name to
    exercise the fail-safe path without reaching into the DB layer."""
    from archimedes.db import get_session
    from archimedes.models.payment_receipt import record_payment_receipt

    amount_base_units = int(payment.amount)
    with get_session() as session:
        record_payment_receipt(
            session,
            user_id=user_id,
            payer_wallet=payment.payer,
            amount_base_units=amount_base_units,
            price_usd=_format_receipt_usd(amount_base_units),
            network=payment.network,
            settlement_ref=payment.transaction,
            job_id=job_id,
        )
        session.commit()


def _persist_payment_receipt(*, user_id: str, payment, job_id: str) -> None:
    """Persist one settled generation payment as a receipt (Dan's directive:
    "we must provide people with their receipts").

    FAIL-SAFE, deliberately: the payment already cleared by the time this
    runs — the user already paid. A receipt-write failure must never fail or
    delay the paid generation, so every exception is swallowed here and only
    logged. This is the ONE place in this module that name is true; every
    other write on the happy path (job enqueue, funnel, identity event) is
    allowed to matter to the response.
    """
    try:
        _write_payment_receipt(user_id=user_id, payment=payment, job_id=job_id)
    except Exception:
        logger.warning(
            "payment receipt write failed for job %s (payment already settled — no user impact)",
            sanitize_log_value(job_id),
            exc_info=True,
        )


async def _paywall_with_credit(request: Request, linked_wallet: str, user_id: str):
    """Run the paywall so that money taken is always money accounted for (#1441).

    Returns ``(payment, credit_id)``. ``credit_id`` names the credit this run
    spends once it is safely enqueued; ``payment`` is the settled
    ``PaymentInfo`` only when this request is what settled it.

    The ordering below is the fix. The charge used to settle and the job to be
    enqueued afterwards, with the entitlement gate in between — so a 402 from
    that gate, an enqueue error, or a worker crash left the money taken and
    nothing delivered. Now the charge buys a durable credit first, and only a
    job that actually reaches the queue spends it.
    """
    # An unspent credit from an earlier paid-but-undelivered run pays for this
    # one. Checked BEFORE the paywall, so a payer whose last generation died is
    # never asked for money twice.
    existing = generation_credits.take_credit(user_id)
    if existing is not None:
        logger.info("generation covered by unspent credit %s — no new charge", existing)
        return None, existing

    if not generation_payment.settles_real_value():
        # Flag off or dry-run: the paywall still quotes and still exercises the
        # 402 approval flow, but no value moves. Nothing to owe and nothing to
        # record, so the ledger stays entirely out of the way.
        settled = await generation_payment.enforce_generation_payment(request, linked_wallet)
        return settled, None

    idempotency_key = (request.headers.get("Idempotency-Key") or "").strip() or None
    outcome, credit_id = generation_credits.claim(user_id, idempotency_key)

    if outcome == "already_settled":
        # This key already paid and the credit is still unspent. Spend it.
        return None, credit_id
    if outcome == "already_consumed":
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "idempotency_key_already_used",
                "message": (
                    "This Idempotency-Key already paid for a generation that started. "
                    "Use a new key to start another. No second charge was taken."
                ),
            },
        )
    if outcome == "in_flight":
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "payment_in_flight",
                "message": (
                    "A payment with this Idempotency-Key is still settling. Wait for it to "
                    "finish rather than retrying — a retry signs a fresh authorization and "
                    "would charge you twice."
                ),
            },
        )

    try:
        settled = await generation_payment.enforce_generation_payment(request, linked_wallet)
    except BaseException:
        # Includes the 402 the paywall raises when no payment was presented. The
        # claim must not outlive the attempt: a `pending` row left behind reads
        # as `in_flight` forever and locks that key out.
        generation_credits.void(credit_id)
        raise

    if settled is None:
        # settles_real_value() said otherwise, so this is a configuration race
        # rather than an expected path. No value moved; release the claim.
        generation_credits.void(credit_id)
        return None, None

    generation_credits.settle(credit_id, settled)
    return settled, credit_id


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

    # Admission control: refuse when the wait queue is full. Deliberately
    # BEFORE the payment gate — a caller must never pay for a slot that
    # doesn't exist. (Counts only waiting jobs; running ones aren't queued.)
    if _max_queued_generations() <= _WAITING_GENERATIONS:
        raise HTTPException(
            status_code=429,
            detail={
                "reason": "generation_queue_full",
                "message": (
                    f"The generation queue is full ({_WAITING_GENERATIONS} jobs waiting). "
                    "No payment was taken. Retry in a few minutes."
                ),
            },
        )

    # Payment gate (flag: GENERATION_PAYMENT_REQUIRED — Dan flips deliberately,
    # see the #834 flip-list). Order is deliberate: AFTER the quota (a
    # quota-blocked caller is refused 429 before ever being asked to pay) and
    # BEFORE entitlement/enqueue (no work is burned for an unpaid request).
    # The 402 carries the full x402 requirements — that response IS the
    # quote-approval flow for humans and agents alike. Paper trading stays free.
    # None under flag-off / dry-run (see enforce_generation_payment); a real
    # settled PaymentInfo only when the flag is on and the payment cleared —
    # that is also the ONLY case a payment receipt is persisted (below, once
    # job_id exists).
    payment = None
    credit_id = None
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
        payment, credit_id = await _paywall_with_credit(request, linked_wallet, user.id)
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

    # Payment receipt (Dan's directive: "we must provide people with their
    # receipts"). Only when a real settled PaymentInfo exists — flag-off and
    # dry-run leave `payment` None and nothing is written. Deliberately AFTER
    # enqueue succeeds, so the receipt carries a real job_id.
    if payment is not None:
        _persist_payment_receipt(user_id=user.id, payment=payment, job_id=job_id)

    # The credit is spent only now, once the job is queued — that is what makes
    # every failure before this point cost the payer nothing (#1441).
    if credit_id is not None:
        generation_credits.consume(credit_id, job_id=job_id)

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


def _heartbeat_age_seconds(heartbeat_at: str | None) -> float | None:
    """Seconds since ``heartbeat_at``, or ``None`` if absent/unparseable.

    ``None`` is a real "no signal" — a job that predates this field, or a
    malformed value — and every caller must treat that as "cannot determine
    staleness", never as "assume stale" or "assume fresh" (the same fail-soft
    discipline as the rest of this module's honest-absence fields).
    """
    if not heartbeat_at:
        return None
    try:
        ts = datetime.fromisoformat(heartbeat_at)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds()


async def _heartbeat_loop(job_id: str, store) -> None:
    """Independent liveness proof for one run — see `runner_lease.py`'s
    `start_renewal` for the precedent this copies: its own clock, not tied to
    the caller's tick/progress. A single debate turn or fan-out backtest
    gather can run for tens of seconds without the pipeline emitting any
    job-store event at all, so gating the touch on pipeline progress would
    reintroduce the exact gap #1355 closes.
    """
    while True:
        await asyncio.sleep(_JOB_HEARTBEAT_INTERVAL_SECONDS)
        try:
            await store.touch(job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A missed heartbeat write must never abort the run itself — the
            # TTL + stale-heartbeat read path is the safety net, matching the
            # cost meter's "instrumentation never changes the outcome" rule.
            logger.exception("heartbeat: touch failed for job %s", sanitize_log_value(job_id))


async def _release_credit_if_undelivered(job_id: str, store) -> None:
    """Give the credit back unless the job actually delivered (#1441).

    The charge bought a credit and the enqueue spent it. If the run then died —
    worker crash, LLM failure, a container roll, or the payer cancelling — the
    payer holds nothing, so the credit goes back and their next generation
    spends it instead of asking for money again.

    Terminal states other than ``done`` all restore. Cancellation is included
    deliberately: a cancelled run produced no strategy, and charging for it
    would make the paywall a fee on trying rather than a price for delivery.

    A job whose Redis record has already expired reads as undelivered, which is
    the safe direction to be wrong in — the alternative silently keeps money for
    a run nobody can prove finished. ``restore_credit_for_job`` only ever moves
    a ``consumed`` credit, so a second call cannot mint a second credit.

    Fail-safe: this runs in a background task's ``finally`` with nobody to
    report to, so it logs and returns.
    """
    try:
        job = await store.get(job_id)
        status = (job or {}).get("status")
        if status == "done":
            return
        if generation_credits.restore_for_job(job_id):
            logger.warning(
                "job %s ended as %s — generation credit restored, the payer owes nothing",
                sanitize_log_value(job_id),
                sanitize_log_value(str(status)),
            )
    except Exception:
        logger.exception("could not evaluate credit release for job %s", sanitize_log_value(job_id))


async def _run_with_cleanup(
    job_id: str,
    brief: GenerateBrief,
    n_candidates: int,
    mode: str | None = None,
    model: str | None = None,
    owner_user_id: str | None = None,
    owner_wallet: str | None = None,
) -> None:
    global _WAITING_GENERATIONS
    store = get_job_store()
    # Heartbeat runs from entry — a QUEUED job (waiting on the admission
    # gate) is alive, and must not read as stalled while it waits its turn.
    heartbeat_task = asyncio.create_task(_heartbeat_loop(job_id, store), name=f"job-heartbeat-{job_id}")
    try:
        gate = _generation_gate()
        if gate.locked():
            _WAITING_GENERATIONS += 1
            try:
                await _emit_queued(job_id, _WAITING_GENERATIONS)
                await gate.acquire()
            finally:
                _WAITING_GENERATIONS -= 1
        else:
            await gate.acquire()
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
        finally:
            gate.release()
    except asyncio.CancelledError:
        raise
    except Exception:  # safety net — run_generation already emits error events
        logger.exception("background job %s crashed outside run_generation", job_id)
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        await _release_credit_if_undelivered(job_id, store)


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

            # Dead-job detection (#1355): the event log alone can't tell a
            # slow job from a dead one, so also read the job record itself
            # every cycle. Two cases:
            #   1. `running` with a heartbeat older than _STALLED_AFTER_SECONDS
            #      — the backend process that owned this job died mid-run
            #      (routine trigger: build-on-deploy rolling the Fargate task).
            #      Closed by a synthetic `error`/`STALLED` frame.
            #   2. Redis already shows a terminal status (done/error/cancelled)
            #      but no terminal event ever reached the log. This is the
            #      ROUTINE case, not a corner case: `EVENT_LOG_TTL` (15 min) is
            #      shorter than `JOB_TTL` (1 hour, refreshed on every write),
            #      so any client that reconnects to a completed job more than
            #      15 minutes after it finished hits this branch. It also
            #      covers the writer dying between the status write and the
            #      event push. A second `list_events` read is given a beat to
            #      catch the ordinary case first — the pipeline writes status
            #      THEN pushes the terminal event as two separate awaits, so a
            #      genuinely-current job racing the very end of that window is
            #      not misreported.
            #
            #      The synthetic frame branches on the REAL terminal status —
            #      collapsing all three onto `error`/STALLED would report a
            #      SUCCESSFUL job as failed to the client, which is exactly
            #      the false claim CLAUDE.md's `Claims must be true` rule
            #      forbids. `STALLED` is reserved for case 1 above.
            job = await store.get(job_id)
            if job is not None:
                status = job.get("status")
                if status in _TERMINAL_STATUSES:
                    trailing = await store.list_events(job_id, after_id=cursor)
                    saw_terminal = False
                    for ev in trailing:
                        cursor = ev["id"]
                        yield _format_sse(ev)
                        if ev.get("event") in _TERMINAL_EVENTS:
                            saw_terminal = True
                            break
                    if saw_terminal:
                        return
                    result = job.get("result") or {}
                    if status == "done":
                        candidates = result.get("candidates") or []
                        yield _format_sse(
                            {
                                "id": cursor + 1,
                                "event": "done",
                                "data": {
                                    "job_id": job_id,
                                    "strategy_id": result.get("best_strategy_id"),
                                    "all_strategy_ids": {
                                        c.get("candidate_id"): c.get("strategy_id")
                                        for c in candidates
                                        if c.get("candidate_id")
                                    },
                                },
                            }
                        )
                    elif status == "cancelled":
                        yield _format_sse(
                            {
                                "id": cursor + 1,
                                "event": "error",
                                "data": {
                                    "job_id": job_id,
                                    "message": "this generation was cancelled",
                                    "recoverable": False,
                                    "code": "CANCELLED",
                                },
                            }
                        )
                    else:  # status == "error"
                        yield _format_sse(
                            {
                                "id": cursor + 1,
                                "event": "error",
                                "data": {
                                    "job_id": job_id,
                                    "message": "this generation failed — check its status directly",
                                    "recoverable": False,
                                    "code": "JOB_FAILED",
                                },
                            }
                        )
                    return
                if status == "running":
                    stale_for = _heartbeat_age_seconds(job.get("heartbeat_at"))
                    if stale_for is not None and stale_for > _STALLED_AFTER_SECONDS:
                        logger.info(
                            "sse: job %s reads stalled (heartbeat %.0fs old) — closing stream",
                            sanitize_log_value(job_id),
                            stale_for,
                        )
                        yield _format_sse(
                            {
                                "id": cursor + 1,
                                "event": "error",
                                "data": {
                                    "job_id": job_id,
                                    "message": (
                                        f"no heartbeat from this job in over {_STALLED_AFTER_SECONDS}s "
                                        "— it likely died mid-run"
                                    ),
                                    "recoverable": False,
                                    "code": "STALLED",
                                },
                            }
                        )
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
        state=_normalize_state(job.get("status") or "queued", job.get("heartbeat_at")),
        brief_intent=brief.get("intent", ""),
        created_at=job.get("created_at", ""),
        updated_at=job.get("updated_at", ""),
        n_candidates=int(payload.get("n_candidates") or 1),
        best_strategy_id=result.get("best_strategy_id"),
        # Raw measurement for this job (#1217): token counts, per-stage
        # seconds, write tallies. None until the job reaches a terminal
        # state, and None for jobs generated before the meter existed.
        cost=result.get("cost"),
    )


def _normalize_state(s: str, heartbeat_at: str | None = None) -> str:
    """Map a raw job-store status onto the wire vocabulary.

    ``heartbeat_at`` is optional and additive (#1355): when given and ``s`` is
    ``"running"``, a heartbeat older than ``_STALLED_AFTER_SECONDS`` reads as
    the derived ``"stalled"`` state — nothing is written back to Redis, this
    is purely a read-time reinterpretation, so ``GET /jobs`` and
    ``GET /jobs/{id}`` (both route through this) can never disagree. Omitting
    ``heartbeat_at`` (the default) preserves the exact pre-#1355 behavior for
    any caller that hasn't been updated to pass it.
    """
    if s not in ("queued", "running", "done", "error", "cancelled"):
        return "queued"
    if s == "running":
        stale_for = _heartbeat_age_seconds(heartbeat_at)
        if stale_for is not None and stale_for > _STALLED_AFTER_SECONDS:
            return "stalled"
    return s


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


@generate_router.get("/jobs/{job_id}/cost", response_model=JobCostResponse)
async def get_job_cost(
    job_id: str,
    request: Request,
    user: CurrentUser = Depends(require_current_user),
) -> JobCostResponse:
    """What this generation actually consumed (#1217).

    Raw measurement only — Bedrock input/output token counts taken from the
    provider's own ``usage`` block, wall + CPU seconds per pipeline stage, peak
    RSS, and the rows the pipeline wrote. **No prices**: the paywall quote seam
    (``GET /api/generate/quote``) stays ``flat_v1`` and is untouched by this
    endpoint; converting these counts into dollars is done off-server, and this
    is the input that lets it stop being an estimate.

    Owner-scoped exactly like ``/candidates`` — a job you do not own 404s rather
    than leaking its existence. ``cost`` is ``null`` for a job that has not
    reached a terminal state yet, and for jobs older than the instrumentation.
    """
    store = get_job_store()
    job = await store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found or expired")
    _require_job_access(job, user.id, job_id, get_linked_wallet_address(request))
    result = job.get("result") or {}
    cost = result.get("cost") if isinstance(result, dict) else None
    return JobCostResponse(
        job_id=job_id,
        # heartbeat_at passed exactly like `_job_summary` (#1355) so this
        # third read surface can't disagree with `/jobs` and `/jobs/{id}` —
        # all three route through the same `_normalize_state`.
        state=_normalize_state(job.get("status") or "queued", job.get("heartbeat_at")),
        cost=cost if isinstance(cost, dict) else None,
    )


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

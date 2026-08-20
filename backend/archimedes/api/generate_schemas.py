"""Pydantic models for the streaming Generate pipeline.

Implements the event protocol in docs/specs/generation-streaming-spec.md.
Each event is shipped as a single SSE `data:` payload — these models exist
so route handlers, the pipeline orchestrator, and the test suite all share
one definition of what an event looks like on the wire.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# ── Request side ──────────────────────────────────────────────────────────

# User-chosen strategy name limits. Whitespace is collapsed BEFORE these
# checks, so \t/\n arriving in a paste are normalized rather than rejected;
# any control character that survives collapsing (NUL, ESC, DEL, …) is a
# hard reject — those never belong in a display name.
NAME_MAX_LEN = 80
_NAME_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class GenerateBrief(BaseModel):
    """User-supplied generation brief."""

    intent: str = Field(..., description="Free-text strategy request")
    risk_appetite: Literal["fixed_income", "conservative", "moderate", "aggressive", "hyper_risky"] = "moderate"
    asset_classes: list[str] | None = None
    capital_usdc: float | None = None
    max_papers: int = Field(default=5, ge=1, le=20)
    name: str | None = Field(
        default=None,
        description=(
            "Optional user-chosen strategy name (1–80 chars after whitespace normalization). "
            "Applied verbatim to the WINNING candidate only; considered-rejects keep their "
            "auto-derived names so the library never shows N identically-named strategies "
            "per generation. Empty/whitespace-only is treated as unset."
        ),
    )

    @field_validator("name")
    @classmethod
    def _sanitize_name(cls, v: str | None) -> str | None:
        """Strip + collapse internal whitespace; empty → None; reject control chars / >80 chars."""
        if v is None:
            return None
        v = re.sub(r"\s+", " ", v).strip()
        if not v:
            return None
        if _NAME_CONTROL_CHARS.search(v):
            raise ValueError("name must not contain control characters")
        if len(v) > NAME_MAX_LEN:
            raise ValueError(f"name must be at most {NAME_MAX_LEN} characters")
        return v


class GenerateStartRequest(BaseModel):
    brief: GenerateBrief
    n_candidates: int = Field(default=1, ge=1, le=5, description="How many candidates to consider internally")
    mode: str | None = Field(
        default=None,
        description=(
            "Optional pipeline override (API compatibility; ignored as of T1.1 Phase-3 cutover). "
            "The debate society is the sole generation pipeline — non-debate overrides are logged and discarded."
        ),
    )
    model: str | None = Field(
        default=None,
        description=(
            "Optional LLM model id chosen on the Generate page (matches ui/src/data/modelPricing.json). "
            "Two server-side gates apply: (1) the paid-tier entitlement gate (T1.8) rejects a premium "
            "(Anthropic) model from a non-entitled caller with HTTP 402 — see PREMIUM_MODELS_ENABLED / "
            "PREMIUM_MODELS_ALLOWLIST; (2) the free-tier allowlist then honors the id only if it is an "
            "allowlisted free model, otherwise it falls back to the env default. Absent → env default."
        ),
    )


class GenerateStartResponse(BaseModel):
    job_id: str
    stream_url: str
    ttl_seconds: int


# ── Event payloads ────────────────────────────────────────────────────────


EventName = Literal[
    "job_queued",
    "brief_validated",
    "pipeline_selected",
    "candidates_selected",
    "agent_iteration",
    "tool_called",
    "tool_result",
    "candidate_drafted",
    "candidate_evaluated",
    "best_selected",
    "trace_hashed",
    "persisted",
    "done",
    "error",
]


def _ts() -> str:
    return datetime.now(UTC).isoformat()


class GenerateEvent(BaseModel):
    """Generic envelope for any event shipped on the SSE stream.

    `event` is the SSE event name; `data` is the JSON payload that the
    frontend's `addEventListener(event, …)` callback receives.
    """

    id: int
    event: EventName
    data: dict[str, Any]

    @classmethod
    def make(cls, *, event_id: int, event: EventName, **payload: Any) -> GenerateEvent:
        payload.setdefault("ts", _ts())
        return cls(id=event_id, event=event, data=payload)


# ── Job listing + candidates list ─────────────────────────────────────────


#: What the cost meter measured for one job. Free-form on purpose: the payload
#: carries its own ``schema`` version (``cost_v1``), so the measurement record
#: can gain fields without a lockstep API-model change. It is RAW MEASUREMENT
#: ONLY — token counts, seconds, byte counts, write tallies. No prices, and no
#: money is derivable from it without the pricing table, which lives outside the
#: server. The paywall quote seam (``generation_payment.quote()``) is unaffected
#: and still reports ``pricing_model: "flat_v1"`` (#1217).
JobCost = dict[str, Any]


class JobSummary(BaseModel):
    job_id: str
    # "stalled" (#1355) is a READ-TIME derived state, never a value stored in
    # Redis: a "running" job whose heartbeat_at has gone stale is normalized
    # to it in generate_routes._normalize_state — see that function's
    # docstring. Not written by anything, only produced on output.
    state: Literal["queued", "running", "stalled", "done", "error", "cancelled"]
    brief_intent: str
    created_at: str
    updated_at: str
    n_candidates: int
    best_strategy_id: str | None = None
    cost: JobCost | None = None


class JobsListResponse(BaseModel):
    jobs: list[JobSummary]


class JobCostResponse(BaseModel):
    """One job's measured resource consumption.

    ``cost`` is ``None`` while the job is still running (the snapshot is written
    once, on the terminal path) and stays ``None`` for a job that predates the
    instrumentation — an honest absence, not a zeroed record.
    """

    job_id: str
    # Widened to include "stalled" alongside JobSummary.state (#1355 review
    # follow-up) — this endpoint routes `heartbeat_at` through the same
    # `_normalize_state` as `/jobs` and `/jobs/{id}`, so all three surfaces
    # must accept the same derived state or a stale job would 500 here
    # instead of reporting `stalled` like its siblings.
    state: Literal["queued", "running", "stalled", "done", "error", "cancelled"]
    cost: JobCost | None = None


class CandidateSummary(BaseModel):
    candidate_id: str
    strategy_id: str | None
    strategy_name: str
    rigor_verdict: dict[str, Any] | None = None
    passes_rigor: bool
    selected: bool
    regime: str | None = None  # "bull", "bear", or "neutral" (Issue #163)
    generation_method: str | None = None  # "debate" | "debate_abstain" | "fusion" | …


class CandidatesListResponse(BaseModel):
    job_id: str
    best_candidate_id: str | None
    candidates: list[CandidateSummary]

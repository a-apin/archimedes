"""Pydantic models for the streaming Generate pipeline.

Implements the event protocol in docs/specs/generation-streaming-spec.md.
Each event is shipped as a single SSE `data:` payload — these models exist
so route handlers, the pipeline orchestrator, and the test suite all share
one definition of what an event looks like on the wire.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Request side ──────────────────────────────────────────────────────────


class GenerateBrief(BaseModel):
    """User-supplied generation brief."""

    intent: str = Field(..., description="Free-text strategy request")
    risk_appetite: Literal["fixed_income", "conservative", "moderate", "aggressive", "hyper_risky"] = "moderate"
    asset_classes: list[str] | None = None
    capital_usdc: float | None = None
    max_papers: int = Field(default=5, ge=1, le=20)


class GenerateStartRequest(BaseModel):
    brief: GenerateBrief
    n_candidates: int = Field(default=1, ge=1, le=5, description="How many candidates to consider internally")
    mode: str | None = Field(default=None, description="Legacy — ignored. Backend auto-routes via _pick_pipeline().")


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


class JobSummary(BaseModel):
    job_id: str
    state: Literal["queued", "running", "done", "error", "cancelled"]
    brief_intent: str
    created_at: str
    updated_at: str
    n_candidates: int
    best_strategy_id: str | None = None


class JobsListResponse(BaseModel):
    jobs: list[JobSummary]


class CandidateSummary(BaseModel):
    candidate_id: str
    strategy_id: str | None
    strategy_name: str
    rigor_verdict: dict[str, Any] | None = None
    passes_rigor: bool
    selected: bool


class CandidatesListResponse(BaseModel):
    job_id: str
    best_candidate_id: str | None
    candidates: list[CandidateSummary]

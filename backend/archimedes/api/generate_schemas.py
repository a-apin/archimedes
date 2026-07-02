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
            "Optional pipeline override: 'fusion', 'architect', or 'agent'. When set, bypasses "
            "auto-routing. The 'debate' society pipeline (T1.1) is selected by the "
            "ARCHIMEDES_DEBATE_ENABLED flag rather than this override, so flag-OFF stays "
            "byte-identical; the deletions of the legacy runners are deferred to the Phase-3 cutover."
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
    regime: str | None = None  # "bull", "bear", or "neutral" (Issue #163)


class CandidatesListResponse(BaseModel):
    job_id: str
    best_candidate_id: str | None
    candidates: list[CandidateSummary]

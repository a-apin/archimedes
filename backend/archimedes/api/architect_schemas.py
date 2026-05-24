"""Request/response schemas for the interactive strategy architect.

DELIBERATELY SEPARATE from `api/schemas.py`. That file is the shared
frontend contract Daniel codes against; touching it triggers the
"announce before changing" policy. These models are purely additive — a
new endpoint, no change to any existing shape — so the strategy-architect
work stays unblocked. Fold into `schemas.py` later if/when it stabilizes
and Daniel is building against it.

Endpoint: POST /api/strategies/construct
Owner: Dan. Consumed by: Daniel (strategy-construction UI), and the
reasoning-trace viewer (the trace block here is the same hash that
ITracePublisher anchors on-chain).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RiskProfileLiteral = Literal["fixed_income", "conservative", "moderate", "aggressive", "hyper_risky"]


class StrategyConstructionRequest(BaseModel):
    """A user's free-text portfolio request."""

    intent: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Plain-language description of what the user wants.",
    )
    risk_profile: RiskProfileLiteral
    capital_usdc: float = Field(..., gt=0, description="USDC the user intends to deploy.")
    regime: str | None = Field(
        None,
        description="Optional regime override; omitted = unknown (detection not yet wired).",
    )


class ConstructionSelectionResponse(BaseModel):
    """One strategy in the proposed book, with final (guardrailed) weight."""

    strategy_id: str
    paper_title: str
    weight: float  # Final fraction of the whole book, post-guardrail (0-1)
    rationale: str
    paper_citation: str = ""


class ConstructionTraceResponse(BaseModel):
    """The verifiable provenance block. trace_hash is what gets anchored."""

    id: str
    decision_type: str  # "construction"
    trigger: str  # "user_request"
    timestamp: str  # ISO 8601
    trace_hash: str  # SHA-256 — recompute off the response to verify
    arc_tx_hash: str | None = None
    is_anchored: bool = False  # False until ITracePublisher anchors it


class StrategyConstructionResponse(BaseModel):
    """The architect's proposal: what, how much, why, and the proof."""

    # Echo of the request
    intent: str
    risk_profile: str
    capital_usdc: float
    regime: str | None = None

    # The proposal
    model_id: str  # which LLM produced this (provenance)
    selected: list[ConstructionSelectionResponse]
    usyc_weight: float  # cash-yield sleeve (>= the risk-profile floor)
    overall_reasoning: str
    risk_notes: str = ""
    guardrail_notes: list[str] = []

    # The proof
    trace: ConstructionTraceResponse

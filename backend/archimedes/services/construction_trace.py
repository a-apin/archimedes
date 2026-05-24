"""Construction reasoning-trace builder (Dan's lane — Step 3).

Assembles a `ReasoningTrace` for an interactive strategy-construction request
and computes its integrity hash. This is the provenance artifact that makes
Archimedes' core claim real: every allocation the agent proposes is recorded,
hashed, and independently verifiable.

Hard seam — this module STOPS at the hash. It never touches the chain.
Publishing the hash to `ReasoningTraceRegistry` is Chuan/Marten's
`ITracePublisher.publish(trace)`; this returns a `ReasoningTrace` with
`trace_hash` populated and `arc_tx_hash=None` for them to anchor.

Honesty rules carried through from Steps 1–2:
- No fabricated `confidence`. There is no calibrated source for it yet
  (it should come from the backtest / DSR layer once that lands), so it is
  left at 0.0 and the absence is stated in `expected_outcome`. Inventing a
  number here would contradict the whole selection-bias thesis.
- The regime is recorded as explicitly unknown when detection isn't wired,
  never guessed.
"""

from __future__ import annotations

import uuid

from archimedes.agents.strategy_architect import ArchitectProposal
from archimedes.models.trace import DecisionType, ReasoningTrace
from archimedes.services.strategy_guardrail import GuardrailResult

# No vault exists yet when a user is *designing* a portfolio pre-deposit.
# A sentinel keeps the trace well-formed and self-describing; the real vault
# address is bound later if/when the user funds the construction.
UNBOUND_VAULT = "0x0000000000000000000000000000000000000000"


def build_construction_trace(
    proposal: ArchitectProposal,
    guardrail: GuardrailResult,
    *,
    vault_address: str = UNBOUND_VAULT,
) -> ReasoningTrace:
    """Build and hash the construction trace. Pure; no chain I/O.

    The hashed payload (see `ReasoningTrace.compute_hash`) covers the
    decision content — intent-driven reasoning, the final allocation, and
    the strategies referenced — so the on-chain anchor binds the *what* and
    the *why*, not just a timestamp.
    """
    citations = {s.strategy_id: s.paper_citation for s in proposal.selected if s.paper_citation}
    rationales = {s.strategy_id: s.rationale for s in proposal.selected}

    # Self-contained, deterministically serializable view of the decision.
    portfolio_after = {
        "strategy_weights": {sid: round(w, 6) for sid, w in sorted(guardrail.strategy_weights.items())},
        "usyc_weight": round(guardrail.usyc_weight, 6),
        "rationales": rationales,
        "paper_citations": citations,
        "dropped": sorted(guardrail.dropped),
    }

    market_context = {
        "regime": proposal.regime or "unknown (regime detection not yet wired)",
        "risk_profile": proposal.risk_profile,
        "capital_usdc": proposal.capital_usdc,
    }

    reasoning = proposal.overall_reasoning.strip()
    if proposal.risk_notes.strip():
        reasoning += f"\n\nRisk notes: {proposal.risk_notes.strip()}"
    if guardrail.adjustments:
        reasoning += "\n\nGuardrail adjustments:\n" + "\n".join(f"- {n}" for n in guardrail.adjustments)

    trace = ReasoningTrace(
        id=str(uuid.uuid4()),
        vault_address=vault_address,
        decision_type=DecisionType.PORTFOLIO_CONSTRUCTION,
        trigger="user_request",
        timestamp=proposal.created_at,
        market_context=market_context,
        portfolio_before={},  # designing pre-deposit: nothing held yet
        portfolio_after=portfolio_after,
        reasoning=reasoning,
        confidence=0.0,  # no calibrated source yet — see module docstring
        expected_outcome=(
            f"Constructed by {proposal.model_id} from the user intent: "
            f'"{proposal.intent}". Empirical validation (backtest / DSR / '
            f"PBO) is not yet wired, so no confidence score is asserted."
        ),
        trades_executed=[],  # construction proposal — no trades executed yet
        strategies_referenced=proposal.strategies_referenced,
    )
    trace.compute_hash()
    return trace

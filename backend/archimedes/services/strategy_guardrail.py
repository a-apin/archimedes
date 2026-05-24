"""Deterministic weight guardrail (Dan's lane, Step 2).

Claude (the Maestro, Step 1) proposes *relative* strategy emphasis. It is told
not to compute auditable numbers. This module is the deterministic Worker that
turns that raw suggestion into a constraint-satisfying allocation:

  - normalize to a valid book that sums to 1.0
  - reserve the risk-profile USYC (cash-yield) floor
  - cap any single strategy's share of the whole portfolio
  - record every adjustment vs. the model's raw proposal

The adjustments log is the point: the Step 3 reasoning trace can say
"Claude proposed 45% to Faber; guardrail capped to 30% and moved 15% to the
cash sleeve" — honest about what the deterministic layer changed, never hidden.

Seam vs. Önder: `IPortfolioConstructor.construct(...)` (math.py) is the real
*asset-level* optimizer — it ranks by risk-adjusted return, optimizes variance,
and maps strategy weights to token allocations using backtest + regime inputs.
This guardrail is the thin strategy-level stand-in for the parts of that the
demo needs (normalize + cap + USYC floor) with no backtest dependency. When
Önder's `construct()` lands it replaces this; `GuardrailResult.strategy_weights`
is the contract both sides speak.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from archimedes.agents.strategy_architect import ArchitectProposal
from archimedes.models.portfolio import RISK_PROFILE_PARAMS, RiskProfile

logger = logging.getLogger(__name__)

DEFAULT_MAX_STRATEGY_WEIGHT = 0.30  # per design.md § 4.3.2 / ecosystem spec
_EPS = 1e-9


@dataclass
class GuardrailResult:
    """A constraint-satisfying allocation derived from an ArchitectProposal.

    `strategy_weights` + `usyc_weight` sum to 1.0 (within float tolerance).
    `adjustments` is human-readable and feeds the reasoning trace.
    """

    strategy_weights: dict[str, float]  # strategy_id -> fraction of portfolio
    usyc_weight: float  # reserved cash-yield sleeve
    risk_profile: str
    dropped: list[str] = field(default_factory=list)  # zeroed-out strategy_ids
    adjustments: list[str] = field(default_factory=list)  # audit log for the trace

    @property
    def total(self) -> float:
        return sum(self.strategy_weights.values()) + self.usyc_weight


def apply_guardrail(
    proposal: ArchitectProposal,
    *,
    max_strategy_weight: float = DEFAULT_MAX_STRATEGY_WEIGHT,
) -> GuardrailResult:
    """Normalize, cap, and floor an ArchitectProposal into a valid book.

    Algorithm:
      1. Keep only strictly-positive raw weights.
      2. Reserve the profile USYC floor; the rest is the investable budget.
      3. Normalize surviving strategy weights into the investable budget.
      4. Cap each strategy at `max_strategy_weight` of the *whole* portfolio,
         redistributing the spill to uncapped strategies. Spill that cannot
         be placed (everything capped) falls into the USYC sleeve.
      5. Residual rounding error is absorbed by USYC so totals are exactly 1.0.
    """
    profile = RiskProfile(proposal.risk_profile)
    params = RISK_PROFILE_PARAMS[profile]
    usyc_floor = params["usyc_floor"]
    usyc_ceiling = params["usyc_ceiling"]
    adjustments: list[str] = []
    dropped: list[str] = []

    raw = proposal.raw_weights
    positive = {sid: w for sid, w in raw.items() if w > _EPS}
    for sid, w in raw.items():
        if w <= _EPS:
            dropped.append(sid)
            adjustments.append(f"Dropped {sid[:12]} (non-positive proposed weight).")

    if not positive:
        adjustments.append(
            "No strategies with positive weight — allocating fully to the "
            "USYC cash-yield sleeve. Safer than a guessed book."
        )
        return GuardrailResult(
            strategy_weights={},
            usyc_weight=1.0,
            risk_profile=profile.value,
            dropped=dropped,
            adjustments=adjustments,
        )

    investable = 1.0 - usyc_floor
    if usyc_floor > 0:
        adjustments.append(
            f"Reserved {usyc_floor:.0%} USYC floor for the {profile.value} profile; {investable:.0%} is investable."
        )

    # Normalize survivors into the investable budget.
    raw_sum = sum(positive.values())
    weights = {sid: (w / raw_sum) * investable for sid, w in positive.items()}

    # Iteratively cap at `max_strategy_weight` of the whole portfolio and
    # redistribute spill to uncapped strategies until stable.
    capped: set[str] = set()
    for _ in range(len(weights) + 1):
        over = {sid: w for sid, w in weights.items() if sid not in capped and w > max_strategy_weight + _EPS}
        if not over:
            break
        spill = 0.0
        for sid in over:
            spill += weights[sid] - max_strategy_weight
            weights[sid] = max_strategy_weight
            capped.add(sid)
            adjustments.append(
                f"Capped {sid[:12]} at {max_strategy_weight:.0%} (model proposed more); redistributing the excess."
            )
        uncapped = [sid for sid in weights if sid not in capped]
        room = sum(max(0.0, max_strategy_weight - weights[s]) for s in uncapped)
        if not uncapped or room <= _EPS:
            # Nowhere to put the spill — it becomes extra cash sleeve.
            adjustments.append(
                f"All strategies at the {max_strategy_weight:.0%} cap; {spill:.0%} spilled into the USYC sleeve."
            )
            break
        base = sum(weights[s] for s in uncapped)
        for sid in uncapped:
            share = (weights[sid] / base) if base > _EPS else 1.0 / len(uncapped)
            weights[sid] = min(max_strategy_weight, weights[sid] + spill * share)

    strategy_total = sum(weights.values())
    usyc_weight = max(0.0, 1.0 - strategy_total)

    if usyc_weight > usyc_ceiling + 1e-6:
        # Concentration caps forced more cash than the profile's USYC ceiling.
        # Honest tension: we let it stand and log it rather than silently
        # breaching the per-strategy cap to chase the ceiling.
        adjustments.append(
            f"USYC at {usyc_weight:.0%} exceeds the {profile.value} ceiling "
            f"of {usyc_ceiling:.0%} because per-strategy caps left cash "
            f"unplaceable. Flagged, not silently rebalanced past the cap."
        )

    # Absorb float residual into USYC so the book is exactly 1.0.
    drift = 1.0 - (strategy_total + usyc_weight)
    if abs(drift) > _EPS:
        usyc_weight += drift

    return GuardrailResult(
        strategy_weights={sid: round(w, 6) for sid, w in weights.items()},
        usyc_weight=round(usyc_weight, 6),
        risk_profile=profile.value,
        dropped=dropped,
        adjustments=adjustments,
    )

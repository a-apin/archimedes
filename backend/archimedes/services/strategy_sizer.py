"""Strategy-level Kelly sizing for vault construction (roadmap Priority 3.1).

Closes the gap where the strategy passport's ``kelly_fraction`` — computed by
the rigor pipeline and served on every passport — was never consumed by vault
construction: the derive-allocations path flat-averaged every selected
strategy's votes regardless of edge or gate verdict.

Sizing model (one source for every constant — do not fork these):

- **The passport ``kelly_fraction`` IS half-Kelly by construction.**
  ``analytics-engine/scripts/regen_buy_hold_fixture.py::compute_kelly``
  applies ``fractional=0.5`` before storing. Do NOT multiply by another 0.5
  here — that would double-discount.
- **The risk profile maps to a Kelly multiple via the same γ table the
  portfolio optimizer uses** (``RISK_AVERSION`` in ``portfolio_optimizer.py``).
  Mean-variance Kelly with risk aversion γ allocates w* = full-Kelly / γ, so
  the multiplier applied to the stored *half*-Kelly is 2/γ. γ = 2 reproduces
  half-Kelly exactly (Bell & Cover 1980).
- **Half-Kelly is the never-exceed ceiling**: the multiplier is capped at 1.0
  (so ``hyper_risky`` γ = 1.5 still gets at most the stored half-Kelly). Full
  Kelly on backtest-estimated edges is overbetting by construction — the
  estimation error in μ/σ² makes the "optimal" fraction an upper bound, not a
  target.
- **Only rigor-gate passers are sizeable.** CANDIDATE / gate-failing
  strategies size to 0.0 — sizing must not become a side-door past the gate.
- **Never lever up to fill a budget.** If the sized fractions sum below the
  investable budget, the remainder stays in USDC; if above, all fractions
  scale down proportionally.

Pure computation: no I/O, no chain, no DB. The deploy path consumes this via
``derive_vault_allocations`` (the derived targets are returned to the UI; the
user submits the on-chain transaction from their own wallet).

Owner: Önder (math lane).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from archimedes.services.portfolio_optimizer import RISK_AVERSION

if TYPE_CHECKING:
    from archimedes.models.strategy import Strategy
    from archimedes.services.strategy_signal_evaluator import StrategySignals

# γ at which the multiplier is exactly 1× the stored half-Kelly (Bell & Cover).
_HALF_KELLY_GAMMA = 2.0


def kelly_multiplier(risk_profile: str) -> float:
    """Risk-profile multiplier applied to the passport's half-Kelly fraction.

    2/γ with γ from RISK_AVERSION, capped at 1.0 (half-Kelly ceiling).
    Unknown profiles fall back to the moderate γ, matching the optimizer's
    own ``RISK_AVERSION.get(risk_profile, 3.0)`` fallback.
    """
    gamma = RISK_AVERSION.get(risk_profile, RISK_AVERSION["moderate"])
    return min(_HALF_KELLY_GAMMA / gamma, 1.0)


def size_strategies(
    strategies: list[Strategy],
    risk_profile: str,
    deployable_ids: set[str] | None = None,
) -> dict[str, float]:
    """Per-strategy capital fractions: passport half-Kelly × profile multiplier.

    Gate-failing strategies and strategies with no stored ``kelly_fraction`` size
    to 0.0 — no gate pass, no capital; no measured edge, no capital.

    ``deployable_ids`` decides the "gate pass" test:
      * ``None`` (default) — a strategy is sizeable iff its level-1 ``passes_rigor_gate``
        badge is truthy (backward-compatible behaviour).
      * a set — a strategy is sizeable iff its id is in the set. The caller computes
        this from the user's chosen strictness level (via the live gate), so a
        strategy the user is allowed to deploy at level L is not silently zeroed by
        the stricter level-1 badge. Sizing still cannot become a side-door past the
        gate: the set only ever contains strategies that pass at the user's level,
        and the always-on correctness floors hold at every level.
    """
    mult = kelly_multiplier(risk_profile)
    sized: dict[str, float] = {}
    for s in strategies:
        deployable = (
            (s.id in deployable_ids) if deployable_ids is not None else bool(getattr(s, "passes_rigor_gate", False))
        )
        if not deployable:
            sized[s.id] = 0.0
            continue
        kelly = getattr(s, "kelly_fraction", None)
        sized[s.id] = round(max(float(kelly), 0.0) * mult, 6) if kelly is not None else 0.0
    return sized


def scale_to_budget(fractions: dict[str, float], budget: float) -> dict[str, float]:
    """Scale fractions DOWN proportionally if they overflow the budget.

    Never scales up: an under-allocated portfolio keeps the remainder in USDC
    rather than levering positions to fill the budget.
    """
    total = sum(fractions.values())
    if total <= 0.0 or total <= budget:
        return dict(fractions)
    scale = budget / total
    return {k: round(v * scale, 6) for k, v in fractions.items()}


def kelly_weighted_allocations(
    all_signals: list[StrategySignals],
    sized_fractions: dict[str, float],
    usdc_floor: float = 0.20,
) -> dict[str, float]:
    """Per-asset target weights from per-strategy sized fractions × votes.

    target(asset) = Σ_s sized_s × vote_{s,asset}, where a strategy's votes are
    its internal allocation across assets (normalized down if they sum > 1, so
    a strategy can never deploy more than its sized fraction). USDC receives
    the floor plus all capital the sized votes did not claim — there is no
    re-normalization up, by design (see module docstring).

    Returns {symbol: weight} with weights ≥ 0 summing to 1.0 (4-dp rounding).
    """
    budget = 1.0 - usdc_floor
    sized = scale_to_budget(sized_fractions, budget)

    weights: dict[str, float] = {}
    for strat_signals in all_signals:
        frac = sized.get(strat_signals.strategy_id, 0.0)
        if frac <= 0.0:
            continue
        positive = [sig for sig in strat_signals.signals if sig.weight > 0.0]
        total_vote = sum(sig.weight for sig in positive)
        if total_vote <= 0.0:
            continue
        vote_scale = 1.0 / total_vote if total_vote > 1.0 else 1.0
        for sig in positive:
            weights[sig.asset] = weights.get(sig.asset, 0.0) + frac * sig.weight * vote_scale

    weights = {k: round(v, 4) for k, v in weights.items()}
    synth_total = sum(weights.values())
    weights["USDC"] = round(max(1.0 - synth_total, 0.0), 4)
    return dict(sorted(weights.items()))

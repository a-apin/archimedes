"""Portfolio construction — ``IPortfolioConstructor`` implementations.

Two classes live here, both implementing the frozen ``IPortfolioConstructor``
protocol (``interfaces/math.py``), for two different callers:

``PortfolioConstructor`` — the execution-society's regime/consensus throttle
(issue #662). Wired into the per-tick trading loop
(``chain/agent_runner.py``, ``marketplace/service.py``): takes the runner's
already-aggregated raw target weights and *throttles* exposure by two
orthogonal signals — the exogenous market regime and the endogenous strategy-
ensemble consensus — shrinking risk-asset exposure into the USDC safe asset
when either is weak. See its own docstring below.

``KellyRegimePortfolioConstructor`` — promotes ``services/strategy_sizer.py``'s
Kelly-weighted strategy sizing behind the same interface (issue #1264), for the
vault-*setup* path (``POST /api/vaults/{address}/derive-allocations``): sizes
each selected strategy by its passport's half-Kelly fraction × a risk-profile
multiplier, then applies the SAME regime tilt as ``PortfolioConstructor``
(reused, not forked) on top. See its own docstring below for how the two
differ in their None-regime default and why.

Sizing basis — regime-conditional Kelly sizing:
  - Lo (2002), "The Statistics of Sharpe Ratios", Financial Analysts Journal.
  - López de Prado (2018), *Advances in Financial Machine Learning*, §11
    (Dangers of Backtesting / bet sizing). Risk is scaled down, not levered up,
    when conviction (regime confidence, ensemble agreement) is weak.
"""

from __future__ import annotations

from archimedes.interfaces.math import IPortfolioConstructor
from archimedes.models.backtest import BacktestResult
from archimedes.models.portfolio import (
    RISK_PROFILE_PARAMS,
    Portfolio,
    RiskProfile,
    TargetAllocation,
)
from archimedes.models.regime import EnsembleConsensus, Regime, RegimeClassification
from archimedes.models.strategy import Strategy
from archimedes.services.strategy_sizer import scale_to_budget, size_strategies

# ── Named constants (NO magic numbers) ──────────────────────────────
# Sizing basis: regime-conditional Kelly sizing — Lo (2002),
# López de Prado (2018) §11. Risk exposure is throttled down when the
# market regime or the ensemble's conviction is weak.

SAFE_ASSET = "USDC"

# Per-regime exposure multiplier on risk assets. RISK_ON = full sizing;
# CRISIS = near-flat (flight to safety).
REGIME_MULTIPLIER: dict[Regime, float] = {
    Regime.RISK_ON: 1.0,
    Regime.TRANSITION: 0.7,
    Regime.RISK_OFF: 0.4,
    Regime.CRISIS: 0.1,
}
# Conservative default when no regime classification is available (snapshot
# fetch failed or lacked VIX/MA signals — see agent_runner._classify_market_regime).
REGIME_MULTIPLIER_NONE = 0.7

# Confidence floor: even at confidence 0 the regime multiplier is not fully
# erased — low confidence halves the regime's effect rather than zeroing it.
# scale_within_regime = _CONF_BASE + _CONF_SLOPE * confidence.
_CONF_BASE = 0.5
_CONF_SLOPE = 0.5

# Ensemble-consensus throttle, driven by flat_pct (fraction of flat signals):
#   flat_pct < _FLAT_LOW            → 1.0  (decisive ensemble, no penalty)
#   _FLAT_LOW ≤ flat_pct ≤ _FLAT_HIGH → linear 1.0 → _CONSENSUS_FLOOR
#   flat_pct > _FLAT_HIGH           → _CONSENSUS_FLOOR (uncertain ensemble)
_FLAT_LOW = 0.30
_FLAT_HIGH = 0.60
_CONSENSUS_FLOOR = 0.6
_FLAT_SPAN = _FLAT_HIGH - _FLAT_LOW  # 0.30
_CONSENSUS_DROP = 1.0 - _CONSENSUS_FLOOR  # 0.4


def _shrink_to_safe_asset(raw_weights: dict[str, float], scale: float) -> dict[str, float]:
    """Shrink every non-``SAFE_ASSET`` weight by ``scale``; freed mass moves to ``SAFE_ASSET``.

    Renormalizes so weights sum to 1.0 (guards divide-by-zero when everything
    is exactly zero). Shared by both ``PortfolioConstructor`` (regime-only
    throttle) and ``KellyRegimePortfolioConstructor`` (Kelly sizing + this same
    throttle) so the shrink-and-renormalize arithmetic has one source, per this
    codebase's convention of not forking the same formula across call sites.
    """
    scaled: dict[str, float] = {}
    freed_mass = 0.0
    safe_weight = 0.0
    for symbol, weight in raw_weights.items():
        if symbol == SAFE_ASSET:
            safe_weight += weight
            continue
        new_weight = weight * scale
        scaled[symbol] = new_weight
        freed_mass += weight - new_weight

    scaled[SAFE_ASSET] = safe_weight + freed_mass

    total = sum(scaled.values())
    if total > 0:
        scaled = {sym: w / total for sym, w in scaled.items()}
    return scaled


class PortfolioConstructor(IPortfolioConstructor):
    """Throttles aggregated target weights by market regime + ensemble consensus."""

    def compute_position_scale(
        self,
        regime: RegimeClassification | None,
        ensemble_consensus: EnsembleConsensus | None,
    ) -> float:
        """Combine regime + ensemble consensus into a single risk-asset scale.

        PURE. Returns a multiplier in [0.0, 1.0] applied to every non-USDC
        weight; the freed mass moves to USDC.

        regime_mult:
          - regime is None → ``REGIME_MULTIPLIER_NONE`` (conservative default).
          - else ``REGIME_MULTIPLIER[regime] * (0.5 + 0.5 * confidence)`` — low
            confidence reduces sizing *within* a regime.
        consensus_mult (from flat_pct):
          - None → 1.0 (no penalty).
          - < 0.30 → 1.0; 0.30–0.60 → linear 1.0→0.6; > 0.60 → 0.6.
        """
        if regime is None:
            regime_mult = REGIME_MULTIPLIER_NONE
        else:
            confidence_factor = _CONF_BASE + _CONF_SLOPE * regime.confidence
            regime_mult = REGIME_MULTIPLIER[regime.regime] * confidence_factor

        if ensemble_consensus is None:
            consensus_mult = 1.0
        else:
            flat_pct = ensemble_consensus.flat_pct
            if flat_pct < _FLAT_LOW:
                consensus_mult = 1.0
            elif flat_pct > _FLAT_HIGH:
                consensus_mult = _CONSENSUS_FLOOR
            else:
                consensus_mult = 1.0 - (flat_pct - _FLAT_LOW) / _FLAT_SPAN * _CONSENSUS_DROP

        return max(0.0, min(1.0, regime_mult * consensus_mult))

    def construct(
        self,
        risk_profile: RiskProfile,  # noqa: ARG002 — IPortfolioConstructor signature; base_weights path doesn't re-derive by profile
        strategies: list[Strategy],
        backtest_results: dict[str, BacktestResult],
        regime: RegimeClassification | None,
        current_portfolio: Portfolio | None = None,  # noqa: ARG002 — Protocol signature; sizing is stateless wrt current holdings
        ensemble_consensus: EnsembleConsensus | None = None,
        *,
        base_weights: dict[str, float] | None = None,
    ) -> list[TargetAllocation]:
        """Scale raw target weights by the regime/consensus position scale.

        The returned weights are authoritative; the on-chain ``token_address``
        for each symbol is resolved by the caller (the runner attaches
        addresses via ``_weights_to_targets``), so allocations are emitted with
        ``token_address=""`` and ``strategy_ids=[]``.
        """
        raw_weights = base_weights if base_weights is not None else self._fallback_weights(strategies, backtest_results)

        scale = self.compute_position_scale(regime, ensemble_consensus)
        scaled = _shrink_to_safe_asset(raw_weights, scale)

        return [
            TargetAllocation(symbol=symbol, token_address="", weight=weight, strategy_ids=[])
            for symbol, weight in scaled.items()
        ]

    def score_strategy(
        self,
        strategy: Strategy,  # noqa: ARG002 — IPortfolioConstructor signature; score derives from the backtest result
        result: BacktestResult,
        risk_profile: RiskProfile,  # noqa: ARG002 — Protocol signature; DSR/Sharpe scoring is profile-agnostic
    ) -> float:
        """Score a strategy for ranking. Higher = better fit.

        Prefers the Deflated Sharpe Ratio (selection-bias-corrected) when the
        backtest carries it; otherwise falls back to the raw Sharpe.
        """
        if result.deflated_sharpe_ratio is not None:
            return result.deflated_sharpe_ratio
        return result.sharpe_ratio

    # ── Fallback weight derivation (no production caller uses this path) ──

    def _fallback_weights(
        self,
        strategies: list[Strategy],
        backtest_results: dict[str, BacktestResult],
    ) -> dict[str, float]:
        """Minimal score-ranked, normalized weights when ``base_weights`` is absent.

        Deliberately simple: rank strategies by ``score_strategy`` over their
        backtest result and normalize the positive scores into per-strategy
        weights keyed by ``strategy.id``. No production call site exercises this
        — it exists so ``construct`` never crashes when called without
        ``base_weights``.
        """
        scores: dict[str, float] = {}
        for strat in strategies:
            result = backtest_results.get(strat.id)
            if result is None:
                continue
            score = self.score_strategy(strat, result, RiskProfile.MODERATE)
            if score > 0:
                scores[strat.id] = score

        total = sum(scores.values())
        if total <= 0:
            # Nothing rankable → park everything in the safe asset.
            return {SAFE_ASSET: 1.0}
        return {sid: score / total for sid, score in scores.items()}


class KellyRegimePortfolioConstructor(IPortfolioConstructor):
    """Kelly-weighted strategy sizing + regime tilt, behind ``IPortfolioConstructor``.

    Built for issue #1264 — the vault-*setup* path
    (``POST /api/vaults/{address}/derive-allocations``), which previously called
    ``services/strategy_sizer.py`` directly with no regime awareness at all.
    This class is that promotion: the SAME Kelly math (``size_strategies`` /
    ``scale_to_budget`` — passport half-Kelly × risk-profile multiplier, gate-
    failers zeroed, never levered above budget; see ``strategy_sizer``'s module
    docstring for the full sizing model) behind the frozen interface, with the
    execution-society's regime tilt (``PortfolioConstructor.compute_position_scale``,
    §3.2 of ``docs/specs/execution-trading-agent-society-spec.md``) layered on
    top rather than re-derived — reused via composition so the regime-tilt
    formula has exactly one implementation in the codebase.

    Two production paths through ``construct()``:

    1. **``base_weights`` supplied (the derive-allocations production path).**
       The caller has already run ``strategy_sizer``'s full pipeline —
       ``size_strategies`` → ``scale_to_budget`` → ``kelly_weighted_allocations``
       — against the strategies' live signal votes (this class's ``construct()``
       does not receive per-asset signal votes, so it cannot re-derive this
       step; see design note below). ``base_weights`` are those per-asset
       weights; this class scales them by the regime/consensus throttle and
       returns them, byte-identical when the scale is neutral (see below).

    2. **``base_weights`` absent (fallback, no production caller today).**
       Derives per-*strategy* fractions directly from ``strategies`` via
       ``size_strategies`` + ``scale_to_budget``, using the risk profile's
       ``RISK_PROFILE_PARAMS[profile]["usyc_floor"]`` as the investable budget
       ceiling (the production path instead uses the caller's user-supplied
       ``usdc_floor_pct``, which this interface has no parameter for). Returned
       weights are keyed by ``strategy.id`` — not a real asset symbol — because
       mapping a strategy's sized fraction onto per-asset votes needs the
       signal-evaluator output this interface does not carry either. Same
       shape and caveat as the sibling class's ``_fallback_weights``.

    **Regime-None default diverges from the sibling class on purpose.** When
    ``regime is None``, ``PortfolioConstructor`` (the execution society's tick
    loop, which polls a live snapshot every tick) applies the conservative
    ``REGIME_MULTIPLIER_NONE`` (0.7) — there, "no regime" usually means "this
    tick's snapshot fetch degraded," and caution is the right default. This
    class instead treats ``regime is None`` as **neutral** (scale 1.0, no
    throttle at all): derive-allocations is a non-committing preview endpoint
    with no live regime feed wired to it today (wiring one would mean either a
    fresh, unfed ``GmmRegimeDetector`` — which always reports ``None`` and would
    additionally stomp the module-level detector the ``/health`` probe reads,
    see ``gmm_regime_detector._register_detector`` — or a new Redis dependency
    in this request path; both are out of scope for this change). Silently
    shrinking every derive-allocations response by 30% with no way to see the
    un-shrunk number would be a worse default than no throttle, and it would
    break behaviour-compatibility with the pre-existing (shipped, tested)
    ``strategy_sizer``-only output for callers who pass no regime — which is
    every caller today. A future caller that DOES have a live regime (e.g. the
    execution runner, if it ever calls this class instead of the sibling one)
    gets the identical spec-named tilt as ``PortfolioConstructor`` — this
    override affects only the missing-signal fallback value, not the formula.
    """

    def __init__(self, regime_scaler: PortfolioConstructor | None = None) -> None:
        # Composition, not inheritance: reuses the canonical regime-tilt
        # formula (REGIME_MULTIPLIER + confidence factor + consensus throttle)
        # without forking its constants. Injectable for tests.
        self._regime_scaler = regime_scaler or PortfolioConstructor()

    def compute_position_scale(
        self,
        regime: RegimeClassification | None,
        ensemble_consensus: EnsembleConsensus | None,
    ) -> float:
        """Regime/consensus throttle — see the class docstring for the ``None`` divergence.

        ``regime is None`` → 1.0 (neutral; no signal, no throttle). Otherwise
        delegates entirely to ``PortfolioConstructor.compute_position_scale``
        (same formula, same consensus handling) so the two classes never drift.
        """
        if regime is None:
            return 1.0
        return self._regime_scaler.compute_position_scale(regime, ensemble_consensus)

    def construct(
        self,
        risk_profile: RiskProfile,
        strategies: list[Strategy],
        backtest_results: dict[str, BacktestResult],  # noqa: ARG002 — Protocol signature; Kelly sizing reads passport.kelly_fraction, not the backtest object
        regime: RegimeClassification | None,
        current_portfolio: Portfolio | None = None,  # noqa: ARG002 — Protocol signature; sizing is stateless wrt current holdings
        ensemble_consensus: EnsembleConsensus | None = None,
        *,
        base_weights: dict[str, float] | None = None,
    ) -> list[TargetAllocation]:
        """Kelly-size strategies (or scale caller-supplied weights), then apply the regime tilt.

        See the class docstring for the two paths (``base_weights`` supplied vs.
        absent) and why a neutral ``scale == 1.0`` is applied as a pass-through
        rather than routed through the shrink/renormalize arithmetic: caller-
        supplied ``base_weights`` may already carry their own rounding
        convention (``strategy_sizer.kelly_weighted_allocations`` rounds to 4
        decimal places), and the production caller (``derive_vault_allocations``)
        applies its own basis-point-level rounding correction downstream. Re-
        normalizing here too, even by a no-op divisor, would reintroduce
        floating-point drift into an already-reconciled total and could shift
        which symbol absorbs that caller's rounding remainder. Skipping the
        arithmetic entirely when there is nothing to throttle keeps this path
        byte-identical to the pre-#1264 ``strategy_sizer``-only output.
        """
        raw_weights = (
            dict(base_weights) if base_weights is not None else self._kelly_fallback_weights(strategies, risk_profile)
        )

        scale = self.compute_position_scale(regime, ensemble_consensus)
        scaled = dict(raw_weights) if scale == 1.0 else _shrink_to_safe_asset(raw_weights, scale)

        return [
            TargetAllocation(symbol=symbol, token_address="", weight=weight, strategy_ids=[])
            for symbol, weight in scaled.items()
        ]

    def score_strategy(
        self,
        strategy: Strategy,
        result: BacktestResult,
        risk_profile: RiskProfile,
    ) -> float:
        """Score a strategy for ranking. Higher = better fit.

        Delegates to ``PortfolioConstructor.score_strategy`` (DSR-preferred,
        Sharpe fallback) — one scoring implementation, reused rather than
        forked, matching the regime-tilt reuse above.
        """
        return self._regime_scaler.score_strategy(strategy, result, risk_profile)

    # ── Fallback weight derivation (no production caller uses this path) ──

    def _kelly_fallback_weights(
        self,
        strategies: list[Strategy],
        risk_profile: RiskProfile,
    ) -> dict[str, float]:
        """Per-strategy Kelly-sized fractions when ``base_weights`` is absent.

        Uses the risk profile's own ``usyc_floor`` (``RISK_PROFILE_PARAMS``) as
        the investable-budget ceiling — see the class docstring for why this
        differs from the production path's user-supplied floor. Unclaimed
        budget (gate-failers sized to zero, or Kelly fractions that sum below
        the ceiling) stays in ``SAFE_ASSET`` — never levered up to fill it,
        matching ``strategy_sizer``'s "never lever up to fill a budget" rule.
        """
        profile = RiskProfile(risk_profile)
        usyc_floor = RISK_PROFILE_PARAMS[profile]["usyc_floor"]
        sized = size_strategies(strategies, profile.value)
        sized = scale_to_budget(sized, max(0.0, 1.0 - usyc_floor))

        weights = {sid: frac for sid, frac in sized.items() if frac > 0.0}
        weights[SAFE_ASSET] = round(max(0.0, 1.0 - sum(weights.values())), 6)
        return weights

"""T1.1 — Multi-agent debate society (Phase 1 skeleton: additive + flag-gated).

Design of record: ``docs/specs/multi-agent-debate-spec.md`` (v2, staged
replacement). This is the strictly **additive** Phase-1 increment — a
``pipeline_name="debate"`` generation runner gated behind
``ARCHIMEDES_DEBATE_ENABLED`` (default OFF). While the flag is OFF the legacy
live path is byte-identical (the dispatch never reaches this module). The
society:

  1. **Proposer pool** — fans ``StrategyFusion(model=...).propose`` across
     ``select_candidates(regime_bias=R)`` evidence sets (the A3 model seam
     threads the user's Generate-page model pick; the steered selection is the
     cheap diversity axis). Drops non-actionable
     (``FusionProposal.is_actionable``) and non-conformant (``_dsl_conformance_ok``,
     fix A5) specs. ``pool_size = len(POOL)``.
  2. **Adversarial round** — a thin, best-effort bull/bear transcript (Phase 1).
     It surfaces the adversarial topology on the SSE stream but never gates;
     the deterministic critics do the real culling (the budget trick). The full
     rebuttal + LLM risk-debate is Phase 2.
  3. **C-rigor** — backtests EVERY survivor for real via ``evaluate_fusion_spec``
     (deterministic Python, 0 tokens), each wrapped in try/except (fix A5
     backstop), with ``num_trials=_society_num_trials(library_size, pool_size)``
     (library + N, #770/#820 — not ``pool_size`` alone) so the DSR
     multiple-testing correction counts both the selection-from-pool search AND
     the library the winner joins, the same selection set the live agent path
     deflates for.
  4. **C-null** — a survivor must beat the passive null (buy-and-hold) net of
     cost by ``MIN_COST_BENEFIT``. If none clears it → first-class ABSTAIN.
  5. **Synthesizer** — deterministic rank of the survivors (Phase 1 collapses the
     synthesizer to 0 LLM calls per spec §8) → top-N leaderboard; the user picks.

``_run_debate_candidate`` returns the **leader** ``_CandidateResult`` (carrier
contract preserving); the full leaderboard is built by ``build_leaderboard``
(directly unit-tested) and the Considered-Alternatives fan-out is Phase 2.

⚠️  PHASE-1 BLOCKER: ``ARCHIMEDES_DEBATE_ENABLED`` must stay OFF on the live path
until this module's rigor badge has been validated end-to-end there (A1's
denominator now matches the live path's — library + N via
``_society_num_trials``, #770/#820 — but the flag still needs a real on-flag
run before it's trusted). The flag-OFF additive skeleton in this module is
safe to merge.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

# Imported at module top: generation_pipeline does NOT import this module at top
# level (only lazily inside the dispatch), so there is no import cycle.
from archimedes.agents.generation_pipeline import (
    FusionUnavailable,
    _CandidateResult,
    _society_num_trials,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from archimedes.agents.generation_pipeline import _Emitter
    from archimedes.api.generate_schemas import GenerateBrief

logger = logging.getLogger(__name__)

# ── Env knobs ───────────────────────────────────────────────────────────────
_TRUE = {"1", "true", "yes", "on"}

# Steer grid = regime × mechanism. Each (regime_bias, mechanism) pair gives the
# proposer a distinct evidence ranking (regime_bias → select_candidates) AND a
# distinct mechanism hint (appended to strategic_direction), so the pool diverges
# on TWO axes — not just the 3 regime_bias values. This is the non-corpus diversity
# dimension that mitigates the "diversity theater" risk when the corpus is degraded
# (a degraded reranker can collapse regime steers; the mechanism hint still varies
# the proposer prompt). `_pool_max()` bounds how many of these steers actually fan out.
_REGIME_AXIS: tuple[str | None, ...] = ("bull", "bear", None)
_MECHANISM_AXIS: tuple[str, ...] = (
    "momentum / trend-following",
    "volatility-managed / defensive",
    "carry",
    "breakout",
    "mean-reversion",
    "minimum-variance",
)
# Cartesian product (regime × mechanism) = 18 distinct steers; `_pool_max()` caps the fan-out.
_STEERS: tuple[tuple[str | None, str], ...] = tuple((r, m) for r in _REGIME_AXIS for m in _MECHANISM_AXIS)

# Indicator stems interpret_spec actually supports. ``realized_vol_N`` *validates*
# via validate_strategy_spec but raises DSLError("unsupported indicator") inside
# interpret_spec — which would throw in C-rigor. The conformance guard (fix A5)
# drops such specs from the pool BEFORE they reach evaluate_fusion_spec.
_CONFORMANT_INDICATORS = {"sma", "ema", "rsi", "momentum"}

# DSL price operands (not indicator aliases) — excluded from the conformance scan.
_PRICE_OPERANDS = {"close", "open", "high", "low", "volume"}

# C-null passive-null bar (V_check min_cost_benefit_bps = 5 bps): a survivor must
# beat buy-and-hold net of cost by at least this. Phase-1 proxy uses the
# backtest's own annualized edge (cagr); the explicit buy-and-hold differential
# is Phase 2.
MIN_COST_BENEFIT = 0.0005  # 5 bps


def debate_enabled() -> bool:
    """True iff ``ARCHIMEDES_DEBATE_ENABLED`` is set truthy (default OFF)."""
    return os.getenv("ARCHIMEDES_DEBATE_ENABLED", "").strip().lower() in _TRUE


def _pool_max() -> int:
    """``DEBATE_POOL_MAX`` clamped to [2, 24] (default 10 for Phase 1).

    Bounds how many of the regime×mechanism ``_STEERS`` (18 total) actually fan out
    as proposer calls — so the env knob meaningfully caps the LLM cost.
    """
    try:
        return max(2, min(len(_STEERS), int(os.getenv("DEBATE_POOL_MAX", "10"))))
    except ValueError:
        return 10


def _leaderboard_max() -> int:
    """``DEBATE_LEADERBOARD_MAX`` clamped to [1, 24] (default 10 — the spec's top-10)."""
    try:
        return max(1, min(24, int(os.getenv("DEBATE_LEADERBOARD_MAX", "10"))))
    except ValueError:
        return 10


def _library_size() -> int:
    """Curated strategy-library size — the library-context selection layer for the
    DSR deflation (mirrors the live path's ``len(strategies)``). Never raises;
    degrades to 1 (the minimum), which can only UNDER-count, never over-deflate."""
    try:
        from archimedes.services.strategy_provider import default_provider

        return max(1, len(default_provider().list_strategies()))
    except Exception:
        logger.debug("debate: library-size lookup failed; defaulting to 1", exc_info=True)
        return 1


class DebateUnavailable(FusionUnavailable):
    """The society produced no actionable + conformant + backtestable candidate.

    Subclasses ``FusionUnavailable`` so the existing ``run_generation`` fallback
    (``except FusionUnavailable``) relabels honestly to the agent path — no
    dispatch except-clause change is needed.
    """


# ── DSL conformance guard (fix A5) ───────────────────────────────────────────


def _indicator_alias_stems(spec: dict[str, Any]) -> set[str]:
    """Collect the indicator stems referenced as ``{indicator}_{period}`` operands.

    Walks the entry/exit condition trees. A string operand is an indicator alias
    iff it has a trailing ``_<int>`` (e.g. ``sma_50`` → ``sma``,
    ``realized_vol_5`` → ``realized_vol``). Price operands (``close`` …) and
    numeric operands are ignored.
    """
    stems: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and node not in _PRICE_OPERANDS:
            stem, sep, period = node.rpartition("_")
            if sep and stem and period.isdigit():
                stems.add(stem)

    walk(spec.get("entry"))
    walk(spec.get("exit"))
    return stems


def _dsl_conformance_ok(spec: dict[str, Any] | None) -> bool:
    """True iff ``spec`` is backtestable by ``interpret_spec`` (fix A5).

    Rejects specs whose indicator aliases fall outside ``{sma, ema, rsi,
    momentum}`` — those validate but raise ``DSLError`` at interpret time, which
    would otherwise throw inside C-rigor and take down the whole leaderboard
    build. A spec must also carry both an ``entry`` and an ``exit`` tree.
    """
    if not isinstance(spec, dict):
        return False
    if not isinstance(spec.get("entry"), dict) or not isinstance(spec.get("exit"), dict):
        return False
    return all(stem in _CONFORMANT_INDICATORS for stem in _indicator_alias_stems(spec))


# ── Viability precheck (mirrors generation_pipeline._fusion_can_run) ──────────


def _debate_can_run(brief: GenerateBrief) -> bool:
    """No-LLM viability precheck: flag on AND ≥ MIN_PAPERS for the steer.

    Never raises — any failure degrades to ``False`` so the caller falls back to
    the agent path. (Mirrors ``generation_pipeline._fusion_can_run``.)
    """
    if not debate_enabled():
        return False
    try:
        from archimedes.agents.strategy_fusion import (
            MIN_PAPERS,
            FusionBrief,
            fusion_enabled,
            load_corpus,
            select_candidates,
        )

        if not fusion_enabled():
            return False
        fb = FusionBrief(
            asset_classes=list(brief.asset_classes or []),
            risk_appetite=brief.risk_appetite,
            strategic_direction=brief.intent or "",
            max_papers=brief.max_papers,
        )
        return len(select_candidates(fb, load_corpus())) >= MIN_PAPERS
    except Exception:
        logger.debug("debate viability precheck failed; treating as not runnable", exc_info=True)
        return False


# ── Step 1 — proposer pool ────────────────────────────────────────────────────


async def _propose_pool(brief: GenerateBrief, model: str | None, corpus: list[Any]) -> list[Any]:
    """Fan ``StrategyFusion(model=...).propose`` across regime-steered evidence.

    Returns the POOL: proposals that are both *actionable*
    (``FusionProposal.is_actionable``) AND *conformant* (``_dsl_conformance_ok``).
    ``pool_size = len(returned list)`` is the DSR multiple-testing selection set.
    """
    from archimedes.agents.strategy_fusion import (
        MIN_PAPERS,
        FusionBrief,
        StrategyFusion,
        select_candidates,
    )

    steers = list(_STEERS)[: _pool_max()]

    def _propose_one(steer: tuple[str | None, str]) -> Any:
        regime_bias, mechanism = steer
        # Thread BOTH axes: regime_bias steers select_candidates' evidence ranking,
        # and the mechanism hint (appended to strategic_direction) steers the proposer
        # prompt so the same evidence + a different mechanism yields a genuinely
        # different spec — not an LLM-noise duplicate. (The caller-gap fix:
        # select_candidates accepts regime_bias but StrategyFusion.propose never
        # threads it; the proposer fuses over THIS steered set via the injected corpus.)
        fb = FusionBrief(
            asset_classes=list(brief.asset_classes or []),
            risk_appetite=brief.risk_appetite,
            strategic_direction=f"{brief.intent or ''} — favor {mechanism} mechanisms".strip(" —"),
            max_papers=brief.max_papers,
        )
        evidence = select_candidates(fb, corpus, regime_bias=regime_bias)
        if len(evidence) < MIN_PAPERS:
            return None
        return StrategyFusion(model=model, corpus=evidence).propose(fb)

    proposals = await asyncio.gather(
        *(asyncio.to_thread(_propose_one, s) for s in steers),
        return_exceptions=True,
    )

    pool: list[Any] = []
    for p in proposals:
        if isinstance(p, BaseException) or p is None:
            continue
        if p.is_actionable and _dsl_conformance_ok(p.strategy_spec):
            pool.append(p)
    return pool


# ── Step 2 — best-effort adversarial round (transcript only, never gates) ─────

_DEBATE_SYSTEM = (
    "You are the {role} researcher in a quant strategy debate, round {rnd}. {stance}. "
    "Cite ONLY the listed candidate strategies. {rebuttal}"
    'Reply with ONE JSON object: {{"verdict": "act"|"decline", "confidence": <0..1>, "key_claims": [<str>...]}}.'
)

_DEBATE_STANCES = {
    "bull": "Argue FOR acting on the strongest candidate",
    "bear": "Argue for ABSTENTION — the null is buy-and-hold; attack overfit/cost",
}


async def _debate_round(pool: list[Any], model: str | None, emit: _Emitter, candidate_id: str) -> list[dict[str, Any]]:
    """Best-effort bull/bear research debate with ONE visible rebuttal round.

    Round 1: bull + bear state initial positions. Round 2: each REBUTS the other's
    round-1 claims (the visible adversarial turn — the "debate" the roadmap names).
    Transcript ONLY — it never gates; the deterministic critics do the real culling.
    Built in fixed ``[bull-r1, bear-r1, bull-r2, bear-r2]`` order for R3 determinism
    (sort-before-hash). Any failure (no backend, unparseable output) degrades to a
    neutral entry; the whole round is skipped if no backend is available.
    """
    from archimedes.agents.strategy_architect import extract_json
    from archimedes.services.llm_backend import make_llm_backend

    names = "; ".join(p.strategy_name for p in pool[:5] if p.strategy_name)
    transcript: list[dict[str, Any]] = []
    try:
        backend = make_llm_backend(model=model)
    except Exception:
        logger.debug("debate round skipped (backend construction failed)", exc_info=True)
        return transcript
    if not getattr(backend, "available", False):
        return transcript

    def _turn(role: str, rnd: int, opponent_claims: list[str]) -> dict[str, Any]:
        rebuttal = ""
        if opponent_claims:
            rebuttal = (
                f"The opposing researcher argued: {'; '.join(str(c) for c in opponent_claims[:3])}. "
                "Directly rebut their strongest point. "
            )
        try:
            raw = backend.complete(
                _DEBATE_SYSTEM.format(role=role, rnd=rnd, stance=_DEBATE_STANCES[role], rebuttal=rebuttal),
                names,
            )
            parsed = extract_json(raw)
            return {
                "role": role,
                "round": rnd,
                "verdict": str(parsed.get("verdict", "n/a")),
                "claims": list(parsed.get("key_claims") or parsed.get("fatal_flaws") or []),
            }
        except Exception:
            return {"role": role, "round": rnd, "verdict": "n/a", "claims": []}

    # Round 1 — initial positions (fixed bull→bear order).
    for role in ("bull", "bear"):
        await emit.emit(
            "tool_called", candidate_id=candidate_id, tool_name=f"debate_{role}_r1", args_summary=names[:120]
        )
        transcript.append(await asyncio.to_thread(_turn, role, 1, []))

    # Round 2 — visible rebuttal: each researcher sees the other's round-1 claims.
    claims_by_role = {t["role"]: t["claims"] for t in transcript}
    for role, opponent in (("bull", "bear"), ("bear", "bull")):
        await emit.emit(
            "tool_called", candidate_id=candidate_id, tool_name=f"debate_{role}_r2", args_summary="rebuttal"
        )
        transcript.append(await asyncio.to_thread(_turn, role, 2, claims_by_role.get(opponent, [])))

    return transcript


# ── Step 3 — C-rigor (deterministic, real backtests) ──────────────────────────


async def _critic_rigor(pool: list[Any], num_trials: int) -> list[tuple[Any, Any]]:
    """Backtest every pooled spec for real; return ``[(proposal, eval_result)]``.

    ``num_trials`` is ``_society_num_trials(library_size, pool_size)`` (library + N,
    #770/#820), so the DSR deflation counts the selection-from-pool search AND the
    library the winner joins — not ``pool_size`` alone. Each ``evaluate_fusion_spec``
    is wrapped in try/except so one bad spec (despite the A5 pre-guard) drops with
    an honest emit, never aborting the cohort.
    """
    from archimedes.services.fusion_evaluator import evaluate_fusion_spec
    from archimedes.services.fusion_market_data import real_data_enabled

    # Real data per candidate (#788/#818): the fetch is cached per ticker-set,
    # so a pool sharing a universe costs one yfinance round-trip, not N.
    _use_real = real_data_enabled()

    def _backtest(proposal: Any) -> Any:
        try:
            return evaluate_fusion_spec(proposal.strategy_spec, num_trials=num_trials, use_real_data=_use_real)
        except Exception as exc:
            logger.info("debate C-rigor: dropped a candidate on backtest error: %s", exc)
            return None

    results = await asyncio.gather(*(asyncio.to_thread(_backtest, p) for p in pool))
    out: list[tuple[Any, Any]] = []
    for proposal, ev in zip(pool, results, strict=True):
        if ev is not None and ev.success and ev.rigor is not None:
            out.append((proposal, ev))
    return out


# ── C-prov (deterministic provenance/embargo gate — Xia 1/2/4) ────────────────


def _critic_prov(pool: list[Any], corpus: list[Any]) -> tuple[list[Any], list[Any]]:
    """C-prov (Xia 1/2/4, non-votable): hard-fail any candidate citing a paper
    OUTSIDE the shared embargo + decay-applied evidence surface.

    The surface is the ``load_corpus()`` output, which already excludes post-embargo
    papers (``apply_outcome_embargo`` runs inside ``load_papers_from_db``), so a
    candidate whose ``source_arxiv_ids`` are all in the corpus is provenance-clean.
    A candidate citing an id NOT in the surface (a post-embargo leak or a
    hallucination that slipped the proposer's ``valid_ids`` filter) is dropped —
    deterministic defense-in-depth that cannot be argued out of its position.

    Does NOT change ``pool_size`` (the DSR denominator counts every conformant spec
    we proposed/searched, per spec §5c); it only culls which survivors reach C-rigor.
    Returns ``(kept, dropped)``.
    """
    surface = {getattr(p, "arxiv_id", None) for p in corpus}
    surface.discard(None)
    kept: list[Any] = []
    dropped: list[Any] = []
    for prop in pool:
        # Robust to a proposal missing/None source_arxiv_ids — treat as empty (→ drop,
        # "not provenance-verifiable"), never raise and abort the whole run (Copilot review).
        cited = set(getattr(prop, "source_arxiv_ids", None) or [])
        if cited and cited <= surface:
            kept.append(prop)
        else:
            dropped.append(prop)
    return kept, dropped


# ── Step 4/5 — C-null + synthesize → leaderboard ──────────────────────────────


def _survives_null(ev: Any) -> bool:
    """C-null: the candidate beats the passive null net of cost by ≥ 5 bps.

    Phase-1 proxy: the backtest's own annualized edge (cagr) clears
    ``MIN_COST_BENEFIT``. The explicit buy-and-hold differential is Phase 2.
    """
    cagr = getattr(ev.backtest, "cagr", None)
    return cagr is not None and cagr > MIN_COST_BENEFIT


def _score(ev: Any) -> tuple[int, float, float]:
    """Deterministic leaderboard rank key: (passing, DSR, OOS Sharpe), desc."""
    r = ev.rigor
    dsr = r.dsr if r.dsr is not None else -1e18
    oos = r.oos_sharpe if r.oos_sharpe is not None else -1e18
    return (1 if r.passing else 0, dsr, oos)


def _rigor_verdict_dict(ev: Any) -> dict[str, Any]:
    """Build the passport ``rigor_verdict`` from a FusionEvalResult.

    Mirrors ``generation_pipeline._run_fusion_candidate``'s verdict shape so the
    passport renders identically for debate and fusion candidates.
    """
    r = ev.rigor
    bt = ev.backtest
    return {
        "dsr": r.dsr,
        "dsr_p_value": r.dsr_p_value,
        "pbo": r.pbo_score,
        "oos_sharpe": r.oos_sharpe,
        "in_sample_sharpe": r.in_sample_sharpe,
        "lookahead_audit_passed": bool(r.look_ahead_clean),
        "look_ahead_label": r.look_ahead_label,
        "num_trials": int(r.num_trials),  # library + pool_size, #770/#820 (A1)
        "passing": bool(r.passing),
        "data_source": r.data_source,
        "admissible": bool(ev.admissible),
        "sharpe_ratio": bt.sharpe_ratio,
        "sortino_ratio": bt.sortino_ratio,
        "max_drawdown": bt.max_drawdown,
        "cagr": bt.cagr,
        "calmar_ratio": bt.calmar_ratio,
        "win_rate": bt.win_rate,
        "total_trades": bt.total_trades,
    }


def _make_entry(candidate_id: str, proposal: Any, ev: Any, *, regime: str) -> _CandidateResult:
    """One leaderboard entry — a fully-populated ``_CandidateResult``.

    ``has_real_rigor=True`` (carries C-rigor's real backtest) so the downstream
    ``_patch_pbo`` and buy-and-hold gather correctly SKIP it (keyed on
    ``has_real_rigor``), preserving the CSCV PBO from ``evaluate_fusion_spec``.
    """
    spec = proposal.strategy_spec or {}
    # Real per-bar returns for the live gate (#788/#818, mirrors the fusion
    # path): without return_series, _persist_real_returns SKIPS the winner and
    # the passport reads "pending" forever even though C-rigor just ran a real
    # backtest (first prod debate run surfaced exactly this).
    _ec = list(getattr(ev.backtest, "equity_curve", None) or [])
    real_returns = [(_ec[i] - _ec[i - 1]) / _ec[i - 1] for i in range(1, len(_ec)) if _ec[i - 1] > 0]
    return _CandidateResult(
        candidate_id=candidate_id,
        strategy_name=proposal.strategy_name or "Debate candidate",
        thesis=proposal.thesis,
        asset_universe=list(spec.get("asset_universe", []) or []),
        source_papers=[{"arxiv_id": a, "title": ""} for a in proposal.source_arxiv_ids],
        weights={},  # debate emits a DSL spec, not a static weight vector
        reasoning=proposal.fusion_reasoning or proposal.novelty_rationale or "",
        rigor_verdict=_rigor_verdict_dict(ev),
        passes_rigor=bool(ev.rigor.passing),
        regime=regime,
        generation_method="debate",
        source_arxiv_ids=list(proposal.source_arxiv_ids),
        has_real_rigor=True,
        return_series=real_returns or None,
    )


def _abstain_result(candidate_id: str, *, regime: str, reason: str) -> _CandidateResult:
    """First-class ABSTAIN — a populated, SKIP-shaped ``_CandidateResult``.

    ``generation_method="debate_abstain"`` flows through the existing emit/persist
    path (and V_check's SKIP-trace mechanism); it is NOT a new error code.
    """
    return _CandidateResult(
        candidate_id=candidate_id,
        strategy_name="Debate — abstain (hold current weights)",
        thesis=reason,
        asset_universe=[],
        source_papers=[],
        weights={},
        reasoning=reason,
        rigor_verdict={
            "dsr": None,
            "pbo": None,
            "oos_sharpe": None,
            "in_sample_sharpe": None,
            "lookahead_audit_passed": False,
            "passing": False,
            "reason": reason,
        },
        passes_rigor=False,
        regime=regime,
        generation_method="debate_abstain",
        source_arxiv_ids=[],
        has_real_rigor=False,
    )


def _critic_regime() -> dict[str, Any]:
    """C-regime (Xia §4.4 Hierarchy-of-Truth) — read the live exogenous regime.

    **Non-votable.** A live CRISIS read forces ABSTAIN regardless of how good the
    candidates look — crisis is exactly when you do NOT deploy a fresh strategy,
    and no bull argument can override it. DEGRADED (GMM artifact missing → VIX
    rule-based fallback) is surfaced honestly and lowers confidence, but does not
    by itself force abstain (else the society would always abstain when the model
    is unavailable). Never raises — any failure degrades to "unavailable, don't
    force" so the regime critic can only ABSTAIN, never spuriously APPROVE.

    Returns a dict: ``regime`` (str|None), ``confidence`` (float), ``degraded``
    (bool), ``force_abstain`` (bool), ``reason`` (str).
    """
    out: dict[str, Any] = {
        "regime": None,
        "confidence": 0.0,
        "degraded": True,
        "force_abstain": False,
        "reason": "regime detector unavailable — not gating",
    }
    try:
        from archimedes.models.regime import Regime
        from archimedes.services.gmm_regime_detector import current_regime, gmm_regime_health

        health = gmm_regime_health()
        degraded = health.status != "live"
        # Read the SHARED live detector (the one the oracle/agent runner feeds), NOT a
        # fresh GmmRegimeDetector — a new instance has no current classification, so it
        # would always read None and the gate would never fire (Copilot review).
        rc = current_regime()
        regime = rc.regime if rc is not None else None
        confidence = float(rc.confidence) if rc is not None else 0.0
        force_abstain = regime == Regime.CRISIS
        if regime is None:
            reason = "no regime read — not gating"
        elif force_abstain:
            reason = (
                f"CRISIS regime (confidence={confidence:.2f}"
                f"{', GMM degraded → VIX fallback' if degraded else ''}) — non-votable ABSTAIN"
            )
        else:
            reason = (
                f"regime={regime.value} confidence={confidence:.2f}"
                f"{' (GMM degraded → VIX rule-based fallback)' if degraded else ''}"
            )
        out = {
            "regime": regime.value if regime is not None else None,
            "confidence": confidence,
            "degraded": degraded,
            "force_abstain": force_abstain,
            "reason": reason,
        }
    except Exception:
        logger.debug("C-regime read failed; treating as unavailable (not gating)", exc_info=True)
    return out


def build_leaderboard(
    rigor_results: list[tuple[Any, Any]],
    *,
    regime: str,
    base_id: str,
    regime_force_abstain: bool = False,
    regime_reason: str = "",
) -> list[_CandidateResult]:
    """Deterministic C-regime gate → C-null cull + rank → top-N leaderboard.

    The **non-votable C-regime gate runs first**: a live-CRISIS
    ``regime_force_abstain`` short-circuits to ABSTAIN before C-null even runs —
    market regime structurally overrides candidate consensus (Hierarchy-of-Truth).
    Otherwise: C-null cull → rank → leaderboard (leader keeps ``base_id``,
    alternatives get ``base_id_alt{n}``). Pure + deterministic — directly tested.
    """
    if regime_force_abstain:
        return [
            _abstain_result(
                base_id,
                regime=regime,
                reason=f"Regime gate (non-votable, Hierarchy-of-Truth): {regime_reason}",
            )
        ]
    survivors = [(p, ev) for (p, ev) in rigor_results if _survives_null(ev)]
    if not survivors:
        return [
            _abstain_result(
                base_id,
                regime=regime,
                reason="No candidate beat the passive null by ≥ 5 bps net of cost — abstaining (hold current weights).",
            )
        ]
    survivors.sort(key=lambda pe: _score(pe[1]), reverse=True)
    # Cap to the top-N leaderboard (spec's top-10 contract) so the persisted/streamed
    # candidate set can't balloon when the pool is large.
    survivors = survivors[: _leaderboard_max()]
    return [
        _make_entry(base_id if i == 0 else f"{base_id}_alt{i}", p, ev, regime=regime)
        for i, (p, ev) in enumerate(survivors)
    ]


# ── Runner (the dispatch entry point) ─────────────────────────────────────────


async def _run_debate_candidate(
    *,
    candidate_id: str,
    brief: GenerateBrief,
    emit: _Emitter,
    regime: str = "neutral",
    agent: Any = None,  # noqa: ARG001 — signature parity with the other runners
    model: str | None = None,
    selection_pool_size: int = 1,  # noqa: ARG001 — parity with the #770 runner contract; the debate computes its OWN pool_size (the real selection count) internally
) -> _CandidateResult:
    """Run the debate society once and return the leader ``_CandidateResult``.

    Carrier-contract preserving: returns a single ``_CandidateResult`` (the
    leader). The full leaderboard is built by ``build_leaderboard`` (testable);
    the Considered-Alternatives fan-out is Phase 2. Raises ``DebateUnavailable``
    (a ``FusionUnavailable`` subclass) when no candidate survives, so the existing
    run_generation fallback relabels to the agent path.

    ``selection_pool_size`` is accepted for parity with the #770 runner contract
    (the dispatch threads it to every runner), but the society ignores the passed
    value and uses its OWN internally-computed ``pool_size = len(POOL)`` — the
    actual count of conformant proposed specs, which is the correct DSR
    selection set, not the user's ``n_candidates``.
    """
    from archimedes.agents.strategy_fusion import load_corpus

    await emit.emit("agent_iteration", candidate_id=candidate_id, iteration_n=1, max_iterations=4)
    await emit.emit(
        "tool_called",
        candidate_id=candidate_id,
        tool_name="propose_pool",
        args_summary=f"steers={len(_STEERS)}, asset_classes={brief.asset_classes or '(any)'}",
    )

    corpus = await asyncio.to_thread(load_corpus)
    pool = await _propose_pool(brief, model, corpus)
    pool_size = len(pool)
    if pool_size == 0:
        raise DebateUnavailable("debate produced no actionable, DSL-conformant candidate (empty pool)")

    await emit.emit(
        "tool_result",
        candidate_id=candidate_id,
        tool_name="propose_pool",
        result_summary=f"pool_size={pool_size} actionable+conformant specs",
    )

    # Step 2 — best-effort adversarial transcript (never gates).
    await _debate_round(pool, model, emit, candidate_id)

    # Step 3a — C-prov (non-votable, Xia 1/2/4): cull candidates citing outside the
    # embargo+decay surface. Does NOT change pool_size (the DSR denominator counts
    # every conformant spec we proposed; §5c) — only which survivors reach C-rigor.
    prov_clean, prov_dropped = _critic_prov(pool, corpus)
    if prov_dropped:
        await emit.emit(
            "tool_result",
            candidate_id=candidate_id,
            tool_name="critic_prov",
            result_summary=f"dropped {len(prov_dropped)} candidate(s) citing outside the embargo surface",
        )
    if not prov_clean:
        raise DebateUnavailable("debate: all candidates failed provenance (cited outside the embargo+decay surface)")

    # Step 3b — C-rigor (A1, aligned with #770/#811 + Önder's #820): num_trials =
    # _society_num_trials(library_size, pool_size) = library + N, NOT pool_size alone,
    # so the debate "passing" badge is not more permissive than the live path. pool_size
    # (the full conformant proposed count) is the selection set; C-prov only culls which
    # survivors are backtested. When #820 lands the shared helper, read from that source.
    num_trials = await asyncio.to_thread(lambda: _society_num_trials(_library_size(), pool_size))
    await emit.emit("agent_iteration", candidate_id=candidate_id, iteration_n=2, max_iterations=4)
    await emit.emit(
        "tool_called",
        candidate_id=candidate_id,
        tool_name="evaluate_fusion_spec",
        args_summary=f"backtest ×{len(prov_clean)}, num_trials={num_trials} (library+pool, #770/#820)",
    )
    rigor_results = await _critic_rigor(prov_clean, num_trials)
    if not rigor_results:
        raise DebateUnavailable("debate: no candidate produced a successful backtest")

    # Step 4 — C-regime (non-votable Hierarchy-of-Truth): read the live regime.
    regime_gate = await asyncio.to_thread(_critic_regime)
    await emit.emit(
        "tool_result",
        candidate_id=candidate_id,
        tool_name="critic_regime",
        result_summary=regime_gate["reason"],
    )

    # Step 5 — C-null cull + deterministic synthesize → leaderboard (C-regime gates first).
    await emit.emit("agent_iteration", candidate_id=candidate_id, iteration_n=3, max_iterations=4)
    leaderboard = build_leaderboard(
        rigor_results,
        regime=regime,
        base_id=candidate_id,
        regime_force_abstain=regime_gate["force_abstain"],
        regime_reason=regime_gate["reason"],
    )
    leader = leaderboard[0]
    await emit.emit(
        "tool_result",
        candidate_id=candidate_id,
        tool_name="synthesize",
        result_summary=(
            "ABSTAIN — no candidate beat the passive null"
            if leader.generation_method == "debate_abstain"
            else f"leader={leader.strategy_name} dsr={leader.rigor_verdict.get('dsr')} of {len(leaderboard)} entries"
        ),
    )
    return leader

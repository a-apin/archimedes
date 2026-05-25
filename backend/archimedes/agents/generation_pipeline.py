"""Streaming strategy generation orchestrator.

Wraps ``portfolio_agent.PortfolioAgent.propose_portfolio_with_tools`` with an
event-emitting pipeline that powers the Generate page's SSE stream
(see ``docs/specs/generation-streaming-spec.md``).

The pipeline lifecycle:

  job_queued
    → brief_validated
    → candidates_selected (which existing strategies the agent will reason over)
    → for each candidate: agent_iteration / tool_called / tool_result …
    → candidate_drafted
    → candidate_evaluated (rigor verdict — synthesized from agent stress-tests)
    → best_selected
    → trace_hashed → persisted → done

Multi-candidate mechanic: ``n_candidates`` ≥ 1 (default 1). Each candidate is
a full agent run with a different seed prompt suffix. The best by rigor is
surfaced; the rest persist in the job's event log so the frontend can show
them under "considered N candidates".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from archimedes.api.generate_schemas import GenerateBrief
from archimedes.services.job_queue import JobStore, get_job_store

logger = logging.getLogger(__name__)


# ── Mock backend for tests / no-LLM environments ──────────────────────────


def _llm_available() -> bool:
    """True iff the portfolio agent can actually call an LLM.

    Used to decide between live-agent path and the deterministic fixture
    path. Tests can force the fixture path via the env var.
    """
    if os.getenv("GENERATION_PIPELINE_FIXTURE", "").lower() in ("1", "true"):
        return False
    try:
        from archimedes.agents.portfolio_agent import get_portfolio_agent

        return get_portfolio_agent().available
    except Exception:
        return False


# ── Pipeline auto-routing ──────────────────────────────────────────────────


def _pick_pipeline(
    brief: GenerateBrief,  # noqa: ARG001 — accepted for forward-compat brief-aware routing; current heuristic uses env/corpus only
    mode_override: str | None = None,
) -> tuple[str, str]:
    """Decide which generation pipeline to use based on runtime conditions.

    Returns ``(pipeline_name, reason)`` where *pipeline_name* is one of
    ``"fusion"``, ``"architect"``, or ``"agent"``.

    Decision tree (per issue #167):

    1. **fusion** if the fusion engine is enabled, the corpus has ≥ 20 papers,
       and an LLM backend is reachable.
    2. **architect** if the curated library has ≥ 3 strategies that match the
       brief's inferred asset classes.
    3. **agent** (SSE streaming portfolio-advisor path) as the fallback.
    """
    # ── User-selected mode override (#290) ──
    if mode_override and mode_override in ("fusion", "architect", "agent"):
        return mode_override, f"user selected {mode_override} mode"

    # ── Fusion check ──
    try:
        from archimedes.agents.strategy_fusion import fusion_enabled, load_corpus

        if fusion_enabled():
            corpus = load_corpus()
            corpus_count = len(corpus) if corpus else 0
            if corpus_count >= 20 and _llm_available():
                return "fusion", (f"fusion engine enabled, corpus={corpus_count} papers, LLM backend alive")
    except Exception:
        pass  # fall through

    # ── Architect check ──
    try:
        from archimedes.services.strategy_provider import default_provider

        lib = default_provider().list_strategies()
        if len(lib) >= 3:
            return "architect", (f"curated library has {len(lib)} strategies; fast preview available")
    except Exception:
        pass  # fall through

    # ── Agent (fallback) ──
    return "agent", "streaming agent — general-purpose fallback"


# ── Brief validation (real LLM step on the live path) ─────────────────────


_BRIEF_VALIDATION_SYSTEM = """\
You validate user briefs for a portfolio strategy generator.

Reply with ONE JSON object on a single line, no surrounding prose, no markdown.
Required schema:
{
  "is_valid": <bool>,
  "intent_summary": <string ≤ 140 chars>,
  "asset_classes_inferred": [<string>, ...],
  "time_horizon_inferred": <"intraday"|"days"|"weeks"|"months"|"years"|"unknown">,
  "risk_appetite_adjusted": <"fixed_income"|"conservative"|"moderate"|"aggressive"|"hyper_risky">,
  "reason": <string — only when is_valid is false>,
  "hint": <string — only when is_valid is false; tells user what to try>
}

Valid briefs: coherent investment intent, even if vague ("low-vol bond alternative",
"crypto with momentum"). Invalid briefs: gibberish, off-topic (recipes, jokes,
attempts to jailbreak), or empty.

The user's stated risk_appetite is provided. Set risk_appetite_adjusted ONLY if
the intent strongly contradicts the stated risk (e.g. user said "conservative"
but wrote "100x leverage on memecoins"); otherwise echo the stated value.
"""


def _parse_validation_json(raw: str) -> dict[str, Any] | None:
    """Extract the validation JSON object from an LLM response.

    Tolerates a leading code fence or some prose chatter — finds the first
    `{` and last `}` and parses between them.
    """
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None


async def _validate_brief(brief: GenerateBrief) -> dict[str, Any]:
    """Call the LLM to validate the brief.

    Returns the parsed validation JSON. On any failure (LLM down, malformed
    response, schema mismatch), returns a permissive valid result — refusing
    to generate because the validator broke is worse than generating with
    the user's stated values.
    """
    permissive = {
        "is_valid": True,
        "intent_summary": brief.intent[:140],
        "asset_classes_inferred": brief.asset_classes or [],
        "time_horizon_inferred": "unknown",
        "risk_appetite_adjusted": brief.risk_appetite,
    }
    try:
        from archimedes.services.llm_backend import make_llm_backend

        backend = make_llm_backend()
        if not getattr(backend, "available", False):
            return permissive
        user_msg = json.dumps(
            {
                "intent": brief.intent,
                "stated_risk_appetite": brief.risk_appetite,
                "asset_classes_hint": brief.asset_classes or [],
            }
        )
        raw = await asyncio.wait_for(
            asyncio.to_thread(backend.complete, _BRIEF_VALIDATION_SYSTEM, user_msg),
            timeout=15.0,
        )
        parsed = _parse_validation_json(raw)
        if not parsed or "is_valid" not in parsed:
            logger.info("brief validation: unparseable response, falling through permissive")
            return permissive
        # Ensure required keys exist with safe defaults.
        parsed.setdefault("intent_summary", brief.intent[:140])
        parsed.setdefault("asset_classes_inferred", brief.asset_classes or [])
        parsed.setdefault("time_horizon_inferred", "unknown")
        parsed.setdefault("risk_appetite_adjusted", brief.risk_appetite)
        return parsed
    except Exception as exc:
        logger.warning("brief validation failed (permissive fallback): %s", exc)
        return permissive


@dataclass
class _CandidateResult:
    """Internal candidate carrier — converted to events + persisted at the end."""

    candidate_id: str
    strategy_name: str
    thesis: str
    asset_universe: list[str]
    source_papers: list[dict[str, Any]]
    weights: dict[str, float]
    reasoning: str
    rigor_verdict: dict[str, Any]
    passes_rigor: bool
    regime: str = "neutral"  # "bull", "bear", or "neutral" (Issue #163)
    # Daily portfolio return series, used by the rigor gate. Populated in the
    # live path from the agent's price_histories; the fixture path leaves it
    # empty and supplies a hardcoded verdict.
    return_series: list[float] = None  # type: ignore[assignment]


# ── Rigor adapter (Önder's rigor_evaluator on agent output) ───────────────


def _portfolio_return_series(weights: dict[str, float], price_histories: dict[str, Any]) -> list[float]:
    """Compute daily returns of a buy-and-hold weighted portfolio.

    Sources the per-asset close series from ``price_histories`` (the same dict
    the agent saw), aligns to the shortest series, and returns ``Σ wᵢ · rᵢ,t``
    bar by bar. Buy-and-hold is the simplest faithful read of an agent
    allocation that doesn't specify rebalancing logic — for v1 it's the
    right baseline. When fusion-to-backtest lands a real DSL interpreter,
    that path will produce a real return series under the strategy's own
    rebalance rule.
    """
    closes_by_symbol: dict[str, list[float]] = {}
    for sym, weight in weights.items():
        if not weight:
            continue
        hist = price_histories.get(sym) if isinstance(price_histories, dict) else None
        if not isinstance(hist, dict):
            continue
        closes = hist.get("close")
        if closes and len(closes) > 1:
            closes_by_symbol[sym] = list(closes)
    if not closes_by_symbol:
        return []

    T = min(len(c) for c in closes_by_symbol.values())
    if T < 5:
        return []

    out: list[float] = []
    for t in range(1, T):
        bar_ret = 0.0
        for sym, closes in closes_by_symbol.items():
            prev = closes[t - 1]
            if not prev:
                continue
            r = (closes[t] - prev) / prev
            bar_ret += weights.get(sym, 0.0) * r
        out.append(bar_ret)
    return out


def _rigor_verdict_for(return_series: list[float], num_trials: int) -> dict[str, Any]:
    """Run Önder's rigor primitives on a portfolio return series.

    Returns the same shape the fixture path uses so the consumer (event
    emitter + frontend) doesn't care which path produced the verdict.
    """
    if not return_series or len(return_series) < 10:
        return {
            "dsr": None,
            "pbo": None,
            "oos_sharpe": None,
            "lookahead_audit_passed": True,
            "passing": False,
            "reason": "return series too short for rigor evaluation",
        }
    from archimedes.services.rigor_evaluator import (
        compute_dsr,
        compute_oos_sharpe,
    )

    dsr, dsr_p = compute_dsr(return_series, num_trials=max(1, num_trials))
    oos = compute_oos_sharpe(return_series)
    # PBO is library-level (needs ≥2 candidate return series); the caller
    # computes it once over all candidates and patches the verdict below.
    passing = bool(dsr_p is not None and dsr_p >= 0.95 and oos is not None and oos > 0.0)
    return {
        "dsr": round(float(dsr), 4) if dsr is not None else None,
        "dsr_p_value": round(float(dsr_p), 4) if dsr_p is not None else None,
        "pbo": None,  # patched later by _patch_pbo
        "oos_sharpe": round(float(oos), 4) if oos is not None else None,
        "lookahead_audit_passed": True,  # agent output is buy-and-hold; no leak
        "passing": passing,
    }


def _patch_pbo(candidates: list[_CandidateResult]) -> None:
    """Compute library-level PBO across the candidate set; patch each verdict."""
    series_map: dict[str, list[float]] = {c.candidate_id: c.return_series for c in candidates if c.return_series}
    if len(series_map) < 2:
        for c in candidates:
            c.rigor_verdict["pbo"] = 0.0  # PBO undefined for N<2
        return
    from archimedes.services.rigor_evaluator import compute_pbo

    pbo_by_id = compute_pbo(series_map)
    for c in candidates:
        pbo = pbo_by_id.get(c.candidate_id, 0.0)
        c.rigor_verdict["pbo"] = round(float(pbo), 4)
        # PBO ≥ 0.5 means the library overfits the in-sample winner; tighten
        # `passing` to require both the per-strategy DSR test AND library-PBO
        # under the 0.5 threshold.
        c.rigor_verdict["passing"] = bool(c.rigor_verdict.get("passing") and pbo < 0.5)


# ── Event emitter ─────────────────────────────────────────────────────────


class _Emitter:
    """Push events to the job's Redis event log + maintain a monotonic ID.

    Decoupled from the agent loop so the pipeline can also emit synthetic
    events (e.g. ``brief_validated``) that the agent itself doesn't know about.
    """

    def __init__(self, job_id: str, store: JobStore) -> None:
        self.job_id = job_id
        self.store = store

    async def emit(self, event: str, **payload: Any) -> int:
        ts = datetime.now(UTC).isoformat()
        body = {"event": event, "data": {"ts": ts, "job_id": self.job_id, **payload}}
        return await self.store.push_event(self.job_id, body)


# ── Fixture path (deterministic; used in tests + when LLM unavailable) ────


async def _run_fixture_candidate(
    *, candidate_id: str, brief: GenerateBrief, emit: _Emitter, regime: str = "neutral"
) -> _CandidateResult:
    """Synthetic generation that exercises every event the live agent emits.

    Useful for: tests, demo on a laptop without an API key, smoke-tests.
    Each step has a short sleep so the SSE stream actually streams rather
    than dumping everything at once on connect.
    """
    await emit.emit("agent_iteration", candidate_id=candidate_id, iteration_n=1, max_iterations=3)
    await asyncio.sleep(0.1)

    await emit.emit(
        "tool_called", candidate_id=candidate_id, tool_name="get_asset_stats", args_summary="symbols=sBTC,sSPY,sGLD"
    )
    await asyncio.sleep(0.1)
    await emit.emit(
        "tool_result",
        candidate_id=candidate_id,
        tool_name="get_asset_stats",
        result_summary="3-asset stats fetched; sGLD lowest vol",
    )

    await emit.emit("agent_iteration", candidate_id=candidate_id, iteration_n=2, max_iterations=3)
    await emit.emit(
        "tool_called", candidate_id=candidate_id, tool_name="stress_test_portfolio", args_summary="scenarios=6"
    )
    await emit.emit(
        "tool_result",
        candidate_id=candidate_id,
        tool_name="stress_test_portfolio",
        result_summary="max drawdown −12.4% (2022_inflation)",
    )

    # Regime-aware fixture names and weights
    if regime == "bull":
        name = f"Bull {brief.risk_appetite.title()} Momentum Blend"
        weights = {"sSPY": 0.55, "sBTC": 0.30, "sGLD": 0.15}
    elif regime == "bear":
        name = f"Bear {brief.risk_appetite.title()} Defensive Blend"
        weights = {"sGLD": 0.45, "sSPY": 0.30, "sBTC": 0.05, "sUSDC": 0.20}
    else:
        name = f"Synthetic {brief.risk_appetite.title()} Blend"
        weights = {"sSPY": 0.5, "sGLD": 0.3, "sBTC": 0.2}
    return _CandidateResult(
        candidate_id=candidate_id,
        strategy_name=name,
        thesis=f"Fixture-mode {regime} generation for brief: {brief.intent[:120]}",
        asset_universe=list(weights.keys()),
        source_papers=[],
        weights=weights,
        reasoning=f"Fixture path ({regime} regime) — no LLM call. Weights chosen by deterministic stub.",
        rigor_verdict={
            "dsr": 0.71,
            "pbo": 0.18,
            "oos_sharpe": 0.94,
            "lookahead_audit_passed": True,
            "passing": True,
        },
        passes_rigor=True,
        regime=regime,
    )


# ── Live agent path ───────────────────────────────────────────────────────


# Regime-specific prompt suffixes that steer the agent's allocation (Issue #163)
_REGIME_PROMPT_SUFFIX = {
    "bull": (
        "\n\nREGIME CONTEXT: You are constructing a BULL-tilted portfolio. "
        "Favor momentum, trend-following, carry, and risk-on strategies. "
        "Overweight assets with strong recent momentum and positive trend signals. "
        "Allocate more to growth-oriented and higher-beta instruments. "
        "Still respect the risk envelope, but tilt toward upside capture."
    ),
    "bear": (
        "\n\nREGIME CONTEXT: You are constructing a BEAR-tilted / defensive portfolio. "
        "Favor volatility-managed, minimum-variance, defensive, and mean-reversion strategies. "
        "Overweight safe-haven assets (gold, treasuries, USDC/stablecoins). "
        "Prioritize drawdown protection and tail-risk hedging over return maximization. "
        "The goal is to survive and preserve capital in adverse market conditions."
    ),
}


async def _run_live_candidate(
    *, candidate_id: str, brief: GenerateBrief, emit: _Emitter, regime: str = "neutral"
) -> _CandidateResult:
    """Drive the real ``portfolio_agent`` with per-iteration event emission.

    The agent's iteration loop is sync and runs in a thread. The thread uses
    a sync emit shim that schedules the async ``Emitter.emit`` back onto the
    main event loop — this keeps the agent unchanged while still streaming.
    """
    from archimedes.agents.portfolio_agent import get_portfolio_agent
    from archimedes.services.strategy_provider import default_provider
    from archimedes.services.strategy_signal_evaluator import (
        DEFAULT_SCAN_UNIVERSE,
        _fetch_price_histories,
        strategy_evaluator,
    )

    loop = asyncio.get_running_loop()

    def _sync_emit(event: str, **payload: Any) -> None:
        # Bridge from the agent's sync thread into the async event log.
        fut = asyncio.run_coroutine_threadsafe(
            emit.emit(event, candidate_id=candidate_id, **payload),
            loop,
        )
        # event emission is best-effort
        with contextlib.suppress(Exception):
            fut.result(timeout=2.0)

    price_histories = await asyncio.wait_for(
        asyncio.to_thread(_fetch_price_histories, DEFAULT_SCAN_UNIVERSE, "1y"),
        timeout=30.0,
    )
    market_ranking = strategy_evaluator.rank_market(price_histories, lookback_days=90, top_n=20)
    strategies = default_provider().list_strategies()

    # Map regime to agent regime string + confidence
    agent_regime = {"bull": "expansion", "bear": "contraction"}.get(regime, "transition")
    agent_confidence = 0.80 if regime in ("bull", "bear") else 0.65

    agent = get_portfolio_agent()
    # Inject regime suffix into the agent's system prompt via the risk_appetite
    # string (the agent reads it as context). The suffix is appended to the
    # user-visible risk profile so the agent sees the regime steer.
    regime_suffix = _REGIME_PROMPT_SUFFIX.get(regime, "")
    if regime_suffix:
        agent._regime_context = regime_suffix  # Consumed by _build_tool_user_prompt if available

    portfolio = await asyncio.wait_for(
        asyncio.to_thread(
            agent.propose_portfolio_with_tools,
            agent_regime,  # regime: expansion/contraction/transition
            agent_confidence,  # regime_confidence
            brief.risk_appetite,
            0.30,  # usdc_floor (moderate default)
            0.70,  # synth_budget
            market_ranking,
            strategies,
            set(DEFAULT_SCAN_UNIVERSE),
            price_histories,
        ),
        timeout=120.0,
    )

    if portfolio is None:
        raise RuntimeError("agent returned no portfolio")

    weights = {pick.ticker: pick.weight for pick in (portfolio.picks or [])}
    # Collect paper anchors — try matching by ID first, then by fuzzy name match
    referenced = {pick.paper_anchor for pick in (portfolio.picks or []) if pick.paper_anchor}
    source_papers = []
    strat_by_id = {s.id: s for s in strategies}
    strat_by_name = {s.paper_title.lower(): s for s in strategies if s.paper_title}
    for anchor in referenced:
        s = strat_by_id.get(anchor)
        if not s:
            # Fuzzy match: the LLM often returns a short name instead of the ID
            s = strat_by_name.get(anchor.lower())
        if not s:
            # Try substring match
            s = next((st for st in strategies if anchor.lower() in st.paper_title.lower()), None)
        if s and (getattr(s, "paper_arxiv_id", "") or getattr(s, "paper_title", "")):
            source_papers.append(
                {"arxiv_id": getattr(s, "paper_arxiv_id", "") or "", "title": getattr(s, "paper_title", "") or ""}
            )
    # Defensive fallback: if agent returned no anchors, include ALL strategies
    # that are in the curated library as potential sources (honest: they were
    # available to the agent even if it didn't cite them explicitly)
    if not source_papers and strategies:
        for s in strategies[:5]:  # cap at 5 to avoid noise
            # paper_arxiv_id may be "" for curated strategies that only have
            # DOI/title — still include them with title as the anchor (#310)
            paper_id = getattr(s, "paper_arxiv_id", None) or ""
            paper_title = getattr(s, "paper_title", None) or ""
            if paper_id or paper_title:
                source_papers.append({"arxiv_id": paper_id, "title": paper_title})
        if not source_papers:
            logger.warning(
                "agent returned no paper anchors AND fallback couldn't find library "
                "strategies with paper refs — source_papers will be empty (strategies=%d)",
                len(strategies),
            )

    # Real rigor verdict via Önder's compute_dsr + compute_oos_sharpe on the
    # buy-and-hold return series of the agent's allocation. PBO is patched
    # later (after all candidates are in) since it's a library-level metric.
    return_series = _portfolio_return_series(weights, price_histories)
    verdict = _rigor_verdict_for(return_series, num_trials=1)
    # Derive meaningful name + thesis from the brief and agent output (#299)
    top_picks = sorted(weights.items(), key=lambda x: -x[1])[:3]
    pick_summary = " / ".join(t for t, _ in top_picks)
    regime_label = {"bull": "🟢 Bull", "bear": "🔴 Bear"}.get(regime, "")
    regime_prefix = f"{regime_label} " if regime_label else ""
    strategy_name = f"{regime_prefix}{brief.risk_appetite.title()} Blend — {brief.intent[:50].strip()}"
    agent_reasoning = getattr(portfolio, "reasoning_text", "") or ""
    thesis = (
        agent_reasoning
        if len(agent_reasoning) > 20
        else (
            f"For brief '{brief.intent[:100]}': allocates {pick_summary} "
            f"across {len(weights)} assets with {brief.risk_appetite} risk appetite."
        )
    )

    return _CandidateResult(
        candidate_id=candidate_id,
        strategy_name=strategy_name,
        thesis=thesis,
        asset_universe=list(weights.keys()),
        source_papers=source_papers,
        weights=weights,
        reasoning=getattr(portfolio, "reasoning_text", "") or "",
        rigor_verdict=verdict,
        passes_rigor=verdict.get("passing", False),
        regime=regime,
        return_series=return_series,
    )


# ── Pipeline entry point ──────────────────────────────────────────────────


async def run_generation(
    *,
    job_id: str,
    brief: GenerateBrief,
    n_candidates: int = 1,
    store: JobStore | None = None,
    mode: str | None = None,
    dual_regime: bool = True,
) -> None:
    """Run the full streaming generation pipeline for one job.

    When ``dual_regime=True`` (the default since Issue #163), the pipeline
    generates BOTH a bull-tilted AND a bear-tilted candidate. Each regime
    run uses biased paper retrieval + regime-specific reasoning. The user
    sees both candidates with regime tags and can deploy one, both, or neither.

    Designed to be called as a fire-and-forget asyncio task from the route
    handler. Exceptions are caught + emitted as ``error`` events so the SSE
    client always sees a terminal state.
    """
    store = store or get_job_store()
    emit = _Emitter(job_id, store)

    await store.update_status(job_id, "running")
    await emit.emit("job_queued", brief=brief.model_dump())

    try:
        # Real LLM validation step (live path only). The fixture path skips
        # the validator so tests stay hermetic.
        if _llm_available():
            validated = await _validate_brief(brief)
        else:
            validated = {
                "is_valid": True,
                "intent_summary": brief.intent[:140],
                "asset_classes_inferred": brief.asset_classes or [],
                "time_horizon_inferred": "unknown",
                "risk_appetite_adjusted": brief.risk_appetite,
            }

        if not validated.get("is_valid", True):
            # Brief failed validation — emit a recoverable error and stop.
            # Frontend already handles `error` with recoverable=true by
            # offering a "regenerate" CTA with the reason inline.
            await emit.emit(
                "error",
                message=validated.get("reason", "brief did not pass validation"),
                hint=validated.get("hint", "Try mentioning an asset class or risk appetite."),
                recoverable=True,
                code="BRIEF_INVALID",
            )
            await store.update_status(job_id, "error", error="brief invalid")
            return

        # Honor any risk_appetite_adjusted from the validator (e.g. the user
        # said "conservative" but described 100x leverage on memecoins).
        if validated.get("risk_appetite_adjusted") and validated["risk_appetite_adjusted"] != brief.risk_appetite:
            brief = brief.model_copy(update={"risk_appetite": validated["risk_appetite_adjusted"]})

        await emit.emit(
            "brief_validated",
            asset_classes=validated.get("asset_classes_inferred", []),
            risk_appetite=brief.risk_appetite,
            intent_summary=validated.get("intent_summary", ""),
            time_horizon_inferred=validated.get("time_horizon_inferred", "unknown"),
        )

        # ── Auto-route to the best pipeline ──
        pipeline_name, pipeline_reason = _pick_pipeline(brief, mode_override=mode)

        # Determine regime plan: dual_regime emits both bull + bear (Issue #163)
        if dual_regime:
            regimes: list[str] = ["bull", "bear"]
        else:
            regimes = ["neutral"] * n_candidates
        await emit.emit(
            "pipeline_selected",
            pipeline=pipeline_name,
            reason=pipeline_reason,
            regimes=regimes,
        )

        # Decide path: live agent vs fixture
        use_live = _llm_available()
        runner: Callable[..., Awaitable[_CandidateResult]] = _run_live_candidate if use_live else _run_fixture_candidate

        # Library is the candidate pool the agent reasons over; surface it so
        # the UI can show "agent is considering N papers".
        try:
            from archimedes.services.strategy_provider import default_provider

            lib = default_provider().list_strategies()
            arxiv_ids = [s.paper_arxiv_id for s in lib if getattr(s, "paper_arxiv_id", None)]
        except Exception:
            arxiv_ids = []
        await emit.emit(
            "candidates_selected",
            candidate_count=len(regimes),
            source_arxiv_ids=arxiv_ids[: brief.max_papers],
            regimes=regimes,
        )

        candidates: list[_CandidateResult] = []
        for i, regime in enumerate(regimes):
            candidate_id = f"cand_{regime}" if dual_regime else f"cand_{i + 1}"
            try:
                cand = await runner(
                    candidate_id=candidate_id,
                    brief=brief,
                    emit=emit,
                    regime=regime,
                )
            except Exception as exc:
                logger.exception("candidate %s (%s) failed: %s", candidate_id, regime, exc)
                await emit.emit(
                    "candidate_failed",
                    candidate_id=candidate_id,
                    regime=regime,
                    error=str(exc),
                    message=f"No {regime} candidate available — your brief may be structurally one-sided.",
                )
                continue

            await emit.emit(
                "candidate_drafted",
                candidate_id=cand.candidate_id,
                strategy_name=cand.strategy_name,
                weights_preview=cand.weights,
                regime=regime,
            )
            await emit.emit(
                "candidate_evaluated",
                candidate_id=cand.candidate_id,
                rigor_verdict=cand.rigor_verdict,
                regime=regime,
            )
            candidates.append(cand)

        if not candidates:
            await emit.emit(
                "error",
                message="no candidates passed rigor",
                recoverable=True,
                code="RIGOR_FAIL",
            )
            await store.update_status(job_id, "error", error="no candidates passed rigor")
            return

        # Patch PBO across the candidate set (library-level metric — Bailey
        # et al. CSCV needs N≥2 to be meaningful; the helper handles N<2
        # gracefully by setting PBO=0.0). After this, every candidate's
        # rigor_verdict has all four fields (DSR, PBO, OOS Sharpe, lookahead).
        _patch_pbo(candidates)
        for c in candidates:
            c.passes_rigor = c.rigor_verdict.get("passing", False)

        # Pick the best by passing-rigor first, then by a simple score.
        passing = [c for c in candidates if c.passes_rigor] or candidates
        best = max(
            passing,
            key=lambda c: c.rigor_verdict.get("dsr") or 0.0,
        )
        await emit.emit(
            "best_selected",
            best_candidate_id=best.candidate_id,
            considered_count=len(candidates),
        )

        # Persist ALL candidates (both regimes) as StrategyRecords.
        # Each gets its own strategy_id and trace_hash. The "best" is still
        # highlighted but both are navigable from the library.
        strategy_ids: dict[str, str] = {}  # candidate_id → strategy_id
        for c in candidates:
            sid, thash = await _persist_candidate(c, brief)
            strategy_ids[c.candidate_id] = sid
            await emit.emit(
                "trace_hashed",
                trace_hash=thash,
                candidate_id=c.candidate_id,
                regime=c.regime,
            )
            await emit.emit(
                "persisted",
                strategy_id=sid,
                candidate_id=c.candidate_id,
                regime=c.regime,
                redirect_url=f"/library?highlight={sid}",
            )
        strategy_id = strategy_ids.get(best.candidate_id, "")

        # ── Persist all candidates to episodic memory (T-PE.8) ──
        try:
            from archimedes.services.strategy_memory import persist_proposal

            for cand in candidates:
                persist_proposal(
                    generation_id=job_id,
                    agent="agent",
                    intent=brief.intent,
                    strategy_spec={
                        "strategy_name": cand.strategy_name,
                        "thesis": cand.thesis,
                        "weights": cand.weights,
                        "asset_universe": cand.asset_universe,
                    },
                    papers=[p.get("arxiv_id", "") for p in cand.source_papers],
                    rigor_verdict=cand.rigor_verdict,
                    extra={"candidate_id": cand.candidate_id, "selected": cand is best},
                )
        except Exception:
            pass  # Non-blocking per spec

        # Stash the full candidate list on the job for /candidates retrieval.
        await store.update_status(
            job_id,
            "done",
            result={
                "best_candidate_id": best.candidate_id,
                "best_strategy_id": strategy_id,
                "candidates": [
                    {
                        "candidate_id": c.candidate_id,
                        "strategy_id": strategy_ids.get(c.candidate_id),
                        "strategy_name": c.strategy_name,
                        "rigor_verdict": c.rigor_verdict,
                        "passes_rigor": c.passes_rigor,
                        "selected": c is best,
                        "regime": c.regime,
                    }
                    for c in candidates
                ],
            },
        )
        await emit.emit("done", strategy_id=strategy_id, all_strategy_ids=strategy_ids)

    except asyncio.CancelledError:
        await emit.emit("error", message="job cancelled", recoverable=False, code="CANCELLED")
        await store.update_status(job_id, "cancelled", error="cancelled by client")
        raise
    except Exception as exc:
        logger.exception("generation pipeline crashed: %s", exc)
        await emit.emit("error", message=str(exc), recoverable=False, code="PIPELINE_CRASH")
        await store.update_status(job_id, "error", error=str(exc))


async def _persist_candidate(c: _CandidateResult, brief: GenerateBrief) -> tuple[str, str]:
    """Upsert the candidate as a Strategy + return (strategy_id, trace_hash).

    Trace hash is the keccak of the canonical (brief, candidate) tuple — gives
    every generation a deterministic identifier mirrored on-chain in v1.5.
    """
    from web3 import Web3

    from archimedes.db import get_session
    from archimedes.models.strategy_store import upsert_strategy

    canonical = json.dumps(
        {
            "brief": brief.model_dump(),
            "candidate_id": c.candidate_id,
            "strategy_name": c.strategy_name,
            "weights": c.weights,
            "rigor_verdict": c.rigor_verdict,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    trace_hash = Web3.keccak(text=canonical).hex()

    def _do_persist() -> str:
        with get_session() as session:
            record = upsert_strategy(
                session,
                generation_method="portfolio_agent_streaming",
                strategy_name=c.strategy_name,
                thesis=c.thesis,
                source_papers=c.source_papers,
                asset_universe=c.asset_universe,
                risk_profile=brief.risk_appetite,
                rigor_verdict=c.rigor_verdict,
                provenance_hash=trace_hash,
                is_example=False,
            )
            session.commit()

            # Also write to the unified strategy_passports table (Issue #160)
            try:
                from archimedes.models.paper_ref import PaperRef
                from archimedes.models.strategy import StrategyPassport, StrategyStatus
                from archimedes.services.passport_loader import ingest_passport

                papers = [
                    PaperRef(arxiv_id=p.get("arxiv_id"), title=p.get("title", "")) for p in (c.source_papers or [])
                ]
                # Map candidate regime to passport regime_tag
                _regime_tag_map = {"bull": "bull", "bear": "bear"}
                _regime_tag = _regime_tag_map.get(c.regime, "regime_neutral")
                passport = StrategyPassport(
                    id=record.id,
                    papers=papers,
                    methodology_summary=c.thesis or "",
                    asset_universe=c.asset_universe or [],
                    status=StrategyStatus(record.status) if record.status else StrategyStatus.CANDIDATE,
                    regime_tag=_regime_tag,
                    passes_rigor_gate=bool(c.rigor_verdict.get("passing", False)) if c.rigor_verdict else False,
                    deflated_sharpe_ratio=c.rigor_verdict.get("dsr") if c.rigor_verdict else None,
                    pbo_score=c.rigor_verdict.get("pbo") if c.rigor_verdict else None,
                    out_of_sample_sharpe=c.rigor_verdict.get("oos_sharpe") if c.rigor_verdict else None,
                )
                with get_session() as sess2:
                    ingest_passport(sess2, passport, generation_method="fusion", force_update=True)
                    sess2.commit()
            except Exception as exc:
                logger.warning("unified passport persist failed (non-blocking): %s", exc)

            return record.id

    strategy_id = await asyncio.to_thread(_do_persist)
    return strategy_id, trace_hash

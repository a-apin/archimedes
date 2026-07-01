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
import hashlib
import json
import logging
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
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


def _fusion_can_run(brief: GenerateBrief) -> bool:
    """No-LLM viability precheck for the fusion path.

    Returns True iff the fusion flag is on AND the deterministic candidate
    selection yields ≥ ``MIN_PAPERS`` papers for this brief's steer — i.e. the
    engine would not immediately return ``insufficient_corpus``. Used to keep
    ``pipeline_selected`` honest: we only announce "fusion" if it can actually
    produce a ≥2-paper synthesis. Never raises — any failure degrades to False
    so the caller falls back to the agent path.
    """
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
        fusion_brief = FusionBrief(
            asset_classes=list(brief.asset_classes or []),
            risk_appetite=brief.risk_appetite,
            strategic_direction=brief.intent or "",
            max_papers=brief.max_papers,
        )
        candidates = select_candidates(fusion_brief, load_corpus())
        return len(candidates) >= MIN_PAPERS
    except Exception:
        logger.debug("fusion viability precheck failed; treating as not runnable", exc_info=True)
        return False


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
    # Provenance method this candidate was produced by — persisted verbatim into
    # the StrategyRecord. The agent/fixture path leaves the default; the fusion
    # runner sets ``"fusion"`` so the library distinguishes multi-paper synthesis
    # from single-agent allocation. (Fusion dispatch wire, Stack A.)
    generation_method: str = "portfolio_agent_streaming"
    # The ≥2 arXiv ids fused into this candidate (fusion path only). Empty on the
    # agent path; the fusion runner populates it from the FusionProposal so the
    # passport renders real cross-paper provenance, not a placeholder.
    source_arxiv_ids: list[str] = field(default_factory=list)
    # Backtest verdict already carries DSR/PBO/OOS when the fusion evaluator ran;
    # ``has_real_rigor`` flags that the verdict is a real DSL backtest (so the
    # downstream buy-and-hold backtest step is skipped — it would clobber the
    # already-computed fusion metrics with a different, weight-less read).
    has_real_rigor: bool = False


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


def _rigor_verdict_for(
    return_series: list[float],
    num_trials: int,
    *,
    lookahead_passed: bool = True,
) -> dict[str, Any]:
    """Run Önder's rigor primitives on a portfolio return series.

    Returns the same shape the fixture path uses so the consumer (event
    emitter + frontend) doesn't care which path produced the verdict.

    ``num_trials`` is the multiple-testing count fed to the Deflated Sharpe
    Ratio — per ``selection-bias-corrections-spec.md`` § 1.3 this is the size
    of the strategy universe the winner was selected from (the curated library),
    NOT 1. With ``num_trials=1`` the DSR expectation-of-max term collapses to 0
    and the ratio is undeflated, which silently defeats the gate.

    ``lookahead_passed`` is the look-ahead-audit verdict, computed by the caller
    (see ``_lookahead_for_candidate``) so the primitive actually gates the
    ``passing`` decision instead of being a hardcoded constant.
    """
    if not return_series or len(return_series) < 10:
        return {
            "dsr": None,
            "pbo": None,
            "oos_sharpe": None,
            "in_sample_sharpe": None,
            "lookahead_audit_passed": lookahead_passed,
            "passing": False,
            "reason": "return series too short for rigor evaluation",
        }
    from archimedes.services.rigor_evaluator import (
        compute_dsr,
        compute_in_sample_sharpe,
        compute_oos_sharpe,
    )

    dsr, dsr_p = compute_dsr(return_series, num_trials=max(1, num_trials))
    oos = compute_oos_sharpe(return_series)
    in_sample_sharpe = compute_in_sample_sharpe(return_series)
    # PBO is library-level (needs ≥2 candidate return series); the caller
    # computes it once over all candidates and patches the verdict below.
    # All four admission primitives gate `passing`: DSR (p ≥ 0.95), OOS Sharpe
    # (> 0, with no IS/OOS cliff), look-ahead audit (clean), and PBO (< 0.5,
    # patched in _patch_pbo).
    oos_pass = oos is not None and oos > 0.0
    if (
        oos_pass
        and in_sample_sharpe is not None
        and math.isfinite(in_sample_sharpe)
        and in_sample_sharpe > 0
        and oos / in_sample_sharpe < 0.5
    ):
        oos_pass = False
    passing = bool(dsr_p is not None and dsr_p >= 0.95 and oos_pass and lookahead_passed)
    return {
        "dsr": round(float(dsr), 4) if dsr is not None else None,
        "dsr_p_value": round(float(dsr_p), 4) if dsr_p is not None else None,
        "pbo": None,  # patched later by _patch_pbo
        "oos_sharpe": round(float(oos), 4) if oos is not None else None,
        "in_sample_sharpe": round(float(in_sample_sharpe), 4) if in_sample_sharpe is not None else None,
        "lookahead_audit_passed": lookahead_passed,
        "passing": passing,
    }


def _lookahead_for_candidate(referenced_strategies: list[Any]) -> bool:
    """Look-ahead-audit verdict for a generated buy-and-hold candidate.

    The live Generate candidate is a weight vector executed buy-and-hold: its
    return series (see ``_portfolio_return_series``) is built purely from
    realized prior-bar returns ``(close[t] − close[t−1]) / close[t−1]``, so the
    *series itself* cannot leak future data. There is no signal-generating code
    of the candidate's own to feed to ``look_ahead_audit`` (which is a static
    AST audit of strategy *source*, not a return series).

    What we can honestly audit is the source of the curated strategies the
    candidate cites as provenance — a candidate grounded in a strategy whose
    code leaks future data should not pass rigor. We audit each referenced
    strategy's ``strategy_code_path`` but treat the conservative
    "negative index — verify backtrader vs pandas" warning as non-fatal: the
    curated library is backtrader-based (``close[-N]`` = N bars ago, confirmed
    leak-free), so only genuinely forward-looking patterns (positive data index,
    ``shift(-n)``, look-ahead-named calls) fail the audit here.

    Returns True when no referenced strategy exposes a genuine forward-looking
    pattern (vacuously True when none expose auditable source).
    """
    from pathlib import Path

    from archimedes.services.rigor_evaluator import look_ahead_audit

    for s in referenced_strategies:
        path = getattr(s, "strategy_code_path", None)
        if not path:
            continue
        try:
            code = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        _, warnings = look_ahead_audit(code)
        genuine = [w for w in warnings if "negative index" not in w]
        if genuine:
            logger.warning(
                "look-ahead audit flagged referenced strategy %s: %s",
                getattr(s, "paper_title", path),
                genuine,
            )
            return False
    return True


def _patch_pbo(candidates: list[_CandidateResult]) -> None:
    """Compute library-level PBO across the agent/fixture candidate set; patch each verdict.

    Fusion candidates are SKIPPED: they carry a real PBO already computed by the
    fusion evaluator's CSCV over the strategy's own parameter-variant grid
    (``has_real_rigor=True``). Overwriting that with a buy-and-hold cross-candidate
    PBO — or with the 0.0 N<2 default — would clobber the correct value and
    silently relax the gate. Only the buy-and-hold (agent/fixture) candidates,
    which have a ``return_series`` and no real rigor, participate here.
    """
    agent_cands = [c for c in candidates if not c.has_real_rigor]
    series_map: dict[str, list[float]] = {c.candidate_id: c.return_series for c in agent_cands if c.return_series}
    if len(series_map) < 2:
        for c in agent_cands:
            c.rigor_verdict["pbo"] = 0.0  # PBO undefined for N<2
        return
    from archimedes.services.rigor_evaluator import compute_pbo

    pbo_by_id = compute_pbo(series_map)
    for c in agent_cands:
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
    *,
    candidate_id: str,
    brief: GenerateBrief,
    emit: _Emitter,
    regime: str = "neutral",
    agent: Any = None,  # noqa: ARG001 — signature parity with _run_live_candidate; fixture path ignores it
    selection_pool_size: int = 1,  # noqa: ARG001 — signature parity; fixture path computes no DSR num_trials
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
    # Include the user's brief intent in the name so each generation
    # produces a distinct, meaningful title (Issue #336).
    intent_snippet = brief.intent[:50].strip() if brief.intent else "Multi-Asset"
    if regime == "bull":
        name = f"🟢 Bull {brief.risk_appetite.title()} — {intent_snippet}"
        weights = {"sSPY": 0.55, "sBTC": 0.30, "sGLD": 0.15}
    elif regime == "bear":
        name = f"🔴 Bear {brief.risk_appetite.title()} — {intent_snippet}"
        weights = {"sGLD": 0.45, "sSPY": 0.30, "sBTC": 0.05, "sUSDC": 0.20}
    else:
        name = f"{brief.risk_appetite.title()} Blend — {intent_snippet}"
        weights = {"sSPY": 0.5, "sGLD": 0.3, "sBTC": 0.2}
    # Fixture source_papers: pull from curated library (same fallback as live)
    fixture_source_papers: list[dict[str, Any]] = []
    try:
        from archimedes.services.strategy_provider import default_provider

        for s in default_provider().list_strategies()[:3]:
            title = getattr(s, "paper_title", "") or ""
            arxiv_id = getattr(s, "paper_arxiv_id", "") or ""
            if title or arxiv_id:
                fixture_source_papers.append({"arxiv_id": arxiv_id, "title": title})
    except Exception:
        logger.debug("failed to collect fixture source papers", exc_info=True)

    return _CandidateResult(
        candidate_id=candidate_id,
        strategy_name=name,
        thesis=f"Fixture-mode {regime} generation for brief: {brief.intent[:120]}",
        asset_universe=list(weights.keys()),
        source_papers=fixture_source_papers,
        weights=weights,
        reasoning=f"Fixture path ({regime} regime) — no LLM call. Weights chosen by deterministic stub.",
        rigor_verdict={
            "dsr": None,
            "pbo": None,
            "oos_sharpe": None,
            "in_sample_sharpe": None,
            "lookahead_audit_passed": False,
            "passing": False,
            "reason": "fixture mode — no LLM call, rigor gate not run",
        },
        passes_rigor=False,
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


def _society_num_trials(library_size: int, selection_pool_size: int) -> int:
    """Effective DSR multiple-testing trial count on the agentic society path (#770).

    The winner survived selection from ``selection_pool_size`` (N) generated candidates
    AND is promoted into a library of ``library_size`` — two independent selection layers,
    so the deflation count is their SUM (approach A, Bailey & López de Prado 2014). See
    ``docs/specs/selection-bias-corrections-spec.md`` § 1.3 addendum. Floored at 1.
    """
    return max(1, library_size + selection_pool_size)


async def _run_live_candidate(
    *,
    candidate_id: str,
    brief: GenerateBrief,
    emit: _Emitter,
    regime: str = "neutral",
    agent: Any = None,
    selection_pool_size: int = 1,
) -> _CandidateResult:
    """Drive the real ``portfolio_agent`` with per-iteration event emission.

    The agent's iteration loop is sync and runs in a thread. The thread uses
    a sync emit shim that schedules the async ``Emitter.emit`` back onto the
    main event loop — this keeps the agent unchanged while still streaming.

    ``agent`` is an optional pre-built ``PortfolioAgent`` (e.g. bound to the
    user's free-tier model pick). When ``None`` the shared process singleton is
    used — the default, unchanged behavior.
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

    agent = agent or get_portfolio_agent()
    # Inject regime suffix into the agent's system prompt via the risk_appetite
    # string (the agent reads it as context). The suffix is appended to the
    # user-visible risk profile so the agent sees the regime steer.
    regime_suffix = _REGIME_PROMPT_SUFFIX.get(regime, "")
    if regime_suffix:
        agent._regime_context = regime_suffix  # noqa: SLF001 — deliberate: consumed by _build_tool_user_prompt if available

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
    referenced_strategies: list[Any] = []  # matched curated strategies (for look-ahead audit)
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
        if s:
            referenced_strategies.append(s)
        if s and getattr(s, "paper_arxiv_id", None):
            source_papers.append({"arxiv_id": s.paper_arxiv_id, "title": s.paper_title})
    # Defensive fallback: if agent returned no anchors, include strategies
    # from the curated library as potential sources (honest: they were
    # available to the agent even if it didn't cite them explicitly).
    # Include any strategy with a paper_title OR paper_arxiv_id — curated
    # strategies reference seminal papers (Faber 2007, Moreira-Muir 2017)
    # that predate arxiv, so paper_arxiv_id is often empty while paper_title
    # is always populated.
    if not source_papers and strategies:
        for s in strategies[:5]:  # cap at 5 to avoid noise
            title = getattr(s, "paper_title", "") or ""
            arxiv_id = getattr(s, "paper_arxiv_id", "") or ""
            if title or arxiv_id:
                source_papers.append({"arxiv_id": arxiv_id, "title": title})
        if not source_papers:
            logger.warning(
                "agent returned no paper anchors AND fallback couldn't find "
                "library strategies with paper refs — source_papers will be empty"
            )

    # Real rigor verdict via Önder's compute_dsr + compute_oos_sharpe on the
    # buy-and-hold return series of the agent's allocation. PBO is patched
    # later (after all candidates are in) since it's a library-level metric.
    # num_trials = library size: the winner was selected from this universe, so
    # the DSR must be deflated for that multiple-testing count (spec § 1.3), not
    # the previous hardcoded 1 (which left the DSR undeflated). The look-ahead
    # primitive is computed from referenced curated source and gates `passing`.
    return_series = _portfolio_return_series(weights, price_histories)
    lookahead_passed = _lookahead_for_candidate(referenced_strategies)
    # num_trials = selection-from-N candidates + library context (#770). The agentic
    # society generates `selection_pool_size` candidates, backtests all, and keeps the
    # best — selection-from-N is itself a multiple-testing search, so the DSR must be
    # deflated for BOTH that pool AND the library the winner joins, not the library
    # alone (which understated deflation and inflated the survivor's DSR). Additive
    # N + library_size per the selection-bias-corrections spec § 1.3 (Bailey & López de
    # Prado 2014); the correction can only make the gate stricter. (Önder's call — A.)
    num_trials = _society_num_trials(len(strategies), selection_pool_size)
    verdict = _rigor_verdict_for(return_series, num_trials=num_trials, lookahead_passed=lookahead_passed)
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


# ── Fusion path (multi-paper synthesis folded into the live stream) ───────
#
# Stack A previously computed a "fusion" LABEL via _pick_pipeline() and emitted
# a cosmetic pipeline_selected event, then ALWAYS dispatched to the single-agent
# portfolio path — the choice was thrown away. This runner makes the choice
# actually drive dispatch: it REUSES the existing, unit-tested fusion engine
# (StrategyFusion.propose → evaluate_fusion_spec, the same path Stack B's
# _run_fusion_job wires) and emits the SAME streaming SSE events the agent path
# emits, so the UI surfaces fusion transparently with real DSR/PBO/OOS.


class FusionUnavailable(Exception):
    """Fusion was selected but couldn't actually run (disabled / <2 papers /
    LLM declined). The caller falls back to the agent path and relabels the
    pipeline honestly rather than silently mislabeling an agent run as fusion."""


async def _run_fusion_candidate(
    *,
    candidate_id: str,
    brief: GenerateBrief,
    emit: _Emitter,
    regime: str = "neutral",
    agent: Any = None,  # noqa: ARG001 — signature parity with _run_live_candidate; fusion path builds its own client
    selection_pool_size: int = 1,  # noqa: ARG001 — signature parity; fusion candidates skip the static DSR path
) -> _CandidateResult:
    """Drive the existing fusion engine with per-step streaming event emission.

    Builds a ``FusionBrief`` from the incoming ``GenerateBrief``, calls the
    EXISTING engine (``StrategyFusion.propose()`` → ``evaluate_fusion_spec()`` —
    reuse, no duplication), and returns a ``_CandidateResult`` carrying the real
    rigor verdict (DSR/PBO/OOS/look-ahead) computed by the DSL backtest.

    Raises ``FusionUnavailable`` when the engine can't produce an actionable,
    ≥2-paper fusion (disabled, insufficient corpus, unparseable, or fewer than
    ``MIN_PAPERS`` fused) so the caller can fall back to the agent path and
    relabel honestly — fusion never silently degrades into a single-paper or
    mislabeled result.
    """
    from archimedes.agents.strategy_fusion import (
        MIN_PAPERS,
        FusionBrief,
        default_fusion,
    )

    # Surface the same "agent is thinking" cadence the agent path emits so the
    # SSE stream renders progress rather than dumping the result on connect.
    await emit.emit("agent_iteration", candidate_id=candidate_id, iteration_n=1, max_iterations=3)
    await emit.emit(
        "tool_called",
        candidate_id=candidate_id,
        tool_name="select_candidates",
        args_summary=f"asset_classes={brief.asset_classes or '(any)'}, regime={regime}",
    )

    # Map the GenerateBrief → FusionBrief (the engine's steer). asset_classes
    # carries the user's universe steer; strategic_direction carries the intent.
    fusion_brief = FusionBrief(
        asset_classes=list(brief.asset_classes or []),
        risk_appetite=brief.risk_appetite,
        strategic_direction=brief.intent or "",
        max_papers=brief.max_papers,
    )

    fusion = default_fusion()
    proposal = await asyncio.wait_for(
        asyncio.to_thread(fusion.propose, fusion_brief),
        timeout=120.0,
    )

    await emit.emit(
        "tool_result",
        candidate_id=candidate_id,
        tool_name="select_candidates",
        result_summary=(f"fusion status={proposal.status}, papers={len(proposal.source_arxiv_ids)}"),
    )

    if not proposal.is_actionable:
        # status in {disabled, insufficient_corpus, unparseable} OR <2 papers.
        # Don't fabricate a fusion — bubble up so the caller falls back honestly.
        raise FusionUnavailable(
            f"fusion not actionable (status={proposal.status}, "
            f"papers={len(proposal.source_arxiv_ids)}): {proposal.thesis[:160]}"
        )

    # ── Run the fusion evaluator pipeline (validate → backtest → rigor gate) ──
    # Same call Stack B's _run_fusion_job makes. Produces the real DSR/PBO/OOS
    # verdict on a DSL-interpreted backtest. Degrade gracefully (text-only
    # fusion) if no machine-readable strategy_spec was emitted.
    rigor_verdict: dict[str, Any]
    has_real_rigor = False
    asset_universe: list[str] = []
    if proposal.strategy_spec is not None:
        await emit.emit("agent_iteration", candidate_id=candidate_id, iteration_n=2, max_iterations=3)
        await emit.emit(
            "tool_called",
            candidate_id=candidate_id,
            tool_name="evaluate_fusion_spec",
            args_summary="backtest + DSR/PBO/OOS rigor gate",
        )
        from archimedes.services.fusion_evaluator import evaluate_fusion_spec

        eval_result = await asyncio.wait_for(
            asyncio.to_thread(evaluate_fusion_spec, proposal.strategy_spec),
            timeout=120.0,
        )
        asset_universe = list(proposal.strategy_spec.get("asset_universe", []) or [])
        if eval_result.success and eval_result.rigor is not None:
            r = eval_result.rigor
            bt = eval_result.backtest
            rigor_verdict = {
                "dsr": r.dsr,
                "dsr_p_value": r.dsr_p_value,
                "pbo": r.pbo_score,
                "oos_sharpe": r.oos_sharpe,
                "in_sample_sharpe": r.in_sample_sharpe,
                "lookahead_audit_passed": bool(r.look_ahead_clean),
                "look_ahead_label": r.look_ahead_label,
                "num_trials": int(r.num_trials),
                "passing": bool(r.passing),
                "data_source": r.data_source,
                "admissible": bool(r.admissible),
                # Backtest headline metrics — surfaced alongside so the passport
                # renders without denormalizing from a separate field (parity
                # with Stack B's rigor_verdict_dict).
                "sharpe_ratio": bt.sharpe_ratio,
                "sortino_ratio": bt.sortino_ratio,
                "max_drawdown": bt.max_drawdown,
                "cagr": bt.cagr,
                "calmar_ratio": bt.calmar_ratio,
                "win_rate": bt.win_rate,
                "total_trades": bt.total_trades,
            }
            has_real_rigor = True
            await emit.emit(
                "tool_result",
                candidate_id=candidate_id,
                tool_name="evaluate_fusion_spec",
                result_summary=(
                    f"DSR={r.dsr} p={r.dsr_p_value} PBO={r.pbo_score} OOS={r.oos_sharpe} passing={r.passing}"
                ),
            )
        else:
            rigor_verdict = {
                "dsr": None,
                "pbo": None,
                "oos_sharpe": None,
                "in_sample_sharpe": None,
                "lookahead_audit_passed": False,
                "passing": False,
                "reason": (eval_result.error or "fusion backtest produced no metrics"),
            }
            await emit.emit(
                "tool_result",
                candidate_id=candidate_id,
                tool_name="evaluate_fusion_spec",
                result_summary=f"evaluation failed: {(eval_result.error or 'no metrics')[:120]}",
            )
    else:
        # Text-only fusion (no DSL spec) — honest pre-backtest verdict, not a
        # fabricated pass. The proposal is still actionable (≥2 papers fused).
        rigor_verdict = {
            "dsr": None,
            "pbo": None,
            "oos_sharpe": None,
            "in_sample_sharpe": None,
            "lookahead_audit_passed": False,
            "passing": False,
            "reason": "fusion produced no machine-readable strategy_spec — rigor gate not run",
        }

    source_papers = [{"arxiv_id": aid, "title": ""} for aid in proposal.source_arxiv_ids]
    # Defensive: the engine already guarantees ≥ MIN_PAPERS via is_actionable.
    assert len(proposal.source_arxiv_ids) >= MIN_PAPERS  # invariant guard (engine guarantees via is_actionable)

    return _CandidateResult(
        candidate_id=candidate_id,
        strategy_name=proposal.strategy_name or f"Fusion — {brief.intent[:50].strip()}",
        thesis=proposal.thesis,
        asset_universe=asset_universe,
        source_papers=source_papers,
        weights={},  # fusion emits a DSL spec, not a static weight vector
        reasoning=proposal.fusion_reasoning or proposal.novelty_rationale or "",
        rigor_verdict=rigor_verdict,
        passes_rigor=bool(rigor_verdict.get("passing", False)),
        regime=regime,
        generation_method="fusion",
        source_arxiv_ids=list(proposal.source_arxiv_ids),
        has_real_rigor=has_real_rigor,
    )


# ── Pipeline entry point ──────────────────────────────────────────────────


def _served_model_for(job_agent: Any, use_live: bool) -> str:
    """Resolve the model id that actually served this job, for provenance.

    Prefers the per-job agent's backend (when the user picked a model), else the
    shared singleton. Reads ``served_model`` (post-call truth, e.g. response.model)
    when present, falling back to ``model_id``. Returns the fixture marker on the
    non-live path so the UI never claims a real model ran when it didn't.
    """
    if not use_live:
        return "fixture"
    try:
        from archimedes.agents.portfolio_agent import get_portfolio_agent

        agent = job_agent or get_portfolio_agent()
        backend = getattr(agent, "_backend", None)
        served = getattr(backend, "served_model", None) or getattr(backend, "model_id", None)
        if served:
            return str(served)
        return getattr(agent, "model_id", "unknown")
    except Exception:
        return "unknown"


async def run_generation(
    *,
    job_id: str,
    brief: GenerateBrief,
    n_candidates: int = 1,
    store: JobStore | None = None,
    mode: str | None = None,
    model: str | None = None,
    dual_regime: bool = True,
) -> None:
    """Run the full streaming generation pipeline for one job.

    When ``dual_regime=True`` (the default since Issue #163), the pipeline
    generates BOTH a bull-tilted AND a bear-tilted candidate. Each regime
    run uses biased paper retrieval + regime-specific reasoning. The user
    sees both candidates with regime tags and can deploy one, both, or neither.

    ``model`` is the user's optional free-tier model pick (already allowlisted
    by the route). When set, the live path constructs a per-job LLM backend on
    that model; when ``None`` it uses the shared singleton on the env default —
    behavior UNCHANGED.

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

        # ── Resolve the ACTUAL runner that will drive dispatch ──
        # _pick_pipeline computes a label; before this fix that label was thrown
        # away and the agent path always ran. Now the choice DRIVES dispatch:
        # when "fusion" is selected, dispatch to the real (unit-tested) fusion
        # engine via _run_fusion_candidate. A lightweight, no-LLM viability
        # precheck (fusion flag on + ≥2 candidate papers for the steer) keeps
        # pipeline_selected honest — we only announce "fusion" if it can run.
        # The single-agent path remains the FALLBACK (fusion not selected / no
        # LLM / corpus <2 papers).
        use_live = _llm_available()
        agent_runner: Callable[..., Awaitable[_CandidateResult]] = (
            _run_live_candidate if use_live else _run_fixture_candidate
        )
        runner = agent_runner
        if pipeline_name == "fusion":
            if use_live and _fusion_can_run(brief):
                runner = _run_fusion_candidate
            else:
                # Selected fusion but it can't actually run (no LLM, or the steer
                # yields <2 papers). Relabel honestly rather than mislabeling an
                # agent run as fusion (claim integrity).
                pipeline_name = "agent"
                pipeline_reason = (
                    "fusion selected but not runnable "
                    f"({'no LLM backend' if not use_live else 'corpus yielded <2 papers for the steer'})"
                    " — falling back to streaming agent"
                )
                runner = agent_runner

        await emit.emit(
            "pipeline_selected",
            pipeline=pipeline_name,
            reason=pipeline_reason,
            regimes=regimes,
        )

        # If the user picked an allowlisted free-tier model, build a per-job
        # portfolio agent bound to that model; otherwise reuse the shared
        # singleton on the env default (behavior unchanged). Constructed once and
        # shared across both regime candidates so we don't rebuild a client twice.
        #
        # NOTE: `use_live` and `runner` are already resolved ABOVE, where the
        # fusion-vs-agent dispatch decision is made. Do NOT redefine `runner`
        # here — a second `runner = _run_live_candidate ...` would clobber the
        # fusion runner selected for a "fusion" pipeline and silently revert to
        # the agent path (the bug this rebase had to reconcile).
        job_agent = None
        if use_live and model:
            try:
                from archimedes.agents.portfolio_agent import PortfolioAgent
                from archimedes.services.llm_backend import make_llm_backend

                job_agent = PortfolioAgent(backend=make_llm_backend(model=model))
            except Exception as exc:
                logger.warning("could not build per-job agent for model %r (%s); using default", model, exc)
                job_agent = None
        # Library is the candidate pool the agent reasons over; surface it so
        # the UI can show "agent is considering N papers". Its size also feeds
        # the DSR multiple-testing correction below (selection-bias-corrections-
        # spec.md § 1.3) — num_trials must be the size of the selection set the
        # winner was chosen from, not 1.
        try:
            from archimedes.services.strategy_provider import default_provider

            lib = default_provider().list_strategies()
            arxiv_ids = [s.paper_arxiv_id for s in lib if getattr(s, "paper_arxiv_id", None)]
            library_size = max(1, len(lib))
        except Exception:
            arxiv_ids = []
            library_size = 1
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
                    agent=job_agent,
                    selection_pool_size=n_candidates,  # #770: DSR deflates for the N-candidate search
                )
            except FusionUnavailable as exc:
                # Fusion was selected + precheck passed, but at runtime the
                # engine declined (e.g. the LLM fused <2 valid papers). Fall
                # back to the agent path for THIS candidate rather than failing
                # — and surface the relabel so the stream stays honest.
                logger.info("fusion declined for %s (%s); falling back to agent: %s", candidate_id, regime, exc)
                await emit.emit(
                    "pipeline_selected",
                    pipeline="agent",
                    reason=f"fusion declined at runtime ({exc}); using streaming agent",
                    regimes=regimes,
                )
                try:
                    cand = await agent_runner(
                        candidate_id=candidate_id,
                        brief=brief,
                        emit=emit,
                        regime=regime,
                        agent=job_agent,  # preserve the user's free-tier model pick on fallback (#748)
                        selection_pool_size=n_candidates,  # #770: DSR deflates for the N-candidate search
                    )
                except Exception as exc2:
                    logger.exception("agent fallback %s (%s) failed: %s", candidate_id, regime, exc2)
                    await emit.emit(
                        "candidate_failed",
                        candidate_id=candidate_id,
                        regime=regime,
                        error=str(exc2),
                        message=f"No {regime} candidate available — your brief may be structurally one-sided.",
                    )
                    continue
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

        # ── Run real multi-year backtests on every persisted candidate ──
        # Closes the "Pending Backtest" gap: until this lands, generated
        # strategies sat in the Library with no `real_sharpe` row, so the
        # passport rendered the placeholder card forever. We backtest both
        # regime variants in parallel (yfinance + numpy is I/O-bound), then
        # upsert the BacktestResult row + updated passport metrics so the
        # next /api/strategies/ read surfaces empirical Sharpe/DSR/PBO/OOS.
        # Fusion candidates NEVER carry a static weight vector — they emit a DSL
        # strategy_spec (weights={}), evaluated by the fusion evaluator, not the
        # buy-and-hold portfolio_backtester. So skip ALL fusion candidates here,
        # keyed on generation_method — not just the has_real_rigor ones. A
        # *text-only* fusion candidate (model emitted no machine-readable spec, so
        # has_real_rigor stayed False) ALSO has weights={}; routing it into the
        # static backtester only emits a misleading
        # backtest_failed("no weights emitted by agent") — the live conversion bug
        # in #784 (every Generate result looked broken). Keying on
        # generation_method preserves the HONEST "no weights" signal for a genuine
        # agent-path failure (agent emitted no allocation) while never running the
        # static backtester on a fusion candidate that has nothing static to run.
        await asyncio.gather(
            *[
                # num_trials = N candidates + library context (#770), not library alone.
                _backtest_and_persist(
                    c, strategy_ids[c.candidate_id], emit, _society_num_trials(library_size, n_candidates)
                )
                for c in candidates
                if c.generation_method != "fusion"
            ]
        )

        # ── Persist all candidates to episodic memory (T-PE.8) ──
        try:
            from archimedes.services.strategy_memory import persist_proposal

            for cand in candidates:
                persist_proposal(
                    generation_id=job_id,
                    # "fusion" for fused candidates, "agent" otherwise — the
                    # episodic record now reflects which engine produced the
                    # proposal rather than always claiming "agent".
                    agent="fusion" if cand.generation_method == "fusion" else "agent",
                    intent=brief.intent,
                    strategy_spec={
                        "strategy_name": cand.strategy_name,
                        "thesis": cand.thesis,
                        "weights": cand.weights,
                        "asset_universe": cand.asset_universe,
                    },
                    papers=[p.get("arxiv_id", "") for p in cand.source_papers] or list(cand.source_arxiv_ids),
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
        # Provenance: surface the model that actually served this job so the UI
        # can show what really ran (vs. what was requested). `served_model` is
        # the post-call value when available (e.g. response.model), else the
        # configured id; falls back to the fixture marker on the non-live path.
        served_model = _served_model_for(job_agent, use_live)
        await emit.emit(
            "done",
            strategy_id=strategy_id,
            all_strategy_ids=strategy_ids,
            served_model=served_model,
        )

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
                # Provenance of record — "fusion" for multi-paper synthesis,
                # "portfolio_agent_streaming" for the single-agent path. Was
                # hardcoded to the agent method; now reads the candidate's own
                # method so fusion candidates land in the library as fusion.
                generation_method=c.generation_method,
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
                    ingest_passport(sess2, passport, generation_method=c.generation_method, force_update=True)
                    sess2.commit()
            except Exception as exc:
                logger.warning("unified passport persist failed (non-blocking): %s", exc)

            return record.id

    strategy_id = await asyncio.to_thread(_do_persist)
    return strategy_id, trace_hash


async def _backtest_and_persist(c: _CandidateResult, strategy_id: str, emit: _Emitter, num_trials: int = 1) -> None:
    """Backtest the generated strategy on real multi-year data and persist results.

    Closes the "Pending Backtest" gap on the Library page. The agent only
    emits ``{ticker: weight}`` + a rebalance period — no ``bt.Strategy``
    subclass — so the analytics-engine's single-asset Cerebro path doesn't
    fit. This function instead runs the pandas/numpy
    :func:`portfolio_backtester.backtest_portfolio` over real yfinance prices,
    persists a full :class:`BacktestResult` to ``backtest_results``, and
    refreshes the passport row with empirical metrics so
    ``is_backtest_placeholder`` flips false on the next API read.

    Failures are non-fatal: if yfinance is unreachable, a ticker doesn't
    resolve, or the historical overlap is too short, the placeholder remains
    and a ``backtest_failed`` SSE event surfaces the reason. The generation
    itself does not fail.

    Args:
        c: The candidate that was just persisted.
        strategy_id: The DB id returned by :func:`_persist_candidate`.
        emit: The SSE emitter to surface backtest progress to the UI.
        num_trials: Effective trial count for the DSR multiple-testing correction —
            N generated candidates + curated-library size on the society path (#770),
            fed to ``backtest_portfolio`` as ``num_trials_for_dsr``.
    """
    # Fixture mode (offline tests, no-LLM environments) — skip the network
    # round-trip. The test suite covers this function's behavior via direct
    # unit tests in test_portfolio_backtester.py and via the pipeline's
    # generation-event tests, neither of which need a live yfinance call.
    if os.getenv("GENERATION_PIPELINE_FIXTURE", "").lower() in ("1", "true") or os.getenv(
        "GENERATION_PIPELINE_SKIP_BACKTEST", ""
    ).lower() in ("1", "true"):
        logger.debug("skipping live backtest for %s (fixture mode)", strategy_id)
        return

    await emit.emit(
        "backtest_running",
        strategy_id=strategy_id,
        candidate_id=c.candidate_id,
        symbols=list((c.weights or {}).keys()),
    )

    if not c.weights:
        await emit.emit(
            "backtest_failed",
            strategy_id=strategy_id,
            candidate_id=c.candidate_id,
            error="no weights emitted by agent",
        )
        return

    def _do_backtest_and_persist() -> dict[str, Any] | None:
        # All heavy work — yfinance fetch, numpy compute, DB writes — runs
        # off the event loop. Returns the metrics dict for the SSE emit.
        import json as _json
        from datetime import date as _date

        from archimedes.db import get_session
        from archimedes.models.paper_ref import PaperRef
        from archimedes.models.strategy import StrategyPassport, StrategyStatus
        from archimedes.models.strategy_store import StrategyRecord
        from archimedes.services.backtest_repository import insert_backtest_if_missing
        from archimedes.services.passport_loader import ingest_passport
        from archimedes.services.portfolio_backtester import backtest_portfolio

        # Run the actual backtest. Raises on insufficient data / fetch failure.
        # num_trials_for_dsr = N candidates + curated-library size (#770,
        # selection-bias-corrections-spec.md § 1.3) — the DSR multiple-testing
        # correction for the full selection set: the N-candidate society search
        # this winner survived PLUS the library it joins, not library alone.
        result, artifact = backtest_portfolio(
            strategy_id=strategy_id,
            weights=c.weights,
            paper_title=c.strategy_name,
            num_trials_for_dsr=max(1, num_trials),
        )
        artifact_json = _json.dumps(artifact, default=str)
        content_hash = hashlib.sha256(artifact_json.encode("utf-8")).hexdigest()

        # Same passes_rigor_gate rule the strategies_routes._to_strategy_response
        # check uses on curated strategies — keeps generated and curated graded
        # on the same scale.
        passes = bool(result.passes_rigor_gate)

        with get_session() as session:
            # 1. Persist the backtest_results row.
            insert_backtest_if_missing(
                session,
                strategy_id=strategy_id,
                content_hash=content_hash,
                result=result,
                run_id=artifact.get("run_id"),
                operation="PORTFOLIO",
                artifact_json=artifact_json,
            )

            # 2. Refresh the strategy_passports row with real_* metrics.
            #    We rebuild the passport using the StrategyRecord we just
            #    persisted (for status + name) plus the candidate's papers /
            #    asset universe / regime mapping — same construction as
            #    `_persist_candidate`'s passport block, now decorated with
            #    real metrics. ingest_passport(force_update=True) does an
            #    in-place update.
            record = session.query(StrategyRecord).filter_by(id=strategy_id).first()
            status_val = StrategyStatus(record.status) if record and record.status else StrategyStatus.CANDIDATE
            papers = [PaperRef(arxiv_id=p.get("arxiv_id"), title=p.get("title", "")) for p in (c.source_papers or [])]
            _regime_tag_map = {"bull": "bull", "bear": "bear"}
            _regime_tag = _regime_tag_map.get(c.regime, "regime_neutral")
            passport = StrategyPassport(
                id=strategy_id,
                papers=papers,
                methodology_summary=c.thesis or "",
                asset_universe=c.asset_universe or [],
                status=status_val,
                regime_tag=_regime_tag,
                # Real backtest fields — the whole point of this function
                real_sharpe=result.sharpe_ratio,
                real_sortino=result.sortino_ratio,
                real_cagr=result.cagr,
                real_max_dd=result.max_drawdown,
                real_calmar=result.calmar_ratio,
                real_corr_spy=result.correlation_to_spy,
                real_total_trades=result.total_trades,
                real_backtest_start=(
                    result.backtest_start.isoformat() if isinstance(result.backtest_start, _date) else None
                ),
                real_backtest_end=(result.backtest_end.isoformat() if isinstance(result.backtest_end, _date) else None),
                deflated_sharpe_ratio=result.deflated_sharpe_ratio,
                dsr_p_value=result.dsr_p_value,
                num_trials_in_selection=result.num_trials_in_selection,
                pbo_score=result.pbo_score,
                out_of_sample_sharpe=result.out_of_sample_sharpe,
                passes_rigor_gate=passes,
                n_obs_daily=len(artifact["results"][0]["metrics"].get("daily_returns", [])),
            )
            ingest_passport(session, passport, generation_method="fusion", force_update=True)
            session.commit()

        return {
            "sharpe_ratio": result.sharpe_ratio,
            "cagr": result.cagr,
            "max_drawdown": result.max_drawdown,
            "dsr_p_value": result.dsr_p_value,
            "out_of_sample_sharpe": result.out_of_sample_sharpe,
            "passes_rigor_gate": passes,
            "n_bars": len(artifact["results"][0]["metrics"].get("daily_returns", [])),
        }

    try:
        metrics = await asyncio.to_thread(_do_backtest_and_persist)
        if metrics is None:
            return
        await emit.emit(
            "backtest_done",
            strategy_id=strategy_id,
            candidate_id=c.candidate_id,
            metrics=metrics,
        )
    except Exception as exc:
        # Non-fatal — the strategy stays in the Library with the placeholder,
        # which is honest. The generation succeeded; the backtest didn't.
        logger.warning("backtest_and_persist failed for %s: %s", strategy_id, exc)
        await emit.emit(
            "backtest_failed",
            strategy_id=strategy_id,
            candidate_id=c.candidate_id,
            error=str(exc),
        )

"""Streaming strategy generation orchestrator — T1.1 Phase-3 (debate-only).

The **debate society** is the sole generation pipeline. The legacy
fusion/architect/agent decision tree is retired as of Phase-3 (issue #834).

Pipeline lifecycle (SSE stream):

  job_queued
    → brief_validated
    → pipeline_selected (always "debate" on the live path)
    → candidates_selected
    → for each leaderboard entry:
        candidate_drafted → candidate_evaluated
    → best_selected
    → trace_hashed → persisted → done

Dispatch rules:

* **debate** — ``_llm_available()`` AND ``_debate_can_run(brief)`` → runs the
  full ``_run_debate_leaderboard`` path in ``debate_engine``.
* **fixture** — deterministic stub for hermetic tests (``GENERATION_PIPELINE_FIXTURE=1``
  or ``TESTING`` env + no LLM). No LLM call, no network.
* **error** (``GENERATION_UNAVAILABLE``) — prod environment with no reachable LLM
  or an empty corpus. No silent fallback.

K=1 persistence: only the leaderboard winner becomes a ``StrategyRecord``;
alternates are recorded in ``strategy_memory.persist_proposal`` (verdict="rejected")
and surfaced via the job's candidates payload.

See ``docs/specs/generation-streaming-spec.md`` for the full SSE contract.
"""

from __future__ import annotations

import asyncio
import functools
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
from archimedes.services.identity_events import emit_identity_event
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
    # ── T1.1 Phase-3 cutover: the debate society IS the generation pipeline. ──
    # The legacy fusion/architect/agent decision tree is retired; a client-sent
    # mode override is accepted for API compatibility but no longer routes —
    # the society owns candidate generation (spec §Phase-3; #834 flag audit).
    if mode_override and mode_override != "debate":
        logger.info("generation: ignoring legacy mode override %r (debate-only cutover)", mode_override)
    return "debate", "debate society is the generation pipeline (T1.1 Phase-3 cutover)"


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


def _invalid_brief_message(reason: object) -> str:
    """Frame a validator rejection as guidance, never as a bare category label.

    Lives in the ORCHESTRATOR (brief validation runs before pipeline dispatch),
    so the debate-society cutover keeps this path. The validator LLM can return
    a terse ``reason`` like "gibberish" — surfacing that verbatim reads as an
    insult with zero direction (a user typing "test" saw ``Error — gibberish``).
    Keep the honest reason, wrap it in what-to-do-instead.
    """
    raw = str(reason or "it did not describe an investment goal").strip().rstrip(".")
    return (
        f"We couldn't turn that into an investment brief ({raw}). "
        "Describe what you want from a portfolio in a sentence — e.g. "
        '"diversified low-volatility strategy for idle USDC".'
    )


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
    # The society num_trials (#770, N + library_size) this candidate's DSR was
    # first computed with. Recorded so the post-loop correlation patch (#822)
    # recomputes DSR at the SAME trial count — only the average_correlation term
    # changes, never the deflation count itself. 0 on the fixture/fusion paths,
    # which don't run the buy-and-hold DSR patch.
    dsr_num_trials: int = 0
    # Which branch strategy_fusion._spec_universe took to pick asset_universe:
    # "user" | "model" | "full" (#857, follow-up to #847). "full" is the honest
    # default for paths that never went through fusion's universe steering (the
    # buy-and-hold agent/fixture path derives its universe from the user's
    # portfolio weights directly, not from a model suggestion).
    universe_source: str = "full"
    # The validated DSL spec dict (rebalancer decouple, Part A #1 of
    # docs/CURATED-STRATEGY-DECOUPLE-AND-CONSOLIDATE-2026-07-08.md) — the SAME
    # dict ``proposal.strategy_spec`` carries and ``evaluate_fusion_spec``
    # graded. Persisted verbatim onto the StrategyRecord (see
    # ``_persist_candidate``) so the live agent runner can later load and
    # evaluate a deployed generated strategy's OWN spec, not just its weight
    # vector. None on the fixture/buy-and-hold-weights path (there is no DSL
    # spec to carry — it re-derives its universe from ``weights`` instead).
    strategy_spec: dict[str, Any] | None = None


def _is_deployable(c: _CandidateResult) -> bool:
    """Whether a winning candidate is safe to advertise as deployable (issue #937).

    ``passes_rigor`` alone is not enough: a candidate graded on the synthetic
    fallback series (``evaluate_fusion_spec`` defaults to a random walk when no
    ``data_feed`` is wired) can have ``passing=True`` yet is NOT admissible — its
    verdict is not grounded in real data. Require BOTH the rigor pass AND
    real-data grading, the same condition that gates real-returns persistence
    (``_persist_real_returns``): ``has_real_rigor`` and a non-synthetic
    ``data_source``. Otherwise the SSE ``deployable`` flag misrepresents rigor
    status even though the server-side vault gate still fail-closes on deploy.
    """
    if not c.passes_rigor:
        return False
    rv = c.rigor_verdict or {}
    return bool(c.has_real_rigor) and str(rv.get("data_source") or "synthetic") != "synthetic"


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
    average_correlation: float = 0.0,
) -> dict[str, Any]:
    """Run Önder's rigor primitives on a portfolio return series.

    Returns the same shape the fixture path uses so the consumer (event
    emitter + frontend) doesn't care which path produced the verdict.

    ``num_trials`` is the multiple-testing count fed to the Deflated Sharpe
    Ratio — per ``selection-bias-corrections-spec.md`` § 1.3 this is the size
    of the strategy universe the winner was selected from (the curated library),
    NOT 1. With ``num_trials=1`` the DSR expectation-of-max term collapses to 0
    and the ratio is undeflated, which silently defeats the gate.

    ``average_correlation`` is the mean pairwise correlation among the
    ``num_trials`` society candidates (approach B, issue #822). Defaults to
    ``0.0`` — the independent-trials assumption (approach A, #811/#770) — which
    the caller keeps as the fallback whenever a pool-wide correlation can't be
    estimated (fewer than two candidate return series).

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

    dsr, dsr_p = compute_dsr(return_series, num_trials=max(1, num_trials), average_correlation=average_correlation)
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


def _patch_dsr_with_pool_correlation(candidates: list[_CandidateResult]) -> None:
    """Re-deflate each buy-and-hold candidate's DSR using the REAL candidate-pool ρ̄.

    Approach B (#822) for the society DSR multiple-testing correction. #811 (#770)
    set ``average_correlation=0.0`` — i.e. treated the N society candidates as
    mutually independent trials. They're generated from the same user brief over
    overlapping universes, so they're typically strongly correlated (ρ̄ ≈ 0.5–0.9),
    and feeding ρ̄=0 over-deflates ``E[max_N]``, over-stating the gate's strictness.
    Under equicorrelation the effective trial count is
    ``N_eff = N / (1 + (N-1)·ρ̄)`` (Cheverud 2001; Nyholt 2004) — this patch estimates
    the real ρ̄ across the pool's return series (``compute_average_pairwise_correlation``)
    and recomputes DSR at the SAME ``dsr_num_trials`` each candidate was already
    deflated at, so only the correlation term changes.

    Runs AFTER ``_patch_pbo`` and mirrors its shape and scope: fusion/debate
    candidates (``has_real_rigor=True``) carry a real DSR from their own CSCV
    evaluator and are SKIPPED — recomputing over the buy-and-hold pool would
    clobber a correctly-computed value with an unrelated one. With fewer than
    two eligible return series (no correlation estimable), every candidate's DSR
    is left untouched — approach A (ρ̄=0) is the fallback, per the issue.

    A correlated pool can only RELAX the deflation relative to ρ̄=0 (never tighten
    beyond it — ``N_eff <= N`` always), so ``dsr_p_value`` only ever moves up or
    stays the same here, never down. ``passing`` is re-derived from the full set
    of admission legs (DSR, OOS/cliff, look-ahead) rather than AND-ed in, since a
    higher DSR p-value can flip a candidate from failing to passing, not just the
    reverse (unlike the PBO patch above, which can only tighten).
    """
    agent_cands = [c for c in candidates if not c.has_real_rigor]
    series_map: dict[str, list[float]] = {c.candidate_id: c.return_series for c in agent_cands if c.return_series}
    if len(series_map) < 2:
        return  # approach A (ρ̄=0) fallback — nothing to patch, verdicts already reflect it

    from archimedes.services.rigor_evaluator import compute_average_pairwise_correlation, compute_dsr

    avg_correlation = compute_average_pairwise_correlation(series_map)
    if avg_correlation <= 0.0:
        return  # no correlation relief to apply — ρ̄=0 verdicts are already correct

    for c in agent_cands:
        v = c.rigor_verdict
        if v.get("dsr") is None or not c.return_series or c.dsr_num_trials < 1:
            continue  # too-short series or never ran the buy-and-hold DSR — nothing to recompute
        dsr, dsr_p = compute_dsr(c.return_series, num_trials=c.dsr_num_trials, average_correlation=avg_correlation)
        if dsr is None or dsr_p is None:
            continue
        v["dsr"] = round(float(dsr), 4)
        v["dsr_p_value"] = round(float(dsr_p), 4)
        oos = v.get("oos_sharpe")
        in_sample = v.get("in_sample_sharpe")
        oos_pass = oos is not None and oos > 0.0
        if oos_pass and in_sample is not None and math.isfinite(in_sample) and in_sample > 0 and oos / in_sample < 0.5:
            oos_pass = False
        # PBO's own leg is folded into `passing` by `_patch_pbo` already; re-derive
        # from the ORIGINAL passing value's PBO contribution by requiring it again
        # here (pbo < 0.5, undefined-PBO / N<2 patched to 0.0 which always passes).
        pbo = v.get("pbo")
        pbo_pass = pbo is None or pbo < 0.5
        v["passing"] = bool(dsr_p >= 0.95 and oos_pass and v.get("lookahead_audit_passed", False) and pbo_pass)


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
    agent: Any = None,  # noqa: ARG001 — signature parity; fixture path ignores it
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


def _society_num_trials(library_size: int, selection_pool_size: int) -> int:
    """Effective DSR multiple-testing trial count on the agentic society path (#770).

    The winner survived selection from ``selection_pool_size`` (N) generated candidates
    AND is promoted into a library of ``library_size`` — two independent selection layers,
    so the deflation count is their SUM (approach A, Bailey & López de Prado 2014). See
    ``docs/specs/selection-bias-corrections-spec.md`` § 1.3 addendum. Floored at 1.
    """
    return max(1, library_size + selection_pool_size)


class FusionUnavailable(Exception):
    """The debate society could not produce a conformant candidate for this brief.

    Raised by ``debate_engine.DebateUnavailable`` (a subclass) when no proposal
    survived the critics. ``run_generation`` catches it and emits
    ``GENERATION_UNAVAILABLE`` — no silent fallback (T1.1 Phase-3).
    """


# ── Pipeline entry point ──────────────────────────────────────────────────


def _resolve_name(brief: GenerateBrief, default: str) -> str:
    """Prefer the user's ``brief.name`` over an auto-derived default.

    Single choke point for user-chosen strategy names: every runner
    (fixture / debate) auto-derives a ``strategy_name``,
    and ``run_generation`` applies the user's name to the WINNER only — right
    before persistence, so the library record, passport, episodic memory, and
    job result all carry it. ``brief.name`` is already sanitized (stripped,
    whitespace-collapsed, 1–80 chars) by the ``GenerateBrief`` validator.
    """
    return brief.name or default


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
    owner_wallet: str | None = None,
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

    ``owner_wallet`` is the SIWE-derived wallet from the job payload (bound
    server-side by ``gate_generation`` — never client-supplied). It is stamped
    onto every persisted candidate so generated strategies are per-user and
    private-until-published. ``None`` (anonymous / flag off) persists an
    ownerless row, preserving today's behavior.

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
                message=_invalid_brief_message(validated.get("reason")),
                hint=validated.get("hint") or "Mention an asset class, a goal, or a risk appetite.",
                recoverable=True,
                code="BRIEF_INVALID",
            )
            await store.update_status(job_id, "error", error="brief invalid")
            return

        # Honor any risk_appetite_adjusted from the validator (e.g. the user
        # said "conservative" but described 100x leverage on memecoins).
        if validated.get("risk_appetite_adjusted") and validated["risk_appetite_adjusted"] != brief.risk_appetite:
            brief = brief.model_copy(update={"risk_appetite": validated["risk_appetite_adjusted"]})

        # Fold the validator's classified asset_classes back into the brief
        # (#892). Bug: brief_validated correctly classified e.g.
        # ["crypto", "treasuries"] and emitted it on the SSE event for display,
        # but the classification was then discarded — brief.asset_classes kept
        # whatever the client originally sent (often empty, or only the
        # explicit ticker picks), so the debate society / fusion universe
        # steering below never saw the LLM's better read of the brief. Union
        # (not replace) so an explicit user ticker pick is never dropped by a
        # coarser class inference.
        # The validator's output is LLM-generated and not type-enforced, so
        # `asset_classes_inferred` could come back as something other than a
        # list (e.g. a bare string) — guard against that before iterating, or
        # a string would silently explode into a "class per character" and
        # pollute brief.asset_classes downstream (Copilot review comment on
        # PR #1033).
        _raw_inferred = validated.get("asset_classes_inferred", [])
        if not isinstance(_raw_inferred, list):
            _raw_inferred = [_raw_inferred] if _raw_inferred else []
        inferred_classes = [str(a).strip() for a in _raw_inferred if str(a).strip()]
        if inferred_classes:
            merged_classes = list(brief.asset_classes or [])
            seen_classes = {c.strip().lower() for c in merged_classes}
            for ac in inferred_classes:
                if ac.lower() not in seen_classes:
                    seen_classes.add(ac.lower())
                    merged_classes.append(ac)
            if merged_classes != (brief.asset_classes or []):
                brief = brief.model_copy(update={"asset_classes": merged_classes})

        await emit.emit(
            "brief_validated",
            asset_classes=validated.get("asset_classes_inferred", []),
            risk_appetite=brief.risk_appetite,
            intent_summary=validated.get("intent_summary", ""),
            time_horizon_inferred=validated.get("time_horizon_inferred", "unknown"),
        )

        # ── Auto-route to the best pipeline ──
        pipeline_name, pipeline_reason = _pick_pipeline(brief, mode_override=mode)

        # ── T1.1 Phase-3 dispatch: debate is THE runner. ──
        # No silent fallback to the retired single-agent paths: if the society
        # cannot run, the job errors HONESTLY. The deterministic fixture runner
        # survives strictly for hermetic tests (TESTING / explicit fixture env).
        from archimedes.agents.debate_engine import _debate_can_run, _run_debate_leaderboard

        use_live = _llm_available()
        fixture_mode = os.getenv("GENERATION_PIPELINE_FIXTURE", "").lower() in ("1", "true") or (
            not use_live and os.getenv("TESTING")
        )
        if use_live and await asyncio.to_thread(_debate_can_run, brief):
            runner: Callable[..., Awaitable[Any]] = functools.partial(_run_debate_leaderboard, model=model)
        elif fixture_mode:
            pipeline_name = "fixture"
            pipeline_reason = "deterministic fixture runner (tests only — no LLM in the environment)"
            runner = _run_fixture_candidate
        else:
            reason = (
                "no LLM backend reachable"
                if not use_live
                else "the corpus yielded <2 papers for this steer — the society cannot fuse"
            )
            await emit.emit(
                "error",
                message=f"Generation is unavailable right now: {reason}.",
                recoverable=True,
                code="GENERATION_UNAVAILABLE",
            )
            await store.update_status(job_id, "error", error=f"generation unavailable: {reason}")
            return

        # Regime plan AFTER the runner is final: the society owns its own
        # regime/mechanism split internally (spec §5b) → a single "neutral"
        # pass; the fixture path keeps the legacy dual-regime shape for tests.
        if pipeline_name == "debate":
            regimes: list[str] = ["neutral"]
        elif dual_regime:
            regimes = ["bull", "bear"]
        else:
            regimes = ["neutral"] * n_candidates

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
        # NOTE: `runner` is resolved ABOVE in the dispatch block. Do NOT redefine it here.
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
                # T1.1 Phase-3: the society declined at runtime (empty pool /
                # nothing conformant). There is NO fallback pipeline anymore —
                # surface the honest first-class ABSTAIN and let the
                # NO_CANDIDATES path below report it if nothing else lands.
                logger.info("debate society abstained for %s (%s): %s", candidate_id, regime, exc)
                await emit.emit(
                    "candidate_failed",
                    candidate_id=candidate_id,
                    regime=regime,
                    error=str(exc),
                    message="The debate society abstained — no proposal survived the critics for this brief.",
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

            # Phase-3 fan-out: the debate runner returns the FULL ranked
            # leaderboard (leader first); legacy/fixture runners return one
            # candidate. Every entry is surfaced on the stream — the tail IS
            # the Considered-Alternatives panel's content.
            entries = cand if isinstance(cand, list) else [cand]
            for entry in entries:
                await emit.emit(
                    "candidate_drafted",
                    candidate_id=entry.candidate_id,
                    strategy_name=entry.strategy_name,
                    weights_preview=entry.weights,
                    regime=regime,
                )
                await emit.emit(
                    "candidate_evaluated",
                    candidate_id=entry.candidate_id,
                    rigor_verdict=entry.rigor_verdict,
                    regime=regime,
                )
            candidates.extend(entries)

        if not candidates:
            # Honest code + message (#818): this branch fires only when ZERO candidates
            # were generated (an upstream generation failure) — distinct from "candidates
            # exist but none passed rigor", which is NOT an error but is surfaced below via
            # best_selected's deployable=False (ABSTAIN) signal. Using RIGOR_FAIL here would
            # mislead clients/telemetry into reading a generation failure as a rigor-gate
            # failure, so this carries its own NO_CANDIDATES code.
            await emit.emit(
                "error",
                message="no candidates generated",
                recoverable=True,
                code="NO_CANDIDATES",
            )
            await store.update_status(job_id, "error", error="no candidates generated")
            return

        # Patch PBO across the candidate set (library-level metric — Bailey
        # et al. CSCV needs N≥2 to be meaningful; the helper handles N<2
        # gracefully by setting PBO=0.0). After this, every candidate's
        # rigor_verdict has all four fields (DSR, PBO, OOS Sharpe, lookahead).
        _patch_pbo(candidates)
        # Re-deflate DSR using the REAL candidate-pool correlation (#822, approach B
        # for #770/#811). Runs after PBO so it can re-derive `passing`'s PBO leg from
        # the already-patched value. Falls back to the untouched ρ̄=0 verdicts (approach
        # A) whenever fewer than two buy-and-hold return series are available.
        _patch_dsr_with_pool_correlation(candidates)
        for c in candidates:
            c.passes_rigor = c.rigor_verdict.get("passing", False)

        # Selection: prefer rigor-passing candidates; fall back to the full set only to
        # still surface a "considered best" for the alternatives panel. Whether that best
        # is DEPLOYABLE is a separate, honest signal (#818): a candidate that failed the
        # gate is persisted with passes_rigor_gate=False and the server-side vault gate
        # refuses to deploy it — we must not imply it is validated.
        validated = [c for c in candidates if c.passes_rigor]
        pool = validated or candidates
        if pipeline_name == "debate":
            # The society's deterministic synthesizer already ranked the board
            # (composite of rigor + null-margin + regime fit) — re-ranking by
            # raw DSR here would silently override the debate outcome. Take the
            # first pool member in board order.
            best = next(c for c in candidates if c in pool)
        else:
            best = max(
                pool,
                key=lambda c: c.rigor_verdict.get("dsr") or 0.0,
            )
        # User-chosen name (brief.name) applies to the WINNER only, and must land
        # BEFORE the persist loop below so every downstream surface (library
        # record, passport, episodic memory, job result) reads it. Considered-
        # rejects keep their auto-derived names — renaming them too would produce
        # multiple identically-named strategies per generation.
        best.strategy_name = _resolve_name(brief, best.strategy_name)
        await emit.emit(
            "best_selected",
            best_candidate_id=best.candidate_id,
            considered_count=len(candidates),
            validated_count=len(validated),
            # deployable=False ⇒ the surfaced best is a considered alternative (ABSTAIN),
            # not a validated, deployable winner. This requires BOTH a rigor pass AND
            # real-data grading — a synthetic-data-graded candidate can pass the gate but
            # is not admissible, so it must not be advertised deployable (issue #937).
            deployable=_is_deployable(best),
        )

        # K=1 persistence (Phase-3): only the WINNER becomes a library
        # strategy (+passport +trace). Considered alternatives are recorded in
        # the episodic strategy_proposals store (content-hashed, with their
        # rigor verdicts) and surfaced via the job's candidates payload — they
        # must NOT accumulate as rejected StrategyRecords (the orphan pattern
        # the ownership purge removed).
        strategy_ids: dict[str, str] = {}  # candidate_id → strategy_id
        sid, thash = await _persist_candidate(best, brief, owner_wallet=owner_wallet)
        strategy_ids[best.candidate_id] = sid
        await emit.emit(
            "trace_hashed",
            trace_hash=thash,
            candidate_id=best.candidate_id,
            regime=best.regime,
        )
        await emit.emit(
            "persisted",
            strategy_id=sid,
            candidate_id=best.candidate_id,
            regime=best.regime,
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
        # Debate candidates ("debate"/"debate_abstain") are the same shape — they
        # emit a DSL spec (weights={}) scored by evaluate_fusion_spec, or are a
        # populated ABSTAIN — so they skip the static backtester too (T1.1).
        _static_skip = ("fusion", "debate", "debate_abstain")
        await asyncio.gather(
            *[
                # num_trials = N candidates + library context (#770), not library alone.
                _backtest_and_persist(
                    c, strategy_ids[c.candidate_id], emit, _society_num_trials(library_size, n_candidates)
                )
                for c in candidates
                if c.generation_method not in _static_skip and c.candidate_id in strategy_ids
            ]
        )

        # Fusion (and debate) candidates skipped the static backtester above but carry
        # a REAL DSL backtest — persist its returns so live_rigor_gate reads pass/fail
        # (not "pending") and the strategy is deployable (#788/#818). Without this, a
        # fusion winner with a genuine backtest forever reads rigor_gate_status=pending
        # and the server-side create_vault gate (#829) refuses to deploy it.
        await asyncio.gather(
            *[
                _persist_real_returns(
                    c, strategy_ids[c.candidate_id], emit, _society_num_trials(library_size, n_candidates)
                )
                for c in candidates
                if c.has_real_rigor and c.return_series and c.candidate_id in strategy_ids
            ]
        )

        # ── Persist all candidates to episodic memory (T-PE.8) ──
        try:
            from archimedes.services.strategy_memory import persist_proposal

            for cand in candidates:
                persist_proposal(
                    generation_id=job_id,
                    # "debate" for society candidates, "fusion" for fused, "agent"
                    # otherwise — the episodic record reflects which engine produced
                    # the proposal rather than always claiming "agent".
                    agent=(
                        "debate"
                        if cand.generation_method in ("debate", "debate_abstain")
                        else "fusion"
                        if cand.generation_method == "fusion"
                        else "agent"
                    ),
                    intent=brief.intent,
                    strategy_spec={
                        "strategy_name": cand.strategy_name,
                        "thesis": cand.thesis,
                        "weights": cand.weights,
                        "asset_universe": cand.asset_universe,
                    },
                    papers=[aid for p in cand.source_papers if (aid := p.get("arxiv_id", ""))]
                    or list(cand.source_arxiv_ids),
                    rigor_verdict=cand.rigor_verdict,
                    # K=1 (Phase-3): this loop is the SINGLE episodic writer for the
                    # whole board — winner marked selected, alternates carry the
                    # honest reject reason. (A second per-alternate writer here
                    # produced 2N-1 rows per run; verify finding, removed.)
                    verdict="selected" if cand is best else "rejected",
                    regime_tag=cand.regime,
                    extra={
                        "candidate_id": cand.candidate_id,
                        "selected": cand is best,
                        "strategy_name": cand.strategy_name,
                        "reject_reason": None
                        if cand is best
                        else (cand.rigor_verdict or {}).get("reason") or "outranked by the society leader",
                    },
                    owner_wallet=owner_wallet,
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
                        "generation_method": c.generation_method,
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

        # Identity ledger (#1028, D2): only an IDENTIFIED run is ledgered — an
        # anonymous generation (owner_wallet=None) has nothing to anchor a row
        # to (D2a: pre-auth stays anonymous). Fail-safe; never affects the job.
        emit_identity_event(
            wallet=owner_wallet,
            event_type="generation_completed",
            actor_class="human",
            meta={
                "job_id": job_id,
                "best_candidate_id": best.candidate_id,
                "best_strategy_id": strategy_id,
                "served_model": served_model,
            },
        )

    except asyncio.CancelledError:
        await emit.emit("error", message="job cancelled", recoverable=False, code="CANCELLED")
        await store.update_status(job_id, "cancelled", error="cancelled by client")
        raise
    except Exception as exc:
        logger.exception("generation pipeline crashed: %s", exc)
        await emit.emit("error", message=str(exc), recoverable=False, code="PIPELINE_CRASH")
        await store.update_status(job_id, "error", error=str(exc))


async def _persist_candidate(
    c: _CandidateResult, brief: GenerateBrief, *, owner_wallet: str | None = None
) -> tuple[str, str]:
    """Upsert the candidate as a Strategy + return (strategy_id, trace_hash).

    Trace hash is the keccak of the canonical (brief, candidate) tuple — gives
    every generation a deterministic identifier mirrored on-chain in v1.5.

    ``owner_wallet`` (SIWE-derived, threaded from run_generation) is stamped on
    both the strategy_store row and its strategy_passports mirror.
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
                owner_wallet=owner_wallet,
                # Rebalancer decouple (Part A #1): persist the candidate's own
                # validated DSL spec so a vault later deployed from this
                # strategy can be autonomously rebalanced by the agent runner.
                # None on the fixture/buy-and-hold path — nothing to persist.
                strategy_spec=c.strategy_spec,
            )
            # Stamp the generating wallet so wallet_can_publish() returns True
            # for this wallet/strategy pair (D5 publish gate).
            if owner_wallet:
                from archimedes.models.strategy_generators import record_generator

                record_generator(session, strategy_id=record.id, wallet_address=owner_wallet)
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
                    universe_source=c.universe_source,
                    status=StrategyStatus(record.status) if record.status else StrategyStatus.CANDIDATE,
                    regime_tag=_regime_tag,
                    passes_rigor_gate=bool(c.rigor_verdict.get("passing", False)) if c.rigor_verdict else False,
                    deflated_sharpe_ratio=c.rigor_verdict.get("dsr") if c.rigor_verdict else None,
                    # dsr_p_value was missing from the initial passport persist (#passport-honesty):
                    # the rigor verdict carries it under "dsr_p_value" but earlier code only wrote
                    # "dsr", "pbo", and "oos_sharpe" — leaving the passport column NULL even when
                    # the generation leaderboard had the correct value.
                    dsr_p_value=c.rigor_verdict.get("dsr_p_value") if c.rigor_verdict else None,
                    pbo_score=c.rigor_verdict.get("pbo") if c.rigor_verdict else None,
                    out_of_sample_sharpe=c.rigor_verdict.get("oos_sharpe") if c.rigor_verdict else None,
                )
                with get_session() as sess2:
                    ingest_passport(
                        sess2,
                        passport,
                        generation_method=c.generation_method,
                        force_update=True,
                        owner_wallet=owner_wallet,
                    )
                    sess2.commit()
            except Exception as exc:
                logger.warning("unified passport persist failed (non-blocking): %s", exc)

            return record.id

    strategy_id = await asyncio.to_thread(_do_persist)
    return strategy_id, trace_hash


def _refresh_passport_real_metrics(
    session: Any, c: _CandidateResult, strategy_id: str, result: Any, *, passes_rigor_gate: bool, n_obs: int
) -> None:
    """Refresh the strategy_passports ``real_*`` columns + ``passes_rigor_gate`` for a
    fusion/debate candidate whose real returns were just persisted.

    The single-strategy read path (``_passport_to_strategy_response``) derives
    ``rigor_gate_status`` from the STORED passport columns (``pending`` while
    ``sharpe_ratio is None``) and the deploy gate (``_strategy_rigor_status``) reads
    ``record.passes_rigor_gate`` — neither re-grades the ``backtest_results`` row. So
    persisting the row alone leaves both at ``pending``; this in-place passport update
    is what makes the endpoint + deploy gate see the real verdict. Mirrors the passport
    refresh in ``_backtest_and_persist``. ``passes_rigor_gate`` is the live-gate re-grade
    of the real returns (single source of truth).
    """
    from datetime import date as _date

    from archimedes.models.paper_ref import PaperRef
    from archimedes.models.strategy import StrategyPassport, StrategyStatus
    from archimedes.models.strategy_store import StrategyRecord
    from archimedes.services.passport_loader import ingest_passport

    record = session.query(StrategyRecord).filter_by(id=strategy_id).first()
    status_val = StrategyStatus(record.status) if record and record.status else StrategyStatus.CANDIDATE
    papers = [PaperRef(arxiv_id=p.get("arxiv_id"), title=p.get("title", "")) for p in (c.source_papers or [])]
    regime_tag = {"bull": "bull", "bear": "bear"}.get(c.regime, "regime_neutral")
    passport = StrategyPassport(
        id=strategy_id,
        papers=papers,
        methodology_summary=c.thesis or "",
        asset_universe=c.asset_universe or [],
        universe_source=c.universe_source,
        status=status_val,
        regime_tag=regime_tag,
        real_sharpe=result.sharpe_ratio,
        real_sortino=result.sortino_ratio,
        real_cagr=result.cagr,
        real_max_dd=result.max_drawdown,
        real_calmar=result.calmar_ratio,
        real_corr_spy=result.correlation_to_spy,
        real_total_trades=result.total_trades,
        real_backtest_start=(result.backtest_start.isoformat() if isinstance(result.backtest_start, _date) else None),
        real_backtest_end=(result.backtest_end.isoformat() if isinstance(result.backtest_end, _date) else None),
        deflated_sharpe_ratio=result.deflated_sharpe_ratio,
        dsr_p_value=result.dsr_p_value,
        num_trials_in_selection=result.num_trials_in_selection,
        pbo_score=result.pbo_score,
        out_of_sample_sharpe=result.out_of_sample_sharpe,
        passes_rigor_gate=passes_rigor_gate,
        n_obs_daily=n_obs,
    )
    ingest_passport(session, passport, generation_method=c.generation_method, force_update=True)


async def _persist_real_returns(c: _CandidateResult, strategy_id: str, emit: _Emitter, num_trials: int) -> None:
    """Persist a has_real_rigor candidate's real DSL-backtest returns so the live
    rigor gate reads ``pass``/``fail`` — not ``pending`` — and the strategy becomes
    deployable (#788/#818).

    Fusion/debate candidates emit a DSL spec (``weights={}``) scored by
    ``evaluate_fusion_spec``, so they skip the static buy-and-hold backtester (#829)
    and would otherwise leave NO ``backtest_results`` row → ``live_rigor_gate`` reads
    ``pending`` → the strategy is never deployable even though it carries a real
    backtest. This writes the row from the captured return series so the live gate
    has real returns to re-grade, and refreshes the passport ``real_*`` columns +
    ``passes_rigor_gate`` (which is what the single-strategy read path + the deploy
    gate actually consume). Non-blocking: any failure is logged + emitted, not raised.

    **Claim-integrity gate:** persists ONLY when the backtest ran on REAL data
    (``data_source != "synthetic"``). ``evaluate_fusion_spec`` defaults to a random
    synthetic series when no ``data_feed`` is wired, and grading a random walk as a
    real pass/fail would be a #1-rule violation — so a synthetic run stays honestly
    ``pending`` (never persisted, never deployable). Real-data wiring is tracked
    separately; until then fusion candidates correctly read ``pending``.
    """
    rv = c.rigor_verdict or {}
    if not c.has_real_rigor or not c.return_series or len(c.return_series) < 10:
        return
    if str(rv.get("data_source") or "synthetic") == "synthetic":
        # Synthetic backtest — grading it as real would be a claims-integrity violation.
        logger.info("persist_real_returns: %s ran on synthetic data — staying honestly 'pending'", strategy_id)
        return

    def _do() -> None:
        import json as _json

        from archimedes.db import get_session
        from archimedes.models.backtest import BacktestResult
        from archimedes.services.backtest_repository import insert_backtest_if_missing
        from archimedes.services.live_rigor_gate import verdict_from_returns

        returns = list(c.return_series)
        # Rebuild an equity curve (base 1.0) so BOTH the artifact daily_returns AND the
        # equity-curve fallback in get_daily_returns resolve the same series.
        equity = [1.0]
        for ret in returns:
            equity.append(equity[-1] * (1.0 + ret))

        def _f(key: str) -> float:
            v = rv.get(key)
            return float(v) if v is not None else 0.0

        def _of(key: str) -> float | None:
            v = rv.get(key)
            return float(v) if v is not None else None

        result = BacktestResult(
            strategy_id=strategy_id,
            sharpe_ratio=_f("sharpe_ratio"),
            sortino_ratio=_f("sortino_ratio"),
            max_drawdown=_f("max_drawdown"),
            cagr=_f("cagr"),
            calmar_ratio=_f("calmar_ratio"),
            win_rate=_f("win_rate"),
            profit_factor=0.0,
            total_trades=int(rv.get("total_trades") or 0),
            avg_holding_period_days=0.0,
            correlation_to_spy=0.0,
            correlation_to_btc=0.0,
            equity_curve=equity,
            deflated_sharpe_ratio=_of("dsr"),
            dsr_p_value=_of("dsr_p_value"),
            num_trials_in_selection=int(num_trials),
            pbo_score=_of("pbo"),
            out_of_sample_sharpe=_of("oos_sharpe"),
            look_ahead_audit_passed=bool(rv.get("lookahead_audit_passed", False)),
            backtest_engine="dsl-fusion",
        )
        artifact = {
            "results": [{"metrics": {"daily_returns": returns}}],
            "source": c.generation_method,
            "data_source": rv.get("data_source"),
        }
        artifact_json = _json.dumps(artifact, default=str)
        content_hash = hashlib.sha256(artifact_json.encode("utf-8")).hexdigest()
        # The live gate is the single source of truth: re-grade the REAL returns so the
        # persisted passes_rigor_gate matches what verdicts_for_strategies computes.
        # look_ahead_audit_passed threads the closed-DSL self-attestation computed
        # above (result.look_ahead_audit_passed, from rv["lookahead_audit_passed"])
        # through to the gate. Without this, strategy_code=None (this path has no
        # inspectable source for the AST audit) left the gate's look_ahead_passed
        # unconditionally False — an always-on floor failure that blocked deploy at
        # EVERY strictness level regardless of DSR/PBO/OOS, no matter how strong the
        # strategy. See live_rigor_gate.verdict_from_returns's docstring for why this
        # matches the accepted DSL self-attestation policy (fusion_evaluator.py).
        live = verdict_from_returns(
            strategy_id,
            returns,
            num_trials=int(num_trials),
            look_ahead_audit_passed=result.look_ahead_audit_passed,
        )

        with get_session() as session:
            insert_backtest_if_missing(
                session,
                strategy_id=strategy_id,
                content_hash=content_hash,
                result=result,
                operation="DSL_FUSION",
                artifact_json=artifact_json,
            )
            _refresh_passport_real_metrics(
                session, c, strategy_id, result, passes_rigor_gate=live.passes, n_obs=len(returns)
            )
            # WITHOUT this commit the flushed rows roll back on close → gate stays "pending"
            # (the sibling _backtest_and_persist commits too). Empirically: flush-only = 0 rows.
            session.commit()

    try:
        await asyncio.to_thread(_do)
        await emit.emit(
            "backtest_done", candidate_id=c.candidate_id, strategy_id=strategy_id, source=c.generation_method
        )
    except Exception as exc:
        logger.warning("persist_real_returns failed for %s: %s", strategy_id, exc)
        await emit.emit("backtest_failed", candidate_id=c.candidate_id, strategy_id=strategy_id, error=str(exc))


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

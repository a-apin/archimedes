"""LLM-driven portfolio agent.

The rule-based ``StrategySignalEvaluator`` produces per-(strategy × asset)
signals against a hard-coded universe.  This agent layer takes those
signals (plus a global market scan) and asks an LLM to construct the
final portfolio — picking specific instruments (including *individual*
stocks and bonds, not just ETFs), justifying each pick, and anchoring
every position to one of our paper-grounded strategies (the "strategy
passport" model from CLAUDE.md § "Architectural primitives").

If no LLM backend is available, the caller should fall back to the
rule-based aggregation in ``api/routes.py``.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from archimedes.services.llm_backend import LLMBackend, make_llm_backend
from archimedes.services.strategy_signal_evaluator import (
    GLOBAL_ASSETS,
    synth_display,
)
from archimedes.services.stress_engine import SCENARIOS, stress_one

logger = logging.getLogger(__name__)


@dataclass
class AgentPick:
    """A single agent-chosen position."""

    ticker: str  # display symbol (e.g. "NVDA", "USD/TRY", "BIST100")
    synth: str  # internal synth code (e.g. "sNVDA")
    asset_class: str  # us_stock, us_bond_long, fx, crypto, ...
    exchange: str
    weight: float  # 0 - 1, fraction of the *synth budget* (not total)
    paper_anchor: str  # which paper-strategy supports this pick
    reasoning: str  # one-sentence justification


@dataclass
class AgentToolCall:
    """A tool call the agent made during portfolio construction."""

    tool: str
    inputs: dict
    output_summary: str


@dataclass
class AgentPortfolio:
    """Full agent output: thesis + picks + tool-call trace."""

    thesis: str
    picks: list[AgentPick]
    model_id: str
    served_model: str
    tool_calls: list[AgentToolCall] | None = None
    iterations: int = 1


# ── Response cache ─────────────────────────────────────────────────
# Avoid hammering the LLM for every advisor request.
_RESPONSE_CACHE: dict[str, tuple[AgentPortfolio, float]] = {}
_CACHE_TTL_SEC = 300  # 5 minutes


def _cache_key(regime: str, risk_profile: str, top_synths: tuple[str, ...]) -> str:
    return f"{regime}|{risk_profile}|{','.join(sorted(top_synths))}"


def _build_system_prompt() -> str:
    return (
        "You are Archimedes, an autonomous portfolio-construction agent for a "
        "non-custodial USDC-settled vault on Arc.\n\n"
        "Your responsibility: pick a diversified portfolio of *individual* tradable "
        "instruments (individual stocks, bonds, futures, FX, crypto — not just "
        "broad ETFs unless they are the best vehicle for a thesis). Every pick "
        "MUST be anchored to one of the paper-grounded quant strategies in our "
        "library (the 'strategy passport' model). You may NOT invent strategies — "
        "anchor only to the ones provided in the user prompt.\n\n"
        "PRINCIPLES\n"
        "- Diversify by asset class AND by exchange (US, European, Asian, Turkish, "
        "  metals/futures, FX, crypto).\n"
        "- Prefer individual stocks where you have a specific thesis (e.g. NVDA, ASML "
        "  for AI capex; THYAO, KCHOL for Turkish play; XOM, CVX for energy).\n"
        "- Use individual bond ETFs by maturity (BIL=t-bills, SHY=1-3y, IEF=7-10y, "
        "  TLT=20y+, TIP=inflation-linked) rather than only aggregate funds.\n"
        "- Respect the synth-budget cap. The remainder is held as USDC (the safety "
        "  floor) which the user already knows about — you don't list USDC.\n"
        "- No single pick > 20% of the synth budget.\n"
        "- Pick 5-12 instruments total.\n\n"
        "OUTPUT FORMAT\n"
        "Return ONLY a JSON object, nothing else (no prose before or after). Schema:\n"
        "{\n"
        '  "thesis": "1-2 sentence portfolio thesis tying regime + risk profile to picks",\n'
        '  "picks": [\n'
        '    {"ticker": "NVDA", "weight": 0.12, "paper_anchor": "moskowitz_2012_tsmom",\n'
        '     "reasoning": "12m return +75%, qualifies for TSMOM long; AI capex cycle"},\n'
        "    ...\n"
        "  ]\n"
        "}\n\n"
        "`ticker` MUST be the display symbol shown in the AVAILABLE UNIVERSE table. "
        "Weights are fractions of the synth budget (will be renormalized if needed). "
        "`paper_anchor` MUST be one of the strategy ids listed below."
    )


def _format_universe(scan_universe_synths: set[str]) -> str:
    """Render the available-universe table by asset class."""
    by_class: dict[str, list[tuple[str, str, str]]] = {}
    for synth, (_yf, display, asset_class, exchange) in GLOBAL_ASSETS.items():
        by_class.setdefault(asset_class, []).append((synth, display, exchange))

    parts: list[str] = []
    for asset_class in sorted(by_class):
        rows = by_class[asset_class]
        names = ", ".join(f"{d}" + ("*" if s in scan_universe_synths else "") for s, d, _e in sorted(rows))
        parts.append(f"  - {asset_class:20s}: {names}")
    return "\n".join(parts)


def _format_market_scan(market_ranking: list[dict]) -> str:
    """Format the top-ranked market opportunities."""
    if not market_ranking:
        return "  (none — live data unavailable)"
    lines: list[str] = []
    for r in market_ranking:
        lines.append(
            f"  {r['display']:12s} {r['asset_class']:18s} "
            f"score={r['score']:+6.2f}  90d_mom={r['momentum_90d'] * 100:+6.1f}%  "
            f"vol={r['vol_ann'] * 100:5.1f}%  [{r['exchange']}]"
        )
    return "\n".join(lines)


def _format_strategies(strategies: list[Any]) -> str:
    """Render paper strategies with their backtest stats + signal rules."""
    rule_summary = {
        "faber": "long when price > 200-day SMA, else flat",
        "moreira": "scale exposure by target_vol / realized_vol (vol-managed)",
        "moskowitz": "long if trailing 12m return > 0 (time-series momentum)",
        "george_hwang": "long when within 5% of 52-week high",
        "capital_preservation": "always long the asset (T-bill proxy)",
        "buy_hold": "always long the asset",
    }

    def _summarize_rule(strategy: Any) -> str:
        path = (strategy.strategy_code_path or "").lower()
        title = (strategy.paper_title or "").lower()
        for key, rule in rule_summary.items():
            if key in path or key in title:
                return rule
        return "see paper"

    lines: list[str] = []
    for s in strategies:
        sr = s.real_sharpe if s.real_sharpe is not None else float("nan")
        cagr = (s.real_cagr or 0.0) * 100
        rule = _summarize_rule(s)
        lines.append(
            f"  - id={s.id[:8]}  title={s.paper_title}\n"
            f"      sharpe={sr:.2f}  cagr={cagr:+.1f}%  rigor={'PASS' if s.passes_rigor_gate else 'candidate'}\n"
            f"      signal rule: {rule}"
        )
    return "\n".join(lines)


def _build_user_prompt(
    regime: str,
    regime_confidence: float,
    risk_profile: str,
    usdc_floor: float,
    synth_budget: float,
    market_ranking: list[dict],
    strategies: list[Any],
    scan_universe_synths: set[str],
) -> str:
    return (
        f"## CONTEXT\n"
        f"- regime: {regime} (confidence {regime_confidence:.0%})\n"
        f"- risk_profile: {risk_profile}\n"
        f"- usdc_floor: {usdc_floor:.0%} (held as USDC, you do not allocate this)\n"
        f"- synth_budget: {synth_budget:.0%} (your weights must sum to <= this)\n\n"
        f"## TOP MARKET OPPORTUNITIES (live 90-day risk-adjusted ranking)\n"
        f"{_format_market_scan(market_ranking)}\n\n"
        f"## PAPER STRATEGIES (you must anchor every pick to one of these ids)\n"
        f"{_format_strategies(strategies)}\n\n"
        f"## AVAILABLE UNIVERSE (pick any of these tickers; * = appeared in top scan)\n"
        f"{_format_universe(scan_universe_synths)}\n\n"
        f"## YOUR TASK\n"
        f"Construct the portfolio. Return ONLY JSON per the schema in the system prompt."
    )


# ── Parsing helpers ────────────────────────────────────────────────


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of an LLM response."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Greedy {...} match as last resort
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _resolve_ticker(ticker: str) -> tuple[str, tuple[str, str, str, str]] | None:
    """Look up an LLM-provided ticker in GLOBAL_ASSETS.

    The LLM is told to use display symbols; this also accepts synth
    codes (sNVDA) and yfinance tickers (NVDA, BRK-B, XU100.IS) as
    fallbacks.
    """
    if ticker in GLOBAL_ASSETS:
        return ticker, GLOBAL_ASSETS[ticker]
    for synth, entry in GLOBAL_ASSETS.items():
        yf_t, display, _ac, _ex = entry
        if ticker in (display, yf_t):
            return synth, entry
    norm = ticker.upper().replace(" ", "")
    for synth, entry in GLOBAL_ASSETS.items():
        yf_t, display, _ac, _ex = entry
        if norm in (display.upper(), yf_t.upper()):
            return synth, entry
    return None


# ── PortfolioAgent ─────────────────────────────────────────────────


class PortfolioAgent:
    """LLM-driven portfolio constructor.

    Construct once at process startup; the held LLM backend caches its
    own client.  Method ``propose_portfolio`` is sync but the caller
    runs it in a thread (the LLM call blocks on the network).
    """

    def __init__(self, backend: LLMBackend | None = None) -> None:
        self._backend: LLMBackend = backend or make_llm_backend()

    @property
    def available(self) -> bool:
        return getattr(self._backend, "available", False)

    @property
    def model_id(self) -> str:
        return getattr(self._backend, "model_id", "unknown")

    def _anthropic_client(self):
        """Pull the underlying anthropic.Anthropic out of the backend if possible.

        Tool-use requires the raw SDK — the LLMBackend.complete() seam only
        does text in/out.  Returns None for non-Anthropic backends.
        """
        client = getattr(self._backend, "_client", None)
        if client is None:
            return None
        # Duck-type check: AnthropicBackend / AnthropicCompatibleBackend
        if not hasattr(client, "messages"):
            return None
        return client

    def propose_portfolio_with_tools(
        self,
        regime: str,
        regime_confidence: float,
        risk_profile: str,
        usdc_floor: float,
        synth_budget: float,
        market_ranking: list[dict],
        strategies: list,
        scan_universe_synths: set[str],
        price_histories: dict,
    ) -> AgentPortfolio | None:
        """Multi-turn tool-use portfolio construction.

        The agent gets tools for asset stats, correlation, and stress
        testing.  It iterates up to ``MAX_AGENT_ITERATIONS`` times, then
        must finalize via ``propose_portfolio``.  Falls back to None if
        the underlying backend is not Anthropic.
        """
        client = self._anthropic_client()
        if client is None:
            return None

        top_synth_codes = tuple(r["synth"] for r in market_ranking)
        cache_key = "tool_" + _cache_key(regime, risk_profile, top_synth_codes)
        cached = _RESPONSE_CACHE.get(cache_key)
        if cached and (time.time() - cached[1]) < _CACHE_TTL_SEC:
            return cached[0]

        system = _build_system_prompt() + (
            "\n\nThis run is interactive: you have tools (get_asset_stats, "
            "get_correlation, stress_test_portfolio). Use them to investigate "
            "before finalizing via propose_portfolio."
        )
        user = _build_tool_user_prompt(
            regime,
            regime_confidence,
            risk_profile,
            usdc_floor,
            synth_budget,
            market_ranking,
            strategies,
            scan_universe_synths,
        )

        messages: list[dict] = [{"role": "user", "content": user}]
        tool_trace: list[AgentToolCall] = []
        final_pick_input: dict | None = None
        final_response_model: str = self.model_id

        for iteration in range(MAX_AGENT_ITERATIONS):
            try:
                resp = client.messages.create(
                    model=self.model_id,
                    max_tokens=4096,
                    system=system,
                    tools=_agent_tools(),
                    messages=messages,
                )
            except Exception as e:  # pylint: disable=broad-except
                logger.warning("PortfolioAgent tool-use turn %d failed: %s", iteration, e)
                return None

            served = getattr(resp, "model", None)
            if served:
                final_response_model = str(served)

            # Capture the assistant turn for the next iteration
            messages.append({"role": "assistant", "content": resp.content})

            # Did the model finalize?
            tool_uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
            if not tool_uses:
                # Model stopped without finalizing — give up
                logger.warning("PortfolioAgent: model emitted no tool calls (turn %d)", iteration)
                break

            # Resolve each tool call and append the results
            tool_results: list[dict] = []
            for tu in tool_uses:
                tool_name = tu.name
                tool_input = dict(tu.input or {})
                if tool_name == "propose_portfolio":
                    final_pick_input = tool_input
                    break
                output = _execute_tool(tool_name, tool_input, price_histories)
                tool_trace.append(
                    AgentToolCall(
                        tool=tool_name,
                        inputs=tool_input,
                        output_summary=_summarize_tool_output(tool_name, output),
                    )
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(output),
                    }
                )

            if final_pick_input is not None:
                break

            messages.append({"role": "user", "content": tool_results})

        if final_pick_input is None:
            logger.warning("PortfolioAgent: ran out of iterations without finalize")
            return None

        # Parse the propose_portfolio inputs into AgentPick objects
        thesis = str(final_pick_input.get("thesis", "")).strip()
        raw_picks = final_pick_input.get("picks") or []
        picks: list[AgentPick] = []
        for entry in raw_picks:
            if not isinstance(entry, dict):
                continue
            ticker = str(entry.get("ticker", "")).strip()
            if not ticker:
                continue
            resolved = _resolve_ticker(ticker)
            if not resolved:
                continue
            synth, (_yf, display, asset_class, exchange) = resolved
            try:
                weight = float(entry.get("weight", 0.0))
            except (TypeError, ValueError):
                continue
            if weight <= 0:
                continue
            weight = min(weight, 0.20)
            picks.append(
                AgentPick(
                    ticker=display,
                    synth=synth,
                    asset_class=asset_class,
                    exchange=exchange,
                    weight=weight,
                    paper_anchor=str(entry.get("paper_anchor", "")).strip(),
                    reasoning=str(entry.get("reasoning", "")).strip(),
                )
            )
        if not picks:
            return None

        # Normalize weights to the synth budget
        total = sum(p.weight for p in picks)
        if total > 0:
            for p in picks:
                p.weight = round(p.weight / total * synth_budget, 4)

        portfolio = AgentPortfolio(
            thesis=thesis or f"{regime} regime, {risk_profile} risk: diversified",
            picks=picks,
            model_id=self.model_id,
            served_model=final_response_model,
            tool_calls=tool_trace,
            iterations=iteration + 1,
        )
        _RESPONSE_CACHE[cache_key] = (portfolio, time.time())
        return portfolio

    def propose_portfolio(
        self,
        regime: str,
        regime_confidence: float,
        risk_profile: str,
        usdc_floor: float,
        synth_budget: float,
        market_ranking: list[dict],
        strategies: list[Any],
        scan_universe_synths: set[str],
    ) -> AgentPortfolio | None:
        if not self.available:
            logger.info("PortfolioAgent unavailable — no LLM backend configured")
            return None

        top_synth_codes = tuple(r["synth"] for r in market_ranking)
        cache_key = _cache_key(regime, risk_profile, top_synth_codes)
        cached = _RESPONSE_CACHE.get(cache_key)
        if cached and (time.time() - cached[1]) < _CACHE_TTL_SEC:
            return cached[0]

        system = _build_system_prompt()
        user = _build_user_prompt(
            regime,
            regime_confidence,
            risk_profile,
            usdc_floor,
            synth_budget,
            market_ranking,
            strategies,
            scan_universe_synths,
        )

        try:
            raw = self._backend.complete(system=system, user=user)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("PortfolioAgent LLM call failed: %s", e)
            return None

        data = _extract_json(raw)
        if not data or not isinstance(data, dict):
            logger.warning("PortfolioAgent: could not parse JSON from response: %r", raw[:300])
            return None

        thesis = str(data.get("thesis", "")).strip()
        raw_picks = data.get("picks") or []
        if not isinstance(raw_picks, list):
            logger.warning("PortfolioAgent: picks not a list")
            return None

        picks: list[AgentPick] = []
        for entry in raw_picks:
            if not isinstance(entry, dict):
                continue
            ticker = str(entry.get("ticker", "")).strip()
            if not ticker:
                continue
            resolved = _resolve_ticker(ticker)
            if not resolved:
                logger.info("PortfolioAgent: unknown ticker %r — skipping", ticker)
                continue
            synth, (_yf, display, asset_class, exchange) = resolved
            try:
                weight = float(entry.get("weight", 0.0))
            except (TypeError, ValueError):
                continue
            if weight <= 0:
                continue
            weight = min(weight, 0.20)  # enforce per-asset cap
            picks.append(
                AgentPick(
                    ticker=display,
                    synth=synth,
                    asset_class=asset_class,
                    exchange=exchange,
                    weight=weight,
                    paper_anchor=str(entry.get("paper_anchor", "")).strip(),
                    reasoning=str(entry.get("reasoning", "")).strip(),
                )
            )

        if not picks:
            logger.warning("PortfolioAgent: parsed zero valid picks from LLM response")
            return None

        # Normalize weights to the synth budget
        total = sum(p.weight for p in picks)
        if total > 0:
            for p in picks:
                p.weight = round(p.weight / total * synth_budget, 4)

        portfolio = AgentPortfolio(
            thesis=thesis or f"{regime} regime, {risk_profile} risk: diversified across asset classes",
            picks=picks,
            model_id=self.model_id,
            served_model=getattr(self._backend, "served_model", self.model_id),
        )
        _RESPONSE_CACHE[cache_key] = (portfolio, time.time())
        return portfolio


# ── Tool-use (multi-turn) implementation ──────────────────────────
#
# Single-turn JSON output is fine for v1; for the demo we give Claude
# real tools so it can investigate the portfolio it's about to recommend.
# The agent gets up to MAX_AGENT_ITERATIONS tool calls before being
# forced to emit a final ``propose_portfolio`` call that finalizes picks.

MAX_AGENT_ITERATIONS = 12


def _agent_tools() -> list[dict]:
    """JSON-schema tool definitions surfaced to Claude."""
    # Anchor IDs MUST be substrings of the actual strategy file paths,
    # otherwise routes._find_strategy_for_anchor falls through to
    # strategies[0] and the paper attribution becomes meaningless.
    valid_anchors = (
        "faber_2007_sma200, moreira_muir_2017_volatility, "
        "moskowitz_ooi_pedersen_2012_tsmom, george_hwang_2004_52w, "
        "capital_preservation_tbill, pipeline_buy_hold"
    )
    return [
        {
            "name": "get_asset_stats",
            "description": (
                "Get annualized return, volatility, Sharpe, and max drawdown for "
                "a single asset over the last year. Use this to evaluate whether "
                "a specific stock or instrument is worth allocating to."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Display symbol (e.g. NVDA, BIST100, USD/TRY)"}
                },
                "required": ["ticker"],
            },
        },
        {
            "name": "get_correlation",
            "description": (
                "Get the trailing 1-year correlation between two assets. Use this "
                "to check whether a proposed addition is genuinely diversifying or "
                "redundant (correlation > 0.7 = redundant)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker_a": {"type": "string"},
                    "ticker_b": {"type": "string"},
                },
                "required": ["ticker_a", "ticker_b"],
            },
        },
        {
            "name": "stress_test_portfolio",
            "description": (
                "Apply one of 6 historical/scenario shocks (equity_crash_2008, "
                "tech_rout_2022, covid_crash_2020, energy_supercycle, em_fx_crisis, "
                "crypto_winter) to a candidate portfolio. Returns the P&L hit. Use "
                "this before finalizing — if the worst case exceeds your risk "
                "tolerance, reconsider the picks."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "allocations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "ticker": {"type": "string"},
                                "weight": {"type": "number"},
                            },
                            "required": ["ticker", "weight"],
                        },
                    },
                    "scenario": {
                        "type": "string",
                        "enum": list(SCENARIOS.keys()),
                    },
                },
                "required": ["allocations", "scenario"],
            },
        },
        {
            "name": "propose_portfolio",
            "description": (
                "Finalize the portfolio. Call this LAST, once you've used the "
                "other tools to vet your picks. Each pick must have a paper_anchor "
                f"from: {valid_anchors}."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "thesis": {
                        "type": "string",
                        "description": "1-2 sentence portfolio thesis tying regime + risk profile to picks",
                    },
                    "picks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "ticker": {"type": "string"},
                                "weight": {"type": "number", "minimum": 0, "maximum": 0.20},
                                "paper_anchor": {"type": "string"},
                                "reasoning": {"type": "string"},
                            },
                            "required": ["ticker", "weight", "paper_anchor", "reasoning"],
                        },
                        "minItems": 5,
                        "maxItems": 12,
                    },
                },
                "required": ["thesis", "picks"],
            },
        },
    ]


def _tool_get_asset_stats(
    ticker: str,
    price_histories: dict,
) -> dict:
    """Compute annualized stats for one asset over the last year."""
    resolved = _resolve_ticker(ticker)
    if not resolved:
        return {"error": f"unknown ticker: {ticker}"}
    synth, (_yf, display, asset_class, exchange) = resolved
    series = price_histories.get(synth)
    if series is None or series.empty or len(series) < 30:
        return {"error": f"no usable price history for {display}"}
    import numpy as np

    returns = series.pct_change().dropna().tail(252)
    if len(returns) < 30:
        return {"error": f"insufficient returns for {display}"}
    mu_d = float(returns.mean())
    sigma_d = float(returns.std())
    mu_ann = mu_d * 252
    sigma_ann = sigma_d * float(np.sqrt(252))
    sharpe = mu_ann / sigma_ann if sigma_ann > 1e-9 else 0.0
    # Max drawdown on the 1y window
    cum = (1 + returns).cumprod()
    running_max = cum.cummax()
    drawdown = (cum - running_max) / running_max
    max_dd = float(drawdown.min())
    return {
        "ticker": display,
        "asset_class": asset_class,
        "exchange": exchange,
        "annualized_return": round(mu_ann, 4),
        "annualized_vol": round(sigma_ann, 4),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(max_dd, 4),
        "n_obs": len(returns),
    }


def _tool_get_correlation(
    ticker_a: str,
    ticker_b: str,
    price_histories: dict,
) -> dict:
    ra = _resolve_ticker(ticker_a)
    rb = _resolve_ticker(ticker_b)
    if not ra or not rb:
        return {"error": f"unknown ticker(s): {ticker_a!r}/{ticker_b!r}"}
    sa, (_y1, da, _ac1, _ex1) = ra
    sb, (_y2, db, _ac2, _ex2) = rb
    import pandas as pd

    pa, pb = price_histories.get(sa), price_histories.get(sb)
    if pa is None or pb is None or pa.empty or pb.empty:
        return {"error": "missing price history"}
    df = pd.DataFrame({sa: pa, sb: pb}).dropna(how="any")
    if len(df) < 30:
        return {"error": "insufficient overlap"}
    r = df.pct_change().dropna()
    rho = float(r[sa].corr(r[sb]))
    return {
        "ticker_a": da,
        "ticker_b": db,
        "correlation_1y": round(rho, 3),
        "n_obs": len(r),
        "interpretation": (
            "highly correlated (redundant)"
            if rho > 0.7
            else "moderately correlated"
            if rho > 0.4
            else "weakly correlated (diversifying)"
            if rho > 0
            else "negatively correlated (strong diversifier)"
        ),
    }


def _tool_stress_test(
    allocations: list[dict],
    scenario: str,
) -> dict:
    """Apply a stress scenario to a candidate portfolio."""
    # Enrich allocations with asset_class (the tool input only has ticker + weight)
    enriched: list[dict] = []
    for a in allocations:
        ticker = a.get("ticker", "")
        resolved = _resolve_ticker(ticker)
        if not resolved:
            continue
        _synth, (_yf, display, asset_class, _exchange) = resolved
        enriched.append(
            {
                "symbol": display,
                "asset_class": asset_class,
                "weight": float(a.get("weight", 0.0)),
            }
        )
    if not enriched:
        return {"error": "no valid allocations"}
    try:
        result = stress_one(enriched, scenario)
    except ValueError as e:
        return {"error": str(e)}
    # Top 5 contributors (positive and negative) for the LLM to react to
    sorted_pnl = sorted(result.per_asset_pnl, key=lambda x: x["contribution_pct"])
    return {
        "scenario": scenario,
        "label": result.label,
        "portfolio_pnl_pct": round(result.portfolio_pnl * 100, 2),
        "worst_contributors": sorted_pnl[:3],
        "best_contributors": sorted_pnl[-3:][::-1],
    }


def _execute_tool(
    name: str,
    tool_input: dict,
    price_histories: dict,
) -> dict:
    """Dispatch a tool call by name."""
    if name == "get_asset_stats":
        return _tool_get_asset_stats(tool_input.get("ticker", ""), price_histories)
    if name == "get_correlation":
        return _tool_get_correlation(
            tool_input.get("ticker_a", ""),
            tool_input.get("ticker_b", ""),
            price_histories,
        )
    if name == "stress_test_portfolio":
        return _tool_stress_test(
            tool_input.get("allocations") or [],
            tool_input.get("scenario", ""),
        )
    return {"error": f"unknown tool: {name}"}


def _summarize_tool_output(name: str, output: dict) -> str:
    """One-line summary for the trace (avoid bloating the saved portfolio)."""
    if "error" in output:
        return f"{name} → error: {output['error']}"
    if name == "get_asset_stats":
        return (
            f"{name}({output.get('ticker')}) → μ={output.get('annualized_return', 0) * 100:+.1f}%, "
            f"σ={output.get('annualized_vol', 0) * 100:.1f}%, sharpe={output.get('sharpe')}"
        )
    if name == "get_correlation":
        return (
            f"{name}({output.get('ticker_a')},{output.get('ticker_b')}) → "
            f"ρ={output.get('correlation_1y')} ({output.get('interpretation', '')})"
        )
    if name == "stress_test_portfolio":
        return f"{name}({output.get('scenario')}) → portfolio P&L {output.get('portfolio_pnl_pct', 0):+.1f}%"
    return f"{name} → {json.dumps(output)[:120]}"


def _build_tool_user_prompt(
    regime: str,
    regime_confidence: float,
    risk_profile: str,
    usdc_floor: float,
    synth_budget: float,
    market_ranking: list[dict],
    strategies: list,
    scan_universe_synths: set[str],
) -> str:
    """Same context as single-turn, but framed for an investigative agent."""
    return (
        f"## CONTEXT\n"
        f"- regime: {regime} (confidence {regime_confidence:.0%})\n"
        f"- risk_profile: {risk_profile}\n"
        f"- usdc_floor: {usdc_floor:.0%} (held as USDC; you do not allocate this)\n"
        f"- synth_budget: {synth_budget:.0%} (your weights must sum to ~this)\n\n"
        f"## TOP MARKET OPPORTUNITIES (live 90-day risk-adjusted ranking)\n"
        f"{_format_market_scan(market_ranking)}\n\n"
        f"## PAPER STRATEGIES (anchor every pick to one of these ids)\n"
        f"{_format_strategies(strategies)}\n\n"
        f"## AVAILABLE UNIVERSE (pick from here; * = appeared in top market scan)\n"
        f"{_format_universe(scan_universe_synths)}\n\n"
        f"## YOUR PROCESS\n"
        f"1. Form a hypothesis about the right portfolio shape for this regime + risk profile.\n"
        f"2. Use `get_asset_stats` on 4-8 candidate names you're considering.\n"
        f"3. Use `get_correlation` to verify your top picks are not redundant (>0.7 = drop one).\n"
        f"4. Use `stress_test_portfolio` on at least one adverse scenario for your tentative "
        f"   allocation; if the loss is unacceptable for the risk profile, revise.\n"
        f"5. Once satisfied, call `propose_portfolio` ONCE with your final 5-12 picks.\n\n"
        f"Budget: at most {MAX_AGENT_ITERATIONS} tool calls in total before you must finalize."
    )


# Singleton — constructed lazily to honor env vars set after import.
_AGENT: PortfolioAgent | None = None


def get_portfolio_agent() -> PortfolioAgent:
    global _AGENT  # pylint: disable=global-statement
    if _AGENT is None:
        _AGENT = PortfolioAgent()
    return _AGENT


def synth_for_display(display: str) -> str | None:
    """Convenience for callers that have a display symbol and need the synth."""
    for synth, (_yf, d, _ac, _ex) in GLOBAL_ASSETS.items():
        if d == display:
            return synth
    return None


# Reuse the canonical display helper for callers that import this module
display_for_synth = synth_display

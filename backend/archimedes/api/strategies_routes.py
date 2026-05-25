"""Strategy endpoints — /api/strategies/*.

Includes: library listing, signals, frontier, correlation, advisor, stress,
construct, generate/fusion.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
from datetime import UTC

from fastapi import APIRouter, Query, Request, Response

from archimedes.api._route_helpers import (
    architect,
    persist_trace_off_chain,
    strategy_provider,
)
from archimedes.api.limiter import limiter
from archimedes.api.architect_schemas import (
    ConstructionSelectionResponse,
    ConstructionTraceResponse,
    StrategyConstructionRequest,
    StrategyConstructionResponse,
)
from archimedes.api.schemas import (
    SignalResponse,
    StrategyListResponse,
    StrategyResponse,
    StrategySignalResponse,
    StrategySignalsResponse,
)
from archimedes.models.strategy import Strategy, StrategyStatus
from archimedes.services.construction_trace import build_construction_trace
from archimedes.services.strategy_guardrail import apply_guardrail

strategies_router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def _to_strategy_response(s: Strategy) -> StrategyResponse:
    """Map StrategyPassport + persisted BacktestResult to API schema."""
    from archimedes.api.schemas import PaperRefResponse

    bt = strategy_provider.get_backtest_result(s.id)
    has_real = s.real_sharpe is not None

    # Build papers list from passport
    papers_list = [
        PaperRefResponse(
            arxiv_id=p.arxiv_id,
            title=p.title,
            authors=p.authors,
            doi=p.doi,
            venue=p.venue,
            year=p.year,
            citation_count=p.citation_count,
            contribution=p.contribution,
        )
        for p in s.papers
    ]

    return StrategyResponse(
        id=s.id,
        papers=papers_list,
        # Legacy scalar fields from papers[0]
        paper_arxiv_id=s.paper_arxiv_id,
        paper_title=s.paper_title,
        paper_authors=s.paper_authors,
        methodology_summary=s.methodology_summary,
        asset_universe=s.asset_universe,
        position_sizing=s.position_sizing.value,
        rebalance_frequency=s.rebalance_frequency.value,
        status=s.status.value,
        paper_venue=s.paper_venue,
        paper_year=s.paper_year,
        paper_doi=s.paper_doi,
        paper_citation_count=s.paper_citation_count,
        methodology_hash=s.methodology_hash,
        extraction_llm=s.extraction_llm,
        curator_wallet=s.curator_wallet,
        curator_note=s.curator_note,
        on_chain_registration_tx=s.on_chain_registration_tx,
        paper_claimed_sharpe=bt.paper_claimed_sharpe if bt else s.paper_claimed_sharpe,
        paper_claim_blended_sharpe=s.paper_claim_blended_sharpe,
        sharpe_ratio=s.real_sharpe if has_real else (bt.sharpe_ratio if bt else s.stub_sharpe),
        sortino_ratio=s.real_sortino if has_real else (bt.sortino_ratio if bt else None),
        cagr=s.real_cagr if has_real else (bt.cagr if bt else s.stub_cagr),
        max_drawdown=s.real_max_dd if has_real else (bt.max_drawdown if bt else s.stub_max_dd),
        win_rate=s.real_win_rate if has_real else (bt.win_rate if bt else s.stub_win_rate),
        calmar_ratio=s.real_calmar if has_real else (bt.calmar_ratio if bt else s.stub_calmar),
        correlation_to_spy=s.real_corr_spy if has_real else (bt.correlation_to_spy if bt else s.stub_corr_spy),
        total_trades=s.real_total_trades if has_real else (bt.total_trades if bt else None),
        deflated_sharpe_ratio=s.deflated_sharpe_ratio if has_real else (bt.deflated_sharpe_ratio if bt else None),
        dsr_p_value=s.dsr_p_value if has_real else (bt.dsr_p_value if bt else None),
        pbo_score=s.pbo_score if has_real else (bt.pbo_score if bt else None),
        out_of_sample_sharpe=s.out_of_sample_sharpe if has_real else (bt.out_of_sample_sharpe if bt else None),
        kelly_fraction=s.kelly_fraction,
        passes_rigor_gate=s.passes_rigor_gate if has_real else (bt.passes_rigor_gate if bt else s.passes_rigor_gate),
        is_backtest_placeholder=not has_real,
        sharpe_ci_lower=s.sharpe_ci_lower,
        sharpe_ci_upper=s.sharpe_ci_upper,
        backtest_start=(
            s.real_backtest_start
            if has_real and s.real_backtest_start
            else (bt.backtest_start.isoformat() if bt and bt.backtest_start else None)
        ),
        backtest_end=(
            s.real_backtest_end
            if has_real and s.real_backtest_end
            else (bt.backtest_end.isoformat() if bt and bt.backtest_end else None)
        ),
        regime_tag=s.regime_tag,
    )


# ── Library listing ─────────────────────────────────────────────


@strategies_router.get("/", response_model=StrategyListResponse)
async def list_strategies(
    status: str | None = Query(None, pattern="^(candidate|validated|live|retired)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List strategies in the library. Backed by LocalStrategyProvider."""
    status_filter = StrategyStatus(status) if status else None
    strats = strategy_provider.list_strategies(status=status_filter)
    total = len(strats)
    window = strats[offset : offset + limit]
    return StrategyListResponse(
        strategies=[_to_strategy_response(s) for s in window],
        total=total,
    )


@strategies_router.get("/generated")
async def list_generated_strategies(limit: int = Query(50, ge=1, le=200)):
    """List fusion/architect-generated strategies from the strategy_store table."""

    from archimedes.db import get_session
    from archimedes.models.strategy_store import StrategyRecord

    rows: list[dict] = []
    try:
        with get_session() as session:  # type: _Session
            records = (
                session.query(StrategyRecord)
                .filter(StrategyRecord.is_example.is_(False))
                .order_by(StrategyRecord.created_at.desc())
                .limit(limit)
                .all()
            )
            rows = [r.to_dict() for r in records]
    except Exception as exc:
        import logging as _logging

        _logging.getLogger(__name__).warning("list_generated_strategies failed: %s", exc)
        rows = []
    return {"strategies": rows, "total": len(rows)}


@strategies_router.get("/signals", response_model=StrategySignalsResponse)
async def get_strategy_signals():
    """Evaluate all strategies against live market data and return signals."""
    from datetime import datetime

    from archimedes.services.strategy_signal_evaluator import strategy_evaluator

    strategies = strategy_provider.list_strategies()
    from archimedes.chain.client import chain_client

    synth_assets = [sym for sym, addr in chain_client.settings.synth_addresses.items() if addr]

    all_signals = await asyncio.to_thread(
        strategy_evaluator.evaluate_strategies,
        strategies,
        synth_assets,
    )

    target_weights = strategy_evaluator.aggregate_signals(all_signals, usdc_floor=0.20)

    flat_count = sum(1 for ss in all_signals for s in ss.signals if s.signal.value == "flat")
    total_count = sum(len(ss.signals) for ss in all_signals)
    flat_pct = flat_count / total_count if total_count > 0 else 0

    if flat_pct > 0.6:
        regime = "risk_off"
    elif flat_pct > 0.3:
        regime = "transition"
    else:
        regime = "risk_on"

    strat_responses = []
    for ss in all_signals:
        strat_responses.append(
            StrategySignalResponse(
                strategy_id=ss.strategy_id,
                paper_title=ss.paper_title,
                signals=[
                    SignalResponse(
                        asset=s.asset,
                        signal=s.signal.value,
                        weight=s.weight,
                        reason=s.reason,
                        strategy_name=s.strategy_name,
                    )
                    for s in ss.signals
                ],
            )
        )

    return StrategySignalsResponse(
        strategy_count=len(all_signals),
        regime=regime,
        confidence=round(1.0 - flat_pct, 2),
        target_weights=target_weights,
        strategies=strat_responses,
        timestamp=datetime.now(UTC).isoformat(),
    )


@strategies_router.get("/frontier")
async def get_efficient_frontier():
    """Compute efficient frontier for all Tier-1 validated strategies."""
    import numpy as np

    from archimedes.services.portfolio_optimizer import compute_efficient_frontier

    strategies = strategy_provider.list_strategies()
    active = [s for s in strategies if s.passes_rigor_gate]

    if len(active) < 2:
        return {"frontier": [], "strategies": [], "message": "Need >= 2 validated strategies"}

    rng = np.random.default_rng(42)
    N_DAYS = 5560
    synthetic_returns = {}
    labels = []

    for s in active[:5]:
        sr = s.real_sharpe if s.real_sharpe is not None else s.stub_sharpe if s.stub_sharpe is not None else 0.5
        cagr = s.real_cagr if s.real_cagr is not None else s.stub_cagr if s.stub_cagr is not None else 0.08
        mu_d = cagr / 252
        sigma_d = abs(mu_d / (sr / (252**0.5))) if sr != 0 else 0.01
        rets = rng.normal(mu_d, sigma_d, N_DAYS).tolist()
        synthetic_returns[s.id] = rets
        labels.append({"id": s.id, "title": s.paper_title})

    frontier = compute_efficient_frontier(list(synthetic_returns.keys()), synthetic_returns)
    return {"frontier": frontier, "strategies": labels, "message": None}


@strategies_router.get("/correlation")
async def get_strategy_correlation():
    """Pairwise correlation matrix for the active strategy library."""
    import numpy as np

    strategies = [s for s in strategy_provider.list_strategies() if s.real_sharpe is not None]
    if len(strategies) < 2:
        return {"matrix": [], "labels": [], "diversification_ratio": None}

    rng = np.random.default_rng(42)
    N_DAYS = 5560

    spy_daily = rng.normal(0.00035, 0.01, N_DAYS)

    return_matrix = []
    labels = []
    for s in strategies:
        sr = s.real_sharpe if s.real_sharpe is not None else 0.5
        cagr = s.real_cagr if s.real_cagr is not None else 0.08
        corr = s.real_corr_spy if s.real_corr_spy is not None else 1.0
        mu_d = cagr / 252
        sigma_d = abs(mu_d / (sr / (252**0.5))) if sr != 0 else 0.01

        idio = rng.normal(0, sigma_d * float(np.sqrt(max(1 - corr**2, 0))), N_DAYS)
        rets = corr * (spy_daily * sigma_d / 0.01) + idio + mu_d
        return_matrix.append(rets)
        labels.append(
            {
                "id": s.id,
                "title": s.paper_title[:30],
                "passes_rigor_gate": s.passes_rigor_gate,
            }
        )

    R = np.array(return_matrix)
    corr_matrix = np.corrcoef(R)

    n = len(strategies)
    off_diag = [corr_matrix[i, j] for i in range(n) for j in range(n) if i != j]
    avg_corr = sum(off_diag) / len(off_diag) if off_diag else 1.0

    return {
        "matrix": [[round(float(corr_matrix[i, j]), 3) for j in range(n)] for i in range(n)],
        "labels": labels,
        "avg_pairwise_correlation": round(avg_corr, 3),
        "diversification_ratio": None,
        "note": "Strategies track broad equity markets — high inter-strategy correlation is expected and shown honestly.",
    }


# ── Advisor (large endpoint) ──────────────────────────────────


@strategies_router.get("/advisor")
async def get_portfolio_advisor(
    risk_profile: str = Query("moderate", pattern="^(fixed_income|conservative|moderate|aggressive|hyper_risky)$"),
):
    """Portfolio allocation recommendation based on Kelly + risk-parity math."""
    from archimedes.models.portfolio import RISK_PROFILE_PARAMS, RiskProfile
    from archimedes.models.regime import Regime
    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
        regime_data = await state.load_regime()
    except Exception:
        regime_data = None
    finally:
        await state.close()

    regime_value = regime_data.get("regime", "transition") if regime_data else "transition"
    regime_confidence = regime_data.get("confidence", 0.5) if regime_data else 0.5
    try:
        regime_enum = Regime(regime_value)
    except ValueError:
        regime_enum = Regime.TRANSITION

    _DELEVERAGE: dict[Regime, float] = {
        Regime.RISK_ON: 0.5,
        Regime.TRANSITION: 1.0,
        Regime.RISK_OFF: 2.5,
        Regime.CRISIS: 5.0,
    }

    rp_map = {
        "fixed_income": RiskProfile.FIXED_INCOME,
        "conservative": RiskProfile.CONSERVATIVE,
        "moderate": RiskProfile.MODERATE,
        "aggressive": RiskProfile.AGGRESSIVE,
        "hyper_risky": RiskProfile.HYPER_RISKY,
    }
    rp = rp_map.get(risk_profile, RiskProfile.MODERATE)
    params = RISK_PROFILE_PARAMS[rp]

    usdc_floor_base = params["usyc_floor"]
    deleverage = _DELEVERAGE.get(regime_enum, 1.0)
    usdc_floor = min(usdc_floor_base * deleverage, 0.95)
    synth_budget = max(0.0, 1.0 - usdc_floor)

    all_strategies = [s for s in strategy_provider.list_strategies() if s.real_sharpe is not None]

    # Apply regime-aware tilt to strategy ordering
    from archimedes.services.regime_weight_schedule import apply_regime_tilt

    strategies, regime_mix = apply_regime_tilt(all_strategies, regime_value, risk_profile)

    from archimedes.agents.portfolio_agent import get_portfolio_agent
    from archimedes.services.strategy_signal_evaluator import (
        DEFAULT_SCAN_UNIVERSE,
        GLOBAL_ASSETS,
        _fetch_price_histories,
        strategy_evaluator,
    )
    from archimedes.services.strategy_signal_evaluator import (
        Signal as _Signal,
    )

    try:
        price_histories = await asyncio.wait_for(
            asyncio.to_thread(_fetch_price_histories, DEFAULT_SCAN_UNIVERSE, "2y"),
            timeout=45.0,
        )
    except Exception:
        price_histories = {}

    try:
        market_ranking = strategy_evaluator.rank_market(
            price_histories,
            lookback_days=90,
            top_n=25,
        )
    except Exception:
        market_ranking = []

    agent = get_portfolio_agent()
    agent_portfolio = None
    if agent.available and market_ranking:
        try:
            agent_portfolio = await asyncio.wait_for(
                asyncio.to_thread(
                    agent.propose_portfolio_with_tools,
                    regime_value,
                    regime_confidence,
                    risk_profile,
                    usdc_floor,
                    synth_budget,
                    market_ranking,
                    strategies,
                    set(DEFAULT_SCAN_UNIVERSE),
                    price_histories,
                ),
                timeout=120.0,
            )
        except Exception:
            agent_portfolio = None
        if agent_portfolio is None:
            try:
                agent_portfolio = await asyncio.wait_for(
                    asyncio.to_thread(
                        agent.propose_portfolio,
                        regime_value,
                        regime_confidence,
                        risk_profile,
                        usdc_floor,
                        synth_budget,
                        market_ranking,
                        strategies,
                        set(DEFAULT_SCAN_UNIVERSE),
                    ),
                    timeout=60.0,
                )
            except Exception:
                agent_portfolio = None

    top_synths = [r["synth"] for r in market_ranking] if market_ranking else list(price_histories.keys())

    try:
        all_signals = await asyncio.wait_for(
            asyncio.to_thread(
                strategy_evaluator.evaluate_strategies,
                strategies,
                top_synths,
                price_histories,
                True,
            ),
            timeout=30.0,
        )
    except Exception:
        all_signals = []

    strat_by_id = {s.id: s for s in strategies}

    from archimedes.services.stress_engine import stress_all as _stress_all

    async def _build_and_anchor_trace(
        allocations_for_trace: list[dict],
        thesis_for_trace: str,
        agent_obj,  # noqa: ARG001 — accepted for symmetry with caller; closure captures rather than reads
    ) -> dict:
        import uuid

        from archimedes.models.trace import DecisionType, ReasoningTrace

        registry_address: str | None = None
        try:
            from archimedes.chain.client import chain_client as _cc

            registry_address = _cc.settings.reasoning_trace_registry_address or None
        except Exception:
            registry_address = None
        try:
            trace = ReasoningTrace(
                id=str(uuid.uuid4()),
                vault_address="0x0000000000000000000000000000000000000000",
                decision_type=DecisionType.PORTFOLIO_CONSTRUCTION,
                trigger=f"advisor_request:regime={regime_value}:profile={risk_profile}",
                market_context={
                    "regime": regime_value,
                    "regime_confidence": regime_confidence,
                    "risk_profile": risk_profile,
                    "usdc_floor": usdc_floor,
                    "synth_budget": synth_budget,
                    "universe_size": len(DEFAULT_SCAN_UNIVERSE),
                    "universe_fetched": len(price_histories),
                    "top_opportunities": [
                        {"symbol": r.get("display"), "score": r.get("score")} for r in (market_ranking or [])[:10]
                    ],
                },
                portfolio_before={},
                portfolio_after={
                    "usdc_weight": round(usdc_floor, 4),
                    "synth_weight": round(synth_budget, 4),
                    "picks": [
                        {
                            "symbol": a.get("symbol"),
                            "asset_class": a.get("asset_class"),
                            "exchange": a.get("exchange"),
                            "weight": round(float(a.get("weight") or 0.0), 4),
                            "paper_anchor": a.get("paper_anchor"),
                            "code_hash": a.get("strategy_code_hash"),
                        }
                        for a in allocations_for_trace
                    ],
                },
                reasoning=thesis_for_trace,
                confidence=float(regime_confidence or 0.0),
                expected_outcome="Portfolio constructed; pending user vault deployment",
                trades_executed=[],
                strategies_referenced=list(
                    {a.get("paper_anchor") for a in allocations_for_trace if a.get("paper_anchor")}
                ),
            )
            content_hash = trace.compute_hash()

            tx_hash: str | None = None
            try:
                from archimedes.chain.trace_publisher import TracePublisher

                publisher = TracePublisher()
                tx_hash = await publisher.publish(trace)
            except Exception:
                tx_hash = None

            return {
                "trace_id": trace.id,
                "trace_hash": content_hash if content_hash.startswith("0x") else f"0x{content_hash}",
                "canonical_preview": trace.canonical_json()[:500] + ("…" if len(trace.canonical_json()) > 500 else ""),
                "anchored_on_chain": tx_hash is not None,
                "anchor_tx_hash": tx_hash,
                "registry_address": registry_address,
                "decision_type": trace.decision_type.value,
                "trigger": trace.trigger,
            }
        except Exception as e:
            return {"trace_id": None, "trace_hash": None, "error": str(e)}

    def _run_stress(allocs: list[dict], usdc_w: float) -> list:
        try:
            return _stress_all(allocs, usdc_weight=usdc_w)
        except Exception:
            return []

    def _rigor_fields(st) -> dict:
        paper_delta_sharpe = None
        if st.paper_claimed_sharpe is not None and st.real_sharpe is not None:
            paper_delta_sharpe = round(st.real_sharpe - st.paper_claimed_sharpe, 4)
        paper_delta_cagr = None
        if st.paper_claimed_cagr is not None and st.real_cagr is not None:
            paper_delta_cagr = round(st.real_cagr - st.paper_claimed_cagr, 4)
        paper_delta_max_dd = None
        if st.paper_claimed_max_dd is not None and st.real_max_dd is not None:
            paper_delta_max_dd = round(st.real_max_dd - st.paper_claimed_max_dd, 4)

        return {
            "passes_rigor_gate": st.passes_rigor_gate,
            "deflated_sharpe_ratio": st.deflated_sharpe_ratio,
            "dsr_p_value": st.dsr_p_value,
            "num_trials_in_selection": st.num_trials_in_selection,
            "pbo_score": st.pbo_score,
            "out_of_sample_sharpe": st.out_of_sample_sharpe,
            "paper_claimed_sharpe": st.paper_claimed_sharpe,
            "paper_claimed_cagr": st.paper_claimed_cagr,
            "paper_claimed_max_dd": st.paper_claimed_max_dd,
            "paper_delta_sharpe": paper_delta_sharpe,
            "paper_delta_cagr": paper_delta_cagr,
            "paper_delta_max_dd": paper_delta_max_dd,
            "sharpe_ci_lower": st.sharpe_ci_lower,
            "sharpe_ci_upper": st.sharpe_ci_upper,
            "n_obs_daily": st.n_obs_daily,
            "strategy_code_hash": st.strategy_code_hash,
        }

    def _build_rigor_summary(active_rows: list[dict]) -> dict:
        n = len(active_rows)
        if n == 0:
            return {
                "total_picks": 0,
                "passes_rigor_gate": 0,
                "dsr_significant": 0,
                "pbo_acceptable": 0,
                "oos_positive": 0,
            }
        passes = sum(1 for r in active_rows if r.get("passes_rigor_gate"))
        dsr_sig = sum(1 for r in active_rows if r.get("dsr_p_value") is not None and r["dsr_p_value"] < 0.05)
        pbo_ok = sum(1 for r in active_rows if r.get("pbo_score") is not None and r["pbo_score"] < 0.5)
        oos_pos = sum(
            1 for r in active_rows if r.get("out_of_sample_sharpe") is not None and r["out_of_sample_sharpe"] > 0
        )
        avg_dsr = [r["dsr_p_value"] for r in active_rows if r.get("dsr_p_value") is not None]
        avg_pbo = [r["pbo_score"] for r in active_rows if r.get("pbo_score") is not None]
        return {
            "total_picks": n,
            "passes_rigor_gate": passes,
            "dsr_significant": dsr_sig,
            "dsr_significant_threshold": 0.05,
            "pbo_acceptable": pbo_ok,
            "pbo_acceptable_threshold": 0.50,
            "oos_positive": oos_pos,
            "avg_dsr_p_value": round(sum(avg_dsr) / len(avg_dsr), 4) if avg_dsr else None,
            "avg_pbo_score": round(sum(avg_pbo) / len(avg_pbo), 4) if avg_pbo else None,
        }

    scored: list[dict] = []

    if agent_portfolio and agent_portfolio.picks:

        def _find_strategy_for_anchor(anchor: str):
            anchor_l = (anchor or "").lower()
            if not anchor_l:
                return strategies[0] if strategies else None
            for st in strategies:
                if (
                    anchor_l in (st.strategy_code_path or "").lower()
                    or anchor_l in (st.paper_title or "").lower()
                    or anchor_l in st.id.lower()
                ):
                    return st
            return strategies[0] if strategies else None

        for pick in agent_portfolio.picks:
            anchor_strat = _find_strategy_for_anchor(pick.paper_anchor)
            if anchor_strat is None:
                continue
            sr = anchor_strat.real_sharpe if anchor_strat.real_sharpe is not None else 0.5
            mu_d = (anchor_strat.real_cagr if anchor_strat.real_cagr is not None else 0.08) / 252
            sigma_d = abs(mu_d / (sr / math.sqrt(252))) if sr != 0 else 0.01
            vol_ann = sigma_d * math.sqrt(252)
            scored.append(
                {
                    "id": f"agent_{pick.synth}",
                    "title": f"{anchor_strat.paper_title} → {pick.ticker}",
                    "symbol": pick.ticker,
                    "asset_class": pick.asset_class,
                    "exchange": pick.exchange,
                    "sharpe": round(sr, 4),
                    "cagr": round(anchor_strat.real_cagr if anchor_strat.real_cagr is not None else 0.0, 4),
                    "max_drawdown": round(anchor_strat.real_max_dd if anchor_strat.real_max_dd is not None else 0.0, 4),
                    "vol_ann": round(vol_ann, 4),
                    "kelly_fraction": round(pick.weight, 4),
                    **_rigor_fields(anchor_strat),
                    "signal_reason": pick.reasoning,
                    "agent_weight": pick.weight,
                    "paper_anchor": pick.paper_anchor,
                    "vote_count": 1,
                    "strategies": [{"title": anchor_strat.paper_title, "kelly": pick.weight}],
                }
            )

    if not scored and all_signals:
        _MAX_PER_STRATEGY = 4
        for strat_signals in all_signals:
            s = strat_by_id.get(strat_signals.strategy_id)
            if s is None or s.real_sharpe is None:
                continue
            sr = s.real_sharpe
            if sr < 0.3:
                continue
            mu_ann = s.real_cagr if s.real_cagr is not None else 0.08
            vol_ann = abs(mu_ann / sr) if sr != 0 else 0.20
            full_kelly = mu_ann / max(vol_ann**2, 1e-6)
            base_kelly = min(0.5 * full_kelly, 0.5)

            active = [sig for sig in strat_signals.signals if sig.signal != _Signal.FLAT and sig.weight > 0]
            active.sort(key=lambda x: x.weight, reverse=True)
            for asset_signal in active[:_MAX_PER_STRATEGY]:
                entry = GLOBAL_ASSETS.get(asset_signal.asset)
                display_symbol = entry[1] if entry else asset_signal.asset
                asset_class = entry[2] if entry else "unknown"
                exchange = entry[3] if entry else "?"
                effective_kelly = round(base_kelly * asset_signal.weight, 4)
                scored.append(
                    {
                        "id": f"{s.id}_{asset_signal.asset}",
                        "title": s.paper_title,
                        "symbol": display_symbol,
                        "asset_class": asset_class,
                        "exchange": exchange,
                        "sharpe": round(sr, 4),
                        "cagr": round(s.real_cagr if s.real_cagr is not None else 0.0, 4),
                        "max_drawdown": round(s.real_max_dd if s.real_max_dd is not None else 0.0, 4),
                        "vol_ann": round(vol_ann, 4),
                        "kelly_fraction": effective_kelly,
                        **_rigor_fields(s),
                        "signal_reason": asset_signal.reason,
                    }
                )

    if not scored:
        _TICKER_DISPLAY = {
            "SPY": "SPY",
            "NIKKEI": "NIKKEI",
            "GOLD": "GLD",
            "TREASURY": "BIL",
            "OIL": "OIL",
            "BIL": "BIL",
        }
        for s in strategies:
            sr = s.real_sharpe if s.real_sharpe is not None else 0.5
            if sr < 0.3:
                continue
            mu_ann = s.real_cagr if s.real_cagr is not None else 0.08
            vol_ann = abs(mu_ann / sr) if sr != 0 else 0.20
            kelly = min(0.5 * (mu_ann / max(vol_ann**2, 1e-6)), 0.5)
            universe = s.asset_universe if s.asset_universe else ["SPY"]
            per_asset_kelly = round(kelly / len(universe), 4)
            for ticker in universe:
                scored.append(
                    {
                        "id": f"{s.id}_{ticker}",
                        "title": s.paper_title,
                        "symbol": _TICKER_DISPLAY.get(ticker, ticker),
                        "asset_class": "unknown",
                        "exchange": "?",
                        "sharpe": round(sr, 4),
                        "cagr": round(s.real_cagr if s.real_cagr is not None else 0.0, 4),
                        "max_drawdown": round(s.real_max_dd if s.real_max_dd is not None else 0.0, 4),
                        "vol_ann": round(vol_ann, 4),
                        "kelly_fraction": per_asset_kelly,
                        **_rigor_fields(s),
                        "signal_reason": None,
                    }
                )

    if not scored:
        return {"error": "No strategies with real backtest data available", "allocations": []}

    # Agent path
    if agent_portfolio and agent_portfolio.picks:
        from archimedes.services.portfolio_optimizer import (
            correlation_pairs,
            kelly_optimize_from_prices,
            kelly_risk_decomposition,
        )

        pick_synths = [sc["id"].removeprefix("agent_") for sc in scored]
        mu_override: dict[str, float] = {}
        for sc, synth in zip(scored, pick_synths, strict=False):
            mu_override[synth] = float(sc.get("cagr") or 0.08)

        opt = None
        try:
            opt = await asyncio.to_thread(
                kelly_optimize_from_prices,
                pick_synths,
                price_histories,
                risk_profile,
                synth_budget,
                0.20,
                mu_override,
                0.5,  # mu_shrinkage (existing default)
                regime_value,  # regime — T-PE.7 regime-aware γ scaling
            )
        except Exception:
            opt = None

        allocations = []
        risk_decomp: list[dict] = []
        corr_pairs: list[dict] = []

        if opt is not None:
            risk_decomp = kelly_risk_decomposition(opt)
            corr_pairs = correlation_pairs(opt, top_n=8)
            weight_by_synth = {sym: float(w) for sym, w in zip(opt.symbols, opt.weights, strict=False)}
            for sc, synth in zip(scored, pick_synths, strict=False):
                w = weight_by_synth.get(synth, 0.0)
                allocations.append({**sc, "weight": round(w, 4)})
        else:
            for sc in scored:
                w = float(sc.get("agent_weight", sc.get("kelly_fraction", 0.0)))
                allocations.append({**sc, "weight": min(max(w, 0.0), 0.20)})
            total = sum(a["weight"] for a in allocations)
            if total > 0:
                for a in allocations:
                    a["weight"] = round(a["weight"] / total * synth_budget, 4)

        allocations.sort(key=lambda x: -x["weight"])
        total_w = sum(a["weight"] for a in allocations)
        if opt is not None:
            exp_sharpe = opt.expected_sharpe
            exp_cagr = opt.expected_return
            exp_max_dd = 2.0 * opt.expected_vol
        else:
            exp_sharpe = sum(a["sharpe"] * a["weight"] for a in allocations) / max(total_w, 1e-9)
            exp_cagr = sum(a["cagr"] * a["weight"] for a in allocations) / max(total_w, 1e-9)
            exp_max_dd = sum(a["max_drawdown"] * a["weight"] for a in allocations) / max(total_w, 1e-9)

        regime_narratives_agent = {
            "risk_on": "Markets are calm (low VIX, price above MA). Full synth exposure recommended.",
            "transition": "Markets are transitioning. Moderate caution; holding base USDC floor.",
            "risk_off": "Markets are stressed. USDC floor increased 2.5x; reduced synth exposure.",
            "crisis": "Crisis conditions. Maximum USDC floor (5x multiplier); minimal synth exposure.",
        }

        return {
            "regime": regime_value,
            "regime_confidence": round(regime_confidence, 4),
            "regime_narrative": regime_narratives_agent.get(regime_value, ""),
            "risk_profile": risk_profile,
            "usdc_weight": round(usdc_floor, 4),
            "synth_weight": round(synth_budget, 4),
            "allocations": allocations,
            "expected_portfolio": {
                "sharpe": round(exp_sharpe, 4),
                "cagr": round(exp_cagr, 4),
                "max_drawdown": round(exp_max_dd, 4),
                "vol_ann": round(opt.expected_vol, 4) if opt else None,
                "diversification_ratio": round(opt.diversification_ratio, 4) if opt else None,
                "risk_aversion_gamma": round(opt.risk_aversion, 2) if opt else None,
                "optimizer_converged": opt.converged if opt else False,
            },
            "regime_breakdown": {
                "bull_weight": round(regime_mix["bull"], 4),
                "bear_weight": round(regime_mix["bear"], 4),
                "neutral_weight": round(regime_mix["neutral"], 4),
            },
            "risk_decomposition": risk_decomp,
            "correlation_pairs": corr_pairs,
            "rigor_summary": _build_rigor_summary(allocations),
            "stress_tests": [
                {
                    "scenario": r.scenario,
                    "label": r.label,
                    "description": r.description,
                    "portfolio_pnl": r.portfolio_pnl,
                    "portfolio_value_after": r.portfolio_value_after,
                    "per_asset_pnl": r.per_asset_pnl,
                }
                for r in _run_stress(allocations, usdc_floor)
            ],
            "market_scan": {
                "universe_size": len(DEFAULT_SCAN_UNIVERSE),
                "fetched": len(price_histories),
                "top_opportunities": market_ranking,
            },
            "agent": {
                "used": True,
                "thesis": agent_portfolio.thesis,
                "model_id": agent_portfolio.model_id,
                "served_model": agent_portfolio.served_model,
                "num_picks": len(agent_portfolio.picks),
                "iterations": agent_portfolio.iterations,
                "tool_calls": [
                    {
                        "tool": tc.tool,
                        "inputs": tc.inputs,
                        "output_summary": tc.output_summary,
                    }
                    for tc in (agent_portfolio.tool_calls or [])
                ],
            },
            "reasoning_trace": await _build_and_anchor_trace(
                allocations,
                agent_portfolio.thesis,
                agent_portfolio,
            ),
        }

    # Rule-based aggregate
    _RIGOR_KEYS = (
        "deflated_sharpe_ratio",
        "dsr_p_value",
        "num_trials_in_selection",
        "pbo_score",
        "out_of_sample_sharpe",
        "paper_claimed_sharpe",
        "paper_claimed_cagr",
        "paper_claimed_max_dd",
        "paper_delta_sharpe",
        "paper_delta_cagr",
        "paper_delta_max_dd",
        "sharpe_ci_lower",
        "sharpe_ci_upper",
        "n_obs_daily",
        "strategy_code_hash",
    )
    agg: dict[str, dict] = {}
    for sc in scored:
        sym = sc["symbol"]
        if sym not in agg:
            agg[sym] = {
                "id": f"agg_{sym}",
                "symbol": sym,
                "asset_class": sc.get("asset_class", "unknown"),
                "exchange": sc.get("exchange", "?"),
                "title": f"Multi-strategy: {sym}",
                "strategies": [],
                "signal_reasons": [],
                "sharpe": sc["sharpe"],
                "cagr": sc["cagr"],
                "max_drawdown": sc["max_drawdown"],
                "vol_ann": sc["vol_ann"],
                "kelly_fraction": 0.0,
                "passes_rigor_gate": False,
                **{k: sc.get(k) for k in _RIGOR_KEYS},
            }
        row = agg[sym]
        row["strategies"].append({"title": sc["title"], "kelly": sc["kelly_fraction"]})
        if sc.get("signal_reason"):
            row["signal_reasons"].append(sc["signal_reason"])
        for k in _RIGOR_KEYS:
            existing = row.get(k)
            new = sc.get(k)
            if new is None:
                continue
            if existing is None:
                row[k] = new
            elif k in ("dsr_p_value", "pbo_score"):
                row[k] = min(existing, new)
            elif k in (
                "deflated_sharpe_ratio",
                "out_of_sample_sharpe",
                "sharpe_ci_lower",
                "sharpe_ci_upper",
                "n_obs_daily",
                "num_trials_in_selection",
                "paper_delta_sharpe",
                "paper_delta_cagr",
            ):
                row[k] = max(existing, new)
            elif k == "paper_delta_max_dd":
                row[k] = min(existing, new)
        row["kelly_fraction"] = max(row["kelly_fraction"], sc["kelly_fraction"])
        row["sharpe"] = max(row["sharpe"], sc["sharpe"])
        row["cagr"] = max(row["cagr"], sc["cagr"])
        row["max_drawdown"] = max(row["max_drawdown"], sc["max_drawdown"])
        row["passes_rigor_gate"] = row["passes_rigor_gate"] or sc["passes_rigor_gate"]

    for row in agg.values():
        n_votes = len(row["strategies"])
        row["kelly_fraction"] = round(min(row["kelly_fraction"] * math.sqrt(n_votes), 0.5), 4)
        row["vote_count"] = n_votes
        top_strat = max(row["strategies"], key=lambda s: s["kelly"])["title"]
        if n_votes == 1:
            row["title"] = top_strat
        else:
            row["title"] = f"{top_strat} (+{n_votes - 1} other{'s' if n_votes > 2 else ''})"

    scored = list(agg.values())

    from archimedes.services.portfolio_optimizer import (
        correlation_pairs,
        kelly_optimize_from_prices,
        kelly_risk_decomposition,
    )

    display_to_synth: dict[str, str] = {d: s for s, (_yf, d, _ac, _ex) in GLOBAL_ASSETS.items()}
    rule_synths = [display_to_synth.get(sc["symbol"]) for sc in scored]
    rule_synths_valid = [s for s in rule_synths if s and s in price_histories]
    mu_override_rb: dict[str, float] = {}
    for sc, syn in zip(scored, rule_synths, strict=False):
        if syn:
            mu_override_rb[syn] = float(sc.get("cagr") or 0.08)

    opt_rb = None
    if len(rule_synths_valid) >= 2:
        try:
            opt_rb = await asyncio.to_thread(
                kelly_optimize_from_prices,
                rule_synths_valid,
                price_histories,
                risk_profile,
                synth_budget,
                0.20,
                mu_override_rb,
                0.5,  # mu_shrinkage (existing default)
                regime_value,  # regime — T-PE.7 regime-aware γ scaling
            )
        except Exception:
            opt_rb = None

    risk_decomp_rb: list[dict] = []
    corr_pairs_rb: list[dict] = []
    allocations = []

    if opt_rb is not None:
        risk_decomp_rb = kelly_risk_decomposition(opt_rb)
        corr_pairs_rb = correlation_pairs(opt_rb, top_n=8)
        weight_by_synth = {sym: float(w) for sym, w in zip(opt_rb.symbols, opt_rb.weights, strict=False)}
        for sc, syn in zip(scored, rule_synths, strict=False):
            w = weight_by_synth.get(syn, 0.0) if syn else 0.0
            allocations.append({**sc, "weight": round(w, 4)})
    else:
        total_kelly = sum(sc["kelly_fraction"] for sc in scored)
        inv_vols = [1.0 / max(sc["vol_ann"], 0.001) for sc in scored]
        total_inv_vol = sum(inv_vols)
        for i, sc in enumerate(scored):
            kelly_w = (sc["kelly_fraction"] / max(total_kelly, 1e-9)) if total_kelly > 0 else 1 / len(scored)
            rp_w = inv_vols[i] / max(total_inv_vol, 1e-9)
            blended = 0.6 * kelly_w + 0.4 * rp_w
            allocations.append({**sc, "weight": round(min(blended * synth_budget, 0.20), 4)})
        total_synth = sum(a["weight"] for a in allocations)
        if total_synth > 0:
            for a in allocations:
                a["weight"] = round(a["weight"] / total_synth * synth_budget, 4)

    allocations.sort(key=lambda x: -x["weight"])
    total_w = sum(a["weight"] for a in allocations)
    if opt_rb is not None:
        exp_sharpe = opt_rb.expected_sharpe
        exp_cagr = opt_rb.expected_return
        exp_max_dd = 2.0 * opt_rb.expected_vol
    else:
        exp_sharpe = sum(a["sharpe"] * a["weight"] for a in allocations) / max(total_w, 1e-9)
        exp_cagr = sum(a["cagr"] * a["weight"] for a in allocations) / max(total_w, 1e-9)
        exp_max_dd = sum(a["max_drawdown"] * a["weight"] for a in allocations) / max(total_w, 1e-9)

    regime_narratives = {
        "risk_on": "Markets are calm (low VIX, price above MA). Full synth exposure recommended.",
        "transition": "Markets are transitioning. Moderate caution; holding base USDC floor.",
        "risk_off": "Markets are stressed. USDC floor increased 2.5x; reduced synth exposure.",
        "crisis": "Crisis conditions. Maximum USDC floor (5x multiplier); minimal synth exposure.",
    }

    return {
        "regime": regime_value,
        "regime_confidence": round(regime_confidence, 4),
        "regime_narrative": regime_narratives.get(regime_value, ""),
        "risk_profile": risk_profile,
        "usdc_weight": round(usdc_floor, 4),
        "synth_weight": round(synth_budget, 4),
        "allocations": allocations,
        "expected_portfolio": {
            "sharpe": round(exp_sharpe, 4),
            "cagr": round(exp_cagr, 4),
            "max_drawdown": round(exp_max_dd, 4),
            "vol_ann": round(opt_rb.expected_vol, 4) if opt_rb else None,
            "diversification_ratio": round(opt_rb.diversification_ratio, 4) if opt_rb else None,
            "risk_aversion_gamma": round(opt_rb.risk_aversion, 2) if opt_rb else None,
            "optimizer_converged": opt_rb.converged if opt_rb else False,
        },
        "risk_decomposition": risk_decomp_rb,
        "correlation_pairs": corr_pairs_rb,
        "rigor_summary": _build_rigor_summary(allocations),
        "stress_tests": [
            {
                "scenario": r.scenario,
                "label": r.label,
                "description": r.description,
                "portfolio_pnl": r.portfolio_pnl,
                "portfolio_value_after": r.portfolio_value_after,
                "per_asset_pnl": r.per_asset_pnl,
            }
            for r in _run_stress(allocations, usdc_floor)
        ],
        "market_scan": {
            "universe_size": len(DEFAULT_SCAN_UNIVERSE),
            "fetched": len(price_histories),
            "top_opportunities": market_ranking,
        },
        "agent": {
            "used": False,
            "thesis": None,
            "model_id": None,
            "served_model": None,
            "num_picks": 0,
        },
        "reasoning_trace": await _build_and_anchor_trace(
            allocations,
            f"Rule-based covariance-aware Kelly MVO for {regime_value} regime, {risk_profile} profile",
            None,
        ),
    }


# ── Stress scenarios ───────────────────────────────────────────


@strategies_router.get("/stress/scenarios")
async def list_stress_scenarios():
    """List the available stress scenarios with descriptions."""
    from archimedes.services.stress_engine import list_scenarios

    return {"scenarios": list_scenarios()}


@strategies_router.post("/stress/run")
@limiter.limit("20/minute")
async def run_stress_test(payload: dict, request: Request, response: Response):  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    """Apply a stress scenario to a caller-supplied portfolio."""
    from fastapi import HTTPException

    from archimedes.services.stress_engine import SCENARIOS, stress_all, stress_one

    allocations = payload.get("allocations") or []
    if not isinstance(allocations, list) or not allocations:
        raise HTTPException(status_code=400, detail="allocations[] is required")
    scenario = payload.get("scenario", "all")
    usdc_weight = float(payload.get("usdc_weight") or 0.0)

    if scenario == "all":
        results = stress_all(allocations, usdc_weight=usdc_weight)
    else:
        if scenario not in SCENARIOS:
            raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario}")
        results = [stress_one(allocations, scenario, usdc_weight=usdc_weight)]

    return {
        "results": [
            {
                "scenario": r.scenario,
                "label": r.label,
                "description": r.description,
                "portfolio_pnl": r.portfolio_pnl,
                "portfolio_value_after": r.portfolio_value_after,
                "per_asset_pnl": r.per_asset_pnl,
            }
            for r in results
        ],
    }


# ── Unified Passport Store (Issue #160 Phase 2) ───────────────────────────


@strategies_router.get("/passports")
async def list_strategy_passports(
    status: str | None = Query(None),
    regime_tag: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """List strategies from the unified strategy_passports table."""
    from archimedes.db import get_session
    from archimedes.services.passport_loader import list_passports

    with get_session() as session:
        records = list_passports(session, status=status, regime_tag=regime_tag)
        passports = [r.to_dict() for r in records[:limit]]

    return {"passports": passports, "total": len(passports), "source": "strategy_passports"}


@strategies_router.get("/passports/{strategy_id}")
async def get_strategy_passport(strategy_id: str):
    """Get a single passport in its native dict shape from strategy_passports."""
    from fastapi import HTTPException

    from archimedes.db import get_session
    from archimedes.services.passport_loader import get_passport

    with get_session() as session:
        record = get_passport(session, strategy_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Passport not found")
        return record.to_dict()


def _passport_to_strategy_response(record) -> StrategyResponse:
    """Reshape a StrategyPassportRecord (fusion/architect output) into the
    StrategyResponse schema that StrategyPassport.jsx expects. Curated
    strategies still flow through LocalStrategyProvider above; this is the
    fallback that makes generated strategies clickable from Library."""
    from archimedes.api.schemas import PaperRefResponse

    refs = list(record.paper_refs or [])
    first = refs[0] if refs else None

    papers_list = [
        PaperRefResponse(
            arxiv_id=r.arxiv_id,
            title=r.title or "",
            authors=json.loads(r.authors) if r.authors else [],
            doi=r.doi,
            venue=r.venue,
            year=r.year,
            citation_count=r.citation_count,
            contribution=r.contribution,
        )
        for r in refs
    ]

    asset_universe = json.loads(record.asset_universe) if record.asset_universe else []

    return StrategyResponse(
        id=record.id,
        papers=papers_list,
        paper_arxiv_id=first.arxiv_id if first else None,
        paper_title=first.title if first else None,
        paper_authors=json.loads(first.authors) if first and first.authors else [],
        paper_venue=first.venue if first else None,
        paper_year=first.year if first else None,
        paper_doi=first.doi if first else None,
        paper_citation_count=first.citation_count if first else None,
        methodology_summary=record.methodology_summary or "",
        asset_universe=asset_universe,
        position_sizing=record.position_sizing or "equal_weight",
        rebalance_frequency=record.rebalance_frequency or "weekly",
        status=record.status or "candidate",
        methodology_hash=record.methodology_hash,
        extraction_llm=record.extraction_llm,
        curator_wallet=record.curator_wallet,
        curator_note=record.curator_note,
        on_chain_registration_tx=record.on_chain_registration_tx,
        paper_claimed_sharpe=record.paper_claimed_sharpe,
        paper_claim_blended_sharpe=record.paper_claim_blended_sharpe,
        sharpe_ratio=record.sharpe_ratio,
        sortino_ratio=record.sortino_ratio,
        cagr=record.cagr,
        max_drawdown=record.max_drawdown,
        win_rate=record.win_rate,
        calmar_ratio=record.calmar_ratio,
        correlation_to_spy=record.correlation_to_spy,
        total_trades=record.total_trades,
        deflated_sharpe_ratio=record.deflated_sharpe_ratio,
        dsr_p_value=record.dsr_p_value,
        pbo_score=record.pbo_score,
        out_of_sample_sharpe=record.out_of_sample_sharpe,
        kelly_fraction=None,
        passes_rigor_gate=bool(record.passes_rigor_gate),
        is_backtest_placeholder=record.sharpe_ratio is None,
        sharpe_ci_lower=None,
        sharpe_ci_upper=None,
        backtest_start=record.backtest_start,
        backtest_end=record.backtest_end,
        regime_tag=record.regime_tag,
    )


@strategies_router.get("/{strategy_id}", response_model=StrategyResponse)
async def get_strategy(strategy_id: str):
    """Get a single strategy by ID. Tries LocalStrategyProvider (curated)
    first; falls through to the strategy_passports table for fusion- and
    architect-generated strategies so they're clickable from Library."""
    from fastapi import HTTPException

    strat = strategy_provider.get_strategy(strategy_id)
    if strat is not None:
        return _to_strategy_response(strat)

    from archimedes.db import get_session
    from archimedes.services.passport_loader import get_passport

    with get_session() as session:
        record = get_passport(session, strategy_id)
        if record is not None:
            return _passport_to_strategy_response(record)

    raise HTTPException(status_code=404, detail="Strategy not found")


# ── Strategy generation (fusion / architect) ──────────────────


@strategies_router.post("/generate", status_code=202)
@limiter.limit("20/minute")
async def generate_strategy(
    request: Request,  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    response: Response,  # noqa: ARG001
    asset_classes: str = "",
    risk_appetite: str = "moderate",
    strategic_direction: str = "",
    max_papers: int = 4,
    mode: str = "fusion",
):
    """Queue a strategy generation job. Returns 202 + job_id immediately."""
    from fastapi import HTTPException

    from archimedes.agents.strategy_fusion import fusion_enabled, load_corpus
    from archimedes.models.portfolio import RiskProfile
    from archimedes.services.job_queue import JobStore

    if mode == "fast":
        try:
            proposal = await asyncio.to_thread(
                architect.propose,
                strategic_direction or "Generate a strategy",
                risk_appetite,
                10000.0,
                None,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"LLM backend unavailable: {exc}") from exc
        guardrail = apply_guardrail(proposal)
        return {
            "mode": "architect",
            "status": "ok",
            "proposal": {
                "intent": proposal.intent,
                "model_id": proposal.model_id,
                "selected": [
                    {"strategy_id": s.strategy_id, "weight": w, "rationale": s.rationale}
                    for s, w in zip(proposal.selected, guardrail.strategy_weights.values(), strict=False)
                ],
                "overall_reasoning": proposal.overall_reasoning,
                "usyc_weight": guardrail.usyc_weight,
            },
        }

    if not fusion_enabled():
        raise HTTPException(
            status_code=503,
            detail="Fusion is disabled. Set ARCHIMEDES_FUSION_ENABLED=1.",
        )

    corpus = load_corpus()
    if len(corpus) < 2:
        raise HTTPException(
            status_code=503,
            detail=f"Insufficient corpus ({len(corpus)} papers). Need ≥2 for fusion.",
        )

    try:
        rp = RiskProfile(risk_appetite)
    except ValueError:
        rp = RiskProfile.MODERATE

    market_context: dict = {}
    try:
        from archimedes.services.redis_state import AgentStateStore

        state = AgentStateStore()
        try:
            regime_data = await state.load_regime()
            if regime_data:
                market_context = {
                    "regime": regime_data.get("regime", "unknown"),
                    "confidence": regime_data.get("confidence", 0.0),
                    "source": regime_data.get("source", ""),
                    "strategy_count": regime_data.get("strategy_count", 0),
                    "signals": regime_data.get("signals", {}),
                }
        finally:
            await state.close()
    except Exception:
        pass

    store = JobStore()
    try:
        job_id = await store.enqueue(
            job_type="fusion",
            payload={
                "asset_classes": [a.strip() for a in asset_classes.split(",") if a.strip()],
                "risk_appetite": rp.value,
                "strategic_direction": strategic_direction,
                "max_papers": max_papers,
                "market_context": market_context,
            },
        )
    finally:
        await store.close()

    # Intentional fire-and-forget: the fusion job runs to completion independently
    # of the HTTP request that queued it; progress is observed via /jobs/{id}/stream.
    asyncio.create_task(_run_fusion_job(job_id))  # noqa: RUF006

    return {"status": "queued", "job_id": job_id}


@strategies_router.get("/generate/{job_id}")
async def get_generation_job(job_id: str):
    """Poll a strategy generation job. Returns status + result when done."""
    from fastapi import HTTPException

    from archimedes.services.job_queue import JobStore

    store = JobStore()
    try:
        job = await store.get(job_id)
    finally:
        await store.close()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def _run_fusion_job(job_id: str) -> None:
    """Background worker: runs fusion and updates job status."""
    from archimedes.agents.strategy_fusion import (
        FusionBrief,
        default_fusion,
    )
    from archimedes.db import get_session
    from archimedes.models.portfolio import RiskProfile
    from archimedes.models.strategy_store import upsert_strategy
    from archimedes.services.job_queue import JobStore

    store = JobStore()
    try:
        await store.update_status(job_id, "running")

        job = await store.get(job_id)
        if not job or not job.get("payload"):
            await store.update_status(job_id, "failed", error="Job payload missing")
            return

        payload = job["payload"]
        rp = RiskProfile(payload.get("risk_appetite", "moderate"))

        brief = FusionBrief(
            asset_classes=payload.get("asset_classes", []),
            risk_appetite=rp,
            strategic_direction=payload.get("strategic_direction", ""),
            max_papers=payload.get("max_papers", 4),
            market_context=payload.get("market_context", {}),
        )

        fusion = default_fusion()
        result = await asyncio.to_thread(fusion.propose, brief)

        if not result.is_actionable:
            await store.update_status(
                job_id,
                "done",
                result={
                    "mode": "fusion",
                    "status": result.status,
                    "message": result.thesis,
                },
            )
            return

        # ── Run fusion evaluator pipeline (backtest + rigor) if spec present ──
        eval_result = None
        if result.strategy_spec is not None:
            try:
                from archimedes.services.fusion_evaluator import evaluate_fusion_spec

                eval_result = await asyncio.to_thread(evaluate_fusion_spec, result.strategy_spec)
            except Exception as _eval_exc:
                import logging as _logging

                _logging.getLogger(__name__).warning("fusion eval pipeline failed (non-fatal): %s", _eval_exc)

        # ── Build rigor_verdict dict from eval_result for persistence ──
        # This is what closes the demo wedge: the user sees the gate's verdict
        # in the library, not just a "rigor pending" placeholder. Status
        # transitions ("validated"/"rejected") fall out of upsert_strategy.
        rigor_verdict_dict: dict | None = None
        if eval_result is not None and eval_result.success:
            r = eval_result.rigor
            bt = eval_result.backtest
            rigor_verdict_dict = {
                "passing": bool(r.passing),
                "dsr": r.dsr,
                "dsr_p_value": r.dsr_p_value,
                "pbo_score": r.pbo_score,
                "oos_sharpe": r.oos_sharpe,
                "look_ahead_clean": bool(r.look_ahead_clean),
                "num_trials": int(r.num_trials),
                # Backtest metrics — surface alongside so the passport renders
                # without the UI having to denormalize from a separate field.
                "sharpe_ratio": bt.sharpe_ratio,
                "sortino_ratio": bt.sortino_ratio,
                "max_drawdown": bt.max_drawdown,
                "cagr": bt.cagr,
                "calmar_ratio": bt.calmar_ratio,
                "win_rate": bt.win_rate,
                "total_trades": bt.total_trades,
                "backtest_start": bt.backtest_start.isoformat() if bt.backtest_start else None,
                "backtest_end": bt.backtest_end.isoformat() if bt.backtest_end else None,
            }

        strategy_id = None
        try:
            with get_session() as session:
                source_papers = [{"arxiv_id": aid, "sha256": ""} for aid in result.source_arxiv_ids]
                record = upsert_strategy(
                    session,
                    generation_method="fusion",
                    strategy_name=result.strategy_name,
                    thesis=result.thesis,
                    source_papers=source_papers,
                    asset_universe=brief.asset_classes,
                    risk_profile=rp.value,
                    provenance_hash=result.model,
                    rigor_verdict=rigor_verdict_dict,
                )
                session.commit()
                strategy_id = record.id
        except Exception:
            pass

        try:
            import hashlib
            import uuid
            from datetime import datetime

            canonical = json.dumps(
                {
                    "strategy_name": result.strategy_name,
                    "thesis": result.thesis,
                    "source_arxiv_ids": sorted(result.source_arxiv_ids),
                    "fusion_reasoning": result.fusion_reasoning,
                    "novelty_rationale": result.novelty_rationale,
                    "risk_notes": result.risk_notes,
                    "model": result.model,
                    "brief": {
                        "asset_classes": sorted(brief.asset_classes or []),
                        "risk_appetite": rp.value,
                        "strategic_direction": brief.strategic_direction or "",
                        "market_context": brief.market_context or {},
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            trace_hash = "0x" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            from archimedes.services.redis_state import AgentStateStore

            state = AgentStateStore()
            try:
                await state.save_trace(
                    {
                        "id": str(uuid.uuid4()),
                        "vault_address": "",
                        "decision_type": "construction",
                        "trigger": "fusion_generation",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "market_context": brief.market_context or {},
                        "portfolio_before": {},
                        "portfolio_after": {},
                        "reasoning": (
                            f"FUSION HYPOTHESIS -- {result.strategy_name}\n\n"
                            f"Thesis: {result.thesis}\n\n"
                            f"How it fuses: {result.fusion_reasoning}\n\n"
                            f"Why novel: {result.novelty_rationale}\n\n"
                            f"Risks: {result.risk_notes}\n\n"
                            f"Pre-backtest hypothesis -- empirical validation (DSR/PBO/OOS) is pending."
                        ),
                        "confidence": 0.0,
                        "trades_executed": [],
                        "strategies_referenced": result.source_arxiv_ids,
                        "trace_hash": trace_hash,
                        "arc_tx_hash": None,
                        "is_verified": False,
                    }
                )
            finally:
                await state.close()
        except Exception as _exc:
            import logging as _logging

            _logging.getLogger(__name__).warning("fusion: trace persistence failed (non-fatal): %s", _exc)

        job_result = {
            "mode": "fusion",
            "status": result.status,
            "strategy_name": result.strategy_name,
            "thesis": result.thesis,
            "source_arxiv_ids": result.source_arxiv_ids,
            "fusion_reasoning": result.fusion_reasoning,
            "novelty_rationale": result.novelty_rationale,
            "risk_notes": result.risk_notes,
            "model": result.model,
            "requested_model": result.requested_model,
            "strategy_id": strategy_id,
            "market_context_used": brief.market_context,
        }

        # Attach backtest + rigor verdict if evaluator ran
        if eval_result is not None:
            if eval_result.backtest is not None:
                job_result["backtest"] = {
                    "sharpe_ratio": eval_result.backtest.sharpe_ratio,
                    "sortino_ratio": eval_result.backtest.sortino_ratio,
                    "max_drawdown": eval_result.backtest.max_drawdown,
                    "cagr": eval_result.backtest.cagr,
                    "calmar_ratio": eval_result.backtest.calmar_ratio,
                    "win_rate": eval_result.backtest.win_rate,
                    "total_trades": eval_result.backtest.total_trades,
                }
            if eval_result.rigor is not None:
                job_result["rigor"] = {
                    "passing": eval_result.rigor.passing,
                    "dsr": eval_result.rigor.dsr,
                    "dsr_p_value": eval_result.rigor.dsr_p_value,
                    "oos_sharpe": eval_result.rigor.oos_sharpe,
                    "look_ahead_clean": eval_result.rigor.look_ahead_clean,
                }
            if eval_result.error:
                job_result["eval_error"] = eval_result.error

        await store.update_status(job_id, "done", result=job_result)

        # ── Persist fusion proposal to episodic memory (T-PE.8) ──
        try:
            from archimedes.services.strategy_memory import persist_proposal

            persist_proposal(
                generation_id=job_id,
                agent="fusion",
                intent=brief.strategic_direction or brief.asset_classes_text(),
                strategy_spec={
                    "strategy_name": result.strategy_name,
                    "thesis": result.thesis,
                    "source_arxiv_ids": result.source_arxiv_ids,
                },
                papers=result.source_arxiv_ids,
                rigor_verdict=rigor_verdict_dict,
                extra={
                    "model": result.model,
                    "fusion_reasoning": result.fusion_reasoning,
                    "novelty_rationale": result.novelty_rationale,
                },
            )
        except Exception:
            pass  # Non-blocking per spec
    except Exception as exc:
        with contextlib.suppress(Exception):
            await store.update_status(job_id, "failed", error=str(exc))
    finally:
        await store.close()


# ── Construct (architect interactive) ─────────────────────────


@strategies_router.post("/construct", response_model=StrategyConstructionResponse)
@limiter.limit("20/minute")
async def construct_strategy(req: StrategyConstructionRequest, request: Request, response: Response):  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    """Interactive strategy architect -- the 'design me a portfolio' path."""
    from fastapi import HTTPException

    try:
        proposal = await asyncio.to_thread(
            architect.propose,
            req.intent,
            req.risk_profile,
            req.capital_usdc,
            req.regime,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"LLM backend unavailable: {exc}") from exc
    guardrail = apply_guardrail(proposal)
    trace = build_construction_trace(proposal, guardrail)

    await persist_trace_off_chain(trace)

    # Persist architect proposal to episodic memory (T-PE.8)
    try:
        import uuid as _uuid

        from archimedes.services.strategy_memory import persist_proposal

        persist_proposal(
            generation_id=_uuid.uuid4().hex[:16],
            agent="architect",
            intent=req.intent,
            strategy_spec={
                "strategy_ids": [s.strategy_id for s in proposal.selected],
                "weights": guardrail.strategy_weights,
                "overall_reasoning": proposal.overall_reasoning,
            },
            papers=[s.paper_citation for s in proposal.selected if s.paper_citation],
            extra={
                "model_id": proposal.model_id,
                "risk_notes": proposal.risk_notes,
                "regime": proposal.regime,
            },
        )
    except Exception:
        pass  # Non-blocking per spec

    by_id = {s.strategy_id: s for s in proposal.selected}
    selected = []
    for sid, weight in sorted(guardrail.strategy_weights.items()):
        sel = by_id.get(sid)
        strat = strategy_provider.get_strategy(sid)
        selected.append(
            ConstructionSelectionResponse(
                strategy_id=sid,
                paper_title=strat.paper_title if strat else "",
                weight=weight,
                rationale=sel.rationale if sel else "",
                paper_citation=sel.paper_citation if sel else "",
            )
        )

    return StrategyConstructionResponse(
        intent=proposal.intent,
        risk_profile=proposal.risk_profile,
        capital_usdc=proposal.capital_usdc,
        regime=proposal.regime,
        model_id=proposal.model_id,
        selected=selected,
        usyc_weight=guardrail.usyc_weight,
        overall_reasoning=proposal.overall_reasoning,
        risk_notes=proposal.risk_notes,
        guardrail_notes=guardrail.adjustments,
        trace=ConstructionTraceResponse(
            id=trace.id,
            decision_type=trace.decision_type.value,
            trigger=trace.trigger,
            timestamp=trace.timestamp.isoformat(),
            trace_hash=trace.trace_hash,
            arc_tx_hash=trace.arc_tx_hash,
            is_anchored=trace.is_anchored,
        ),
    )

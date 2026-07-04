"""Regime endpoints — /api/regime/*."""

from __future__ import annotations

from fastapi import APIRouter

from archimedes.api._route_helpers import strategy_provider
from archimedes.api.schemas import RegimeResponse, RegimeSignalsResponse

regime_router = APIRouter(prefix="/api/regime", tags=["regime"])


@regime_router.get("/current", response_model=RegimeResponse)
async def get_current_regime():
    """Get current market regime -- reads live state from Redis (agent writes it)."""
    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
        data = await state.load_regime()
        if not data:
            # No exogenous market regime yet (no detector wired). Fall back to
            # the endogenous ensemble consensus so the surface still shows the
            # agent's directional state — labelled `source=strategy_consensus`
            # so callers know it is consensus, not a market regime (#659).
            consensus = await state.load_ensemble_consensus()
            if consensus:
                data = {**consensus, "regime": consensus.get("label", "unknown")}
    except Exception:
        data = None
    finally:
        await state.close()

    # Default transition priors (Dirichlet-inspired)
    default_transitions = {
        "risk_on": {"risk_on": 0.85, "transition": 0.10, "risk_off": 0.04, "crisis": 0.01},
        "transition": {"risk_on": 0.20, "transition": 0.50, "risk_off": 0.25, "crisis": 0.05},
        "risk_off": {"risk_on": 0.05, "transition": 0.15, "risk_off": 0.70, "crisis": 0.10},
        "crisis": {"risk_on": 0.02, "transition": 0.08, "risk_off": 0.30, "crisis": 0.60},
    }

    if data:
        regime_value = data.get("regime", "unknown")
        raw_transitions = data.get("transition_probabilities")
        transitions = raw_transitions or default_transitions
        transitions_source = "redis_measured" if raw_transitions else "default_prior"
        history = data.get("regime_history_summary") or {"total": 0}

        regime_to_keywords = {
            "risk_on": ["momentum", "tsmom", "52w_high", "52-week"],
            "transition": ["volatility", "managed", "tsmom"],
            "risk_off": ["volatility", "managed", "t-bill"],
            "crisis": ["t-bill", "preservation", "capital"],
        }
        all_strats = strategy_provider().list_strategies()
        regime_keywords = regime_to_keywords.get(regime_value, [])
        recommended_ids: list[str] = []
        recommended_titles: list[str] = []
        for keyword in regime_keywords:
            for s in all_strats:
                title_lower = s.paper_title.lower().replace("_", " ")
                if (
                    keyword in title_lower or keyword.replace("-", "") in title_lower.replace("-", "")
                ) and s.id not in recommended_ids:
                    recommended_ids.append(s.id)
                    recommended_titles.append(s.paper_title)
                    break

        # NOTE: do NOT coerce missing vix/* into 0.0 — see RegimeSignalsResponse
        # docstring. None means "no data" and the UI hides the row honestly.
        raw_vix = data.get("vix_level")
        if raw_vix is None:
            raw_vix = data.get("vix")

        return RegimeResponse(
            regime=regime_value,
            confidence=data.get("confidence", 0.0),
            timestamp=data.get("timestamp", ""),
            regime_changed=data.get("regime_changed", False),
            signals=RegimeSignalsResponse(
                vix_level=raw_vix,
                sp500_above_ma50=data.get("sp500_above_ma50", True),
                sp500_above_ma200=data.get("sp500_above_ma200", True),
                vix_rate_of_change=data.get("vix_rate_of_change"),
                vix_score=data.get("vix_score"),
                ma_score=data.get("ma_score"),
                composite_score=data.get("composite_score"),
                credit_spread_ig=data.get("credit_spread_ig"),
                credit_spread_hy=data.get("credit_spread_hy"),
                btc_dominance=data.get("btc_dominance"),
            ),
            transition_probabilities=transitions,
            transitions_source=transitions_source,
            regime_history=history,
            recommended_strategies=recommended_ids[:2],
            recommended_strategy_titles=recommended_titles[:2],
        )

    return RegimeResponse(
        regime="unknown",
        confidence=0.0,
        timestamp="",
        regime_changed=False,
        signals=RegimeSignalsResponse(
            vix_level=None,
            sp500_above_ma50=True,
            sp500_above_ma200=True,
        ),
        transition_probabilities=default_transitions,
        transitions_source="default_prior",
        regime_history={"total": 0},
        recommended_strategies=[],
        recommended_strategy_titles=[],
    )


@regime_router.get("/transitions")
async def get_regime_transitions():
    """Get regime transition probability matrix."""
    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
        data = await state.load_regime()
        transitions = data.get("transition_probabilities") if data else None
        history = data.get("regime_history_summary") if data else None
    except Exception:
        transitions = None
        history = None
    finally:
        await state.close()

    source = "redis_measured" if transitions else "default_prior"
    if not transitions:
        transitions = {
            "risk_on": {"risk_on": 0.85, "transition": 0.10, "risk_off": 0.04, "crisis": 0.01},
            "transition": {"risk_on": 0.20, "transition": 0.50, "risk_off": 0.25, "crisis": 0.05},
            "risk_off": {"risk_on": 0.05, "transition": 0.15, "risk_off": 0.70, "crisis": 0.10},
            "crisis": {"risk_on": 0.02, "transition": 0.08, "risk_off": 0.30, "crisis": 0.60},
        }

    return {
        "transition_probabilities": transitions,
        "history": history or {"total": 0},
        "source": source,
    }

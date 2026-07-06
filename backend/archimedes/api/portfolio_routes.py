"""Portfolio optimization endpoints — /api/portfolio/*.

Thin HTTP layer over :func:`archimedes.services.portfolio_optimizer.optimize_weights`.
The solvers themselves (MVO / HRP / Black-Litterman / robust) landed via #554 +
#570; this router exposes them to the UI (PortfolioAdvisorPanels' optimizer
selector POSTs here).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from archimedes.api.limiter import limiter

logger = logging.getLogger(__name__)

portfolio_router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

_METHOD_ALIASES = {
    "mvo": "mvo",
    "hrp": "hrp",
    "bl": "black_litterman",
    "black_litterman": "black_litterman",
    "robust": "robust",
}

_PRICE_FETCH_TIMEOUT_S = 45.0
_MIN_HISTORY_BARS = 60

_ALLOWED_SWEEP_PARAMS = {"rebalance_days", "tx_cost_bps", "initial_cash"}

PREDEFINED_SCENARIOS = [
    {
        "name": "Equity Crash 2008-style",
        "shocks": [
            {"asset": "SPY", "shock": -0.40},
            {"asset": "QQQ", "shock": -0.48},
            {"asset": "TLT", "shock": +0.14},
            {"asset": "GLD", "shock": -0.28},
        ],
    },
    {
        "name": "COVID Crash (Mar 2020)",
        "shocks": [
            {"asset": "SPY", "shock": -0.34},
            {"asset": "QQQ", "shock": -0.29},
            {"asset": "TLT", "shock": +0.15},
            {"asset": "GLD", "shock": -0.08},
        ],
    },
    {
        "name": "Rate Shock +200bps (2022)",
        "shocks": [
            {"asset": "TLT", "shock": -0.28},
            {"asset": "AGG", "shock": -0.13},
            {"asset": "SPY", "shock": -0.19},
            {"asset": "QQQ", "shock": -0.33},
        ],
    },
    {
        "name": "Tech Crash -30%",
        "shocks": [
            {"asset": "QQQ", "shock": -0.30},
            {"asset": "SPY", "shock": -0.15},
        ],
    },
    {
        "name": "Flight to Safety",
        "shocks": [
            {"asset": "SPY", "shock": -0.10},
            {"asset": "TLT", "shock": +0.08},
            {"asset": "GLD", "shock": +0.06},
        ],
    },
    {
        "name": "Stagflation (bonds & equities down)",
        "shocks": [
            {"asset": "SPY", "shock": -0.20},
            {"asset": "TLT", "shock": -0.15},
            {"asset": "GLD", "shock": +0.12},
        ],
    },
]


class OptimizeRequest(BaseModel):
    method: str = Field("mvo", description="One of: mvo, hrp, bl, black_litterman, robust")
    risk_profile: str = Field("moderate", pattern="^(fixed_income|conservative|moderate|aggressive|hyper_risky)$")
    symbols: list[str] | None = Field(
        None,
        description="Synth symbols to allocate across; defaults to the standard scan universe.",
        max_length=50,
    )


class ParameterSweepRequest(BaseModel):
    strategy_id: str
    weights: dict[str, float]
    param1_name: str
    # Compute-amplification guard (audit 2026-06-14): the sweep runs one full
    # backtest per cell of the param1 × param2 Cartesian product. Without a
    # bound, a single unauthenticated request with two large ranges schedules
    # millions of backtests and pins the worker (DoS). 25 × 25 = 625 cells is a
    # generous heatmap ceiling; the request is rejected at validation, before
    # any compute. min_length=1 keeps an empty range from yielding zero cells.
    param1_range: list[float] = Field(..., min_length=1, max_length=25)
    param2_name: str
    param2_range: list[float] = Field(..., min_length=1, max_length=25)
    metric: str = "sharpe_ratio"
    start_date: str | None = None
    end_date: str | None = None


class ScenarioShock(BaseModel):
    asset: str
    shock: float


class ScenarioRequest(BaseModel):
    name: str
    shocks: list[ScenarioShock]


class ScenarioAnalysisRequest(BaseModel):
    # Compute-amplification guard (#917): scenario analysis iterates
    # len(weights) × len(scenarios), so both must be bounded at validation —
    # before any work — or a single unauthenticated request can pin the worker.
    weights: dict[str, float] = Field(..., max_length=100)
    portfolio_value: float = 10000.0
    scenarios: list[ScenarioRequest] | None = Field(None, max_length=10)


@portfolio_router.post("/optimize")
@limiter.limit("5/minute")
async def optimize_portfolio(req: OptimizeRequest, request: Request) -> dict:  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    """Compute optimal synth-asset weights with the requested optimizer.

    Returns {method, optimizer, risk_profile, usdc_weight, weights, symbols_used,
    history_bars, timestamp}. Weights sum to (1 - usdc_floor) for the profile;
    the USDC floor is reported separately as usdc_weight.

    503 when price history is unavailable (e.g. data provider down) — the UI
    degrades gracefully on that status.
    """
    from archimedes.models.portfolio import RISK_PROFILE_PARAMS, RiskProfile
    from archimedes.services.portfolio_optimizer import optimize_weights
    from archimedes.services.strategy_signal_evaluator import (
        DEFAULT_SCAN_UNIVERSE,
        _fetch_price_histories,
    )

    optimizer = _METHOD_ALIASES.get(req.method.lower())
    if optimizer is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown optimizer '{req.method}'. Expected one of {sorted(_METHOD_ALIASES)}.",
        )

    rp = RiskProfile(req.risk_profile)
    usdc_floor = RISK_PROFILE_PARAMS[rp]["usyc_floor"]
    synth_budget = max(0.0, 1.0 - usdc_floor)

    universe = req.symbols or list(DEFAULT_SCAN_UNIVERSE)
    try:
        price_histories = await asyncio.wait_for(
            asyncio.to_thread(_fetch_price_histories, universe, "2y"),
            timeout=_PRICE_FETCH_TIMEOUT_S,
        )
    except Exception:
        price_histories = {}

    daily_returns: dict[str, list[float]] = {}
    for sym, series in price_histories.items():
        rets = series.pct_change().dropna()
        if len(rets) >= _MIN_HISTORY_BARS:
            daily_returns[sym] = rets.tolist()

    if len(daily_returns) < 2:
        raise HTTPException(
            status_code=503,
            detail="Price history unavailable — optimizer needs ≥2 assets with ≥60 bars of returns.",
        )

    symbols = sorted(daily_returns)
    weights = await asyncio.to_thread(
        optimize_weights,
        symbols,
        daily_returns,
        rp,
        synth_budget,
        optimizer,
    )

    return {
        "method": req.method,
        "optimizer": optimizer,
        "risk_profile": req.risk_profile,
        "usdc_weight": round(usdc_floor, 4),
        "weights": {s: round(w, 6) for s, w in weights.items()},
        "symbols_used": symbols,
        "history_bars": min(len(r) for r in daily_returns.values()),
        "timestamp": datetime.now(UTC).isoformat(),
    }


@portfolio_router.post("/parameter-sweep")
@limiter.limit("5/minute")
async def parameter_sweep(req: ParameterSweepRequest, request: Request) -> dict:  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    """Run a 2D sensitivity sweep over two backtest parameters.

    Returns a heatmap-ready grid_2d with rows=param1 values, cols=param2 values,
    plus summary statistics (mean, std, range, sensitivity_ratio, best/worst params).

    422 on invalid param names or sweep failure. 503 on unexpected errors.
    """
    from archimedes.services.portfolio_backtester import sensitivity_sweep

    if req.param1_name not in _ALLOWED_SWEEP_PARAMS:
        raise HTTPException(
            status_code=422,
            detail=f"param1_name must be one of {sorted(_ALLOWED_SWEEP_PARAMS)}, got '{req.param1_name}'.",
        )
    if req.param2_name not in _ALLOWED_SWEEP_PARAMS:
        raise HTTPException(
            status_code=422,
            detail=f"param2_name must be one of {sorted(_ALLOWED_SWEEP_PARAMS)}, got '{req.param2_name}'.",
        )

    param_grid = {
        req.param1_name: [int(v) for v in req.param1_range],
        req.param2_name: [int(v) for v in req.param2_range],
    }

    try:
        result = await asyncio.to_thread(
            sensitivity_sweep,
            strategy_id=req.strategy_id,
            weights=req.weights,
            param_grid=param_grid,
            metric=req.metric,
            start_date=req.start_date,
            end_date=req.end_date,
            n_workers=2,
        )
    except ValueError as exc:
        # ValueError carries intentional, user-facing validation feedback.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        # Unexpected failure — log full detail server-side, return generic message.
        logger.exception("Sensitivity sweep failed")
        raise HTTPException(status_code=503, detail="Sensitivity sweep failed") from exc

    rows = sorted({int(v) for v in req.param1_range})
    cols = sorted({int(v) for v in req.param2_range})

    cell_lookup: dict[tuple[int, int], float] = {}
    for cell in result.get("grid", []):
        p = cell.get("params", {})
        r_val = int(p.get(req.param1_name, 0))
        c_val = int(p.get(req.param2_name, 0))
        cell_lookup[(r_val, c_val)] = cell.get("metric_value", 0.0)

    # PERF: O(R*C) dense matrix build — bounded by the 25x25 (625-cell) sweep
    # schema cap, so this stays well under 1ms.
    grid_2d = [[cell_lookup.get((r, c), 0.0) for c in cols] for r in rows]

    return {
        "strategy_id": req.strategy_id,
        "param1_name": req.param1_name,
        "param1_range": rows,
        "param2_name": req.param2_name,
        "param2_range": cols,
        "metric": req.metric,
        "rows": rows,
        "cols": cols,
        "grid_2d": grid_2d,
        "metric_mean": result.get("metric_mean"),
        "metric_std": result.get("metric_std"),
        "metric_range": result.get("metric_range"),
        "sensitivity_ratio": result.get("sensitivity_ratio"),
        "best_params": result.get("best_params"),
        "worst_params": result.get("worst_params"),
    }


@portfolio_router.post("/scenario-analysis")
@limiter.limit("10/minute")
async def scenario_analysis(req: ScenarioAnalysisRequest, request: Request) -> dict:  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    """Apply mark-to-model stress shocks to a portfolio and return per-scenario P&L.

    Uses predefined scenarios when none are supplied. Applies a 1.2x stress beta
    amplification to reflect correlation clustering in crisis regimes, and adds a
    10bps round-trip rebalance cost estimate.

    Returns list of {scenario_name, base_value, scenario_value, impact_dollars,
    impact_pct, stress_adjusted_pct, estimated_rebalance_cost}.
    """
    scenarios = req.scenarios
    if scenarios is None:
        scenarios = [
            ScenarioRequest(
                name=s["name"],
                shocks=[ScenarioShock(asset=sh["asset"], shock=sh["shock"]) for sh in s["shocks"]],
            )
            for s in PREDEFINED_SCENARIOS
        ]

    results = []
    for scenario in scenarios:
        shock_map: dict[str, float] = {s.asset: s.shock for s in scenario.shocks}

        new_value = 0.0
        for asset, weight in req.weights.items():
            holding_value = weight * req.portfolio_value
            shock = shock_map.get(asset)
            if shock is None:
                for shock_asset, shock_val in shock_map.items():
                    if asset.lstrip("s") == shock_asset or shock_asset in asset:
                        shock = shock_val
                        break
            new_value += holding_value * (1.0 + (shock or 0.0))

        impact = new_value - req.portfolio_value
        impact_pct = impact / req.portfolio_value if req.portfolio_value else 0.0
        stress_adjusted_pct = impact_pct * 1.2
        estimated_rebalance_cost = abs(impact) * 0.0010

        results.append(
            {
                "scenario_name": scenario.name,
                "base_value": round(req.portfolio_value, 2),
                "scenario_value": round(new_value, 2),
                "impact_dollars": round(impact, 2),
                "impact_pct": round(impact_pct, 6),
                "stress_adjusted_pct": round(stress_adjusted_pct, 6),
                "estimated_rebalance_cost": round(estimated_rebalance_cost, 2),
            }
        )

    return {"portfolio_value": req.portfolio_value, "scenarios": results}

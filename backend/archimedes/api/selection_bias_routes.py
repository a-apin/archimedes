"""Selection-bias correction API routes.

Exposes the rigor gate for strategy validation. The main consumer is the
strategy-list page (shows PASS/FAIL per strategy) and the strategy detail
page (shows full gate breakdown).
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from archimedes.api.limiter import limiter

from archimedes.services.rigor_evaluator import (
    compute_pbo,
    run_rigor_gate,
)
from archimedes.services.strategy_provider import default_provider

selection_bias_router = APIRouter(prefix="/api/selection-bias", tags=["selection-bias"])

_provider = default_provider()


# ── Schemas ──────────────────────────────────────────────────


from pydantic import BaseModel


class RigorGateDetail(BaseModel):
    """Per-check pass/fail detail."""

    dsr: str = "MISSING"
    pbo: str = "MISSING"
    oos_sharpe: str = "MISSING"
    look_ahead: str = "MISSING"


class StrategyRigorResult(BaseModel):
    """Rigor gate result for a single strategy."""

    strategy_id: str
    strategy_name: str
    passes_all: bool
    gate_details: RigorGateDetail
    deflated_sharpe: float | None = None
    dsr_p_value: float | None = None
    pbo_score: float | None = None
    oos_sharpe: float | None = None
    in_sample_sharpe: float | None = None


class RigorGateResponse(BaseModel):
    """Response for the library-level rigor gate check."""

    strategies: list[StrategyRigorResult]
    total: int
    passing: int
    failing: int


class PBORequest(BaseModel):
    """Request to compute PBO for a set of strategy returns."""

    returns_matrix: dict[str, list[float]]
    s_partitions: int = 16


class PBOResponse(BaseModel):
    """PBO computation result."""

    pbo_scores: dict[str, float]
    interpretation: str


# ── Endpoints ────────────────────────────────────────────────


@selection_bias_router.get("/gate", response_model=RigorGateResponse)
async def evaluate_rigor_gate():
    """Evaluate the rigor gate for all strategies in the library.

    Runs DSR, PBO, walk-forward OOS, and look-ahead audit for each
    strategy. Returns pass/fail per strategy with detailed breakdown.
    """
    strategies = _provider.list_strategies()

    if not strategies:
        return RigorGateResponse(strategies=[], total=0, passing=0, failing=0)

    # ── Collect real daily returns from persisted backtest results ──
    from archimedes.db import get_session, init_db
    from archimedes.services.backtest_repository import (
        get_all_daily_returns,
        update_rigor_gate_fields,
    )

    init_db()

    strategy_ids = [s.id for s in strategies]
    strategy_code_map: dict[str, str | None] = {}

    # Load real returns from DB
    with get_session() as session:
        returns_by_strategy = get_all_daily_returns(session, strategy_ids)

    for s in strategies:
        code = _load_strategy_code(s.strategy_code_path) if s.strategy_code_path else None
        strategy_code_map[s.id] = code

    # Fallback: if no persisted data, try stub-based synthetic returns
    # (graceful degradation for strategies not yet backtested)
    for s in strategies:
        if (s.id not in returns_by_strategy or len(returns_by_strategy[s.id]) < 10) and s.stub_sharpe is not None:
            returns_by_strategy[s.id] = _synthetic_returns_from_stub(
                sharpe=s.stub_sharpe,
                cagr=s.stub_cagr,
                max_dd=s.stub_max_dd,
            )

    # Compute PBO across all strategies that have returns
    valid_returns = {k: v for k, v in returns_by_strategy.items() if len(v) >= 10}
    pbo_scores = compute_pbo(valid_returns) if len(valid_returns) >= 2 else {}

    num_trials = max(len(valid_returns), 1)

    # Run rigor gate for each strategy
    results: list[StrategyRigorResult] = []
    for s in strategies:
        daily_returns = returns_by_strategy.get(s.id, [])

        if len(daily_returns) < 10:
            results.append(
                StrategyRigorResult(
                    strategy_id=s.id,
                    strategy_name=s.paper_title,
                    passes_all=False,
                    gate_details=RigorGateDetail(
                        dsr="MISSING (no backtest data)",
                        pbo="MISSING (no backtest data)",
                        oos_sharpe="MISSING (no backtest data)",
                        look_ahead="MISSING (no code)",
                    ),
                )
            )
            continue

        # Use real in-sample Sharpe from persisted data when available
        in_sample_sharpe = s.stub_sharpe
        with get_session() as session:
            from archimedes.services.backtest_repository import (
                latest_backtests_by_strategy,
            )

            bt_map = latest_backtests_by_strategy(session, [s.id])
            if s.id in bt_map:
                in_sample_sharpe = bt_map[s.id].sharpe_ratio

        gate_result = run_rigor_gate(
            strategy_id=s.id,
            daily_returns=daily_returns,
            num_trials=num_trials,
            pbo_scores=pbo_scores,
            strategy_code=strategy_code_map.get(s.id),
            in_sample_sharpe=in_sample_sharpe,
            paper_claimed_sharpe=s.paper_claimed_sharpe,
        )

        # Persist rigor gate results to DB
        with get_session() as session:
            update_rigor_gate_fields(
                session,
                s.id,
                deflated_sharpe_ratio=gate_result.deflated_sharpe,
                dsr_p_value=gate_result.dsr_p_value,
                num_trials_in_selection=num_trials,
                pbo_score=gate_result.pbo_score,
                out_of_sample_sharpe=gate_result.oos_sharpe,
                look_ahead_audit_passed=gate_result.look_ahead_passed,
            )
            session.commit()

        details = gate_result.gate_details
        results.append(
            StrategyRigorResult(
                strategy_id=s.id,
                strategy_name=s.paper_title,
                passes_all=gate_result.passes_all,
                gate_details=RigorGateDetail(
                    dsr=details.get("dsr", "MISSING"),
                    pbo=details.get("pbo", "MISSING"),
                    oos_sharpe=details.get("oos_sharpe", "MISSING"),
                    look_ahead=details.get("look_ahead", "MISSING"),
                ),
                deflated_sharpe=gate_result.deflated_sharpe,
                dsr_p_value=gate_result.dsr_p_value,
                pbo_score=gate_result.pbo_score,
                oos_sharpe=gate_result.oos_sharpe,
                in_sample_sharpe=gate_result.in_sample_sharpe,
            )
        )

    passing = sum(1 for r in results if r.passes_all)
    return RigorGateResponse(
        strategies=results,
        total=len(results),
        passing=passing,
        failing=len(results) - passing,
    )


@selection_bias_router.get("/gate/{strategy_id}", response_model=StrategyRigorResult)
async def evaluate_strategy_rigor(strategy_id: str):
    """Evaluate rigor gate for a single strategy."""
    strategy = _provider.get_strategy(strategy_id)
    if strategy is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Strategy not found")

    # Run the full gate and extract the matching strategy result
    full_response = await evaluate_rigor_gate()
    for result in full_response.strategies:
        if result.strategy_id == strategy_id:
            return result

    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail="Strategy not found in gate results")


@selection_bias_router.post("/pbo", response_model=PBOResponse)
@limiter.limit("20/minute")
async def compute_pbo_endpoint(req: PBORequest, request: Request, response: Response):  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    """Compute PBO across a set of strategy return series.

    This is the library-level metric — all strategies get the same score.
    """
    pbo_scores = compute_pbo(req.returns_matrix, s_partitions=req.s_partitions)

    score = next(iter(pbo_scores.values())) if pbo_scores else 0.0
    if score >= 0.5:
        interpretation = (
            f"PBO={score:.4f}: The in-sample-optimal strategy is expected to "
            f"underperform the median out-of-sample. FAILED rigor gate."
        )
    else:
        interpretation = f"PBO={score:.4f}: Low overfitting probability. PASSED rigor gate."

    return PBOResponse(pbo_scores=pbo_scores, interpretation=interpretation)


# ── Helpers ──────────────────────────────────────────────────


def _synthetic_returns_from_stub(
    sharpe: float,
    cagr: float | None = None,  # noqa: ARG001 — accepted for forward-compat; current stub only needs sharpe
    max_dd: float | None = None,  # noqa: ARG001 — accepted for forward-compat; current stub only needs sharpe
    T: int = 504,  # ~2 years of daily data
) -> list[float]:
    """Generate synthetic daily returns matching stub Sharpe.

    This is a hackathon placeholder. In production, the analytics-engine
    runs the actual backtest and produces real BacktestResult.daily_returns.
    """
    import numpy as np

    rng = np.random.default_rng(hash(sharpe) % (2**32))

    # Target daily vol = 1% (reasonable for equity-like assets)
    daily_vol = 0.01
    # Target daily mean from annualized Sharpe
    daily_mean = sharpe * daily_vol / np.sqrt(252)

    return rng.normal(daily_mean, daily_vol, size=T).tolist()


def _load_strategy_code(code_path: str) -> str | None:
    """Load strategy source code for look-ahead audit.

    Security: resolved path must stay within the project tree to prevent
    path-traversal reads of arbitrary files (e.g. ``../../etc/passwd``).
    """
    import os
    from pathlib import Path

    if not code_path:
        return None

    project_root = Path(os.getcwd()).resolve()
    strategies_dir = (project_root / "analytics-engine" / "strategies").resolve()

    # Resolve relative to project root
    candidates = [
        code_path,
        os.path.join(os.getcwd(), code_path),
        os.path.join(os.getcwd(), "analytics-engine", code_path),
    ]

    for raw_path in candidates:
        resolved = Path(raw_path).resolve()
        # Guard: must be within the project tree
        if not (resolved.is_relative_to(project_root) or resolved.is_relative_to(strategies_dir)):
            continue
        if resolved.is_file():
            try:
                return resolved.read_text()
            except Exception:
                pass

    return None

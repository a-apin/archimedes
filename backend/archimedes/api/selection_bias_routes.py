"""Selection-bias correction API routes.

Exposes the rigor gate for strategy validation. The main consumer is the
strategy-list page (shows PASS/FAIL per strategy) and the strategy detail
page (shows full gate breakdown).
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from archimedes.api.limiter import limiter
from archimedes.services.rigor_evaluator import (
    compute_average_pairwise_correlation,
    compute_library_pbo,
    compute_pbo,
    load_daily_returns_store,
    run_rigor_gate,
)
from archimedes.services.strategy_provider import default_provider

selection_bias_router = APIRouter(prefix="/api/selection-bias", tags=["selection-bias"])

_provider = default_provider()


# ── Schemas ──────────────────────────────────────────────────


from pydantic import BaseModel


class RigorGateDetail(BaseModel):
    """Per-check pass/fail detail.

    Mirrors every key of ``RigorGateResult.gate_details`` so the passport surfaces
    the gate honestly — including ``cpcv`` (a real pass/fail criterion when a
    combinatorial OOS matrix exists), the ``dsr_convention`` disclosure (#547), and
    the advisory ``iid`` diagnostic (#621). Dropping a computed criterion here would
    show ``passes_all=False`` with no rendered reason.
    """

    dsr: str = "MISSING"
    pbo: str = "MISSING"
    oos_sharpe: str = "MISSING"
    look_ahead: str = "MISSING"
    cpcv: str = "MISSING"
    dsr_convention: str = "MISSING"
    iid: str = "MISSING"
    regime_robustness: str = "MISSING"


class LibraryPbo(BaseModel):
    """Library-level CSCV PBO (Bailey et al. 2014) over the whole selection set.

    Display-only (#546, option 2): this is a *selection-set property*, identical
    across every strategy, surfaced ALONGSIDE the per-strategy cohort
    ``pbo_score`` — it is strictly additive and does NOT feed any gate verdict.
    The same value is attached to ``RigorGateResponse`` and to every
    ``StrategyRigorResult`` (the passport endpoint renders from the per-strategy
    result, so the field must be present there too even though it is library-wide,
    not strategy-specific). ``value is None`` is the fail-closed / store-absent
    signal, in which case ``source == "unavailable"``.
    """

    value: float | None = None  # the single library CSCV PBO, or None (fail-closed / store absent)
    data_vintage: str | None = None  # store vintage, e.g. "2026-06-11"
    selection_set_size: int = 0  # number of strategies in the selection set
    source: str = "library_cscv"  # provenance label; "unavailable" when value is None


class StrategyRigorResult(BaseModel):
    """Rigor gate result for a single strategy.

    ``library_pbo`` is a *selection-set property* (identical across strategies),
    not a per-strategy metric; it is included here only so the passport endpoint
    (which renders from this per-strategy result) can surface it. It is
    display-only and never affects ``passes_all`` or ``pbo_score`` (#546).
    """

    strategy_id: str
    strategy_name: str
    passes_all: bool
    gate_details: RigorGateDetail
    deflated_sharpe: float | None = None
    dsr_p_value: float | None = None
    pbo_score: float | None = None
    oos_sharpe: float | None = None
    in_sample_sharpe: float | None = None
    library_pbo: LibraryPbo = LibraryPbo()


class RigorGateResponse(BaseModel):
    """Response for the library-level rigor gate check."""

    strategies: list[StrategyRigorResult]
    total: int
    passing: int
    failing: int
    library_pbo: LibraryPbo = LibraryPbo()


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

    Runs three statistical primitives (DSR, PBO, chronological OOS Sharpe)
    plus the look-ahead static audit for each strategy.

    CPCV (Combinatorial Purged Cross-Validation) is implemented in
    rigor_evaluator.run_rigor_gate() but requires a 2-D (S, T) matrix of
    per-split OOS returns that comes from re-running the full backtest engine
    across combinatorial window splits.  That rolling re-backtest pipeline is
    not yet wired here, so run_rigor_gate() is called without cv_returns_matrix
    and CPCV is reported as an explicit NOT_RUN status (with the reason) on every
    strategy — never a bare "MISSING" that would imply a method silently producing
    no number (#771).  Wire it once the analytics-engine supports combinatorial
    window output.
    """
    strategies = _provider.list_strategies()

    # Library-level CSCV PBO (#546, option 2): a display-only selection-set
    # property attached to the response and to each per-strategy result. It is
    # strictly additive — it never feeds run_rigor_gate or any gate verdict.
    # Computed once (cached on the store file signature) and reused everywhere.
    library_pbo = _library_pbo_payload()

    if not strategies:
        return RigorGateResponse(strategies=[], total=0, passing=0, failing=0, library_pbo=library_pbo)

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

    # Strategies with no real backtest data report all gate fields as MISSING
    # (handled in the per-strategy loop below). Do NOT synthesize returns from
    # stub_sharpe — DSR would trivially pass because the series was constructed
    # to hit exactly that Sharpe, creating a circular validation that is
    # meaningless. The stubs remain available for UI display (portfolio page)
    # but must not feed into the rigor gate.

    # Compute PBO across all strategies that have returns
    valid_returns = {k: v for k, v in returns_by_strategy.items() if len(v) >= 10}
    pbo_scores = compute_pbo(valid_returns) if len(valid_returns) >= 2 else {}

    # num_trials = library size here (#770). This route grades the EXISTING persisted
    # library, so the selection set is the library itself — there is no fresh
    # N-candidate society pool to add (that additive correction, N + library_size,
    # applies only on the live society generation path in generation_pipeline.py).
    num_trials = max(len(valid_returns), 1)

    # The strategy library is the multiple-testing selection set; correlated
    # strategies (overlapping assets/signals) carry fewer independent trials, so
    # the DSR effective-N correction relaxes the penalty via N_eff = N/(1+(N-1)ρ̄).
    avg_correlation = compute_average_pairwise_correlation(valid_returns) if len(valid_returns) >= 2 else 0.0

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
                    library_pbo=library_pbo,
                )
            )
            continue

        # in_sample_sharpe is left None on purpose: run_rigor_gate derives it
        # from the first 70% of `daily_returns`, the same series whose last 30%
        # produces oos_sharpe. Passing the *full-sample* backtest Sharpe here
        # (the previous `bt_map[s.id].sharpe_ratio`) made the OOS/IS cliff check
        # trivially passable — a bad OOS tail drags the full-sample denominator
        # down, inflating the ratio (see rigor_evaluator.run_rigor_gate's own
        # warning at the IS-slice fallback). Let the gate compute the honest
        # first-70% in-sample denominator instead of overriding it.
        in_sample_sharpe = None

        # cv_returns_matrix intentionally omitted — CPCV requires a 2-D array
        # of per-combinatorial-split OOS returns that the analytics-engine does
        # not yet produce.  run_rigor_gate() reports cpcv as an explicit NOT_RUN
        # status with the reason (#771), not a bare "MISSING".
        gate_result = run_rigor_gate(
            strategy_id=s.id,
            daily_returns=daily_returns,
            num_trials=num_trials,
            pbo_scores=pbo_scores,
            strategy_code=strategy_code_map.get(s.id),
            in_sample_sharpe=in_sample_sharpe,
            paper_claimed_sharpe=s.paper_claimed_sharpe,
            average_correlation=avg_correlation,
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
                    cpcv=details.get("cpcv", "MISSING"),
                    dsr_convention=details.get("dsr_convention", "MISSING"),
                    iid=details.get("iid", "MISSING"),
                    regime_robustness=details.get("regime_robustness", "MISSING"),
                ),
                deflated_sharpe=gate_result.deflated_sharpe,
                dsr_p_value=gate_result.dsr_p_value,
                pbo_score=gate_result.pbo_score,
                oos_sharpe=gate_result.oos_sharpe,
                in_sample_sharpe=gate_result.in_sample_sharpe,
                library_pbo=library_pbo,
            )
        )

    passing = sum(1 for r in results if r.passes_all)
    return RigorGateResponse(
        strategies=results,
        total=len(results),
        passing=passing,
        failing=len(results) - passing,
        library_pbo=library_pbo,
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


# Cache the (expensive) library CSCV PBO keyed on the store's file signature so
# the C(16, 8) = 12,870-combination CSCV runs only when the daily-returns store
# actually changes — matching compute_library_pbo's documented "recompute on
# store growth" refresh cadence. The store is add-only + idempotent, so a change
# in the (filename, st_mtime_ns) signature is the "selection set changed" signal.
_LIBRARY_PBO_CACHE: dict[tuple[tuple[str, int], ...], tuple[float | None, str | None, int]] = {}


def _store_signature(store_dir) -> tuple[tuple[str, int], ...] | None:
    """Sorted ``(filename, st_mtime_ns)`` over the store's ``*.json`` files.

    Returns ``None`` when the directory is absent (degrades gracefully). Used as
    the cache key: a new/changed/removed file flips the signature and forces a
    recompute; an unchanged store reuses the cached value.
    """
    from pathlib import Path

    if store_dir is None or not Path(store_dir).is_dir():
        return None
    try:
        return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in Path(store_dir).glob("*.json")))
    except OSError:
        return None


def _cached_library_pbo() -> tuple[float | None, str | None, int]:
    """Load the daily-returns store and compute the single library CSCV PBO.

    Returns ``(value, data_vintage, selection_set_size)`` where ``value`` is the
    library PBO (``None`` fail-closed / store absent), ``data_vintage`` is the
    store's max vintage, and ``selection_set_size`` is the number of aligned
    series actually used by the CSCV. Cached on the store file signature so the
    expensive CSCV does not re-run on every request.

    Never raises: an absent/empty/malformed store yields ``(None, None, 0)``.
    """
    from archimedes.services.rigor_evaluator import (
        _resolve_daily_returns_store_dir,
        align_returns_store,
    )

    store_dir = _resolve_daily_returns_store_dir()
    signature = _store_signature(store_dir)
    if signature is None:
        return None, None, 0
    if signature in _LIBRARY_PBO_CACHE:
        return _LIBRARY_PBO_CACHE[signature]

    store, data_vintage = load_daily_returns_store(store_dir)
    # selection_set_size = number of series that actually survive date-alignment
    # (the count CSCV runs over), not the raw file count.
    selection_set_size = len(align_returns_store(store))
    value = compute_library_pbo(store)
    result = (value, data_vintage, selection_set_size)
    _LIBRARY_PBO_CACHE[signature] = result
    return result


def _library_pbo_payload() -> LibraryPbo:
    """Build the display-only ``LibraryPbo`` for attachment to gate responses.

    Display-only (#546): never feeds the gate verdict. When the store is
    unavailable or the PBO fails closed, returns ``LibraryPbo(value=None,
    source="unavailable")`` and never crashes.
    """
    value, data_vintage, selection_set_size = _cached_library_pbo()
    if value is None:
        return LibraryPbo(
            value=None, data_vintage=data_vintage, selection_set_size=selection_set_size, source="unavailable"
        )
    return LibraryPbo(
        value=value,
        data_vintage=data_vintage,
        selection_set_size=selection_set_size,
        source="library_cscv",
    )


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

"""Strategy endpoints — /api/strategies/*.

Includes: library listing, signals, frontier, correlation, advisor, stress,
generate/fusion.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
from datetime import UTC

import numpy as np
from fastapi import APIRouter, Depends, Query, Request, Response

from archimedes.api._route_helpers import strategy_provider
from archimedes.api.account_auth import CurrentUser, get_current_user, require_current_user
from archimedes.api.limiter import limiter
from archimedes.api.schemas import (
    SignalResponse,
    StrategyListResponse,
    StrategyResponse,
    StrategyReturnsResponse,
    StrategySignalResponse,
    StrategySignalsResponse,
)
from archimedes.api.wallet_routes import get_linked_wallet_address
from archimedes.models.strategy import Strategy, StrategyStatus
from archimedes.services.live_rigor_gate import (
    RigorGateVerdict,
    verdicts_for_strategies,
)
from archimedes.services.rigor_evaluator import RigorGateResult

logger = logging.getLogger(__name__)

strategies_router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def _to_strategy_response(
    s: Strategy,
    verdict: RigorGateVerdict | None = None,
    rigor_result: RigorGateResult | None = None,
) -> StrategyResponse:
    """Map StrategyPassport + persisted BacktestResult to API schema.

    ``verdict`` is the LIVE rigor-gate verdict for this strategy (#821), computed
    on its persisted real returns via ``run_rigor_gate`` — the SAME machinery the
    ``/api/selection-bias/gate`` route uses. The served ``passes_rigor_gate`` badge
    and the CANDIDATE → VALIDATED promotion are derived from it, NOT from the stored
    fixture boolean. When ``verdict is None`` (single-strategy fetch) it is computed
    on demand here. A strategy with no real returns yields a ``pending`` verdict so
    the badge surfaces "unknown" rather than a fixture ``True``/``False``.

    ``rigor_result`` is the companion full ``RigorGateResult`` (#868) — the SAME
    live gate run ``verdict`` was reduced from — used to serve the numeric fields
    (``deflated_sharpe_ratio``, ``dsr_p_value``, ``pbo_score``,
    ``out_of_sample_sharpe``) so the leaderboard can never disagree with
    ``GET /api/selection-bias/gate`` for a given strategy id. ``None`` means the
    live gate could not run for this strategy (no/insufficient persisted returns,
    or a batch/DB failure); the numeric fields then render as ``None`` — the API's
    honest "not run" — rather than falling back to ``s.<field>``/``bt.<field>``
    (#1187: those columns trace back to a migrated test-fixture snapshot
    (``backend/tests/fixtures/backtest_fixtures_snapshot.json``, PR #863) that
    predates the current DSR convention (raw vs. excess returns) and gate
    threshold (#901, 0.95 → 0.90) and cannot be reproduced by any single code
    version — presenting it as a measured number next to a ``pending`` badge is a
    claim-integrity defect, not a display nicety). When ``verdict is None``
    (single-strategy fetch) ``rigor_result`` is also computed on demand here.
    """
    from archimedes.api.schemas import PaperRefResponse
    from archimedes.services.return_source_classifier import classify_strategy

    if verdict is None:
        verdict = _live_verdict_for_one(s)
        rigor_result = _live_rigor_result_for_one(s)

    bt = strategy_provider().get_backtest_result(s.id)
    # has_real: a BacktestResultRecord (persisted daily-returns row) exists.
    # Previously derived from ``s.real_sharpe is not None`` (a metric field that can
    # be populated from fixture stubs without a real returns row — a false positive).
    # Now strictly tied to the persisted backtest/daily-returns row so that
    # ``is_backtest_placeholder`` is honest: it is False ONLY when we have actual
    # persisted run data the rigor gate can re-grade (#passport-honesty).
    has_real = bt is not None
    return_source, return_source_note = classify_strategy(s)

    # Served status overlays the LIVE gate verdict on the file-declared status:
    # a CANDIDATE is promoted to VALIDATED only when the live gate PASSES on real
    # returns (#821). The fixture boolean no longer promotes anything. Hand-declared
    # advanced states (live/retired) are preserved.
    served_status = s.status.value
    if s.status == StrategyStatus.CANDIDATE and verdict.passes:
        served_status = StrategyStatus.VALIDATED.value

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
        universe_source=s.universe_source,
        position_sizing=s.position_sizing.value,
        rebalance_frequency=s.rebalance_frequency.value,
        status=served_status,
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
        # Metric display: use s.real_* (fixture data) when available, fall through
        # to the persisted backtest row, then the stub placeholder.  This is
        # independent of ``has_real`` so curated strategies retain their fixture
        # metrics even when no BacktestResultRecord row exists yet.
        sharpe_ratio=s.real_sharpe if s.real_sharpe is not None else (bt.sharpe_ratio if bt else s.stub_sharpe),
        sortino_ratio=s.real_sortino if s.real_sortino is not None else (bt.sortino_ratio if bt else None),
        cagr=s.real_cagr if s.real_cagr is not None else (bt.cagr if bt else s.stub_cagr),
        max_drawdown=s.real_max_dd if s.real_max_dd is not None else (bt.max_drawdown if bt else s.stub_max_dd),
        win_rate=s.real_win_rate if s.real_win_rate is not None else (bt.win_rate if bt else s.stub_win_rate),
        calmar_ratio=s.real_calmar if s.real_calmar is not None else (bt.calmar_ratio if bt else s.stub_calmar),
        correlation_to_spy=s.real_corr_spy
        if s.real_corr_spy is not None
        else (bt.correlation_to_spy if bt else s.stub_corr_spy),
        total_trades=s.real_total_trades if s.real_total_trades is not None else (bt.total_trades if bt else None),
        # Numeric rigor fields (#868, honesty fix #1187): SOLELY the LIVE gate
        # result — the SAME run_rigor_gate call that produced `verdict` above —
        # so the leaderboard can never disagree with GET /api/selection-bias/gate
        # for this id. rigor_result is None when the live gate could not run
        # (no/insufficient persisted returns, or a batch failure); the field then
        # renders None (served as the API's honest "not run"), NEVER the stale
        # s.<field> / bt.<field> values. Those trace back to a migrated
        # test-fixture snapshot (#1187) that predates the current DSR convention
        # and gate threshold and cannot be reproduced by any single code version —
        # falling back to it silently re-labels a `pending` verdict's numbers as
        # measured. Do not reintroduce the s.<field>/bt.<field> fallback here;
        # that is precisely the defect #1187 tracks. The basic display metrics
        # below (sharpe_ratio, cagr, etc.) are a DIFFERENT, out-of-scope concern —
        # they are descriptive backtest stats, not a rigor-gate pass/fail claim.
        deflated_sharpe_ratio=(rigor_result.deflated_sharpe if rigor_result is not None else None),
        dsr_p_value=(rigor_result.dsr_p_value if rigor_result is not None else None),
        pbo_score=(rigor_result.pbo_score if rigor_result is not None else None),
        out_of_sample_sharpe=(rigor_result.oos_sharpe if rigor_result is not None else None),
        kelly_fraction=s.kelly_fraction,
        # Badge from the LIVE gate verdict (#821) — never the fixture boolean.
        # passes_rigor_gate is the fail-closed boolean (True only when status=="pass");
        # rigor_gate_status carries the honest four-state badge (#1184):
        # "pass" | "fail" | "pending" | "degenerate".
        passes_rigor_gate=verdict.passes,
        rigor_gate_status=verdict.status,
        # is_backtest_placeholder: True when no BacktestResultRecord row exists.
        # ``has_real`` is now bt is not None so this is the honest gate.
        is_backtest_placeholder=not has_real,
        sharpe_ci_lower=s.sharpe_ci_lower,
        sharpe_ci_upper=s.sharpe_ci_upper,
        backtest_start=(
            s.real_backtest_start
            if s.real_backtest_start
            else (bt.backtest_start.isoformat() if bt and bt.backtest_start else None)
        ),
        backtest_end=(
            s.real_backtest_end
            if s.real_backtest_end
            else (bt.backtest_end.isoformat() if bt and bt.backtest_end else None)
        ),
        regime_tag=s.regime_tag,
        return_source=return_source,
        return_source_note=return_source_note,
    )


def _live_verdict_for_one(s: Strategy) -> RigorGateVerdict:
    """Live rigor-gate verdict for a single strategy (#821).

    Used by the single-strategy fetch path (``get_strategy``). Delegates to
    ``verdicts_for_strategies`` over the FULL library so the verdict is computed
    with the same cohort PBO + avg correlation context the list badge uses,
    keeping the detail view consistent with the list. ``num_trials`` is
    self-contained (1 per strategy, decouple #2) — it does NOT come from the
    library size; only PBO/avg_correlation are cohort-derived. No real returns
    → ``pending``, never a fixture value. Never raises: any failure degrades to
    ``pending`` (fail-closed badge).
    """
    try:
        cohort = _library_cohort_including(s)
        return verdicts_for_strategies(cohort).get(s.id, RigorGateVerdict.pending())
    except Exception as exc:
        logger.warning("live verdict failed for %s (badge → pending): %s", s.id, exc)
        return RigorGateVerdict.pending()


def _library_cohort_including(s: Strategy) -> list[Strategy]:
    """The full library cohort, guaranteed to contain ``s``.

    This cohort feeds cohort-scoped PBO + avg_correlation ONLY — it must NOT
    drive ``num_trials`` (self-contained at 1 per strategy, decouple #2). ``s``
    is appended only if the provider list somehow misses it (e.g. a
    just-generated strategy not yet listed).
    """
    try:
        cohort = list(strategy_provider().list_strategies())
    except Exception:
        cohort = []
    if not any(x.id == s.id for x in cohort):
        cohort.append(s)
    return cohort


def _live_rigor_result_for_one(s: Strategy) -> RigorGateResult | None:
    """Full live ``RigorGateResult`` for a single strategy (#868).

    Companion to ``_live_verdict_for_one``: that function reduces the live gate
    to the four-state pass/fail/pending/degenerate badge (``RigorGateVerdict``
    carries no numeric fields), but the served leaderboard numbers (``dsr_p_value``,
    ``pbo_score``, ``out_of_sample_sharpe``, ``deflated_sharpe_ratio``) must
    also come from the SAME live gate run, not the stale ``s.dsr_p_value`` /
    ``bt.dsr_p_value`` fixture fields — otherwise the leaderboard can show
    numbers that disagree with what ``GET /api/selection-bias/gate`` computes
    for the same strategy right now. Delegates to
    ``_live_rigor_results_for_strategies`` over the FULL library so the single
    fetch carries the same cohort PBO + avg correlation context as the list.
    ``num_trials`` is self-contained (1, decouple #2) — it does NOT come from
    the library size. Returns ``None`` on no/insufficient persisted returns or
    any failure — the caller (#1187) renders that as the numeric fields being
    ``None``, never a fabricated number, matching the fail-closed badge contract.
    """
    try:
        cohort = _library_cohort_including(s)
        return _live_rigor_results_for_strategies(cohort).get(s.id)
    except Exception as exc:
        logger.warning("live rigor gate failed for %s (numbers → None): %s", s.id, exc)
        return None


def _live_rigor_results_for_strategies(strategies: list[Strategy]) -> dict[str, RigorGateResult]:
    """Batch live ``RigorGateResult`` per strategy (#868), for the library list.

    Companion to ``verdicts_for_strategies``: that function collapses the live
    gate to a four-state pass/fail/pending/degenerate badge, discarding the underlying
    DSR/PBO/OOS numbers. The served leaderboard numeric fields must equal what
    ``GET /api/selection-bias/gate`` computes for the same strategy right now
    (not the stale ``s.dsr_p_value``/``bt.dsr_p_value`` fixture fields), so this
    mirrors that route's cohort context exactly: real persisted returns from
    the DB, zero-variance series excluded before they can dilute avg_correlation
    (same fix as #868's selection_bias_routes.py change), cohort PBO +
    avg-correlation over the survivors, one ``run_rigor_gate`` call per
    strategy. ``num_trials`` is self-contained (1 per strategy, decouple #2) —
    it is NOT derived from this cohort. Strategies with no/insufficient
    persisted returns are simply absent from the returned dict — the caller
    (#1187) serves ``None`` for those ids' numeric rigor fields (fail-closed:
    no fabricated number for a strategy the live gate cannot evaluate; the
    pre-#1187 fixture-field fallback is gone). A degenerate (zero-variance)
    series IS graded and included here — it just runs with self-contained
    cohort context (see the ``gate_kwargs`` branch below) so it can't dilute
    ``avg_correlation`` for the rest of the cohort.

    Any DB or cohort-compute failure degrades to ``{}`` (every id's numeric
    rigor fields render ``None``) rather than raising into the library-list
    response.

    **Perf (Library page load latency):** the batch cohort compute below (PBO,
    average correlation, per-strategy look-ahead code load, one ``run_rigor_gate``
    call per strategy — measured ~6s for this route) is memoized in
    ``services.rigor_cache`` keyed on a data-version token
    (``rigor_cache.cohort_key``) derived from the exact persisted-returns read just
    above, PLUS each strategy's ``strategy_code_hash`` — the look-ahead audit
    inside ``run_rigor_gate`` also depends on the strategy's code, so the code
    hash has to participate in the key or a code edit could serve a stale
    look-ahead verdict for up to the TTL (Copilot review, PR #1040). The DB read
    itself is NEVER cached — it always runs live, which is what lets the key
    react the instant persisted returns change. This is honest caching: the
    cached value IS the real live-computed result, so a cache hit serves exactly
    what a cache miss would have computed. ``rigor_cache.get_or_compute`` fails
    open (any cache-layer error falls back to calling the compute closure
    directly), so a cache bug can only make a request slow, never wrong. It also
    never caches the ``{}`` failure sentinel (``cache_if=bool``) so
    a transient cohort-compute failure can't strand every strategy on ``None``
    numeric rigor fields for the full TTL.
    """
    if not strategies:
        return {}

    strategy_ids = [s.id for s in strategies]

    try:
        from archimedes.db import get_session, init_db
        from archimedes.services.backtest_repository import get_all_daily_returns

        init_db()
        with get_session() as session:
            returns_by_strategy = get_all_daily_returns(session, strategy_ids)
    except Exception as exc:
        logger.warning("live rigor result batch: DB read failed (all → None): %s", exc)
        return {}

    from archimedes.services.rigor_cache import cohort_key, get_or_compute

    # Fold each strategy's code-version token into the key (Copilot review, PR
    # #1040): run_rigor_gate's look-ahead audit reads `strategy_code`, so a key
    # built from returns alone can serve a stale look-ahead verdict/passes_all
    # after a code edit even though returns are unchanged. `strategy_code_hash`
    # is a SHA-256 of the strategy file contents already computed onto the
    # Strategy object by LocalStrategyProvider — reading it here costs no extra
    # I/O on the request path (the cheap-identifier preference `cohort_key`'s
    # docstring calls for).
    code_versions = {s.id: getattr(s, "strategy_code_hash", None) for s in strategies}
    cache_key = "strategies_list:" + cohort_key(strategy_ids, returns_by_strategy, code_versions)

    def _compute() -> dict[str, RigorGateResult]:
        from archimedes.services.rigor_evaluator import (
            assert_self_contained_cohort_correlation,
            compute_average_pairwise_correlation,
            compute_pbo,
            run_rigor_gate,
        )

        # Exclude zero-variance (degenerate/placeholder-flat) series from the cohort
        # context — same fix as selection_bias_routes.py's valid_returns filter
        # (#868), so avg_correlation/pbo_scores here match that route's cohort gate
        # exactly rather than drifting apart on this input.
        valid_returns = {
            k: v
            for k, v in returns_by_strategy.items()
            if len(v) >= 10 and float(np.ptp(np.asarray(v, dtype=float))) > 0.0
        }

        try:
            pbo_scores = compute_pbo(valid_returns) if len(valid_returns) >= 2 else {}
            # num_trials = 1: each strategy is graded on ITS OWN Sharpe, never
            # deflated by how many OTHER strategies sit in the library (decouple
            # #2). PBO/avg_correlation stay cohort-wide (out of scope here).
            num_trials = 1
            avg_correlation = compute_average_pairwise_correlation(valid_returns) if len(valid_returns) >= 2 else 0.0
            # V4 guard (num_trials-provenance audit 2026-08-03): cohort-wide
            # avg_correlation is INERT at num_trials=1 (E[max_N]=0 when N==1) —
            # this makes it IMPOSSIBLE for a future edit to silently reintroduce
            # num_trials>1 here without re-coupling every strategy's DSR to the
            # library's correlation structure; it raises instead (caught below,
            # same fail-closed contract as the rest of this cohort-context block).
            assert_self_contained_cohort_correlation(num_trials, avg_correlation)
        except Exception as exc:
            logger.warning("live rigor result batch: cohort-context compute failed (all → None): %s", exc)
            return {}

        # Load code for every strategy that has >= 10 returns — degenerate series
        # (excluded from valid_returns) still run their own gate (#868) and need the
        # look-ahead audit input.
        code_by_id = {
            s.id: _load_strategy_code_safe_local(s) for s in strategies if len(returns_by_strategy.get(s.id, [])) >= 10
        }

        computed: dict[str, RigorGateResult] = {}
        for s in strategies:
            daily_returns = returns_by_strategy.get(s.id, [])
            if len(daily_returns) < 10:
                continue  # No sufficient returns — caller (#1187) serves None, never a fixture number
            if s.id in valid_returns:
                # Non-degenerate series: num_trials is self-contained (1, decouple
                # #2); pbo_scores / avg_correlation still come from the cohort so
                # they match the library gate.
                gate_kwargs: dict = {
                    "num_trials": num_trials,
                    "pbo_scores": pbo_scores,
                    "average_correlation": avg_correlation,
                }
            else:
                # Degenerate (zero-variance) series: excluded from cohort context to
                # prevent diluting avg_correlation (#868); num_trials is self-contained
                # (1) either way, but the strategy still runs its own gate so the
                # caller gets a live "degenerate" verdict (#1184) rather than falling
                # back to a stale fixture value.
                gate_kwargs = {"num_trials": 1, "pbo_scores": {}, "average_correlation": 0.0}
            try:
                computed[s.id] = run_rigor_gate(
                    strategy_id=s.id,
                    daily_returns=daily_returns,
                    strategy_code=code_by_id.get(s.id),
                    in_sample_sharpe=None,
                    paper_claimed_sharpe=getattr(s, "paper_claimed_sharpe", None),
                    **gate_kwargs,
                )
            except Exception as exc:
                logger.warning("live rigor gate failed for %s in batch (numbers → None): %s", s.id, exc)
        return computed

    # cache_if=bool: `_compute()` returns `{}` on a transient
    # cohort-context compute failure (see the `except Exception` above), and an
    # empty dict must never be memoized — caching it would make that transient
    # failure "sticky" for the full TTL, serving every strategy's numeric rigor
    # fields as None long after the underlying failure has passed (Copilot
    # review, PR #1040). The live `{}` is still returned to THIS caller either
    # way; only whether it's written to the store for the NEXT caller changes.
    return get_or_compute(cache_key, _compute, cache_if=bool)


def _load_strategy_code_safe_local(strategy: Strategy) -> str | None:
    """Best-effort strategy-source read for the batch look-ahead audit input.

    Local twin of ``live_rigor_gate._load_strategy_code_safe`` (out of scope for
    #868 — that module is untouched) so ``_live_rigor_results_for_strategies``
    doesn't need to reach into it. Never raises: ``None`` on any failure, which
    makes the gate's look-ahead leg fail rather than crash the library list.
    """
    code_path = getattr(strategy, "strategy_code_path", None)
    if not code_path:
        return None
    try:
        from archimedes.api.selection_bias_routes import _load_strategy_code

        return _load_strategy_code(code_path)
    except Exception:
        return None


def _verdict_from_result(result: RigorGateResult | None) -> RigorGateVerdict:
    """Derive a RigorGateVerdict from an already-computed RigorGateResult.

    Used by list_strategies so the badge and the numeric rigor fields always
    come from the same single live-gate computation — avoids the double DB read and
    duplicate gate run that verdicts_for_strategies would add, and eliminates
    the badge/numeric-fields divergence that arose when the two paths used different
    cohort filtering (#868, Copilot review).

    #1184: delegates to ``RigorGateVerdict.from_result`` (rather than
    hand-rolling ``passed()``/``failed()`` off ``passes_all`` here) so a
    zero-variance persisted series reports the distinct ``degenerate`` status
    through this route too, not just through ``live_rigor_gate.verdict_from_returns``
    — the two badge-producing call sites can't drift apart on this check.
    """
    if result is None:
        return RigorGateVerdict.pending()
    return RigorGateVerdict.from_result(result)


# ── Library listing ─────────────────────────────────────────────


@strategies_router.get("/", response_model=StrategyListResponse)
async def list_strategies(
    request: Request,
    status: str | None = Query(None, pattern="^(candidate|validated|live|retired)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List strategies in the library. Backed by LocalStrategyProvider.

    Both the ``passes_rigor_gate`` badge and the numeric rigor fields
    (``dsr_p_value``, ``pbo_score``, ``out_of_sample_sharpe``,
    ``deflated_sharpe_ratio``) come from a SINGLE live-gate run via
    ``_live_rigor_results_for_strategies`` (#868). The badge is derived from the
    result via ``_verdict_from_result`` — a second DB read + duplicate gate run
    through ``verdicts_for_strategies`` is not needed, and the inconsistency that
    arose when the two paths used different cohort filtering (degenerate-series
    exclusion existed only in ``_live_rigor_results_for_strategies``) is
    eliminated.

    NOTE on the ``status`` filter: it filters on the file-declared status BEFORE the
    live-gate promotion overlay, so a CANDIDATE that the live gate promotes to
    VALIDATED still appears under ``?status=candidate`` (its stored status) with a
    served ``status: "validated"``. This is intentional — the stored status is the
    stable filter key; the served status reflects the live verdict.
    """
    from archimedes.db import get_session
    from archimedes.models.strategy_generators import wallet_can_publish

    status_filter = StrategyStatus(status) if status else None

    # Grade over the FULL library — never a filtered or paginated subset (#1173).
    # The detail route grades via _library_cohort_including(), which calls
    # list_strategies() with NO status filter, so the cohort here must match it
    # exactly or the same strategy's badge changes depending on how it was
    # requested. Two distinct ways that broke:
    #
    #   1. Pagination. Scoring over `window` made the badge depend on which page
    #      a strategy landed on: a short window can fall under
    #      MIN_LIBRARY_N_FOR_PBO_GATING (criterion 4 skipped) and the CSCV/PBO
    #      value itself shifts with the cohort. Verified live: strategy
    #      d90b357a…4bbd graded False in a 5-item window but True in the
    #      full-library view and True on its own detail/passport route.
    #   2. The `status` filter. Grading `list_strategies(status=...)` graded a
    #      SUBSET, so `?status=candidate` and `?status=validated` could return
    #      different verdicts for the same strategy, and both could disagree with
    #      the passport. Same class of bug as (1), same fix — the filter is a
    #      display concern and must not reach the cohort.
    #
    # Both are the list-vs-detail contradiction dfa8fc1 was written to prevent,
    # and which this route's docstring asserts cannot happen.
    #
    # Bonus: the cache key (see cohort_key) is derived from the cohort's ids, so
    # grading the full library collapses the previous one-cohort-computation-per
    # -offset AND per-status-filter (~6s each) into a single shared entry.
    library = strategy_provider().list_strategies()
    rigor_results = _live_rigor_results_for_strategies(library)

    # Filter/paginate only AFTER grading. Delegated to the provider rather than
    # filtered in-process so the `status` semantics stay byte-identical to the
    # previous behaviour (file-declared status, before the live-gate promotion
    # overlay — see the docstring note above).
    strats = strategy_provider().list_strategies(status=status_filter) if status_filter else library
    total = len(strats)
    window = strats[offset : offset + limit]
    caller = get_linked_wallet_address(request)
    responses: list[StrategyResponse] = []
    with get_session() as session:
        for s in window:
            resp = _to_strategy_response(s, _verdict_from_result(rigor_results.get(s.id)), rigor_results.get(s.id))
            resp.can_publish = bool(caller) and wallet_can_publish(
                session, strategy_id=s.id, wallet_address=caller, is_example=True
            )
            responses.append(resp)
    return StrategyListResponse(
        strategies=responses,
        total=total,
    )


@strategies_router.get("/generated")
async def list_generated_strategies(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(require_current_user),
):
    """List fusion/architect-generated strategies from the strategy_store table.

    Private-until-published: row is visible when published or owned by current
    canonical user; verified linked wallet handles legacy ``owner_wallet`` rows.
    Legacy ownerless rows remain invisible until purged (scripts/purge_orphan_generated.py)
    or published. Curated examples live on GET /api/strategies/ and stay public.
    """

    from sqlalchemy import and_, or_

    from archimedes.db import get_session
    from archimedes.models.strategy_generators import wallet_can_publish
    from archimedes.models.strategy_store import StrategyRecord

    caller = get_linked_wallet_address(request)  # None when anonymous — never an error

    rows: list[dict] = []
    try:
        with get_session() as session:  # type: _Session
            query = session.query(StrategyRecord).filter(StrategyRecord.is_example.is_(False))
            owner_filters = [StrategyRecord.owner_user_id == user.id]
            if caller:
                owner_filters.append(
                    and_(
                        StrategyRecord.owner_user_id.is_(None),
                        StrategyRecord.owner_wallet == caller.lower(),
                    )
                )
            query = query.filter(StrategyRecord.is_published.is_(True) | or_(*owner_filters))
            records = query.order_by(StrategyRecord.created_at.desc()).limit(limit).all()
            # One query for the whole page's generation-cost records (#1326) —
            # the library's cost column reads this. Strategies with nothing
            # measured are simply absent from the map and stay absent from the
            # row, which the table renders as an em-dash, never as zero.
            from archimedes.models.generation_cost import generation_costs_for_strategies

            costs = generation_costs_for_strategies(session, [r.id for r in records])
            rows = []
            for r in records:
                d = r.to_dict()
                d["can_publish"] = bool(caller) and wallet_can_publish(
                    session, strategy_id=r.id, wallet_address=caller, is_example=False
                )
                d["generation_cost"] = costs.get(r.id)
                rows.append(_redact_owner_wallet(d, caller))
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

    strategies = strategy_provider().list_strategies()
    from archimedes.chain.client import chain_client

    synth_assets = [sym for sym, addr in chain_client.settings.synth_addresses.items() if addr]

    all_signals = await asyncio.to_thread(
        strategy_evaluator.evaluate_strategies,
        strategies,
        synth_assets,
    )

    target_weights = strategy_evaluator.aggregate_signals(all_signals, usdc_floor=0.20)

    # flat_pct → ensemble-consensus bucket (#659). This is the agent's
    # directional consensus, NOT a market regime; the model owns the thresholds.
    from archimedes.models.regime import EnsembleConsensus

    flat_count = sum(1 for ss in all_signals for s in ss.signals if s.signal.value == "flat")
    total_count = sum(len(ss.signals) for ss in all_signals)
    consensus = EnsembleConsensus.from_signal_counts(flat_count, total_count)
    flat_pct = consensus.flat_pct
    # `regime` kept for backward-compat; it carries the consensus bucket value.
    regime = consensus.label.value

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
        ensemble_consensus=consensus.label.value,
        confidence=round(1.0 - flat_pct, 2),
        target_weights=target_weights,
        strategies=strat_responses,
        timestamp=datetime.now(UTC).isoformat(),
    )


# /frontier and /correlation endpoints deleted (Issue #383).
# They fabricated returns via np.random.default_rng(42) — synthetic data
# masquerading as measured correlations. Honest alternatives require real
# backtest return series, which is a post-submission feature.


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
        if not regime_data:
            # No market detector wired — fall back to the ensemble-consensus
            # bucket so the advisor still has a directional prior (#659). The
            # bucket names line up with Regime values, so the deleverage map
            # below still resolves; it is consensus-driven, not market-driven.
            consensus = await state.load_ensemble_consensus()
            if consensus:
                regime_data = {"regime": consensus.get("label"), "confidence": consensus.get("confidence")}
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

    all_strategies = [s for s in strategy_provider().list_strategies() if s.real_sharpe is not None]

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

    # Live rigor gate (#821/#868) — computed BEFORE the portfolio LLM runs, as
    # ONE memoized batch (the same machinery + cache entry the library list
    # uses), serving three consumers from a single computation:
    #   * the LLM prompt's per-strategy rigor label — the in-memory
    #     Strategy.passes_rigor_gate is a fail-closed sentinel (always False),
    #     and presenting it to the model as a verdict told it every curated
    #     strategy had failed the gate;
    #   * the per-pick badge in the response (_rigor_fields);
    #   * the five numeric rigor statistics in the response, previously served
    #     from the frozen fixture fields on the in-memory objects.
    # Both layers fail closed without raising: an empty batch result maps every
    # id to a "pending" verdict, never a fabricated pass.
    advisor_results = await asyncio.to_thread(_live_rigor_results_for_strategies, strategies)
    advisor_verdicts = {s.id: _verdict_from_result(advisor_results.get(s.id)) for s in strategies}
    rigor_statuses = {sid: v.status for sid, v in advisor_verdicts.items()}

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
                    rigor_statuses,
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
                        rigor_statuses,
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

    # advisor_verdicts / advisor_results were computed above, BEFORE the
    # portfolio LLM call, so the prompt and this response share one live-gate run.

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
        except Exception:
            import logging as _logging

            _logging.getLogger(__name__).exception("build/anchor reasoning trace failed")
            return {"trace_id": None, "trace_hash": None, "error": "trace build failed"}

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

        # Badge from the live gate (#821), not st.passes_rigor_gate (always False on
        # the in-memory object). Fail-closed to "pending" if the strategy isn't in the
        # verdict map (it always should be, but never claim an unearned pass).
        _verdict = advisor_verdicts.get(st.id)
        # Numeric rigor stats from the SAME live gate run that produced the badge
        # (#868 contract, mirrored from _to_strategy_response): the fixture fields
        # on the in-memory object are frozen values the live gate never wrote, so
        # serving them next to a live badge let the advisor's numbers disagree
        # with GET /api/selection-bias/gate. SOLELY the live result (honesty fix
        # #1187, same scope as _to_strategy_response): these five fields render
        # None — never st.<field> — when the live gate could not run for this
        # strategy. Do not reintroduce the st.<field> fallback here; that is
        # precisely the defect #1187 tracks, now closed for both response paths.
        _live = advisor_results.get(st.id)
        return {
            "passes_rigor_gate": _verdict.passes if _verdict else False,
            "rigor_gate_status": _verdict.status if _verdict else "pending",
            "deflated_sharpe_ratio": _live.deflated_sharpe if _live else None,
            "dsr_p_value": _live.dsr_p_value if _live else None,
            "num_trials_in_selection": _live.num_trials if _live else None,
            "pbo_score": _live.pbo_score if _live else None,
            "out_of_sample_sharpe": _live.oos_sharpe if _live else None,
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
            vol_ann = abs(mu_ann / sr)
            full_kelly = mu_ann / max(vol_ann**2, 1e-6)
            # Shrink full Kelly by the ratio OOS/IS Sharpe so the fraction
            # reflects walk-forward edge rather than inflated in-sample edge.
            # Falls back to half-Kelly when no OOS Sharpe is stored.
            sr_oos = s.out_of_sample_sharpe if s.out_of_sample_sharpe is not None else sr
            base_kelly = min(0.5 * (sr_oos / max(sr, 1e-6)) * full_kelly, 0.5)

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
            vol_ann = abs(mu_ann / sr)
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

    # Validate each allocation's shape before handing it to the stress engine.
    # stress_one indexes ``a["symbol"]`` and does ``float(a.get("weight") or 0.0)``,
    # so a missing symbol (KeyError) or a non-numeric weight (ValueError) would
    # otherwise surface as an opaque 500. Reject those as a 422 client error
    # instead (issue #926). ``usdc_weight`` per-element is not consumed by the
    # engine (it is the top-level field below), so it is not required here.
    for i, a in enumerate(allocations):
        if not isinstance(a, dict):
            raise HTTPException(status_code=422, detail=f"allocations[{i}] must be an object")
        sym = a.get("symbol")
        if not isinstance(sym, str) or not sym.strip():
            raise HTTPException(
                status_code=422,
                detail=f"allocations[{i}].symbol is required and must be a non-empty string",
            )
        w = a.get("weight")
        if w is not None:
            try:
                float(w)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"allocations[{i}].weight must be a number") from None

    scenario = payload.get("scenario", "all")
    try:
        usdc_weight = float(payload.get("usdc_weight") or 0.0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="usdc_weight must be a number") from None

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


def _redact_owner_wallet(d: dict, caller: str | None) -> dict:
    """Strip ``owner_wallet`` from a public payload unless the caller IS the owner.

    Wallet addresses are pseudonymous PII and a linkability vector; publishing a
    strategy publishes the strategy, not its creator's wallet. Attribution is a
    marketplace (#713) decision to make deliberately later.
    """
    ow = d.get("owner_wallet")
    if not (caller and ow and str(ow).lower() == caller.lower()):
        d.pop("owner_wallet", None)
    return d


def _visible_passports(session, records: list, caller: str | None = None, caller_user_id: str | None = None) -> list:
    """Apply private-until-published to raw passport records.

    The passports table mirrors ``strategy_store`` ids, so leaving these
    endpoints ungated would defeat the 404-hides-existence design on
    ``GET /api/strategies/{id}`` by simple id substitution. Curated passports
    are always public (the curated corpus has no store row and no owner). For
    generated passports, the per-row DECISION delegates to the single shared
    predicate (``services.strategy_visibility.is_strategy_visible``, #1120) —
    this function only supplies its inputs: the ``is_example``/``is_published``
    flags come from the strategy_store row (the passport record does not carry
    them), ownership fields from the passport record. Ownerless generated
    legacy rows stay hidden (purge-pending — scripts/purge_orphan_generated.py).
    """
    from archimedes.models.strategy_store import StrategyRecord
    from archimedes.services.strategy_visibility import is_strategy_visible

    ids = [r.id for r in records]
    store_flags: dict[str, tuple[bool, bool]] = {}
    if ids:
        rows = (
            session.query(StrategyRecord.id, StrategyRecord.is_example, StrategyRecord.is_published)
            .filter(StrategyRecord.id.in_(ids))
            .all()
        )
        store_flags = {sid: (bool(ex), bool(pub)) for sid, ex, pub in rows}

    visible = []
    for r in records:
        if (r.generation_method or "").lower() == "curated":
            visible.append(r)
            continue
        is_example, is_published = store_flags.get(r.id, (False, False))
        row_view = {
            "is_example": is_example,
            "is_published": is_published,
            "owner_user_id": r.owner_user_id,
            "owner_wallet": r.owner_wallet,
        }
        if is_strategy_visible(row_view, caller, caller_user_id=caller_user_id):
            visible.append(r)
    return visible


@strategies_router.get("/passports")
async def list_strategy_passports(
    request: Request,
    status: str | None = Query(None),
    regime_tag: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """List strategies from the unified strategy_passports table.

    Private-until-published applies here exactly as on ``/generated`` — see
    ``_visible_passports``.
    """
    from archimedes.db import get_session
    from archimedes.services.passport_loader import list_passports

    caller = get_linked_wallet_address(request)  # optional linked-wallet compatibility
    user = get_current_user(request)
    with get_session() as session:
        records = list_passports(session, status=status, regime_tag=regime_tag)
        records = _visible_passports(session, records, caller, user.id if user else None)
        passports = [_redact_owner_wallet(r.to_dict(), caller) for r in records[:limit]]

    return {"passports": passports, "total": len(passports), "source": "strategy_passports"}


@strategies_router.get("/passports/{strategy_id}")
async def get_strategy_passport(request: Request, strategy_id: str):
    """Get a single passport in its native dict shape from strategy_passports.

    Unpublished non-example passports 404 for non-owners (never 403 — a 403
    would confirm the id exists).
    """
    from fastapi import HTTPException

    from archimedes.db import get_session
    from archimedes.services.passport_loader import get_passport

    caller = get_linked_wallet_address(request)
    user = get_current_user(request)
    with get_session() as session:
        record = get_passport(session, strategy_id)
        if record is None or not _visible_passports(session, [record], caller, user.id if user else None):
            raise HTTPException(status_code=404, detail="Passport not found")
        return _redact_owner_wallet(record.to_dict(), caller)


def _enrich_paper_titles_from_corpus(
    refs: list,
    session,
) -> dict[str, str]:
    """Return a map of arxiv_id → corpus title for refs with empty stored titles.

    Queries the ``papers`` corpus table (PaperRecord) for refs whose stored
    ``title`` is blank but whose ``arxiv_id`` is known.  Non-fatal — any DB
    error returns an empty map so the caller falls back to the bare arxiv_id.
    Only fires when at least one ref needs enrichment.
    """
    missing_ids = list(dict.fromkeys(r.arxiv_id for r in refs if r.arxiv_id and not (r.title or "").strip()))
    if not missing_ids:
        return {}
    try:
        from archimedes.models.corpus_store import PaperRecord

        rows = session.query(PaperRecord).filter(PaperRecord.arxiv_id.in_(missing_ids)).all()
        return {row.arxiv_id: row.title for row in rows if row.title}
    except Exception:
        return {}


def _generation_cost_for(strategy_id: str, session) -> dict | None:
    """The durable generation-cost record for one strategy, or ``None`` (#1326).

    ``None`` covers three genuinely different situations — no session on this
    call path, no record for this strategy, and a record whose measurement will
    not decode — and every one of them is the same claim to a reader: *nothing
    measured this*. That is deliberate. The alternative, distinguishing them in
    the payload, would invite a caller to treat "we didn't look" as "measured
    zero". The corrupt-record case is logged loudly by the model layer.

    A lookup failure must never take down a strategy read: the cost card is
    decoration on a page whose subject is the strategy.
    """
    if session is None or not strategy_id:
        return None
    try:
        from archimedes.models.generation_cost import generation_cost_for_strategy

        return generation_cost_for_strategy(session, strategy_id)
    except Exception as exc:  # pragma: no cover — defensive; DB-level failure
        import logging as _logging

        _logging.getLogger(__name__).warning("generation cost lookup failed for %s: %s", strategy_id, exc)
        return None


def _passport_to_strategy_response(record, session=None) -> StrategyResponse:
    """Reshape a StrategyPassportRecord (fusion/architect output) into the
    StrategyResponse schema that StrategyPassport.jsx expects. Curated
    strategies still flow through LocalStrategyProvider above; this is the
    fallback that makes generated strategies clickable from Library.

    ``session`` — optional SQLAlchemy session used to enrich empty paper titles
    from the corpus ``papers`` table at read time.  When titles are missing
    (fusion generation stores only arxiv_ids), the corpus join backfills them so
    the UI can display a human-readable title instead of a bare arxiv id.
    Falls back to the arxiv_id string when the corpus has no matching row.
    """
    from archimedes.api.schemas import PaperRefResponse
    from archimedes.services.return_source_classifier import (
        StrategyView,
        classify_return_source,
    )

    # What the generation run that produced this strategy consumed (#1326).
    # Needs the session, so it is None on the session-less call path — and None
    # is also the answer for every strategy generated before the meter existed.
    # Either way the UI renders "not measured"; nothing is zeroed or invented.
    generation_cost = _generation_cost_for(record.id, session)

    refs = list(record.paper_refs or [])
    first = refs[0] if refs else None

    # Enrich missing titles from the corpus when a session is available.
    corpus_titles: dict[str, str] = _enrich_paper_titles_from_corpus(refs, session) if session is not None else {}

    def _resolved_title(r) -> str:
        """Stored title wins; fall back to corpus; fall back to bare arxiv_id."""
        if (r.title or "").strip():
            return r.title
        if r.arxiv_id and corpus_titles.get(r.arxiv_id):
            return corpus_titles[r.arxiv_id]
        return r.arxiv_id or ""

    papers_list = [
        PaperRefResponse(
            arxiv_id=r.arxiv_id,
            title=_resolved_title(r),
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

    # The enriched first-paper title (may have been filled from corpus above).
    first_title = papers_list[0].title if papers_list else (first.title if first else "")

    return_source_enum, return_source_note = classify_return_source(
        StrategyView(
            paper_title=first_title or "",
            methodology_summary=record.methodology_summary or "",
            asset_universe=tuple(asset_universe),
            deflated_sharpe_ratio=record.deflated_sharpe_ratio,
            dsr_p_value=record.dsr_p_value,
            passes_rigor_gate=bool(record.passes_rigor_gate),
        )
    )

    return StrategyResponse(
        id=record.id,
        papers=papers_list,
        paper_arxiv_id=first.arxiv_id if first else None,
        paper_title=first_title or None,
        paper_authors=json.loads(first.authors) if first and first.authors else [],
        paper_venue=first.venue if first else None,
        paper_year=first.year if first else None,
        paper_doi=first.doi if first else None,
        paper_citation_count=first.citation_count if first else None,
        methodology_summary=record.methodology_summary or "",
        asset_universe=asset_universe,
        universe_source=record.universe_source,
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
        # Generated/fusion strategies carry a PERSISTED live-gate verdict written by
        # the generation pipeline (strategy_passports.passes_rigor_gate) — a stored
        # *live* verdict, not a fixture boolean — so it is a legitimate badge source
        # per #821 ("read a persisted live-gate verdict"). Map it to the tri-state:
        # a passport with no real backtest (sharpe_ratio is None) is "pending".
        passes_rigor_gate=bool(record.passes_rigor_gate),
        # #1184 KNOWN GAP: this three-state map is NOT threaded through the
        # DEGENERATE category live_rigor_gate/_verdict_from_result added — it
        # reads only the stored aggregate (record.sharpe_ratio /
        # passes_rigor_gate), not the persisted daily-return series, so a
        # generated/fusion strategy with a zero-variance series still reports
        # "fail" here rather than "degenerate". Fixing this needs the same
        # is_zero_variance_series/is_oos_zero_variance_series check run against
        # this passport's own persisted returns (get_daily_returns), which this
        # read path does not currently load. Tracked as an open follow-up on
        # #1184, not closed by this PR.
        rigor_gate_status=(
            "pending" if record.sharpe_ratio is None else ("pass" if bool(record.passes_rigor_gate) else "fail")
        ),
        is_backtest_placeholder=record.sharpe_ratio is None,
        sharpe_ci_lower=None,
        sharpe_ci_upper=None,
        backtest_start=record.backtest_start,
        backtest_end=record.backtest_end,
        regime_tag=record.regime_tag,
        return_source=return_source_enum.value,
        return_source_note=return_source_note,
        generation_cost=generation_cost,
    )


# ── Curated ∪ generated resolvers for read-surfaces beyond Library ────────
# (leaderboard, risk, chat — the "unify source" decouples in
# docs/CURATED-STRATEGY-DECOUPLE-AND-CONSOLIDATE-2026-07-08.md Part A).
# Curated strategies are UNCHANGED: callers keep sourcing those from
# strategy_provider() and concatenate the GENERATED half these return on top —
# nothing here alters the curated path.


def _generated_strategy_responses(
    session, caller: str | None = None, caller_user_id: str | None = None
) -> list[StrategyResponse]:
    """GENERATED (non-curated) strategies visible to *caller*, as StrategyResponse.

    Same #850 ownership-visibility rule as ``list_generated_strategies`` /
    ``_visible_passports``: a row is visible when ``is_published`` or the
    caller's verified wallet matches ``owner_wallet`` (``is_example`` is never
    True for a non-curated row). Used by surfaces that need real per-caller
    generated strategies (risk endpoints, chat vault-context) — NOT for
    unauthenticated public surfaces, which want ``_public_generated_strategy_responses``
    instead so a private candidate never leaks.
    """
    from archimedes.services.passport_loader import list_passports

    records = [r for r in list_passports(session) if (r.generation_method or "").lower() != "curated"]
    if not records:
        return []
    visible = _visible_passports(session, records, caller, caller_user_id)
    return [_passport_to_strategy_response(r, session) for r in visible]


def _public_generated_strategy_responses(session) -> list[StrategyResponse]:
    """GENERATED strategies visible on PUBLIC, unauthenticated surfaces (the
    leaderboard). No wallet context exists here, so visibility requires the
    OWNER to have opted in by PUBLISHING — ``is_published`` ONLY.

    ``status`` is deliberately NOT a visibility criterion: ``upsert_strategy``
    sets ``status="live"`` on ANY strategy whose rigor passes, published or not,
    so keying off it would leak a user's PRIVATE (unpublished) strategy — its
    name + metrics — onto a public ranking the moment it passed rigor. Publish
    is the consent signal, not rigor. (#850 privacy principle.)

    NOTE: ``is_published`` is currently a dormant flag — the publish flow does
    not yet flip it — so this is intentionally inert in prod until that wiring
    lands (tracked as a follow-up). Inert-but-safe beats leaky.
    """
    from archimedes.models.strategy_store import StrategyRecord
    from archimedes.services.passport_loader import list_passports

    records = [r for r in list_passports(session) if (r.generation_method or "").lower() != "curated"]
    if not records:
        return []
    ids = [r.id for r in records]
    published_ids = {
        sid
        for (sid,) in (
            session.query(StrategyRecord.id)
            .filter(StrategyRecord.id.in_(ids), StrategyRecord.is_published.is_(True))
            .all()
        )
    }
    visible = [r for r in records if r.id in published_ids]
    return [_passport_to_strategy_response(r, session) for r in visible]


def _owned_generated_strategy_responses(
    session, caller_wallet: str | None, caller_user_id: str | None
) -> list[StrategyResponse]:
    """GENERATED strategies OWNED by *caller* — the single-user leaderboard's
    "own" scope (leaderboard-goes-single-user MVP: with no publish mechanism
    live, ranking against a global cohort was incoherent, so a signed-in
    caller instead ranks THEIR OWN strategies against each other).

    Deliberately narrower than ``_generated_strategy_responses`` (published-
    by-anyone ∪ owned-by-caller): here the question is "is this MINE", not
    "am I allowed to see this", so another user's published strategy must
    NOT appear just because it is public. Reuses ``is_strategy_visible`` (the
    single #850 predicate — never re-implement ownership matching at a call
    site) for the two-tier owner_user_id/owner_wallet check, but pins
    ``is_published`` False in the row_view fed to it so a stranger's
    published row can never ride that predicate's publish-visibility clause
    onto this caller's board. Curated (``is_example``) rows have no owner and
    are never returned here — they are the separate "curated" scope.
    """
    from archimedes.services.passport_loader import list_passports
    from archimedes.services.strategy_visibility import is_strategy_visible

    if not caller_user_id and not caller_wallet:
        return []

    records = [r for r in list_passports(session) if (r.generation_method or "").lower() != "curated"]
    if not records:
        return []

    owned = []
    for r in records:
        row_view = {
            "is_example": False,
            "is_published": False,  # ownership only — publish state is irrelevant to "own"
            "owner_user_id": r.owner_user_id,
            "owner_wallet": r.owner_wallet,
        }
        if is_strategy_visible(row_view, caller_wallet, caller_user_id=caller_user_id):
            owned.append(r)
    return [_passport_to_strategy_response(r, session) for r in owned]


@strategies_router.get("/{strategy_id}/returns", response_model=StrategyReturnsResponse)
async def get_strategy_returns(strategy_id: str, request: Request):
    """Return persisted real daily returns for a strategy.

    Response schema: {strategy_id, source: "persisted_backtest", start, end,
    n, daily_returns: [...]}

    404 when the strategy does not exist (or is private and the caller is not
    the owner — 404-hides-existence per the #850 ownership gating contract).
    404 with body ``{"detail": "no persisted returns"}`` when the strategy
    exists but has no BacktestResultRecord row. Never synthesizes data from
    fixture metrics; only real persisted run data is returned (#passport-honesty).

    ``owner_wallet`` is intentionally absent from the response — pseudonymous
    PII, redacted per the same policy as GET /api/strategies/{id}.
    """
    from fastapi import HTTPException

    # ── 1. Existence + ownership gate (mirrors get_strategy) ────────────────
    # Curated strategies (in LocalStrategyProvider) are always public.
    strat = strategy_provider().get_strategy(strategy_id)
    is_curated = strat is not None

    if not is_curated:
        from archimedes.api.auth_siwe import get_verified_wallet
        from archimedes.db import get_session
        from archimedes.models.strategy_store import StrategyRecord
        from archimedes.services.strategy_visibility import is_strategy_visible

        with get_session() as session:
            row = session.query(StrategyRecord).filter_by(id=strategy_id).first()
            # The legacy-owner fallback compares a wallet the caller has
            # PROVEN control of this session, so this site uses the SIWE
            # get_verified_wallet, not get_linked_wallet_address.
            caller = get_verified_wallet(request)
            user = get_current_user(request)
            if not is_strategy_visible(row, caller, caller_user_id=user.id if user else None):
                raise HTTPException(status_code=404, detail="Strategy not found")

    # ── 2. Load persisted daily returns from backtest_results ────────────────
    try:
        from archimedes.db import get_session, init_db
        from archimedes.services.backtest_repository import get_daily_returns, latest_backtests_by_strategy

        init_db()
        with get_session() as session:
            daily_returns = get_daily_returns(session, strategy_id)
            rows = latest_backtests_by_strategy(session, [strategy_id])
            latest_row = rows.get(strategy_id)
    except Exception as exc:
        logger.warning("returns endpoint DB read failed for %s: %s", strategy_id, exc)
        raise HTTPException(status_code=500, detail="Failed to load returns") from exc

    if not daily_returns:
        raise HTTPException(status_code=404, detail="no persisted returns")

    # ── 3. Build date window from the backtest row (best-effort) ─────────────
    start: str | None = None
    end: str | None = None
    if latest_row is not None:
        if latest_row.backtest_start:
            start = str(latest_row.backtest_start)
        if latest_row.backtest_end:
            end = str(latest_row.backtest_end)

    return StrategyReturnsResponse(
        strategy_id=strategy_id,
        source="persisted_backtest",
        start=start,
        end=end,
        n=len(daily_returns),
        daily_returns=daily_returns,
    )


@strategies_router.get("/{strategy_id}", response_model=StrategyResponse)
async def get_strategy(strategy_id: str, request: Request):
    """Get a single strategy by ID. Tries LocalStrategyProvider (curated)
    first; falls through to the strategy_passports table for fusion- and
    architect-generated strategies so they're clickable from Library.

    Private-until-published: non-public row is 404 unless canonical user owns it,
    with linked-wallet fallback for legacy rows. 404 prevents existence probing.
    Curated strategies (provider path / is_example rows) stay fully public.
    """
    from fastapi import HTTPException

    strat = strategy_provider().get_strategy(strategy_id)
    if strat is not None:
        return _to_strategy_response(strat)

    from archimedes.api.auth_siwe import get_verified_wallet
    from archimedes.db import get_session
    from archimedes.models.strategy_store import StrategyRecord
    from archimedes.services.passport_loader import get_passport
    from archimedes.services.strategy_visibility import is_strategy_visible

    with get_session() as session:
        row = session.query(StrategyRecord).filter_by(id=strategy_id).first()
        # SIWE-proven wallet, not the linked-wallet lookup — see the note at
        # the sibling call site above.
        caller = get_verified_wallet(request)
        user = get_current_user(request)
        if row is not None and not is_strategy_visible(row, caller, caller_user_id=user.id if user else None):
            raise HTTPException(status_code=404, detail="Strategy not found")
        record = get_passport(session, strategy_id)
        if record is not None:
            return _passport_to_strategy_response(record, session=session)

    raise HTTPException(status_code=404, detail="Strategy not found")


@strategies_router.patch("/{strategy_id}")
async def rename_strategy(
    strategy_id: str,
    payload: dict,
    request: Request,
    user: CurrentUser = Depends(require_current_user),
):
    """Rename an owned, generated strategy — ``{"name": "<1..80 chars>"}``.

    Owner-gated: requires Better Auth session and canonical row ownership.
    Curated examples (``is_example``) are not renamable.
    The generation-time ``content_hash``/``provenance_hash`` are deliberately
    NOT recomputed — they are provenance of the original generation, not of the
    display name. The strategy_passports table carries no display-name column,
    so only strategy_store is updated.

    Legacy-wallet fallback (#1283): a pre-account row (``owner_user_id`` NULL)
    matched via the caller's linked wallet is reclaimed onto canonical account
    ownership in the same transaction as the rename, using the same bulk claim
    (``claim_legacy_wallet_data``) a verified wallet link performs — but
    scoped to the strategy-side tables only (``StrategyRecord`` /
    ``StrategyPassportRecord`` / ``StrategyProposal``), with
    ``include_profile=False``. The write is irreversible (no un-claim path)
    and reaches every unclaimed strategy row for this wallet, not just the
    one being renamed — that is judged acceptable here because it can only
    ever touch rows already tied to a wallet ``get_linked_wallet_address``
    resolves for *this* account (a wallet linked elsewhere 409s at link time;
    see ``_link_verified_wallet``), the same reach a real wallet re-link would
    have. ``vault_metadata`` and ``user_profiles`` are deliberately excluded:
    a rename has no business moving vault ownership — that 409-gates on
    ``owner_user_id`` being ``None`` specifically so a legitimately
    transferred on-chain owner can still write it, and a stale reclaim here
    would slam that door shut with no way back — or adopting a PII-bearing
    profile on a lookup that never asked for a fresh signature. Full
    reclaim of those two legs stays behind the signature-verified wallet-link
    flow. This migrates pre-account strategy rows toward zero over time;
    deleting the fallback branch entirely is a follow-up gated on verifying
    no unclaimed rows remain.
    """
    from datetime import datetime

    from fastapi import HTTPException

    from archimedes.api.wallet_routes import claim_legacy_wallet_data
    from archimedes.db import get_session
    from archimedes.models.strategy_passport_record import StrategyPassportRecord
    from archimedes.models.strategy_proposal import StrategyProposal
    from archimedes.models.strategy_store import StrategyRecord

    name = payload.get("name")
    if not isinstance(name, str):
        raise HTTPException(status_code=422, detail="'name' (string) is required")
    name = name.strip()
    if not 1 <= len(name) <= 80:
        raise HTTPException(status_code=422, detail="name must be 1–80 characters after trimming")

    with get_session() as session:
        row = session.query(StrategyRecord).filter_by(id=strategy_id).first()
        if row is None or row.is_example:
            # Curated examples are not user-owned — same 404 as a missing row.
            raise HTTPException(status_code=404, detail="Strategy not found")
        caller = get_linked_wallet_address(request)
        is_owner = row.owner_user_id == user.id
        if not is_owner and row.owner_user_id is None and row.owner_wallet and caller == row.owner_wallet.lower():
            # Proven via linked-wallet match on a still-unclaimed row: reclaim
            # every pre-account STRATEGY row tied to this wallet (not just
            # this one), matching what re-verifying the wallet link would do
            # for those tables. vault_metadata/user_profiles are excluded —
            # see the docstring above.
            claim_legacy_wallet_data(
                session,
                user.id,
                caller,
                models=(
                    (StrategyRecord, StrategyRecord.owner_wallet),
                    (StrategyPassportRecord, StrategyPassportRecord.owner_wallet),
                    (StrategyProposal, StrategyProposal.owner_wallet),
                ),
                include_profile=False,
            )
            row.owner_user_id = user.id
            is_owner = True
        if not is_owner:
            # Hide unpublished rows from non-owners (404); published rows are
            # visible, so an honest 403 is returned instead.
            if row.is_published:
                raise HTTPException(status_code=403, detail="Not authorized to rename this strategy.")
            raise HTTPException(status_code=404, detail="Strategy not found")

        row.strategy_name = name
        row.updated_at = datetime.now(UTC)
        session.commit()
        return {"strategy": row.to_dict()}


# ── Strategy generation (fusion) ────────────────────────────────


@strategies_router.post("/generate", status_code=202)
@limiter.limit("20/minute")
async def generate_strategy(
    request: Request,
    response: Response,  # noqa: ARG001
    asset_classes: str = "",
    risk_appetite: str = "moderate",
    strategic_direction: str = "",
    max_papers: int = 4,
    user: CurrentUser = Depends(require_current_user),
):
    """Queue a strategy generation job. Returns 202 + job_id immediately.

    Direct-fusion path only — the ``mode=fast`` (interactive Strategy
    Architect) branch was removed in #1064; the debate society
    (``POST /api/generate/start``) is the sole interactive generation path.
    """
    from fastapi import HTTPException

    from archimedes.agents.strategy_fusion import fusion_enabled, load_corpus
    from archimedes.models.portfolio import RiskProfile
    from archimedes.services.job_queue import JobStore

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
            consensus_data = await state.load_ensemble_consensus()
            # Surface market regime (exogenous, may be absent) and ensemble
            # consensus (endogenous, from flat_pct) as DISTINCT context (#659).
            if regime_data or consensus_data:
                market_context = {
                    "regime": (regime_data or {}).get("regime", "unknown"),
                    "ensemble_consensus": (consensus_data or {}).get("label", "unknown"),
                    "confidence": (consensus_data or regime_data or {}).get("confidence", 0.0),
                    "source": (consensus_data or regime_data or {}).get("source", ""),
                    "strategy_count": (consensus_data or regime_data or {}).get("strategy_count", 0),
                    "signals": (consensus_data or regime_data or {}).get("signals", {}),
                }
        finally:
            await state.close()
    except Exception:
        logger.debug("market regime context read failed", exc_info=True)

    linked_wallet = get_linked_wallet_address(request)
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
                "owner_user_id": user.id,
                "owner_wallet": linked_wallet,
            },
        )
    finally:
        await store.close()

    # Intentional fire-and-forget: the fusion job runs to completion independently
    # of the HTTP request that queued it; progress is observed via /jobs/{id}/stream.
    asyncio.create_task(_run_fusion_job(job_id))  # noqa: RUF006

    return {"status": "queued", "job_id": job_id}


@strategies_router.get("/generate/{job_id}")
async def get_generation_job(job_id: str, user: CurrentUser = Depends(require_current_user)):
    """Poll a strategy generation job. Returns status + result when done."""
    from fastapi import HTTPException

    from archimedes.services.job_queue import JobStore

    store = JobStore()
    try:
        job = await store.get(job_id)
    finally:
        await store.close()

    if job is None or (job.get("payload") or {}).get("owner_user_id") not in {None, user.id}:
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
                from archimedes.agents.generation_pipeline import _society_num_trials
                from archimedes.services.fusion_evaluator import evaluate_fusion_spec
                from archimedes.services.fusion_market_data import real_data_enabled

                # Decouple #2: num_trials = the strategy's OWN selection pool, NOT
                # the curated library's count. A single direct-fusion job proposes
                # exactly one candidate spec (pool=1) — no N-candidate search
                # happens on this route — so the self-contained trial count is
                # _society_num_trials(1) == 1. Passed explicitly (not left as
                # None) so this route's deflation matches the same formula the
                # society/live generation paths use, without reaching for the
                # library size the way the old ``library_size + pool`` term did.
                eval_result = await asyncio.to_thread(
                    evaluate_fusion_spec,
                    result.strategy_spec,
                    use_real_data=real_data_enabled(),
                    num_trials=_society_num_trials(1),
                )
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
                # Honest label distinct from the bare bool above: the DSL's
                # self-attested look_ahead_safe is enforced as an admission
                # gate, but it is NOT the independent AST audit that
                # rigor_evaluator.look_ahead_audit runs against cited curated
                # source. Surfaced so the passport doesn't read this as that
                # audit having passed (audit 06-14, Q6).
                "look_ahead_label": r.look_ahead_label,
                "num_trials": int(r.num_trials),
                # Methodology marker (#1075): this verdict was computed under the
                # self-contained num_trials convention (decouple #2). Blobs
                # WITHOUT this key predate the change (formula A, N+library_size)
                # and are not directly comparable.
                "num_trials_convention": "self_contained_v2",
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
        persist_error: str | None = None
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
                    owner_wallet=payload.get("owner_wallet"),
                    owner_user_id=payload.get("owner_user_id"),
                )
                session.commit()
                strategy_id = record.id
        except Exception as exc:
            # Persist failure is NOT cosmetic: without a saved strategy_id there is
            # no record for the "view" link to open, so the job must not be reported
            # as a successful "done". Log at ERROR (was debug — the failure was
            # silently swallowed) and mark the job failed below (#948).
            persist_error = str(exc)
            logger.error("fusion strategy persist failed for job %s", job_id, exc_info=True)

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
                    # Honest label — see rigor_verdict_dict above (audit 06-14, Q6).
                    "look_ahead_label": eval_result.rigor.look_ahead_label,
                }
            if eval_result.error:
                job_result["eval_error"] = eval_result.error

        if strategy_id is None:
            # The fusion produced an actionable strategy but it could not be saved,
            # so there is nothing for the "view" link to open. Report the job as
            # failed rather than a "done" job with a null strategy_id + dead link
            # (#948). The proposal is still recorded in episodic memory below.
            await store.update_status(
                job_id,
                "failed",
                error=f"Strategy generated but could not be saved: {persist_error or 'persistence failed'}",
            )
        else:
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
                owner_wallet=payload.get("owner_wallet"),
                owner_user_id=payload.get("owner_user_id"),
            )
        except Exception:
            pass  # Non-blocking per spec
    except Exception as exc:
        with contextlib.suppress(Exception):
            await store.update_status(job_id, "failed", error=str(exc))
    finally:
        await store.close()

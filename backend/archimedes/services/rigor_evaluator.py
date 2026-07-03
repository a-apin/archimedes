"""Selection-bias corrections: DSR, PBO, walk-forward OOS Sharpe, look-ahead audit.

Implements the four-primitive admission gate that separates Tier-1 (Archimedes
Verified) strategies from curve-fit noise:

  1. Deflated Sharpe Ratio — Bailey & López de Prado (2014)
  2. Probability of Backtest Overfitting via CSCV — Bailey et al. (2014)
  3. Out-of-sample Sharpe — single chronological hold-out today (see
     compute_oos_sharpe); rolling Combinatorial Purged CV (compute_cpcv_oos_sharpe)
     is the principled upgrade, wired once a combinatorial OOS matrix exists
  4. Look-ahead static audit (AST-based)

Pure computation and orchestration: no I/O, no web framework, no on-chain
dependencies. Arithmetic helpers extracted to _rigor_helpers module for clarity.

Owner: Önder (math lane)
Spec:  docs/specs/selection-bias-corrections-spec.md
"""

from __future__ import annotations

import ast
import logging
import math

import numpy as np
from sqlalchemy.orm import Session

from archimedes.services._rigor_helpers import (
    _ANNUALIZATION,
    _RF_DAILY,
    benjamini_hochberg_fdr,  # noqa: F401 - re-exported for test_rigor_evaluator
    bonferroni_correction,  # noqa: F401 - re-exported for test_rigor_evaluator
    classify_regimes,  # used by run_rigor_gate (regime-robustness) + re-exported for test_rigor_regime
    compute_average_pairwise_correlation,  # noqa: F401 - re-exported for fusion_evaluator/selection_bias_routes
    compute_cpcv_oos_sharpe,
    compute_dsr,  # noqa: F401 - re-exported for portfolio_backtester / stockbench adapter
    compute_dsr_hac_and_iid,
    compute_in_sample_sharpe,  # noqa: F401 - re-exported for fusion_evaluator
    compute_oos_sharpe,
    compute_pbo,  # used by compute_library_pbo below + re-exported (fusion/generation/test_pbo_parity)
    compute_sharpe_ci,  # noqa: F401 - re-exported for strategy_provider
    monte_carlo_dsr_pvalue,  # noqa: F401 - re-exported for test_rigor_evaluator
    regime_conditional_dsr,  # noqa: F401 - re-exported for test_rigor_regime
    regime_conditional_sharpe,  # noqa: F401 - re-exported for test_rigor_regime
    regime_robustness_score,  # used by run_rigor_gate (regime-robustness) + re-exported for test_rigor_regime
)
from archimedes.services.return_diagnostics import diagnose

logger = logging.getLogger(__name__)


# ─── Look-ahead static audit (AST-based) ──────────────────────────────

_LOOK_AHEAD_FUNCTIONS = {
    "future",
    "forecast",
    "predict",
    "peek",
    "lookahead",
    "look_ahead",
}


def look_ahead_audit(strategy_code: str) -> tuple[bool, list[str]]:
    """Static-analysis check for look-ahead bias in strategy code.

    Parses the strategy source with AST and checks for:
    1. Forward data access patterns (e.g., self.data.close[+N])
    2. Calls to functions with look-ahead-suggestive names
    3. Direct indexing into data feeds beyond current bar
    4. Negative shifts (e.g., pandas df.shift(-1)) which leak future data.

    Args:
        strategy_code: Python source code of the strategy.

    Returns:
        (passed, warnings) — passed=True if no look-ahead detected.
    """
    warnings: list[str] = []

    try:
        tree = ast.parse(strategy_code)
    except SyntaxError as e:
        return False, [f"Cannot parse strategy code: {e}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = _get_func_name(node.func)
            if func_name and func_name.lower() in _LOOK_AHEAD_FUNCTIONS:
                warnings.append(f"Line {node.lineno}: call to '{func_name}' may indicate look-ahead bias")

            # Check for pandas negative shifts, e.g., shift(-1)
            if func_name == "shift":

                def _is_negative(val_node: ast.AST) -> bool | int | float:
                    if isinstance(val_node, ast.UnaryOp) and isinstance(val_node.op, ast.USub):
                        if isinstance(val_node.operand, ast.Constant) and isinstance(
                            val_node.operand.value, (int, float)
                        ):
                            return val_node.operand.value
                    elif (
                        isinstance(val_node, ast.Constant)
                        and isinstance(val_node.value, (int, float))
                        and val_node.value < 0
                    ):
                        return abs(val_node.value)
                    return False

                # The first positional argument is 'periods'
                if len(node.args) > 0:
                    val = _is_negative(node.args[0])
                    if val is not False:
                        warnings.append(f"Line {node.lineno}: negative shift(-{val}) references future data")
                # Alternatively, check keyword arguments for 'periods'
                for kw in node.keywords:
                    if kw.arg == "periods":
                        val = _is_negative(kw.value)
                        if val is not False:
                            warnings.append(f"Line {node.lineno}: negative shift(-{val}) references future data")

        if isinstance(node, ast.Subscript):
            slice_val = node.slice
            if isinstance(slice_val, ast.UnaryOp) and isinstance(slice_val.op, ast.USub):
                # Negative indices are safe in backtrader ([-N] = N bars ago) but
                # would reference the last row of future data in a pandas DataFrame
                # (df.iloc[-1], df["col"][-1]).  Flag so reviewers can verify the
                # calling context before promotion to Tier-1.
                warnings.append(
                    f"Line {node.lineno}: negative index — verify this is backtrader "
                    f"(bars-ago) not pandas (last-row) access."
                )
            elif isinstance(slice_val, ast.Constant) and isinstance(slice_val.value, int) and slice_val.value > 0:
                warnings.append(
                    f"Line {node.lineno}: positive data index [{slice_val.value}] may reference future bars"
                )

    return len(warnings) == 0, warnings


def _get_func_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


# ─── Library-level PBO (criterion-4 input, #546) ──────────────────────


def align_returns_store(
    returns_store: dict[str, dict[str, list]],
) -> dict[str, list[float]]:
    """Inner-join dated return series on ISO dates → an aligned PBO matrix.

    Mirrors ``analytics-engine/scripts/compute_library_pbo.py::build_aligned_matrix``
    so the backend gate and the offline diagnostic compute the library PBO from
    the *same* alignment. The library's strategies trade different calendars
    (^N225 vs SPY vs joined pair windows); CSCV requires that row *i* of the
    returns matrix means the same trading day for every strategy, so we keep
    only the dates present in *every* series before handing off to
    ``compute_pbo`` (which itself only truncates to the shortest length).

    Args:
        returns_store: ``{strategy_stem: {"dates": [...], "daily_returns": [...]}}``
            — one entry per library strategy, each carrying a 1:1 dated series
            (the shape of ``analytics-engine/strategies/daily_returns/<stem>.json``).
            The caller loads it; this module stays I/O-free.

    Returns:
        ``{strategy_stem: returns_on_joint_dates}`` — date-aligned, ready for
        ``compute_pbo``. Returns ``{}`` when fewer than two usable series are
        supplied or the dated intersection is empty (the caller fail-closes on
        the empty matrix — see ``compute_library_pbo``).
    """
    series = {stem: rec for stem, rec in returns_store.items() if rec.get("dates") and rec.get("daily_returns")}
    if len(series) < 2:
        return {}

    date_sets = [set(rec["dates"]) for rec in series.values()]
    joint = sorted(set.intersection(*date_sets))
    if not joint:
        return {}

    matrix: dict[str, list[float]] = {}
    for stem, rec in series.items():
        # strict=True (matching the offline build_aligned_matrix) fails loud on a
        # malformed record whose dates/daily_returns lengths disagree, rather than
        # silently truncating and risking a misaligned row / KeyError on join.
        by_date = dict(zip(rec["dates"], rec["daily_returns"], strict=True))
        matrix[stem] = [float(by_date[d]) for d in joint]
    return matrix


def compute_library_pbo(
    returns_store: dict[str, dict[str, list]],
    s_partitions: int = 16,
) -> float | None:
    """Single library-level CSCV PBO over the whole selection set (#546).

    This is the criterion-4 input for ``run_rigor_gate``: PBO is a property of
    the *selection set*, not of an individual strategy (Bailey et al. 2014), so
    the principled gate input is one library-wide value, not a per-cohort score.
    Date-aligns the store via ``align_returns_store`` then runs the same
    parity-tested ``compute_pbo`` the cohort path uses.

    **Refresh policy (gate-affecting — documented in docs/specs/library-pbo.md).**
    CSCV PBO is a property of the selection set: adding, removing, or re-running
    any library strategy changes it. The caller MUST recompute (call this
    function afresh) whenever the selection set changes — i.e. on every library
    add, removal, or daily-returns-store regeneration — and MUST NOT freeze the
    value into a per-strategy fixture. The store is add-only + idempotent
    (``gen_daily_returns_store.py``), so "selection set changed" reduces to "a
    new ``daily_returns/<stem>.json`` exists"; recomputing on store growth is
    the cadence.

    Args:
        returns_store: ``{strategy_stem: {"dates": [...], "daily_returns": [...]}}``.
        s_partitions: Number of equal time-partitions S (even ≥ 2; default 16,
            the paper's recommended value).

    Returns:
        The single library PBO ∈ [0, 1], or ``None`` when it cannot be computed
        (fewer than two aligned series, empty date intersection, a joint window
        shorter than ``s_partitions``, or a degenerate ``compute_pbo`` result).
        ``None`` is the fail-closed signal: the gate treats a missing/non-finite
        library PBO as criterion-4 FAIL rather than silently passing. A
        non-finite value (should never arise from the rounded ``compute_pbo``
        output, but guarded for safety) also returns ``None``.
    """
    matrix = align_returns_store(returns_store)
    if len(matrix) < 2:
        return None

    # Fail closed on a too-short joint window. compute_pbo returns an all-0.0
    # sentinel (a spurious criterion-4 PASS, PBO < 0.5) when rows_per_block =
    # T // s_partitions < 1 — i.e. fewer joint dates than partitions. That 0.0
    # is non-finite-clean but meaningless, so guard it here: an under-length
    # window is non-computable, which must FAIL criterion 4, not pass it.
    shortest = min(len(r) for r in matrix.values())
    if shortest // s_partitions < 1:
        return None

    scores = compute_pbo(matrix, s_partitions=s_partitions)
    if not scores:
        return None

    # compute_pbo attaches the same library-level value to every member; take any.
    pbo = next(iter(scores.values()))
    if pbo is None or not math.isfinite(pbo):
        return None
    return float(pbo)


def load_daily_returns_store(
    session: Session | None = None,
) -> tuple[dict[str, dict[str, list]], str | None]:
    """Load the daily-returns store into the shape ``compute_library_pbo`` wants.

    #774: reads the ``strategy_daily_returns`` DB table (one row per
    (stem, date) observation) instead of scanning
    ``analytics-engine/strategies/daily_returns/*.json`` — the same shape
    ``align_returns_store`` / ``compute_library_pbo`` consume, unchanged.
    ``date`` round-trips to the same ``YYYY-MM-DD`` string the JSON records
    used (``date.isoformat()``), so callers see byte-identical output.

    Also returns the data vintage: the MAX ``data_vintage`` string seen across
    every row, so the caller can surface "as of <vintage>" provenance — the
    same semantics as before (each stem's rows share one vintage; the overall
    value is the max across stems).

    **Never raises.** A DB read failure (unreachable DB, table not yet
    migrated) degrades gracefully to ``({}, None)`` — this mirrors the
    fail-closed contract of ``compute_library_pbo``, same as the pre-#774 JSON
    path degrading gracefully when the analytics-engine tree was absent.

    Args:
        session: An existing DB session to query on (tests pass an isolated
            one). When ``None``, opens and closes its own session via
            ``archimedes.db.get_session``.

    Returns:
        ``(store, data_vintage)`` where ``store`` is
        ``{stem: {"dates": [...], "daily_returns": [...]}}`` (empty when the
        table has no rows or the read fails) and ``data_vintage`` is the max
        vintage string across rows (``None`` when no row carried one).
    """
    from archimedes.models.daily_returns_store import StrategyDailyReturn

    def _query(sess: Session) -> tuple[dict[str, dict[str, list]], str | None]:
        rows = sess.query(StrategyDailyReturn).order_by(StrategyDailyReturn.stem, StrategyDailyReturn.date).all()
        store: dict[str, dict[str, list]] = {}
        vintages: list[str] = []
        for row in rows:
            entry = store.setdefault(row.stem, {"dates": [], "daily_returns": []})
            entry["dates"].append(row.date.isoformat())
            entry["daily_returns"].append(row.daily_return)
            if row.data_vintage:
                vintages.append(row.data_vintage)
        data_vintage = max(vintages) if vintages else None
        return store, data_vintage

    try:
        if session is not None:
            return _query(session)
        from archimedes.db import get_session

        with get_session() as sess:
            return _query(sess)
    except Exception:
        logger.warning("load_daily_returns_store: DB read failed, degrading to empty store", exc_info=True)
        return {}, None


# ─── 6. Rigor Gate — composite check ─────────────────────────────────


class RigorGateResult:
    """Result of running all four selection-bias checks on a strategy."""

    def __init__(
        self,
        strategy_id: str,
        deflated_sharpe: float | None = None,
        dsr_p_value: float | None = None,
        dsr_p_value_iid: float | None = None,
        dsr_se_method: str = "iid",
        num_trials: int = 1,
        pbo_score: float | None = None,
        oos_sharpe: float | None = None,
        look_ahead_passed: bool = False,
        in_sample_sharpe: float | None = None,
        paper_claimed_sharpe: float | None = None,
        cpcv_mean_oos_sharpe: float | None = None,
        cpcv_positive_fraction: float | None = None,
        pbo_source: str = "cohort",
        iid_assumption_violated: bool | None = None,
        iid_diagnostics: dict | None = None,
        regime_robustness: dict | None = None,
    ) -> None:
        self.strategy_id = strategy_id
        self.deflated_sharpe = deflated_sharpe
        self.dsr_p_value = dsr_p_value
        # The gating dsr_p_value uses a serial-correlation-robust (Newey–West
        # HAC) standard error (#621 follow-up). dsr_p_value_iid is what the IID
        # SE would have produced — surfaced as an advisory delta so the passport
        # shows how much serial dependence moved the verdict; dsr_se_method
        # records which SE backs the gating p-value ("hac" or "iid").
        self.dsr_p_value_iid = dsr_p_value_iid
        self.dsr_se_method = dsr_se_method
        self.num_trials = num_trials
        self.pbo_score = pbo_score
        # Which selection set produced the criterion-4 PBO: "library" when the
        # full-library CSCV PBO was supplied (the principled Bailey et al. input,
        # #546), "cohort" when it fell back to the per-cohort dict score. Surfaced
        # in gate_details so the verdict is honest about its provenance.
        self.pbo_source = pbo_source
        self.oos_sharpe = oos_sharpe
        self.look_ahead_passed = look_ahead_passed
        self.in_sample_sharpe = in_sample_sharpe
        self.paper_claimed_sharpe = paper_claimed_sharpe
        # Combinatorial Purged CV results (None when the series is too short to
        # partition). When present, the gate additionally requires that the
        # strategy's edge holds OOS across a majority of CPCV paths.
        self.cpcv_mean_oos_sharpe = cpcv_mean_oos_sharpe
        self.cpcv_positive_fraction = cpcv_positive_fraction
        # IID / random-walk return diagnostics (#621). ADVISORY, not a pass/fail
        # criterion: autocorrelated returns are the *signal* for trend/momentum
        # strategies (Faber SMA200, TSMOM), so an IID violation must NOT fail the
        # gate — it would wrongly reject legitimate strategies. We surface it so
        # the diagnostic is honest (computed AND reported, not computed-and-dropped)
        # and the passport can flag "interpret the Sharpe SE with caution here".
        # iid_diagnostics carries the full Ljung-Box / variance-ratio / runs detail.
        self.iid_assumption_violated = iid_assumption_violated
        self.iid_diagnostics = iid_diagnostics
        # Regime-robustness (per-volatility-regime Sharpe consistency). ADVISORY for
        # the same reason: a single-regime edge can be a legitimate, disclosed strategy.
        # Carries per_regime / min_regime_sharpe / consistency / robust (see
        # _rigor_helpers.regime_robustness_score). None when the series is too short.
        self.regime_robustness = regime_robustness

    @property
    def passes_all(self) -> bool:
        # NaN-hardening: every IEEE-754 comparison against NaN is False, so a NaN
        # metric (not None — None is guarded) would silently skip its fail branch
        # and let an under-credentialed strategy pass. Treat any non-finite metric
        # as an automatic fail. pbo_score is sourced either from the full-library
        # CSCV PBO (#546, the principled input) or — fallback — an external cohort
        # dict; oos/IS Sharpe can carry NaN if upstream returns contain NaN.
        if self.dsr_p_value is None or not math.isfinite(self.dsr_p_value):
            return False
        if self.dsr_p_value < 0.95:
            return False
        if self.pbo_score is None or not math.isfinite(self.pbo_score):
            return False
        if self.pbo_score >= 0.5:
            return False
        if self.oos_sharpe is None or not math.isfinite(self.oos_sharpe):
            return False
        if self.oos_sharpe <= 0.0:  # absolute OOS floor: negative OOS cannot pass
            return False
        if (
            self.in_sample_sharpe is not None
            and math.isfinite(self.in_sample_sharpe)
            and self.in_sample_sharpe > 0
            and self.oos_sharpe / self.in_sample_sharpe < 0.5
        ):
            return False
        # Combinatorial Purged CV: when computed, the edge must hold OOS across a
        # majority of held-out paths (not just the single 70/30 tail above).
        if self.cpcv_positive_fraction is not None and (
            not math.isfinite(self.cpcv_positive_fraction) or self.cpcv_positive_fraction < 0.5
        ):
            return False
        # NOTE: the IID (#621) and regime-robustness diagnostics are deliberately NOT
        # pass/fail criteria. They are surfaced via gate_details as advisories — see __init__.
        return self.look_ahead_passed

    @property
    def gate_details(self) -> dict[str, str]:
        details: dict[str, str] = {}

        if self.dsr_p_value is not None and self.dsr_p_value >= 0.95:
            details["dsr"] = f"PASS (p={self.dsr_p_value:.4f})"
        elif self.dsr_p_value is not None:
            details["dsr"] = f"FAIL (p={self.dsr_p_value:.4f}, need ≥ 0.95)"
        else:
            details["dsr"] = "MISSING"
        # Disclose the Sharpe convention behind the DSR (#547). The backend gate
        # computes excess-return Sharpe; served library fixtures carry their own
        # per-entry "dsr_convention" ("raw" for frozen legacy, "excess" for new).
        details["dsr_convention"] = "excess"
        # Disclose the standard-error model behind the DSR (#621 follow-up): the
        # gate uses a Newey–West HAC SE robust to serial dependence. Surface the
        # IID-SE p-value as a delta so the reader sees the size of the correction.
        if self.dsr_se_method == "hac":
            if self.dsr_p_value is not None and self.dsr_p_value_iid is not None:
                details["dsr_se"] = (
                    f"HAC (Newey–West); IID-SE p={self.dsr_p_value_iid:.4f} "
                    f"→ HAC p={self.dsr_p_value:.4f} (Δ={self.dsr_p_value - self.dsr_p_value_iid:+.4f})"
                )
            else:
                details["dsr_se"] = "HAC (Newey–West)"
        else:
            details["dsr_se"] = "IID (classical Bailey-LdP)"

        # Criterion 4 input provenance (#546): "library" = full-library CSCV PBO
        # (the principled Bailey et al. selection-set input), "cohort" = the
        # per-cohort dict score. Disclosed so the verdict states which set fed it.
        if self.pbo_score is not None and math.isfinite(self.pbo_score) and self.pbo_score < 0.5:
            details["pbo"] = f"PASS (PBO={self.pbo_score:.4f}, source={self.pbo_source})"
        elif self.pbo_score is not None and math.isfinite(self.pbo_score):
            details["pbo"] = f"FAIL (PBO={self.pbo_score:.4f}, need < 0.5, source={self.pbo_source})"
        else:
            details["pbo"] = f"MISSING (source={self.pbo_source})"

        if self.oos_sharpe is not None and self.in_sample_sharpe and self.in_sample_sharpe > 0:
            ratio = self.oos_sharpe / self.in_sample_sharpe
            if ratio >= 0.5:
                details["oos_sharpe"] = f"PASS (OOS/IS={ratio:.2f})"
            else:
                details["oos_sharpe"] = f"FAIL (OOS/IS={ratio:.2f}, need ≥ 0.50)"
        elif self.oos_sharpe is not None:
            details["oos_sharpe"] = f"SET (OOS={self.oos_sharpe:.4f}, no IS reference)"
        else:
            details["oos_sharpe"] = "MISSING"

        if self.cpcv_positive_fraction is not None:
            if self.cpcv_positive_fraction >= 0.5:
                details["cpcv"] = (
                    f"PASS (OOS+ {self.cpcv_positive_fraction:.0%} of paths, "
                    f"mean OOS SR={self.cpcv_mean_oos_sharpe:.2f})"
                )
            else:
                details["cpcv"] = f"FAIL (OOS+ only {self.cpcv_positive_fraction:.0%} of paths, need ≥ 50%)"
        else:
            # Honest not-run status (#771): the surface must never advertise CPCV as a
            # method while silently emitting a bare "MISSING". CPCV needs a 2-D (S, T)
            # matrix of per-combinatorial-split OOS returns; it is mathematically invalid
            # on a single static return series, so when no matrix is supplied we say so
            # explicitly rather than implying a method that produced no number.
            details["cpcv"] = (
                "NOT_RUN (no combinatorial OOS matrix supplied; CPCV is invalid on a single static return series)"
            )

        details["look_ahead"] = "PASS" if self.look_ahead_passed else "FAIL"

        # IID / random-walk diagnostic (#621) — ADVISORY, never gates pass/fail.
        # The diagnostic no longer rests on an SE it cannot defend: the gate's
        # DSR now uses a Newey–West HAC standard error (see details["dsr_se"]),
        # so a detected autocorrelation is *corrected* in the verdict, not merely
        # flagged. We still surface the diagnostic because it tells the reader
        # WHY the HAC correction did (or did not) move the p-value — and because
        # autocorrelation is the expected, legitimate signal for trend/momentum.
        if self.iid_assumption_violated is None:
            details["iid"] = "MISSING"
        elif self.iid_assumption_violated:
            details["iid"] = (
                "ADVISORY: autocorrelation detected — Sharpe SE corrected via HAC (Newey–West); "
                "expected for trend/momentum, where the autocorrelation is the edge"
            )
        else:
            details["iid"] = "ADVISORY: returns consistent with IID / random-walk (HAC ≈ classical SE)"

        # Regime-robustness — ADVISORY, never gates pass/fail. Shows whether the edge
        # survives across volatility regimes or is fragile (earned in only one).
        rr = self.regime_robustness
        if not rr or rr.get("min_regime_sharpe") is None:
            details["regime_robustness"] = "MISSING"
        elif rr.get("robust"):
            details["regime_robustness"] = (
                f"ADVISORY: edge holds across regimes (min regime SR={rr['min_regime_sharpe']:.2f}, "
                f"consistency={rr.get('consistency', 0.0):.2f})"
            )
        else:
            details["regime_robustness"] = (
                f"ADVISORY: regime-fragile (min regime SR={rr['min_regime_sharpe']:.2f} ≤ 0 in ≥1 regime, "
                f"consistency={rr.get('consistency', 0.0):.2f})"
            )

        return details


def run_rigor_gate(
    strategy_id: str,
    daily_returns: list[float],
    num_trials: int = 1,
    pbo_scores: dict[str, float] | None = None,
    strategy_code: str | None = None,
    in_sample_sharpe: float | None = None,
    paper_claimed_sharpe: float | None = None,
    look_ahead_audit_passed: bool | None = None,
    average_correlation: float = 0.0,
    cv_returns_matrix: np.ndarray | list[list[float]] | None = None,
    library_pbo: float | None = None,
) -> RigorGateResult:
    """Run all four selection-bias checks on a strategy.

    Main entry point called by the orchestrator and API routes.

    Args:
        library_pbo: The single full-library CSCV PBO (Bailey et al. 2014) for
            the *current selection set* — the principled criterion-4 input
            (#546). PBO is a property of the whole library, not of one strategy,
            so when this is supplied it is used for criterion 4 in preference to
            the per-cohort ``pbo_scores`` lookup. Compute it from the
            daily-returns store via ``compute_library_pbo`` and recompute on
            every selection-set change (see that function's refresh-policy note).
            Fail-closed: ``None`` (or a non-finite value) makes criterion 4 FAIL
            rather than silently pass — exactly as a missing cohort score does.
            When ``None``, the gate falls back to ``pbo_scores.get(strategy_id)``
            and the verdict is labelled ``source=cohort``.
        average_correlation: Mean pairwise correlation among the ``num_trials``
            trials in the selection set, used by the DSR effective-N correction.
            The caller holds the library/variant returns and computes it via
            ``compute_average_pairwise_correlation``; ``0.0`` (the default)
            applies no relief — conservative for an unknown correlation.
        cv_returns_matrix: A 2-D ``(S, T)`` matrix of per-split out-of-sample
            returns (rows = ``C(n_groups, test_groups)`` combinatorial splits)
            for the CPCV path-stability check. CPCV is mathematically invalid on
            a single static 1-D series, so when no combinatorial paths are
            supplied this stays ``None`` and the CPCV gate is honestly reported
            as ``MISSING`` rather than silently passing.
    """
    if num_trials == 1:
        logger.debug(
            "Rigor gate [%s]: num_trials=1 — no multiple-testing correction. "
            "Pass num_trials=len(strategy_library) for meaningful DSR.",
            strategy_id,
        )

    # 1. DSR — serial-correlation-robust standard error (#621 follow-up).
    #    The Deflated Sharpe z-statistic's IID variance term understates sampling
    #    uncertainty when returns are autocorrelated (Lo 2002), inflating
    #    significance — exactly the premise the IID diagnostic detects. The gate
    #    therefore uses the Newey–West (1987) HAC long-run variance of the Sharpe
    #    influence function, which NESTS the IID form (a no-op on serially
    #    independent returns) and avoids the pre-test distortion of conditionally
    #    swapping SEs only when the IID flag fires (Leeb & Pötscher 2005). The
    #    effective-N correction (average_correlation) still relaxes the
    #    multiple-testing penalty when the trials themselves are correlated.
    #    The HAC-robust verdict (gating) and the IID-SE verdict (advisory) are
    #    computed in a single pass — one numpy coercion, one SciPy moment
    #    computation, one influence-function LRV — so this is ~half the work of
    #    two compute_dsr calls on a path that may evaluate many candidates. The
    #    IID-SE p-value is surfaced as a delta (never gates) so the passport
    #    shows how much the serial-dependence correction moved the verdict.
    deflated_sharpe, dsr_p_value, _, dsr_p_value_iid = compute_dsr_hac_and_iid(
        daily_returns, num_trials, average_correlation, hac_lags="auto"
    )

    # 2. PBO (criterion 4 in the gate ordering) — prefer the full-library CSCV
    #    PBO when supplied (#546). PBO is a property of the selection set, not of
    #    one strategy (Bailey et al. 2014), so the library-wide value is the
    #    principled input; the per-cohort dict is the fallback when no library
    #    PBO is passed. Fail-closed semantics live in RigorGateResult.passes_all:
    #    a None/NaN pbo_score fails criterion 4 regardless of source.
    if library_pbo is not None:
        pbo_score = library_pbo
        pbo_source = "library"
    else:
        pbo_score = pbo_scores.get(strategy_id) if pbo_scores else None
        pbo_source = "cohort"

    # 3. Walk-forward OOS Sharpe (single holdout) + Combinatorial Purged CV.
    #    CPCV runs only when a real 2-D combinatorial OOS matrix is supplied.
    oos_sharpe = compute_oos_sharpe(daily_returns)
    cpcv = compute_cpcv_oos_sharpe(cv_returns_matrix)

    # IID / random-walk diagnostics (#621) — computed AND surfaced (previously
    # computed-and-dropped). ADVISORY only: it never gates pass/fail because a
    # trend/momentum strategy's autocorrelation is its edge, so an IID violation
    # must not reject it. See RigorGateResult.passes_all / gate_details["iid"].
    iid = diagnose(daily_returns)

    # Regime-robustness diagnostic — does the edge survive across volatility regimes,
    # or is it earned only in calm/only-in-one regime (fragile)? Uses vol-based regime
    # labels derived from the series itself (classify_regimes). ADVISORY like IID — it is
    # surfaced, never gates pass/fail: a single-regime edge can still be a legitimate,
    # honestly-disclosed strategy, and promoting this to a hard gate is an admission-policy
    # call for the rigor lane (Dan/Önder). Computed only when the series is long enough to
    # label more than one regime; never allowed to break the gate.
    regime_robustness: dict | None = None
    if len(daily_returns) >= 63:  # ~3 vol windows (vol_window=21) — enough to label >1 regime
        try:
            regime_labels = classify_regimes(daily_returns)
            regime_robustness = regime_robustness_score(daily_returns, regime_labels)
        except Exception as exc:  # an advisory diagnostic must never break the gate
            logger.debug("Regime-robustness diagnostic skipped [%s]: %s", strategy_id, exc)

    # 4. Look-ahead audit
    if strategy_code is not None:
        la_passed, la_warnings = look_ahead_audit(strategy_code)
        if la_warnings:
            for w in la_warnings:
                logger.info("Look-ahead audit [%s]: %s", strategy_id, w)
    else:
        la_passed = False

    if look_ahead_audit_passed is not None:
        la_passed = look_ahead_audit_passed

    # Derive in-sample Sharpe from IS slice (first 70%) only — not the full series.
    # Using the full series blends IS+OOS and makes the OOS/IS ratio trivially easy to pass.
    if in_sample_sharpe is None and len(daily_returns) >= 4:
        arr = np.asarray(daily_returns, dtype=float)
        split = int(len(arr) * 0.70)
        is_arr = arr[:split]
        if len(is_arr) >= 2:
            sigma_is = float(is_arr.std(ddof=1))
            if sigma_is > 0:
                in_sample_sharpe = ((float(is_arr.mean()) - _RF_DAILY) / sigma_is) * math.sqrt(_ANNUALIZATION)

    result = RigorGateResult(
        strategy_id=strategy_id,
        deflated_sharpe=deflated_sharpe,
        dsr_p_value=dsr_p_value,
        dsr_p_value_iid=dsr_p_value_iid,
        dsr_se_method="hac",
        num_trials=num_trials,
        pbo_score=pbo_score,
        oos_sharpe=oos_sharpe,
        look_ahead_passed=la_passed,
        in_sample_sharpe=in_sample_sharpe,
        paper_claimed_sharpe=paper_claimed_sharpe,
        cpcv_mean_oos_sharpe=cpcv["mean_oos_sharpe"] if cpcv else None,
        cpcv_positive_fraction=cpcv["positive_fraction"] if cpcv else None,
        pbo_source=pbo_source,
        iid_assumption_violated=iid.get("iid_assumption_violated"),
        iid_diagnostics=iid,
        regime_robustness=regime_robustness,
    )

    logger.info(
        "Rigor gate [%s]: %s (DSR p=%s, PBO=%s [%s], OOS=%s, CPCV+=%s, LA=%s, IID_violated=%s, regime_robust=%s [advisories])",
        strategy_id,
        "PASS" if result.passes_all else "FAIL",
        dsr_p_value,
        pbo_score,
        pbo_source,
        oos_sharpe,
        cpcv["positive_fraction"] if cpcv else None,
        la_passed,
        iid.get("iid_assumption_violated"),
        regime_robustness.get("robust") if regime_robustness else None,
    )

    return result

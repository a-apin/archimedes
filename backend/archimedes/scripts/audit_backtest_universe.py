"""Audit: does every strategy's PERSISTED backtest actually trade its declared universe?

Mechanical detector for the "backtesting the wrong asset" defect class (audit
2026-07-27) — see ``services/vol_plausibility.py`` for the full rule design and its
honest reasoning/limits. Confirmed on production data: ``capital_preservation_tbill``
declares ``ASSET_UNIVERSE = ["BIL"]`` (a T-bill ETF, real-world vol ~0.2%) but its
persisted backtest has annualized vol 18.65% — that is the S&P, not BIL.
``maillard_2010_risk_parity`` declares a 5-asset risk-parity book (which by
construction should have LOWER vol than pure equity) but persists 18.46% vol —
also pure equity. Both PASS the rigor gate on returns that are not theirs.

DATA SOURCE (read the code, not guessed): the live rigor gate reads persisted daily
returns via ``services/backtest_repository.py::get_daily_returns(session,
strategy_id)`` — the LATEST ``BacktestResultRecord`` row's ``artifact_json ->
results[0].metrics.daily_returns``, falling back to deriving from the equity curve.
This script reads the SAME store, keyed the SAME way (``strategy_id``), so what it
reports is exactly what the gate sees — not a parallel, possibly-stale view.

There is a SECOND, independent store: the ``strategy_daily_returns`` table, keyed by
file ``stem`` rather than ``strategy_id`` (feeds the library-level PBO cross-section
via ``services/rigor_evaluator.py::load_daily_returns_store``). The two stores
disagree for at least one strategy (maillard: ~10.93% vol in ``strategy_daily_returns``
vs 18.46% in ``backtest_results``) — that disagreement is itself a defect signal, so
this script reports the cross-store delta wherever both exist, in addition to (never
instead of) grading against the primary gate-facing store.

Usage (run from the repo root; ``PYTHONPATH=backend`` makes ``archimedes`` importable
since the package lives under ``backend/`` with no top-level ``archimedes/``)::

    PYTHONPATH=backend python -m archimedes.scripts.audit_backtest_universe
    PYTHONPATH=backend python -m archimedes.scripts.audit_backtest_universe --json
    PYTHONPATH=backend python -m archimedes.scripts.audit_backtest_universe --margin 2.0

Exit code: 0 when every strategy is "clean", "skipped" (no persisted backtest yet), or
"insufficient_data"; 1 when ANY strategy is "flagged", "degenerate", "unclassified"
(an audit gap — an honest "needs human review" beats a fabricated verdict, per the
module design), or shows a large cross-store delta. This script never re-runs or
regenerates a backtest — it only reads what is already persisted.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from archimedes.db import get_session, init_db
from archimedes.services.backtest_repository import get_daily_returns, latest_backtests_by_strategy
from archimedes.services.rigor_evaluator import load_daily_returns_store
from archimedes.services.strategy_provider import default_provider
from archimedes.services.vol_plausibility import (
    BAND_MARGIN,
    PlausibilityVerdict,
    assess_strategy,
    compute_vol_stats,
)

logger = logging.getLogger(__name__)

_BASELINE_STRATEGY_STEM = "pipeline_buy_hold"

# Cross-store delta reporting thresholds (informational + attention-worthy, not a
# precision claim): flag a "large" disagreement between backtest_results and
# strategy_daily_returns when it is BOTH a meaningful relative gap AND not just
# noise from a tiny baseline.
_CROSS_STORE_ABS_TOL = 0.02
_CROSS_STORE_REL_TOL = 0.25


def _repo_root() -> Path:
    """Nearest ancestor that contains ``analytics-engine`` — mirrors
    ``scripts/run_backtests.py::_repo_root`` (layout-robust across host/container)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "analytics-engine").is_dir():
            return parent
    return here.parents[3]


class StrategyAuditRow:
    """One printable row: a strategy's declared universe vs. its persisted reality.

    Also carries the before/after rigor metrics ``docs/sprint/a6-rerun.md`` named as
    this script's deliverable table: Sharpe, DSR p, PBO, OOS — read straight off the
    latest ``BacktestResultRecord`` (``backtest_repository.latest_backtests_by_strategy``),
    the same store the module docstring above already grades against. Two of the
    named columns, turnover and cost drag, are deliberately NOT added: the analytics
    engine computes ``turnover_annualized`` / ``cost_drag_annual_pct`` per-run
    (``analytics-engine/.../engine.py::BacktestResult``), but
    ``backtest_mapper.EngineMetricsModel`` declares ``extra="ignore"`` and does not
    define either field, so they are dropped before ever reaching ``backtest_results``
    — persisted rows structurally never carry them. Adding a column for a field that
    does not exist on the model would render '—' for every row forever, which reads as
    "measured but missing" when the truth is "never captured" — the fabrication this
    module's docstring (and CLAUDE.md's hard constraint) warns against. Tracked as a
    follow-up: persist the two fields on ``BacktestResultRecord`` (or read them back out
    of ``artifact_json``, which does still carry them raw, the same way
    ``get_daily_returns`` recovers ``daily_returns``) before adding those columns here.
    """

    def __init__(
        self,
        *,
        name: str,
        strategy_id: str,
        stem: str,
        asset_universe: list[str],
        verdict: PlausibilityVerdict | None,
        cross_store_vol: float | None,
        sharpe_ratio: float | None = None,
        dsr_p_value: float | None = None,
        pbo_score: float | None = None,
        out_of_sample_sharpe: float | None = None,
    ) -> None:
        self.name = name
        self.strategy_id = strategy_id
        self.stem = stem
        self.asset_universe = asset_universe
        self.verdict = verdict  # None means "skipped: no persisted backtest_results row"
        self.cross_store_vol = cross_store_vol
        # Before/after rigor metrics (a6-rerun.md deliverable table) — read directly
        # off the latest backtest_results row, independent of the vol-plausibility
        # verdict above. None means "no persisted BacktestResultRecord", NOT zero.
        self.sharpe_ratio = sharpe_ratio
        self.dsr_p_value = dsr_p_value
        self.pbo_score = pbo_score
        self.out_of_sample_sharpe = out_of_sample_sharpe

    @property
    def status(self) -> str:
        if self.verdict is None:
            return "skipped"
        return self.verdict.status

    @property
    def cross_store_delta(self) -> float | None:
        if self.verdict is None or self.cross_store_vol is None:
            return None
        primary_vol = self.verdict.vol_stats.annualized_vol
        if primary_vol is None:
            return None
        return primary_vol - self.cross_store_vol

    @property
    def cross_store_large_delta(self) -> bool:
        delta = self.cross_store_delta
        if delta is None:
            return False
        primary_vol = self.verdict.vol_stats.annualized_vol if self.verdict else None
        if primary_vol is None:
            return False
        tol = max(_CROSS_STORE_ABS_TOL, _CROSS_STORE_REL_TOL * max(primary_vol, self.cross_store_vol or 0.0))
        return abs(delta) > tol

    @property
    def needs_attention(self) -> bool:
        """Drives the script's exit code — see the module docstring's exit-code contract."""
        if self.verdict is not None and self.verdict.needs_attention:
            return True
        return self.cross_store_large_delta

    def to_dict(self) -> dict:
        v = self.verdict
        return {
            "name": self.name,
            "strategy_id": self.strategy_id,
            "stem": self.stem,
            "asset_universe": self.asset_universe,
            "status": self.status,
            "n_obs": v.vol_stats.n_obs if v else None,
            "annualized_vol": v.vol_stats.annualized_vol if v else None,
            "annualized_return": v.vol_stats.annualized_return if v else None,
            "buckets": list(v.universe.buckets) if v else [],
            "band": list(v.universe.band) if (v and v.universe.band) else None,
            "baseline_vol": v.baseline_vol if v else None,
            "baseline_delta": v.baseline_delta if v else None,
            "cross_store_vol": self.cross_store_vol,
            "cross_store_delta": self.cross_store_delta,
            "cross_store_large_delta": self.cross_store_large_delta,
            "reasons": list(v.reasons) if v else [],
            "needs_attention": self.needs_attention,
            "sharpe_ratio": self.sharpe_ratio,
            "dsr_p_value": self.dsr_p_value,
            "pbo_score": self.pbo_score,
            "out_of_sample_sharpe": self.out_of_sample_sharpe,
        }


def run_audit(*, repo_root: Path | None = None, margin: float = BAND_MARGIN) -> list[StrategyAuditRow]:
    """Read-only pass over every strategy with a persisted backtest. Never writes."""
    repo_root = repo_root or _repo_root()
    init_db()

    provider = default_provider(repo_root=repo_root)
    strategies = [s for s in provider.list_strategies() if s.strategy_code_path]

    with get_session() as session:
        daily_by_strategy_id = {s.id: get_daily_returns(session, s.id) for s in strategies}
        latest_by_strategy_id = latest_backtests_by_strategy(session, [s.id for s in strategies])
        cross_store, cross_store_vintage = load_daily_returns_store(session)

    if cross_store_vintage:
        logger.info("strategy_daily_returns cross-store vintage: %s", cross_store_vintage)

    baseline_vol: float | None = None
    baseline = next((s for s in strategies if Path(s.strategy_code_path).stem == _BASELINE_STRATEGY_STEM), None)
    if baseline is not None:
        baseline_returns = daily_by_strategy_id.get(baseline.id) or []
        if baseline_returns:
            stats = compute_vol_stats(baseline_returns)
            baseline_vol = stats.annualized_vol
    if baseline_vol is None:
        logger.warning(
            "no usable persisted backtest for %r — Rule B (equity-baseline match) skipped for this whole run; "
            "only the self-contained asset-class-band rule (Rule A) will apply",
            _BASELINE_STRATEGY_STEM,
        )

    rows: list[StrategyAuditRow] = []
    for strategy in sorted(strategies, key=lambda s: Path(s.strategy_code_path).stem):
        stem = Path(strategy.strategy_code_path).stem
        daily_returns = daily_by_strategy_id.get(strategy.id) or []

        cross_entry = cross_store.get(stem)
        cross_store_vol: float | None = None
        if cross_entry and cross_entry.get("daily_returns"):
            cross_stats = compute_vol_stats(cross_entry["daily_returns"])
            cross_store_vol = cross_stats.annualized_vol

        # Before/after rigor metrics — read off the SAME latest row get_daily_returns
        # resolved above, independent of whether the vol-plausibility verdict below
        # runs (e.g. a persisted row with an unparseable daily_returns series would
        # still have real sharpe_ratio/dsr_p_value/pbo_score/out_of_sample_sharpe).
        latest_record = latest_by_strategy_id.get(strategy.id)

        if not daily_returns:
            # No persisted backtest_results row for this strategy — skipped, not
            # crashed, and not counted as a failure (nothing to grade yet).
            rows.append(
                StrategyAuditRow(
                    name=strategy.paper_title,
                    strategy_id=strategy.id,
                    stem=stem,
                    asset_universe=list(strategy.asset_universe),
                    verdict=None,
                    cross_store_vol=cross_store_vol,
                    sharpe_ratio=latest_record.sharpe_ratio if latest_record else None,
                    dsr_p_value=latest_record.dsr_p_value if latest_record else None,
                    pbo_score=latest_record.pbo_score if latest_record else None,
                    out_of_sample_sharpe=latest_record.out_of_sample_sharpe if latest_record else None,
                )
            )
            continue

        verdict = assess_strategy(strategy.asset_universe, daily_returns, baseline_vol=baseline_vol, margin=margin)
        rows.append(
            StrategyAuditRow(
                name=strategy.paper_title,
                strategy_id=strategy.id,
                stem=stem,
                asset_universe=list(strategy.asset_universe),
                verdict=verdict,
                cross_store_vol=cross_store_vol,
                sharpe_ratio=latest_record.sharpe_ratio if latest_record else None,
                dsr_p_value=latest_record.dsr_p_value if latest_record else None,
                pbo_score=latest_record.pbo_score if latest_record else None,
                out_of_sample_sharpe=latest_record.out_of_sample_sharpe if latest_record else None,
            )
        )

    return rows


def _fmt_pct(value: float | None) -> str:
    return f"{value * 100:.2f}%" if value is not None else "—"


def _fmt_num(value: float | None, decimals: int = 2) -> str:
    """Plain-decimal formatter for ratio/probability fields (Sharpe, DSR p, PBO,
    OOS Sharpe) — matches the UI convention (StrategyPassport.jsx / Leaderboard.jsx
    render these with `fmt()`, not as a percentage). '—' for None; never fabricated."""
    return f"{value:.{decimals}f}" if value is not None else "—"


def render_table(rows: list[StrategyAuditRow]) -> str:
    headers = [
        "strategy",
        "universe",
        "status",
        "n",
        "vol",
        "return",
        "sharpe",
        "dsr_p",
        "pbo",
        "oos",
        "baseline_Δ",
        "cross_Δ",
        "reason",
    ]
    out_rows: list[list[str]] = []
    for row in rows:
        v = row.verdict
        universe_str = ",".join(row.asset_universe) or "—"
        if len(universe_str) > 28:
            universe_str = universe_str[:25] + "..."
        n_obs = str(v.vol_stats.n_obs) if v else "—"
        vol = _fmt_pct(v.vol_stats.annualized_vol) if v else "—"
        ret = _fmt_pct(v.vol_stats.annualized_return) if v else "—"
        sharpe = _fmt_num(row.sharpe_ratio)
        dsr_p = _fmt_num(row.dsr_p_value, decimals=3)
        pbo = _fmt_num(row.pbo_score)
        oos = _fmt_num(row.out_of_sample_sharpe)
        baseline_delta = _fmt_pct(v.baseline_delta) if (v and v.baseline_delta is not None) else "—"
        cross_delta = _fmt_pct(row.cross_store_delta) if row.cross_store_delta is not None else "—"
        cross_flag = "!" if row.cross_store_large_delta else ""
        reason = "; ".join(v.reasons) if v and v.reasons else ""
        if len(reason) > 60:
            reason = reason[:57] + "..."
        marker = "*" if row.needs_attention else " "
        out_rows.append(
            [
                f"{marker}{row.stem}",
                universe_str,
                row.status,
                n_obs,
                vol,
                ret,
                sharpe,
                dsr_p,
                pbo,
                oos,
                baseline_delta,
                f"{cross_delta}{cross_flag}",
                reason,
            ]
        )

    widths = [max(len(h), *(len(r[i]) for r in out_rows)) if out_rows else len(h) for i, h in enumerate(headers)]
    lines = [
        "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)),
        "  ".join("-" * w for w in widths),
    ]
    for r in out_rows:
        lines.append("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit persisted backtests against each strategy's declared ASSET_UNIVERSE."
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=BAND_MARGIN,
        help=f"Safety margin over each asset-class vol band before flagging (default {BAND_MARGIN}).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a text table.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repo root containing analytics-engine/ (default: auto-detected from this file's location).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args(argv)

    rows = run_audit(repo_root=args.repo_root, margin=args.margin)
    flagged = [r for r in rows if r.needs_attention]

    if args.json:
        print(json.dumps({"strategies": [r.to_dict() for r in rows], "flagged_count": len(flagged)}, indent=2))
    else:
        print(render_table(rows))
        print()
        print(f"{len(rows)} strategies audited, {len(flagged)} need attention (marked '*').")
        if flagged:
            print("Statuses: flagged=implausible vol for declared universe; degenerate=zero-variance series;")
            print(
                "unclassified=unrecognized ticker(s), needs human review; cross_Δ '!'=large cross-store disagreement."
            )

    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())

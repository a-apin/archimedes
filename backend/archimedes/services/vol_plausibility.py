"""Universe/vol plausibility checks — the mechanical detector for "backtesting the
wrong asset" (audit 2026-07-27).

Confirmed defect class: a strategy declares ``ASSET_UNIVERSE`` (e.g. ``["BIL"]``, the
SPDR 1-3 Month T-Bill ETF) but its persisted daily returns are statistically
indistinguishable from pure S&P 500 equity — i.e. the backtest silently ran against
the wrong price series. Root cause (read in ``analytics_engine.cli.run_command``,
2026-07-27): a strategy is only routed through the declared-universe-aware multi-feed
path when its class declares ``REQUIRED_FEEDS == 2`` (pairs). Every other strategy —
single-asset AND multi-asset alike — is backtested against
``RunConfig.operations`` (``BACKTEST_OPERATIONS`` env var, default ``"SPY"``),
**regardless of its own declared ``ASSET_UNIVERSE``**. A strategy that is fully
invested with no real timing signal on that default feed (e.g.
``capital_preservation_tbill``, or ``maillard_2010_risk_parity`` once its
``self.datas`` collapses to the single default feed) therefore reproduces the pure
buy-and-hold S&P return series almost exactly.

This module is deliberately split into two complementary, independently-defensible
rules rather than one "precise" model — see ``assess_strategy`` for how they combine:

Rule A — asset-class plausibility band (self-contained, no cross-strategy state)
----------------------------------------------------------------------------
Every ticker that appears in any strategy's ``ASSET_UNIVERSE`` is resolved to a
*broad* asset-class bucket (cash/T-bill, short/mid/long government bond, broad
equity, sector/thematic equity, single-name equity, REIT, commodity, FX, crypto,
volatility) via the SAME curated classification the live on-chain universe uses —
``archimedes.universe.SYNTHETIC_UNIVERSE`` (``asset_class`` per
``yfinance_ticker``) — plus a small, explicit supplemental table for the handful of
legacy analytics-engine "operation" aliases (``NIKKEI``, ``GOLD``, ``TREASURY``,
``OIL``) and single-name pairs legs (``KO``, ``PEP``) that predate the on-chain SSOT
and are not present in it. Each bucket carries a WIDE, generously-bounded annualized
realized-vol band drawn from well-known multi-decade historical ranges for that asset
class (not per-ticker precision — see ``_BUCKET_VOL_BANDS`` for the numbers and their
honest provenance).

For a strategy whose *entire* declared universe sits in ONE bucket, the realized vol
must fall within ``[lo / margin, hi * margin]``. For a strategy spanning MULTIPLE
buckets (a genuinely diversified book), only the upper bound is enforced — the
portfolio-variance inequality ``sigma_portfolio <= sum_i(w_i * sigma_i) <=
max_i(sigma_i)`` for non-negative, fully-invested weights and correlations <= 1 means
a long-only diversified book can never be MORE volatile than its most volatile single
leg's band, but it can legitimately be far LESS volatile (e.g. a risk-parity book
dominated by its T-bill leg) — so no lower bound is meaningful there.

**Honest limits of Rule A:** these bands are a sanity check, not an oracle. They will
not catch a diversified multi-asset strategy whose realized vol happens to land
inside the (deliberately wide) union of its components' bands — which is exactly why
Rule B exists. They can also be wrong for leveraged/inverse instruments, or for
periods where multi-decade "normal" ranges are a poor guide.

Rule B — equity-baseline match (cross-strategy, targets the exact root cause)
----------------------------------------------------------------------------
``pipeline_buy_hold`` (``ASSET_UNIVERSE = ["SPY"]``, unconditionally fully invested)
is the confirmed-correct reference: its persisted vol IS the real S&P annualized vol.
Because the confirmed root cause is "the backtest silently fell back to the SPY-only
default feed," any OTHER strategy whose declared universe is not that same trivial
single-broad-equity book, but whose realized annualized vol lands within a tight
tolerance of the ``pipeline_buy_hold`` baseline, is exhibiting the exact statistical
signature of that fallback — regardless of whether Rule A's wide bands would also
catch it. This is what catches ``maillard_2010_risk_parity`` (18.46% vs the 18.65%
baseline): its declared 5-asset, risk-parity-weighted universe should structurally
produce a MATERIALLY lower vol than pure single-name equity; landing within ~1% of
the baseline is not a coincidence, it is the single-feed collapse.

**Honest limits of Rule B:** it needs the ``pipeline_buy_hold`` reference to itself
have a persisted backtest (callers degrade gracefully — see ``assess_strategy``'s
``baseline_vol=None`` path — logging the gap rather than silently skipping it). A
strategy that is legitimately, coincidentally close to the equity baseline (e.g. a
genuinely fully-invested-in-SPY strategy with a near-no-op timing signal) will also
trip this rule; that is still an actionable finding worth a human's eyes, not a false
alarm to be tuned away.

This module itself stays pure and hermetic (no DB, no network — everything below
takes plain lists/tuples of tickers and returns). Both callers do their own I/O
first: ``scripts/audit_backtest_universe.py`` (Deliverable 1) reads every
strategy's persisted returns plus the ``pipeline_buy_hold`` baseline once per run
and applies both rules to every strategy; ``scripts/run_backtests.py``'s
write-time guard (Deliverable 2) fetches that same pre-existing baseline ONCE
before its per-strategy loop (not per strategy — one extra query per run) and
applies Rule A + Rule B to each freshly-computed artifact before persisting it,
degrading to Rule-A-only (self-contained, no baseline needed) the first time ever
run — before ``pipeline_buy_hold`` itself has a persisted backtest to compare
against.

Degenerate series
------------------
A constant (zero-range) daily-return series makes ``numpy.std`` numerically
meaningless and is exactly the check ``services/_rigor_helpers.py::compute_dsr``
already uses (``np.ptp(arr) == 0.0``) to bail out to ``None`` rather than emit a
Sharpe/DSR built on division-adjacent noise. This module uses the identical check and
reports it as its own ``degenerate`` status — never silently "clean", never a crash.

Owner: Dan (backtest universe audit, 2026-07-27)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = [
    "BAND_MARGIN",
    "BASELINE_ABS_TOL",
    "BASELINE_REL_TOL",
    "MIN_OBS_FOR_ASSESSMENT",
    "PlausibilityVerdict",
    "UniverseClassification",
    "VolPlausibilityError",
    "VolStats",
    "assess_strategy",
    "classify_ticker",
    "classify_universe",
    "compute_vol_stats",
    "daily_returns_from_equity_curve",
]

_ANNUALIZATION = 252  # trading days/year — matches portfolio_backtester.ANNUALIZATION
# and _rigor_helpers._ANNUALIZATION so numbers agree repo-wide.

# Generous safety margin applied to each bucket's historical vol band before
# flagging (Rule A). 1.5x is deliberately loose: the goal is to catch GROSS
# universe mismatches (an order-of-magnitude-off T-bill, a 5-asset risk-parity
# book reading as pure equity) without tripping on ordinary regime variation
# (e.g. equity vol was ~12% in 2017 and ~35%+ in the 2020 COVID crash — both
# comfortably inside the un-margined band already).
BAND_MARGIN = 1.5

# Rule B tolerance: how close realized vol must be to the pipeline_buy_hold
# baseline to count as a "matches equity baseline" flag. Absolute floor of 75bp
# (so tiny baselines don't produce a vanishingly tight window) OR 3% relative,
# whichever is LARGER. Calibrated against the two confirmed cases: BIL's 18.65%
# is an exact match (0bp delta); maillard's 18.46% is a ~1% relative delta —
# both comfortably inside; a genuine market-timing strategy that spends a
# meaningful fraction of its days out of the market moves vol by double-digit
# percent (vol scales roughly with sqrt(time-invested-fraction)), safely outside.
BASELINE_ABS_TOL = 0.0075
BASELINE_REL_TOL = 0.03

# Below this many observations, vol/return estimates are too noisy to assess —
# report "insufficient_data" rather than a confident verdict either way.
MIN_OBS_FOR_ASSESSMENT = 5

# ─── Fine asset_class (archimedes.universe SSOT) → broad plausibility bucket ──
#
# The SSOT (`backend/archimedes/data/synthetic_universe.json`, loaded via
# `archimedes.universe.SYNTHETIC_UNIVERSE`) already curates ~281 real,
# yfinance-backed instruments into ~30 FINE asset classes for the on-chain
# universe. We fold those fine classes into a small set of BROAD vol buckets —
# reusing an existing, reviewed SSOT rather than hand-rolling a second ticker
# taxonomy. Any fine class not listed here maps to `None` (unclassified) rather
# than guessing.
_FINE_CLASS_TO_BUCKET: dict[str, str] = {
    "us_bond_tbill": "cash_tbill",
    "us_bond_short": "short_bond",
    "us_muni": "short_bond",
    "us_bond_mid": "mid_bond",
    "us_bond_tips": "mid_bond",
    "credit_ig": "mid_bond",
    "us_bond_long": "long_bond",
    "us_bond_agg": "long_bond",
    "intl_bond": "long_bond",
    "em_bond": "long_bond",
    "credit_hy": "long_bond",
    "us_equity_etf": "broad_equity",
    "intl_equity_etf": "broad_equity",
    "eu_equity_etf": "broad_equity",
    "eu_index": "broad_equity",
    "asia_equity_etf": "broad_equity",
    "asia_index": "broad_equity",
    "em_equity_etf": "broad_equity",
    "latam_equity_etf": "broad_equity",
    "tr_equity_etf": "broad_equity",
    "tr_index": "broad_equity",
    "factor_etf": "broad_equity",
    "us_sector_etf": "sector_thematic_equity",
    "us_thematic_etf": "sector_thematic_equity",
    "reit_etf": "reit",
    "metal_etf": "commodity",
    "metal_eq_etf": "commodity",
    "metal_spot": "commodity",
    "metal_fut": "commodity",
    "energy_etf": "commodity",
    "energy_fut": "commodity",
    "commodity_etf": "commodity",
    "agri_etf": "commodity",
    "agri_fut": "commodity",
    "fx": "fx",
    "crypto": "crypto",
    "volatility_etf": "volatility",
}

# Supplemental map for tickers that are NOT in the on-chain synthetic-universe
# SSOT: legacy analytics-engine "operation" aliases (the original 5-operation
# universe predates the SSOT — see
# analytics-engine/src/archimedes_analytics_engine/instruments.py) and the two
# single-name Gatev-pairs legs. Kept intentionally tiny and explicit — every
# entry here is a documented, named exception, not a fallback heuristic.
_SUPPLEMENTAL_TICKER_BUCKET: dict[str, str] = {
    "NIKKEI": "broad_equity",  # operation alias for ^N225 — broad Japanese equity index
    "GOLD": "commodity",  # operation alias for GC=F — gold futures
    "TREASURY": "long_bond",  # operation alias for TLT — long-duration UST proxy
    "OIL": "commodity",  # operation alias for CL=F — crude oil futures
    "KO": "single_name_equity",  # Coca-Cola — Gatev 2006 pairs leg
    "PEP": "single_name_equity",  # PepsiCo — Gatev 2006 pairs leg
}

# Broad-bucket → (lo, hi) plausible annualized-vol band, un-margined. Wide,
# multi-decade, order-of-magnitude-honest ranges — NOT a precise per-ticker
# oracle. Sourced from well-known asset-class realized-vol characteristics
# (e.g. Ilmanen "Expected Returns" asset-class vol tables; standard practitioner
# ranges). Deliberately erring toward under-flagging: the goal is to catch
# strategies trading a fundamentally different RISK CLASS than declared, not to
# certify precise correctness.
_BUCKET_VOL_BANDS: dict[str, tuple[float, float]] = {
    "cash_tbill": (0.0, 0.03),
    "short_bond": (0.0, 0.07),
    "mid_bond": (0.0, 0.11),
    "long_bond": (0.02, 0.20),
    "broad_equity": (0.08, 0.35),
    "sector_thematic_equity": (0.10, 0.45),
    "single_name_equity": (0.12, 0.55),
    "reit": (0.10, 0.40),
    "commodity": (0.10, 0.55),
    "fx": (0.03, 0.18),
    "crypto": (0.25, 1.50),
    "volatility": (0.30, 2.00),
}


def _ticker_asset_class_map() -> dict[str, str]:
    """``{yfinance_ticker.upper(): asset_class}`` from the on-chain universe SSOT.

    Lazy + re-derived per call (cheap — ``SYNTHETIC_UNIVERSE`` is already a
    module-level dict loaded once by ``archimedes.universe`` at import time; this
    just re-indexes it) so a test can monkeypatch ``archimedes.universe.SYNTHETIC_UNIVERSE``
    without needing to reload this module.
    """
    from archimedes.universe import SYNTHETIC_UNIVERSE

    out: dict[str, str] = {}
    for spec in SYNTHETIC_UNIVERSE.values():
        ticker = (spec.yfinance_ticker or "").upper()
        if ticker:
            out[ticker] = spec.asset_class
    return out


def classify_ticker(ticker: str) -> str | None:
    """Resolve one declared-universe ticker to a broad vol bucket, or ``None``.

    Resolution order: (1) the on-chain synthetic-universe SSOT
    (``archimedes.universe.SYNTHETIC_UNIVERSE``, keyed by ``yfinance_ticker``),
    (2) the small explicit supplemental table for legacy operation aliases /
    single-name pairs legs. Anything else is honestly reported as unclassified —
    see the module docstring: "needs human review" beats a fabricated verdict.
    """
    t = ticker.strip().upper()
    if not t:
        return None
    fine = _ticker_asset_class_map().get(t)
    if fine is not None:
        return _FINE_CLASS_TO_BUCKET.get(fine)
    return _SUPPLEMENTAL_TICKER_BUCKET.get(t)


@dataclass(frozen=True)
class UniverseClassification:
    """Bucket classification of one strategy's declared ``ASSET_UNIVERSE``."""

    tickers: tuple[str, ...]
    buckets: tuple[str, ...]  # sorted distinct buckets actually resolved
    unclassified: tuple[str, ...]  # tickers with no known bucket, sorted
    band: tuple[float, float] | None  # combined (lo, hi); None if any ticker unclassified

    @property
    def is_diversified(self) -> bool:
        """True when the declared universe spans more than one broad bucket."""
        return len(self.buckets) > 1

    @property
    def is_fully_classified(self) -> bool:
        return not self.unclassified


def classify_universe(tickers: list[str] | tuple[str, ...]) -> UniverseClassification:
    """Classify every declared ticker and combine into a plausibility band.

    Combination rule (see module docstring for the portfolio-variance
    justification): the band's upper bound is the MAX across all resolved
    buckets' upper bounds (a long-only diversified book cannot be more volatile
    than its most volatile leg); the lower bound is only meaningful — and only
    applied by ``assess_strategy`` — when the universe resolves to a SINGLE
    bucket. ``band`` is ``None`` whenever any declared ticker fails to resolve,
    so callers never silently apply a partial/wrong band.
    """
    resolved: dict[str, str] = {}
    unclassified: list[str] = []
    for t in tickers:
        bucket = classify_ticker(t)
        if bucket is None:
            unclassified.append(t.strip().upper())
        else:
            resolved[t.strip().upper()] = bucket

    buckets = sorted(set(resolved.values()))
    band: tuple[float, float] | None = None
    if buckets and not unclassified:
        los = [_BUCKET_VOL_BANDS[b][0] for b in buckets]
        his = [_BUCKET_VOL_BANDS[b][1] for b in buckets]
        band = (min(los), max(his))

    return UniverseClassification(
        tickers=tuple(t.strip().upper() for t in tickers),
        buckets=tuple(buckets),
        unclassified=tuple(sorted(set(unclassified))),
        band=band,
    )


@dataclass(frozen=True)
class VolStats:
    """Realized annualized vol/return computed from a persisted daily-return series."""

    n_obs: int
    annualized_vol: float | None
    annualized_return: float | None
    degenerate: bool
    insufficient_data: bool


def compute_vol_stats(daily_returns: list[float] | np.ndarray) -> VolStats:
    """Annualized vol + return from a daily-return series.

    Vol: ``std(ddof=1) * sqrt(252)`` — matches
    ``services/portfolio_backtester.py::_annualized_metrics`` and
    ``services/_rigor_helpers.py`` exactly, so the numbers this tool prints agree
    with every other rigor-gate computation in the repo.

    Return: geometric CAGR over a synthetic equity curve built by compounding the
    daily returns from 1.0 — ``(curve[-1] / curve[0]) ** (252 / T) - 1`` — the same
    formula ``_annualized_metrics`` uses. Returns ``None`` if the series ever
    drives the synthetic curve to <= 0 (a >= -100% day makes CAGR undefined).

    Degenerate check: ``np.ptp(arr) == 0.0`` — IDENTICAL to the guard
    ``services/_rigor_helpers.py::_sharpe_dsr_inputs`` uses to bail ``compute_dsr``
    out to ``(None, None)`` on a constant series (checking range rather than
    ``std(ddof=1) == 0`` because floating-point cancellation can leave a tiny
    non-zero std for an actually-constant series).
    """
    arr = np.asarray(daily_returns, dtype=float)
    n = int(arr.size)

    if n < MIN_OBS_FOR_ASSESSMENT:
        return VolStats(n_obs=n, annualized_vol=None, annualized_return=None, degenerate=False, insufficient_data=True)

    if float(np.ptp(arr)) == 0.0:
        return VolStats(n_obs=n, annualized_vol=None, annualized_return=None, degenerate=True, insufficient_data=False)

    sigma = float(arr.std(ddof=1))
    ann_vol = sigma * math.sqrt(_ANNUALIZATION)

    curve = np.cumprod(1.0 + arr)
    ann_return: float | None = None if curve[-1] <= 0 else float(curve[-1] ** (_ANNUALIZATION / n) - 1.0)

    return VolStats(
        n_obs=n, annualized_vol=ann_vol, annualized_return=ann_return, degenerate=False, insufficient_data=False
    )


def daily_returns_from_equity_curve(equity_curve: list[float] | np.ndarray) -> list[float]:
    """Derive a daily-return series from an equity curve via pct-change.

    Shared by the write-time guard (``scripts/run_backtests.py``), which has a
    freshly-mapped ``BacktestResult.equity_curve`` available before the artifact
    is persisted but not yet a DB row to re-read ``daily_returns`` from. This is
    the SAME fallback formula ``services/backtest_repository.py::get_daily_returns``
    uses when a persisted row's ``artifact_json`` has no raw ``daily_returns``
    array — so a strategy checked at write time and re-checked later by the audit
    script (Deliverable 1) sees numerically consistent vol figures either way.
    """
    arr = np.asarray(equity_curve, dtype=float)
    if arr.size < 2:
        return []
    return ((arr[1:] - arr[:-1]) / arr[:-1]).tolist()


class VolPlausibilityError(Exception):
    """Raised by the write-time guard when a freshly-computed backtest's realized
    vol is wildly inconsistent with its strategy's declared ``ASSET_UNIVERSE``.

    Callers (e.g. ``scripts/run_backtests.py``) should catch this alongside their
    other typed engine errors and REFUSE to persist the row rather than silently
    writing a backtest that reads as the wrong asset — this is the deliverable-2
    permanent guard for the confirmed "backtesting the wrong asset" defect class
    (see the module docstring for the Rule A / Rule B split it applies).
    """


@dataclass(frozen=True)
class PlausibilityVerdict:
    """Combined verdict from ``assess_strategy``."""

    status: str  # "clean" | "flagged" | "unclassified" | "degenerate" | "insufficient_data"
    reasons: tuple[str, ...]
    universe: UniverseClassification
    vol_stats: VolStats
    baseline_vol: float | None
    baseline_delta: float | None

    @property
    def is_clean(self) -> bool:
        return self.status == "clean"

    @property
    def needs_attention(self) -> bool:
        """True for every status a human should look at — everything except a
        confirmed-clean read and a "too little data yet" read."""
        return self.status in ("flagged", "unclassified", "degenerate")


def _evaluate_class_band(
    universe: UniverseClassification,
    realized_vol: float,
    *,
    margin: float,
) -> list[str]:
    """Rule A. Returns a list of human-readable violation reasons (empty = passes)."""
    if universe.band is None:
        return []  # unclassified — handled separately, not a Rule-A verdict
    lo, hi = universe.band
    reasons: list[str] = []
    hi_bound = hi * margin
    if realized_vol > hi_bound:
        reasons.append(
            f"realized vol {realized_vol:.4f} exceeds the declared-universe plausible band "
            f"upper bound {hi_bound:.4f} ({margin}x margin over bucket(s) {', '.join(universe.buckets)})"
        )
    # Lower bound only meaningful (and only enforced) for a single-bucket
    # universe — see module docstring: diversification can legitimately push a
    # multi-bucket book's vol far below any single component's floor.
    if len(universe.buckets) == 1 and lo > 0:
        lo_bound = lo / margin
        if realized_vol < lo_bound:
            reasons.append(
                f"realized vol {realized_vol:.4f} is below the declared-universe plausible band "
                f"lower bound {lo_bound:.4f} (bucket {universe.buckets[0]!r})"
            )
    return reasons


def _evaluate_baseline_match(
    universe: UniverseClassification,
    realized_vol: float,
    baseline_vol: float,
    *,
    abs_tol: float,
    rel_tol: float,
) -> list[str]:
    """Rule B. Returns violation reasons (empty = no suspicious match)."""
    # The reference strategy's own trivial single-broad-equity universe
    # legitimately matches itself — nothing to flag there. Any other universe
    # that lands this close to the pure-equity baseline is suspicious.
    if universe.buckets == ("broad_equity",) and len(universe.tickers) == 1:
        return []
    tol = max(abs_tol, rel_tol * baseline_vol)
    delta = abs(realized_vol - baseline_vol)
    if delta <= tol:
        return [
            f"realized vol {realized_vol:.4f} matches the pipeline_buy_hold equity baseline "
            f"{baseline_vol:.4f} within tolerance {tol:.4f} despite a non-equity-only declared "
            f"universe {list(universe.tickers)!r} — statistical signature of the confirmed "
            "single-feed-default fallback (see module docstring)"
        ]
    return []


def assess_strategy(
    tickers: list[str] | tuple[str, ...],
    daily_returns: list[float] | np.ndarray,
    *,
    baseline_vol: float | None = None,
    margin: float = BAND_MARGIN,
    baseline_abs_tol: float = BASELINE_ABS_TOL,
    baseline_rel_tol: float = BASELINE_REL_TOL,
) -> PlausibilityVerdict:
    """Single entry point combining degenerate/insufficient-data/Rule-A/Rule-B.

    ``baseline_vol=None`` skips Rule B entirely — the caller degrades to the
    self-contained Rule A when no ``pipeline_buy_hold`` baseline is available yet
    (see the module docstring). Both the audit script and the write-time guard
    normally pass the ``pipeline_buy_hold`` realized vol here to get the full
    two-rule check.
    """
    universe = classify_universe(list(tickers))
    vol_stats = compute_vol_stats(daily_returns)

    if vol_stats.insufficient_data:
        return PlausibilityVerdict(
            status="insufficient_data",
            reasons=(f"only {vol_stats.n_obs} observations (< {MIN_OBS_FOR_ASSESSMENT})",),
            universe=universe,
            vol_stats=vol_stats,
            baseline_vol=baseline_vol,
            baseline_delta=None,
        )

    if vol_stats.degenerate:
        return PlausibilityVerdict(
            status="degenerate",
            reasons=(f"constant daily-return series across {vol_stats.n_obs} observations (zero range)",),
            universe=universe,
            vol_stats=vol_stats,
            baseline_vol=baseline_vol,
            baseline_delta=None,
        )

    assert vol_stats.annualized_vol is not None  # guaranteed by the two branches above
    realized_vol = vol_stats.annualized_vol

    reasons: list[str] = []
    reasons.extend(_evaluate_class_band(universe, realized_vol, margin=margin))

    baseline_delta: float | None = None
    if baseline_vol is not None:
        baseline_delta = realized_vol - baseline_vol
        reasons.extend(
            _evaluate_baseline_match(
                universe, realized_vol, baseline_vol, abs_tol=baseline_abs_tol, rel_tol=baseline_rel_tol
            )
        )

    if reasons:
        status = "flagged"
    elif not universe.tickers:
        status = "unclassified"
        reasons = ["strategy declares no ASSET_UNIVERSE — cannot assess plausibility"]
    elif not universe.is_fully_classified:
        status = "unclassified"
        reasons = [f"unrecognized ticker(s), cannot assess plausibility: {list(universe.unclassified)!r}"]
    else:
        status = "clean"

    return PlausibilityVerdict(
        status=status,
        reasons=tuple(reasons),
        universe=universe,
        vol_stats=vol_stats,
        baseline_vol=baseline_vol,
        baseline_delta=baseline_delta,
    )

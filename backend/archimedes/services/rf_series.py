"""Historical 3-month T-bill risk-free-rate series (FRED DGS3MO), issue #1409.

Owner decision (2026-08-20, supersedes the open question in #1278): the rigor
gate's risk-free rate moves from the flat 5% annual constant
(``_rigor_helpers._RF_ANNUAL``) to the actual historical 3-month T-bill
series, aligned per backtest window. The 3M T-bill is the universal
convention for a USD Sharpe ratio (Sharpe 1994); Bailey & Lopez de Prado
(2014) define the Deflated Sharpe Ratio on EXCESS returns, so the rf input is
load-bearing for every DSR the gate computes. A flat constant mis-grades any
window whose true rf differs materially — the flat 5% over-subtracts by
roughly 120bps against the ~3.79%-3.87% environment this series was vendored
in, and historically rf ranged from ~0.00% (2008-2015 ZIRP era) to ~17% (early
1980s) — a single scalar cannot represent that.

**Vendored, not fetched at runtime.** Determinism is load-bearing here (same
inputs -> same gate verdict, per the provenance/commit-reveal posture this
repo holds everywhere else): ``data/rf/DGS3MO.csv`` is a ONE-TIME download
(see ``scripts/refresh_rf_series.py`` to re-fetch/extend it), never a network
call from the grading path. This module is stdlib-only (``csv``, ``bisect``,
``datetime``) by design — the CSV is trivial enough that a pandas/numpy
dependency here would be pure overhead, and this module must stay importable
and fast on every rigor-gate hot-path call.

**Alignment (design item 2).** Per backtest bar date: an exact match uses
that date's published rate. A date that falls between two published dates
(a weekend or a market holiday) forward-fills from the most recent PRIOR
published date (a Saturday resolves to the preceding Friday's rate). A date
past the vendored series' last published date forward-fills the same way, up
to a 14-calendar-day grace window; beyond that the WHOLE grade falls back to
the flat rate, loudly (never silently) — see ``resolve_annual_rf_for_dates``.

**Provenance marker.** Every resolution returns one of two conventions,
carried wherever ``dsr_convention`` already flows (passport payloads
included):
  - ``RF_CONVENTION_SERIES``   -- resolved from the vendored T-bill series.
  - ``RF_CONVENTION_FALLBACK`` -- the disclosed flat-rate fallback (no dates
    supplied, dates outside the vendored coverage, or the vendored file is
    missing/unreadable).
"""

from __future__ import annotations

import csv
import logging
from bisect import bisect_right
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── Provenance markers (ride the same payload paths dsr_convention does) ──
RF_CONVENTION_SERIES = "excess_tbill_series"
RF_CONVENTION_FALLBACK = "excess_flat_fallback"

# Design item 2: forward-fill past the vendored series' last date is allowed
# up to this many calendar days before the WHOLE grade falls back to flat.
MAX_FORWARD_FILL_DAYS = 14

# Duplicated in VALUE (not imported) from `_rigor_helpers._RF_ANNUAL` (0.05 ==
# 5%): `rf_series` stays a leaf module with zero archimedes-internal imports
# (see the module docstring — stdlib only), so it cannot import the constant
# it mirrors. `_rigor_helpers._resolve_rf_daily_array` always passes its own
# `_RF_ANNUAL * 100.0` explicitly as `flat_annual_pct` on every real call, so
# this default only matters to a caller of `rf_series` directly (as the tests
# do) — it is not the value actually used by the gate.
DEFAULT_FLAT_ANNUAL_PCT = 5.0

_CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "rf" / "DGS3MO.csv"

# Missing-observation markers seen across FRED's export paths: the classic
# series-API CSV uses a literal ".", the fredgraph.csv path used to vendor
# this file emits an empty field instead. Both are skipped without poisoning
# neighboring dates (never coerced to 0.0 or interpolated).
_MISSING_MARKERS = frozenset({"", "."})

# Module-level cache: the ~11.7k-row CSV is parsed exactly once per process.
# Historical values never change (only new dates append via
# scripts/refresh_rf_series.py), so caching is safe for the process lifetime.
_cache: dict[str, float] | None = None
_cache_sorted_dates: list[str] | None = None


def _parse_csv_text(text: str) -> dict[str, float]:
    """Pure parse of FRED DGS3MO CSV text into ``{iso_date: annual_percent}``.

    Skips ``#``-prefixed header-comment lines (the staleness note vendored at
    the top of ``DGS3MO.csv``) and any row whose value is a missing-value
    marker (see ``_MISSING_MARKERS``) -- those rows are dropped entirely,
    never coerced to a number, so a market-holiday "." (or empty) row cannot
    poison a neighboring date's rate.

    Accepts either ``DATE`` or ``observation_date`` as the date column name
    (FRED has used both across its export paths) and ``DGS3MO`` as the value
    column.
    """
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    out: dict[str, float] = {}
    for row in reader:
        raw_date = row.get("DATE") or row.get("observation_date")
        raw_value = row.get("DGS3MO")
        if not raw_date or raw_value is None:
            continue
        value = raw_value.strip()
        if value in _MISSING_MARKERS:
            continue
        try:
            out[raw_date.strip()] = float(value)
        except ValueError:
            logger.warning("rf_series: unparseable DGS3MO value %r on %s -- row skipped", raw_value, raw_date)
            continue
    return out


def _load_csv() -> dict[str, float]:
    """Load + cache the vendored series. Never raises -- a missing/corrupt
    vendored file degrades to an empty series, which makes every resolution
    fall back to the flat rate (loudly), never crash the rigor gate."""
    global _cache, _cache_sorted_dates
    if _cache is not None:
        return _cache
    try:
        text = _CSV_PATH.read_text(encoding="utf-8")
        parsed = _parse_csv_text(text)
    except OSError as exc:
        logger.warning(
            "rf_series: could not read vendored series at %s (%s) -- every rf resolution "
            "will fall back to the flat rate (rf_convention=%s)",
            _CSV_PATH,
            exc,
            RF_CONVENTION_FALLBACK,
        )
        parsed = {}
    _cache = parsed
    _cache_sorted_dates = sorted(parsed.keys())
    return _cache


def _sorted_dates() -> list[str]:
    _load_csv()
    assert _cache_sorted_dates is not None  # populated by _load_csv() above
    return _cache_sorted_dates


def _to_iso(value: str | date | datetime) -> str:
    if isinstance(value, str):
        return value[:10]
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    # numpy.datetime64 (2026-08-21 review fix): duck-typed by class name, not
    # `isinstance(value, np.datetime64)` -- this module stays stdlib-only by
    # design (see the module docstring), so it cannot import numpy just to
    # recognize this one type. `.values` / `.to_numpy()` on a pandas
    # DatetimeIndex or Series (as opposed to iterating the index directly,
    # which yields `pd.Timestamp` -- already handled above via the
    # `datetime` branch, `Timestamp` being a `datetime` subclass) is exactly
    # the "type a pandas/numpy backtest series is most likely to actually
    # emit" this module's own docstring anticipates as a real caller. Every
    # numpy.datetime64 precision (D/s/ms/us/ns/...) stringifies to an
    # ISO-8601-prefixed form (`str(np.datetime64("2015-01-02")) ==
    # "2015-01-02"`, `str(np.datetime64("2015-01-02T00:00:00.000000000")) ==
    # "2015-01-02T00:00:00.000000000"`), so `[:10]` recovers the date the
    # same way the `datetime`/`date` branch above does -- no numpy import
    # needed to convert it, only to recognize it. A malformed value smuggled
    # in this way (e.g. `numpy.datetime64("NaT")`, which stringifies to
    # `"NaT"`) is NOT silently accepted here -- it falls through to the
    # `date.fromisoformat` parse below, which raises `ValueError` and is
    # caught by this function's caller the same way any other malformed
    # string is (degrades the WHOLE grade to the flat fallback, loudly).
    if type(value).__name__ == "datetime64":
        return str(value)[:10]
    raise TypeError(f"rf_series: unsupported date type {type(value)!r} (expected str or datetime.date)")


def _forward_fill_at(iso: str, sorted_dates: list[str], series: dict[str, float]) -> float | None:
    """Most recent published rate at or before ``iso`` (weekend/holiday
    forward-fill within the vendored series' own coverage) -- None if ``iso``
    is before the series' first published date."""
    idx = bisect_right(sorted_dates, iso) - 1
    if idx < 0:
        return None
    return series[sorted_dates[idx]]


def resolve_annual_rf_for_dates(
    dates: Sequence[str | date | datetime] | None,
    *,
    flat_annual_pct: float = DEFAULT_FLAT_ANNUAL_PCT,
) -> tuple[list[float], str]:
    """Resolve a per-bar annualized rf rate (in PERCENT, matching FRED's own
    units) aligned to ``dates``, plus the provenance marker.

    Returns ``([], RF_CONVENTION_FALLBACK)`` when ``dates`` is empty/None (no
    date index to align against at all) -- this is the disclosed
    "caller didn't thread dates" case (see the module docstring); the empty
    list length signals the caller must broadcast ``flat_annual_pct`` itself.

    Otherwise returns ``(rates, RF_CONVENTION_SERIES)`` when every date
    resolves against the vendored series (exact match or in-window
    forward-fill), or ``(flat_rates, RF_CONVENTION_FALLBACK)`` -- one flat
    value per date -- the moment ANY date in the window is unresolvable (more
    than ``MAX_FORWARD_FILL_DAYS`` past the series' last published date,
    before the series' first date, malformed / non-ISO / an unsupported type,
    or the vendored series itself is empty). Fail-soft rule: the whole grade
    falls back together rather than mixing real and flat rates bar-by-bar,
    and the fallback is always logged loudly.
    """
    if not dates:
        logger.info(
            "rf_series: no date index supplied -- using the flat %.2f%% risk-free rate "
            "(rf_convention=%s, disclosed fallback, not an error)",
            flat_annual_pct,
            RF_CONVENTION_FALLBACK,
        )
        return [], RF_CONVENTION_FALLBACK

    series = _load_csv()
    sorted_dates = _sorted_dates()
    if not series:
        logger.warning(
            "rf_series: vendored DGS3MO series unavailable -- whole grade (%d bars) falls back "
            "to the flat %.2f%% risk-free rate (rf_convention=%s)",
            len(dates),
            flat_annual_pct,
            RF_CONVENTION_FALLBACK,
        )
        return [flat_annual_pct] * len(dates), RF_CONVENTION_FALLBACK

    last_date = sorted_dates[-1]
    last_date_obj = date.fromisoformat(last_date)

    resolved: list[float] = []
    for raw in dates:
        # #1409 review fix (2026-08-20): a malformed date (non-ISO string, or
        # an unsupported type such as numpy.datetime64 -- see `_to_iso`) used
        # to raise uncaught out of this function, crashing the whole rigor
        # gate. That contradicts this module's own fail-soft posture (every
        # OTHER unresolvable-date case below degrades to the flat rate,
        # loudly) and the mechanism is explicitly "ready to wire the moment a
        # caller has real per-bar dates" -- a caller's date format is exactly
        # the kind of input this function must not trust blindly. Same
        # response as every other failure mode here: the WHOLE grade falls
        # back to flat, loudly, never a crash.
        try:
            iso = _to_iso(raw)
        except TypeError as exc:
            logger.warning(
                "rf_series: unsupported date type %r for entry %r (%s) -- WHOLE grade (%d bars) "
                "falls back to the flat %.2f%% risk-free rate (rf_convention=%s)",
                type(raw),
                raw,
                exc,
                len(dates),
                flat_annual_pct,
                RF_CONVENTION_FALLBACK,
            )
            return [flat_annual_pct] * len(dates), RF_CONVENTION_FALLBACK

        exact = series.get(iso)
        if exact is not None:
            resolved.append(exact)
            continue

        try:
            d_obj = date.fromisoformat(iso)
        except ValueError as exc:
            logger.warning(
                "rf_series: malformed date %r (%s) -- WHOLE grade (%d bars) falls back to the "
                "flat %.2f%% risk-free rate (rf_convention=%s)",
                raw,
                exc,
                len(dates),
                flat_annual_pct,
                RF_CONVENTION_FALLBACK,
            )
            return [flat_annual_pct] * len(dates), RF_CONVENTION_FALLBACK
        if d_obj > last_date_obj:
            gap_days = (d_obj - last_date_obj).days
            if gap_days > MAX_FORWARD_FILL_DAYS:
                logger.warning(
                    "rf_series: date %s is %d calendar days past the vendored series' last "
                    "published date (%s), beyond the %d-day forward-fill grace window -- WHOLE "
                    "grade (%d bars) falls back to the flat %.2f%% risk-free rate "
                    "(rf_convention=%s). Run scripts/refresh_rf_series.py to extend coverage.",
                    iso,
                    gap_days,
                    last_date,
                    MAX_FORWARD_FILL_DAYS,
                    len(dates),
                    flat_annual_pct,
                    RF_CONVENTION_FALLBACK,
                )
                return [flat_annual_pct] * len(dates), RF_CONVENTION_FALLBACK
            resolved.append(series[last_date])  # forward-fill the series' own last value
            continue

        filled = _forward_fill_at(iso, sorted_dates, series)
        if filled is None:
            logger.warning(
                "rf_series: date %s is before the vendored series' first published date (%s) -- "
                "WHOLE grade (%d bars) falls back to the flat %.2f%% risk-free rate "
                "(rf_convention=%s)",
                iso,
                sorted_dates[0],
                len(dates),
                flat_annual_pct,
                RF_CONVENTION_FALLBACK,
            )
            return [flat_annual_pct] * len(dates), RF_CONVENTION_FALLBACK
        resolved.append(filled)

    return resolved, RF_CONVENTION_SERIES
